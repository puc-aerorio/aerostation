import aiohttp
import asyncio
import contextlib
import json
import configparser
import io
import base64
from datetime import date, datetime
from urllib.parse import urlencode
from .update_periodically_consumer import get_device_from_list_by_id, append_device_to_persistant_list
from channels.generic.websocket import AsyncWebsocketConsumer

from ..utils.logger import Logger


ALLOWED_METHODS = ('get', 'post', 'delete')

# Emitted by the ground station itself (never by a drone) when an outbound
# request fails. Without it a failed request becomes an unretrieved asyncio
# task exception and disappears -- which matters most for polls, where a 404
# is a routine outcome rather than a bug.
GS_ERROR_TYPE = 900


def parse_command_entry(command):
  # "<endpoint>,<method>[,<param>...]" -> (endpoint, method, allowed_params)
  # The endpoint is NOT stripped: a trailing slash is part of the contract.
  parts = config['commands-list'][str(command)].split(',')
  endpoint = parts[0]
  method = parts[1].strip().lower() if len(parts) > 1 else 'get'
  allowed = [p.strip() for p in parts[2:] if p.strip()]
  return endpoint, method, allowed


def build_url(device_ip, endpoint):
  # device['ip'] is "host:port/". Strip only the IP's trailing slash and only
  # the endpoint's leading one, so a trailing slash on the endpoint survives.
  return "http://" + device_ip.rstrip('/') + '/' + endpoint.lstrip('/')


def build_query(params, allowed):
  # Whitelist: anything the command table does not name is dropped.
  return {k: v for k, v in (params or {}).items()
          if k in allowed and v is not None and v != ''}


def poll_key(command, receiver_id, params):
  # Keyed by identity rather than the old 28/29-30/31 parity convention, so
  # e.g. a stdout tail and a stderr tail can poll concurrently.
  return f"{command}|{receiver_id}|" + urlencode(sorted((params or {}).items()))


class PostConsumer(AsyncWebsocketConsumer):
  # Websocket consumer that handles POST requests received.
  # The 'post_to_socket' VIEW receive the request, call 'receive_post' method from this class and send it to JS.
  # Receive msgs from JS, with the 'receive' method and send it to the specific device(s) with HTTP request.

  def __init__(self) -> None:
      super().__init__()
      self.async_tasks = set()
      self.polling_tasks = {}

  async def connect(self):
    # Called when websocket connection is required (when corresponding url is accessed).
    global post_consumer_instance
    await self.accept()
    # Instantiate itself, so 'post_to_socket' view can access this class method.
    post_consumer_instance = self


  async def disconnect(self, close_code):
    # Called when websocket connection is closed. Cancellation is awaited:
    # a bare .cancel() only schedules it, so without the gather the poll loops
    # can outlive the socket they were reporting to.
    tasks = list(self.async_tasks) + list(self.polling_tasks.values())
    for task in tasks:
      task.cancel()
    if tasks:
      await asyncio.gather(*tasks, return_exceptions=True)
    self.async_tasks.clear()
    self.polling_tasks.clear()
    print(f'Post websocket disconnected {close_code}')


  def track(self, coro):
    # Fire-and-forget a coroutine while keeping a reference (asyncio only holds
    # a weak one) and discarding it on completion, so the set cannot grow
    # unbounded over the life of the consumer.
    task = asyncio.create_task(coro)
    self.async_tasks.add(task)
    task.add_done_callback(self.async_tasks.discard)
    return task


  async def send_to_ui(self, payload, gs_command=None):
    # Stamp the frame with the command that produced it. uav_api's own numeric
    # 'type' codes are deprecated upstream, so the interface dispatches on
    # 'gs_command' instead and survives their removal.
    if gs_command is not None and isinstance(payload, dict):
      payload['gs_command'] = int(gs_command)
    await self.send(json.dumps(payload))


  async def send_request(self, method, url, device_type, id, json_body=None, params=None, gs_command=None):
    # Single path for GET/POST/DELETE -- they differ only in verb.
    if method not in ALLOWED_METHODS:
      logger.log_info(source='gs', data=f'unsupported method {method} for {url}', code_origin='send-error')
      return

    kwargs = {}
    if params:
      kwargs['params'] = params
    if method == 'post':
      kwargs['json'] = json_body or {}
      # A missing trailing slash makes uav_api 307 and aiohttp drop the body,
      # which otherwise presents as a command that silently does nothing.
      kwargs['allow_redirects'] = False

    logger.log_info(source='gs', data=f'{method.upper()} {url} {params or {}}', code_origin=f'send-{method}')
    try:
      async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
        async with session.request(method, url, **kwargs) as resp:
          response = await resp.json(content_type=None)
          logger.log_info(source=f'{device_type}-{id}', data=response, code_origin=f'send-{method}-response')
          await self.send_to_ui(response, gs_command)
    except asyncio.CancelledError:
      raise
    except Exception as e:
      logger.log_except()
      await self.send_to_ui(
        {'type': GS_ERROR_TYPE, 'device': 'gs', 'id': id, 'url': url, 'error': str(e)},
        gs_command,
      )


  async def upload_file_to_device(self, url, device_type, id, file_data, gs_command=None):
    # Upload file to a device through HTTP POST endpoint
    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
      # Decode into a fresh local buffer for THIS request only. We must NOT
      # mutate the shared file_data dict: when "Send to all" is selected the
      # same dict is passed to every drone's upload task, and overwriting
      # file_data["content"] with an already-consumed BytesIO makes every
      # upload after the first one fail silently.
      content_stream = io.BytesIO(base64.b64decode(file_data["content"]))

      data = aiohttp.FormData()
      data.add_field('file', content_stream, filename=file_data["filename"], content_type=file_data["type"])

      logger.log_info(source='gs', data=file_data["filename"], code_origin='upload-post')
      try:
        async with session.post(url, data=data) as resp:
          response = await resp.json(content_type=None)
          logger.log_info(source=f'{device_type}-{id}', data=response, code_origin='upload-post-response')
          await self.send_to_ui(response, gs_command)
      except asyncio.CancelledError:
        raise
      except Exception as e:
        logger.log_except()
        await self.send_to_ui(
          {'type': GS_ERROR_TYPE, 'device': 'gs', 'id': id, 'url': url, 'error': str(e)},
          gs_command,
        )


  async def poll_loop(self, key, command, receiver_id, params, interval):
    # Everything keep_sending lacked: a sleep, per-tick error isolation, and a
    # device list re-resolved every tick so a drone that registers later is
    # picked up instead of being missed forever.
    endpoint, method, allowed = parse_command_entry(command)
    query = build_query(params, allowed)
    try:
      while True:
        started = asyncio.get_event_loop().time()
        for device in get_device_from_list_by_id(receiver_id):
          url = build_url(device['ip'], endpoint)
          # Awaited in sequence, not gathered: a slow drone must not let
          # requests stack up behind it.
          await self.send_request(method, url, device['device'], str(device['id']),
                                  None, query, command)
        elapsed = asyncio.get_event_loop().time() - started
        await asyncio.sleep(max(MIN_POLL_INTERVAL, interval - elapsed))
    except asyncio.CancelledError:
      raise
    finally:
      self.polling_tasks.pop(key, None)


  async def start_polling(self, command, receiver_id, params, interval):
    key = poll_key(command, receiver_id, params)
    if key in self.polling_tasks:
      return  # idempotent: re-opening a panel must not stack a second loop
    try:
      interval = float(interval)
    except (TypeError, ValueError):
      interval = DEFAULT_POLL_INTERVAL
    interval = max(MIN_POLL_INTERVAL, min(interval, MAX_POLL_INTERVAL))
    self.polling_tasks[key] = asyncio.create_task(
      self.poll_loop(key, command, receiver_id, params, interval))


  async def stop_polling(self, command, receiver_id, params):
    task = self.polling_tasks.pop(poll_key(command, receiver_id, params), None)
    if task is not None:
      task.cancel()
      with contextlib.suppress(asyncio.CancelledError):
        await task


  async def send_via_http(self, text_data):
    # The command received via socket is dispatched on 'button_type':
    # upload (multipart), poll_start / poll_stop (recurring), or a one-shot
    # request whose method comes from the command table in config.ini.
    received_json = json.loads(text_data)

    button_type = received_json.get('button_type', 'default')
    device_receiver_id = str(received_json.get('receiver'))
    command = str(received_json['type'])
    params = received_json.get('params') or {}

    if button_type == 'poll_start':
      await self.start_polling(command, device_receiver_id, params,
                               received_json.get('interval', DEFAULT_POLL_INTERVAL))
      return
    if button_type == 'poll_stop':
      await self.stop_polling(command, device_receiver_id, params)
      return

    endpoint, method, allowed = parse_command_entry(command)
    query = build_query(params, allowed)
    device_to_send_list = get_device_from_list_by_id(device_receiver_id)

    for device in device_to_send_list:
      url = build_url(device['ip'], endpoint)
      id = str(device['id'])

      if button_type == 'upload':
        self.track(self.upload_file_to_device(url, device['device'], id,
                                              received_json['data'], command))
      else:
        self.track(self.send_request(method, url, device['device'], id,
                                     received_json.get('data'), query, command))


  async def receive(self, text_data):
    # Receive msg (text_data) from socket and call 'send_via_http' method to handle it
    try:
      await self.send_via_http(text_data)
    except Exception:
      logger.log_except()

  async def receive_post(self, data):
    # Called from 'post_to_socket' view, when a POST arrives from a device
    data['method'] = 'post'
    data['time'] = get_time_now().replace('"', '')
    data['status'] = 'active'

    append_device_to_persistant_list(data)

    source = data['device'] + '-' + str(data['id'])
    logger.log_info(source=source, data=data, code_origin='receive-info')
    try:
      await self.send(json.dumps(data))  # Send to JS via socket
    except Exception:
      logger.log_except()



# Auxiliary functions
# -------------------
def get_post_consumer_instance():
  global post_consumer_instance
  return post_consumer_instance


def get_time_now():
  return json.dumps(datetime.now(), default=json_serializer)


def json_serializer(obj):
  # Function to help formatting
  if isinstance(obj, (datetime, date)):
    return obj.isoformat()
  raise TypeError ("Type %s not serializable" % type(obj))
# End of Auxiliary functions
# -------------------


# --- Pre-process to get .ini info ---
config = configparser.ConfigParser()
config.read('config.ini')

MIN_POLL_INTERVAL = float(config['polling']['min_interval'])
MAX_POLL_INTERVAL = float(config['polling']['max_interval'])
DEFAULT_POLL_INTERVAL = float(config['polling']['default_interval'])

HTTP_TIMEOUT = aiohttp.ClientTimeout(
  total=float(config['http']['request_timeout']),
  connect=float(config['http']['connect_timeout']),
)
# --- End of pre-processing ---

post_consumer_instance = None
logger = Logger()

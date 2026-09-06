from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from asgiref.sync import async_to_sync

from .consumers_wrapper.post_consumers import get_post_consumer_instance
import configparser

config = configparser.ConfigParser()
config.read('config.ini')


def index(request):
  return render(request, 'index.html')


def create_new_dict(request_received):
  ip = config['fallback']['default_uav_address']

  new_dict = {}
  if request_received.POST.get('id') != None:
    new_dict['id'] = int(request_received.POST.get('id'))
  if request_received.POST.get('type') != None:
    new_dict['type'] = int(request_received.POST.get('type'))
  if request_received.POST.get('seq') != None:
    new_dict['seq'] = int(request_received.POST.get('seq'))
  if request_received.POST.get('lat') != None:
    new_dict['lat'] = float(request_received.POST.get('lat'))
  if request_received.POST.get('lng') != None:
    new_dict['lng'] = float(request_received.POST.get('lng'))
  if request_received.POST.get('alt') != None:
    new_dict['alt'] = float(request_received.POST.get('alt'))
  # Flight telemetry pushed by uav_api (gs_dev branch): speed, heading, battery.
  if request_received.POST.get('ground_speed') != None:
    new_dict['ground_speed'] = float(request_received.POST.get('ground_speed'))
  if request_received.POST.get('air_speed') != None:
    new_dict['air_speed'] = float(request_received.POST.get('air_speed'))
  if request_received.POST.get('heading') != None:
    new_dict['heading'] = float(request_received.POST.get('heading'))
  if request_received.POST.get('battery_percent') != None:
    new_dict['battery_percent'] = float(request_received.POST.get('battery_percent'))
  if request_received.POST.get('battery_voltage') != None:
    new_dict['battery_voltage'] = float(request_received.POST.get('battery_voltage'))
  # Mode name, e.g. GUIDED / LOITER / RTL. uav_api sends the string "None"
  # before the first heartbeat; that stays None here so the interface can show
  # "unknown" rather than inventing a mode.
  if request_received.POST.get('flight_mode') != None:
    raw = request_received.POST.get('flight_mode')
    new_dict['flight_mode'] = None if raw == 'None' else raw
  # ready_to_arm arrives as the string "True"/"False", or "None" when uav_api
  # has had no SYS_STATUS for 5s -- which must stay distinct from False.
  if request_received.POST.get('ready_to_arm') != None:
    raw = request_received.POST.get('ready_to_arm')
    new_dict['ready_to_arm'] = None if raw == 'None' else (raw == 'True')
  if request_received.POST.get('device') != None:
    new_dict['device'] = request_received.POST.get('device')
  if request_received.POST.get('data') != None:
    new_dict['data'] = request_received.POST.get('data')
  
  if request_received.POST.get('ip') != None:
    new_dict['ip'] = request_received.POST.get('ip')
  else:
    print('Não há ip base')
    new_dict['ip'] = ip
  return new_dict


@csrf_exempt 
@async_to_sync
async def post_to_socket(request):
  # Receives a POST request with information on it's body
  # Start ACK with an error code on type (101?).
  # If the post is sent to the consumer, the type is 103.
  ack = {"id": 1, "type": 101, "seq": 0, "lat": 0, "lng": 0, "alt": 0, "DATA": "0"}

  if request.method == 'POST':
    new_dict = create_new_dict(request)

    post_consumer_instance = get_post_consumer_instance()
    if post_consumer_instance is not None:
      await post_consumer_instance.receive_post(new_dict)
      ack['type'] = 103

  return JsonResponse(ack)

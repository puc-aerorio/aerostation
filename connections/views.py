from django.shortcuts import render
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from asgiref.sync import async_to_sync

from .consumers_wrapper.post_consumers import get_post_consumer_instance
import configparser

config = configparser.ConfigParser()
config.read('config.ini')


def index(request):
  context = {
    'google_maps_key': settings.GOOGLE_MAPS_API_KEY,
    'server_address': config['server']['ip_groundstation_server']
  }
  return render(request, 'index.html', context=context)


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


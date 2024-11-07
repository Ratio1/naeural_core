import paho.mqtt.client as mqtt
mqtt.MQTT_ERR_SUCCESS
from time import sleep

DEVICE_ID = '12345'

_CONFIG_RECEIVER = {
  'MQTT_HOST': 'HOSTNAME',
  'MQTT_PORT': 1883,
  'MQTT_USER': 'USERNAME',
  'MQTT_PASS': 'PASSWORD',
  'MQTT_PATH': 'PATH',
  'MQTT_TOPIC': 'config1',
  'MQTT_QOS': 1
  }

def on_connect(client, userdata, flags, rc):
  print('Connected with result code {}'.format(rc))
  path  = _CONFIG_RECEIVER['MQTT_PATH']
  topic = _CONFIG_RECEIVER['MQTT_TOPIC']
  client.subscribe(topic='{}/{}'.format(path, topic), qos=_CONFIG_RECEIVER['MQTT_QOS'])
  return

def on_disconnect(client, userdata, rc):
  # if rc != 0:
  print('Disconnected with result code {}'.format(rc))
  return

def on_message(client, userdata, message):
  try:
    import base64
    import io
    import numpy as np
    import json
    from PIL import Image
    payload = json.loads(message.payload)
    _id = payload['ID']
    img_base64 = payload['IMG']
    img = base64.b64decode(img_base64)
    img = Image.open(io.BytesIO(img))
    
    lst_received.append(_id)
    
    print('Received message _id {}/{} on topic {} with QoS {}. Total messages: {}'.format(
      _id,
      str(np.array(img).shape), 
      message.topic, 
      str(message.qos),
      len(lst_received)
      ))
  except Exception as e:
    print(str(e))
  return

def on_publish(client, userdata, mid):
  print('Publish {}, {}, {}'.format(client, userdata, mid))
  return
  
def on_log(mqttc, userdata, level, string):
  print(string)
  return
  

if __name__ == '__main__':
  lst_received = []
  
  mqttc = mqtt.Client(
    client_id=DEVICE_ID,
    clean_session=False
    )
  
  mqttc.username_pw_set(
    username=_CONFIG_RECEIVER['MQTT_USER'],
    password=_CONFIG_RECEIVER['MQTT_PASS']
    )
  
  mqttc.on_connect = on_connect
  mqttc.on_disconnect = on_disconnect
  mqttc.on_message = on_message
  mqttc.on_publish = on_publish
  mqttc.on_log = on_log
  
  mqttc.connect(
    host=_CONFIG_RECEIVER['MQTT_HOST'],
    port=_CONFIG_RECEIVER['MQTT_PORT']
    )
  
  # mqttc.loop_forever()
  
  topic_name = '{}/{}'.format(_CONFIG_RECEIVER['MQTT_PATH'], _CONFIG_RECEIVER['MQTT_TOPIC'])
  # while True:
  #   mqttc.loop(timeout=0.01)
  #   sleep(0.3)
  
  # mqttc.loop_forever()
  # mqttc.loop_start()
  
  
  while True:
    mqttc.loop_read()
    # mqttc.loop(timeout=0.01)
    # mqttc.loop_misc()
    sleep(0.1)
      

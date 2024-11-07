import json
import numpy as np
import paho.mqtt.client as mqtt
from naeural_core import Logger
from time import sleep

DEVICE_ID = '1234'

_CONFIG_SENDER = {
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
  return

def on_disconnect(client, userdata, rc):
  # if rc != 0:
  print('Disconnected with result code {}'.format(rc))
  return

def on_message(client, userdata, message):
  print('Received message "{}" on topic {} with QoS {}'.format(message, message.topic, str(message.qos)))
  return

def on_publish(client, userdata, mid):
  print('Publish {}, {}, {}'.format(client, userdata, mid))
  return
  
def on_log(mqttc, userdata, level, string):
  print(string)
  return
  

if __name__ == '__main__':  
  cfg_file = 'main_config.txt'
  log = Logger(
    lib_name='TST', 
    config_file=cfg_file, 
    max_lines=1000, 
    TF_KERAS=False
    )
  mqttc = mqtt.Client(
    client_id=DEVICE_ID,
    clean_session=False
    )  
  
  mqttc.username_pw_set(
    username=_CONFIG_SENDER['MQTT_USER'],
    password=_CONFIG_SENDER['MQTT_PASS']
    )
  
  mqttc.on_connect = on_connect
  mqttc.on_disconnect = on_disconnect
  mqttc.on_message = on_message
  mqttc.on_publish = on_publish
  mqttc.on_log = on_log
  
  mqttc.connect(
    host=_CONFIG_SENDER['MQTT_HOST'],
    port=_CONFIG_SENDER['MQTT_PORT']
    )
  
  # mqttc.loop_misc() #nu afiseaza nimic
  mqttc.loop_read()

  # i = 0
  # topic_name = '{}/{}'.format(_CONFIG_SENDER['MQTT_PATH'], _CONFIG_SENDER['MQTT_TOPIC'])
  # while True:
  #   i+= 1
  #   mqttc.publish(topic_name, 'Iter {}'.format(i))
  #   # mqttc.loop(timeout=0.1)
  #   sleep(120)
  
  # while True:
  # for i in range(100):
  #   img = np.random.randint(0, 256, (2000, 3000, 3), dtype=np.uint8)
  #   img_base64 = log.np_image_to_base64(img)
  #   # mqttc.publish(topic_name, 'Iter {}'.format(i), qos=_CONFIG_SENDER['MQTT_QOS'])    
  #   payload = {
  #     'ID': i + 1,
  #     'IMG': img_base64
  #     }
  #   mqttc.publish(
  #     topic=topic_name, 
  #     payload=json.dumps(payload), 
  #     qos=_CONFIG_SENDER['MQTT_QOS']
  #     )    
  #   mqttc.loop(timeout=0.01)
  #   sleep(0.1)
    
    



















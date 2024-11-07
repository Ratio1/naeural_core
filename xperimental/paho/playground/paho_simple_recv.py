import paho.mqtt.subscribe as subscribe

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

_CONFIG = _CONFIG_RECEIVER

if __name__ == '__main__':  
  topic = '{}/{}'.format(_CONFIG['MQTT_PATH'], _CONFIG['MQTT_TOPIC'])
  
  while True:
    m = subscribe.simple(
      topics=topic, 
      qos=1, 
      hostname=_CONFIG['MQTT_HOST'],
      port=_CONFIG['MQTT_PORT'], 
      auth={'username': _CONFIG['MQTT_USER'], 'password': _CONFIG['MQTT_PASS']}
      )
    print(m.payload.decode('utf-8'))
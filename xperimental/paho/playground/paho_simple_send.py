import paho.mqtt.publish as publish

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

_CONFIG = _CONFIG_SENDER

if __name__ == '__main__':  
  topic = '{}/{}'.format(_CONFIG['MQTT_PATH'], _CONFIG['MQTT_TOPIC'])
  
  for i in range(10):
    payload = 'Msg {}'.format(i)
    
    publish.single(
      topic=topic, 
      payload=payload, 
      qos=1,
      hostname=_CONFIG['MQTT_HOST'],
      port=_CONFIG['MQTT_PORT'], 
      client_id=DEVICE_ID, 
      keepalive=60, 
      auth={'username': _CONFIG['MQTT_USER'], 'password': _CONFIG['MQTT_PASS']}
      )
    
  
  
from naeural_core.data.default.iot.iot_queue_listener import IoTQueueListenerDataCapture


_CONFIG = {
  **IoTQueueListenerDataCapture.CONFIG,
  
  'MAX_DEQUE_LEN'   : 32, 


  'VALIDATION_RULES': {
    **IoTQueueListenerDataCapture.CONFIG['VALIDATION_RULES'],
  },
}


class NetworkListenerDataCapture(IoTQueueListenerDataCapture):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(NetworkListenerDataCapture, self).__init__(**kwargs)
    return

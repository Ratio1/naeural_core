"""
TODO: Modify stream window for more than 1 data step after plugins such as net-config support multiple data steps.

2024-11-16: This is a single data step DCT designed for plugins that expect only one data step from upstream.

"""

from naeural_core.data.default.iot.iot_queue_listener import IoTQueueListenerDataCapture


_CONFIG = {
  **IoTQueueListenerDataCapture.CONFIG,
  
  'MAX_DEQUE_LEN'   : 32, 
  'STREAM_WINDOW'   : 1,
  'ONE_AT_A_TIME'   : True,
  
  'DEBUG_IOT_PAYLOADS' : False,
  
  


  'VALIDATION_RULES': {
    **IoTQueueListenerDataCapture.CONFIG['VALIDATION_RULES'],
  },
}


class NetworkListenerDataCapture(IoTQueueListenerDataCapture):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(NetworkListenerDataCapture, self).__init__(**kwargs)
    return
  
  def _init(self):
    super(NetworkListenerDataCapture, self)._init()
    self.P(f"Initializing Network Listener DCT with filter {self.cfg_path_filter} and message filter {self.cfg_message_filter}")
    return

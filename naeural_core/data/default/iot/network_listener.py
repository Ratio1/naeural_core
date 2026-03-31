"""
NetworkListener defaults are tuned for small batched payload delivery.

Notes
-----
The listener now drains and forwards short bursts instead of enforcing one
payload per loop. Adaptive batching keeps the normal batch small while letting
the listener scale up under backlog. If end-to-end latency grows again,
increase ``MAX_STREAM_WINDOW`` first and then ``MAX_DEQUE_LEN`` for burst
absorption before raising the capture frequency.

"""

from naeural_core.data.default.iot.iot_queue_listener import IoTQueueListenerDataCapture


_CONFIG = {
  **IoTQueueListenerDataCapture.CONFIG,
  
  # Allow short bursts to accumulate locally so the main loop can harvest a
  # larger batch without dropping into immediate backpressure.
  'MAX_DEQUE_LEN'   : 512,
  # Keep the steady-state batch small and let the adaptive window scale up when
  # the ingress queue grows faster than the plugin can consume it.
  'STREAM_WINDOW'   : 8,
  'ADAPTIVE_STREAM_WINDOW': True,
  'MIN_STREAM_WINDOW': 4,
  'MAX_STREAM_WINDOW': 64,
  'STREAM_WINDOW_STEP': 4,
  # Keep batch mode explicit because NetworkListener throughput now depends on
  # draining more than one payload when backlog exists.
  'ONE_AT_A_TIME'   : False,
  
  'DEBUG_IOT_PAYLOADS' : False,
  "FILTER_BY_DESTINATION": True,
  
  


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
    self.P(f"Initializing {self.__class__.__name__} with filter {self.cfg_path_filter} and message filter {self.cfg_message_filter}")
    return

from naeural_core.data.base.base_iot_queue_listener import \
    BaseIoTQueueListenerDataCapture

_CONFIG = {
  **BaseIoTQueueListenerDataCapture.CONFIG,

  'VALIDATION_RULES': {
    **BaseIoTQueueListenerDataCapture.CONFIG['VALIDATION_RULES'],
  },
}


class IoTQueueListenerDataCapture(BaseIoTQueueListenerDataCapture):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(IoTQueueListenerDataCapture, self).__init__(**kwargs)
    return

  def _filter_message(self, unfiltered_message):
    filtered_message = unfiltered_message
    return filtered_message

  def _parse_message(self, filtered_message):
    return filtered_message

from naeural_core.data.base.base_iot_queue_listener import \
    BaseIoTQueueListenerDataCapture

_CONFIG = {
  **BaseIoTQueueListenerDataCapture.CONFIG,

  'VALIDATION_RULES': {
    **BaseIoTQueueListenerDataCapture.CONFIG['VALIDATION_RULES'],
  },
}


class IoTQueueListenerStructDataDataCapture(BaseIoTQueueListenerDataCapture):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(IoTQueueListenerStructDataDataCapture, self).__init__(**kwargs)
    return

  def _filter_message(self, unfiltered_message):
    filtered_message = unfiltered_message
    return filtered_message

  def _parse_message(self, filtered_message):
    # We want to return a dict, as we send structured data downstream
    # We can also return lists, tuples or strings
    return {'message': filtered_message}

from naeural_core.data.base.base_iot_queue_listener import \
    BaseIoTQueueListenerDataCapture

_CONFIG = {
  **BaseIoTQueueListenerDataCapture.CONFIG,

  'VALIDATION_RULES': {
    **BaseIoTQueueListenerDataCapture.CONFIG['VALIDATION_RULES'],
  },
}


class IoTQueueListenerImageDataCapture(BaseIoTQueueListenerDataCapture):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(IoTQueueListenerImageDataCapture, self).__init__(**kwargs)
    return

  def _filter_message(self, unfiltered_message):
    filtered_message = None
    if isinstance(unfiltered_message, str):
      # We received a string, we assume it is a base64 encoded image (but we should check this)
      if self.log.base64_to_np_image(unfiltered_message) is not None:
        filtered_message = unfiltered_message
      # end if image is valid

    elif isinstance(unfiltered_message, dict):
      if 'IMG' in unfiltered_message and isinstance(unfiltered_message['IMG'], str):
        if self.log.base64_to_np_image(unfiltered_message['IMG']) is not None:
          filtered_message = unfiltered_message
        # end if image is valid
      # end if image is in dict
    # end if message is string or dict

    return filtered_message

  def _parse_message(self, filtered_message):
    # We want to return a numpy image, as we send images downstream

    # we already checked that the message is valid
    image = None
    if isinstance(filtered_message, str):
      image = self.log.base64_to_np_image(filtered_message)
    if isinstance(filtered_message, dict):
      image = self.log.base64_to_np_image(filtered_message['IMG'])
    return image

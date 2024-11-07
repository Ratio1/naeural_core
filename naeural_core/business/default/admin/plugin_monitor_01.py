from naeural_core.business.base import BasePluginExecutor

__VER__ = '1.0.0'

_CONFIG = {
  **BasePluginExecutor.CONFIG,

  'ALLOW_EMPTY_INPUTS': True,

  'VALIDATION_RULES': {
    **BasePluginExecutor.CONFIG['VALIDATION_RULES'],
  },
}


class PluginMonitor01Plugin(BasePluginExecutor):
  CONFIG = _CONFIG

  def on_init(self):
    self.__capture_manager = self.global_shmem["capture_manager"]
    self.__serving_manager = self.global_shmem["serving_manager"]
    self.__business_manager = self.global_shmem["business_manager"]

    self.requests_queue = []
    return

  def __send_error_payload(self, error_msg, data):
    self.P(error_msg, color='r')
    self.add_payload_by_fields(
      plugin_default_configuration=None,
      command_params=data,
      error=error_msg,
    )
    return

  def __check_plugin_type_ok(self, plugin_type):
    type_not_none = plugin_type is not None
    type_string = isinstance(plugin_type, str)
    type_correct = type_string and plugin_type in ["capture", "serving", "business"]

    type_ok = type_not_none and type_string and type_correct
    error_msg = None

    if not type_ok:
      error_msg = f"Plugin type incorrect, expected string, one of {['capture', 'serving', 'business']}, received {plugin_type}."
    # end if

    return type_ok, error_msg

  def __check_plugin_signature_ok(self, plugin_signature):
    signature_not_none = plugin_signature is not None
    signature_string = isinstance(plugin_signature, str)

    signature_ok = signature_not_none and signature_string
    error_msg = None

    if not signature_ok:
      error_msg = f"Plugin signature incorrect, expected string, received {plugin_signature}."
    # end if

    return signature_ok, error_msg

  def __get_plugin_default_config(self, plugin_type, plugin_signature):
    plugin_default_config = None
    if plugin_type.lower() == "business":
      plugin_default_config = self.__business_manager.get_plugin_default_config(plugin_signature)
    elif plugin_type.lower() == "capture":
      plugin_default_config = self.__capture_manager.get_plugin_default_config(plugin_signature)
    elif plugin_type.lower() == "serving":
      plugin_default_config = self.__serving_manager.get_plugin_default_config(plugin_signature)

    return plugin_default_config

  def on_command(self, data, plugin_default_configuration=None, plugin_type=None, plugin_signature=None, **kwargs):
    """
    Called when the instance receives new INSTANCE_COMMAND

    Parameters
    ----------
    data : any
      object, string, etc.

    Returns
    -------
    None.

    """

    if plugin_default_configuration is None or not plugin_default_configuration:
      self.P(f"WARNING! Received UNKNOWN command {str(data)}", color='r')
      return

    self.P("Received \"PLUGIN_DEFAULT_CONFIGURATION\" command...")

    plugin_type_ok, error_message = self.__check_plugin_type_ok(plugin_type)
    if not plugin_type_ok:
      self.__send_error_payload(error_message, data)
      return

    plugin_signature_ok, error_message = self.__check_plugin_signature_ok(plugin_signature)
    if not plugin_signature_ok:
      self.__send_error_payload(error_message, data)
      return

    self.requests_queue.append((plugin_type, plugin_signature, data))
    return

  def process(self):
    while len(self.requests_queue) > 0:

      plugin_type, plugin_signature, data = self.requests_queue.pop(0)

      plugin_default_config = self.__get_plugin_default_config(plugin_type, plugin_signature)

      if plugin_default_config is None:
        self.__send_error_payload(f"Plugin signature {plugin_signature} not found in {plugin_type} manager.", data)
        return
      else:
        self.add_payload_by_fields(
          plugin_default_configuration=plugin_default_config,
          command_params=data,
        )

    return

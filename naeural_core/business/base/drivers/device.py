from naeural_core.business.base import BasePluginExecutor

_CONFIG = {
  **BasePluginExecutor.CONFIG,

  'ALLOW_EMPTY_INPUTS': True,

  'DEVICE_DEBUG': False,
}


class Device(BasePluginExecutor):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(Device, self).__init__(**kwargs)
    self.__driver_cache = self.defaultdict(lambda: self.NestedDotDict({"timestamp": 0, "data": None}))
    return

  def __is_expired(self, path) -> bool:
    """
    Check if data in cache is expired
    Parameters
    ----------
    path

    Returns
    -------
    bool
    """
    if path in self.__driver_cache:
      if self.__driver_cache[path].timestamp > 0:
        return self.__driver_cache[path].timestamp < self.time()
      return False
    return True

  def __data_prep(self, data):
    """
    Prepare data for dispatch
    Parameters
    ----------
    data : dict

    Returns
    -------
    dict
    """
    if isinstance(data, dict):
      return {k.lower(): v for k, v in data.items()}
    return {}

  def __dispatch_command(self, data, **kwargs):
    """
    Dispatch the command to the right method
    Parameters
    ----------
    data : dict
    kwargs : dict

    Returns
    -------
    any
    """
    dct_actions = self.__data_prep(data)
    action = dct_actions.pop("action", None)
    if isinstance(action, str):
      command = "device_action_{}".format(action.lower())
      func = getattr(self, command, None)
      if func:
        return func(**dct_actions)
      else:
        msg = "Undefined action: {}".format(action)
    else:
      msg = "Invalid action format {}".format(action)
    self._create_error_notification(msg)
    return None

  def _create_error_payload(self, message, command_params=None):
    """
    Create error payload
    Parameters
    ----------
    message

    Returns
    -------

    """
    payload = {"device_error": message, "status": "device_error"}
    self.add_payload_by_fields(
      command_params=command_params, **payload
    )
    return

  def _create_action_payload(self, action, message, **kwargs):
    """
    Create action payload as result of command
    Parameters
    ----------
    action
    message

    Returns
    -------

    """
    payload = {"device_action": action, "device_message": message, "status": "device_action", **kwargs}
    self.add_payload_by_fields(**payload)
    return

  def _create_generic_payload(self, **kwargs):
    """
    Create payload
    Parameters
    ----------
    kwargs : dict

    Returns
    -------
    dict
    """
    payload = {"status": "device_payload", **kwargs}
    self.add_payload_by_fields(**payload)

  def _create_device_state_payload(self, state, **kwargs):
    """
    Create device state payload
    Parameters
    ----------
    kwargs : dict

    Returns
    -------
    dict
    """
    payload = {"status": "device_state", "device_state": state, **kwargs}
    self.add_payload_by_fields(**payload)
    return

  def device_set_cache(self, path, data, seconds):
    """
    Store data in cache
    Parameters
    ----------
    path str
    data any
    seconds int

    Returns
    -------
    NestedDotDict

    """
    self.__driver_cache[path].data = data
    self.__driver_cache[path].timestamp = self.time() + seconds if seconds > 0 else 0
    return self.__driver_cache[path]

  def device_get_cache(self, path):
    """
    Get data from cache
    Parameters
    ----------
    path

    Returns
    -------

    """
    if self.__is_expired(path):
      return None
    return self.__driver_cache[path].data

  def on_command(self, data, **kwargs):
    """
    On command received
    Parameters
    ----------
    data : dict
    kwargs : dict

    Returns
    -------
    any
    """
    return self.__dispatch_command(data, **kwargs)

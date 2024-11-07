from PyE2 import BaseDecentrAIObject
from naeural_core.core_logging import Logger
from collections import deque
import traceback

class DecentrAIObject(BaseDecentrAIObject):
  """
  Generic class

  Instructions:

    1. use `super().__init__(**kwargs)` at the end of child `__init__`
    2. define `startup(self)` method for the child class and call 
       `super().startup()` at beginning of `startup()` method

      OR

    use `super().__init__(**kwargs)` at beginning of child `__init__` and then
    you can safely proceed with other initilization 

  """

  def __init__(self, log: Logger,
               DEBUG=False,
               show_prefixes=False,
               prefix_log=None,
               maxlen_notifications=None,
               log_at_startup=False,
               **kwargs):
    self._messages = deque(maxlen=maxlen_notifications)
    super(DecentrAIObject, self).__init__(
        log=log,
        DEBUG=DEBUG,
        show_prefixes=show_prefixes,
        prefix_log=prefix_log,
        maxlen_notifications=maxlen_notifications,
        log_at_startup=log_at_startup,
        **kwargs
    )
    return

  def _parse_config_data(self, *args, **kwargs):
    """
    args: keys that are used to prune the config_data. Examples:
                1. args=['TEST'] -> kwargs will be searched in
                                    log.config_data['TEST']
                2. args=['TEST', 'K1'] -> kwargs will be searched in
                                          log.config_data['TEST']['K1']
    kwargs: dictionary of k:v pairs where k is a parameter and v is its value.
            If v is None, then k will be searched in logger config data in order to set
            the value specified in json.
            Finally, the method will set the final value to a class attribute named 
            exactly like the key.
    """
    cfg = self.log.config_data
    for x in args:
      if x is not None:
        cfg = cfg[x]

    for k, v in kwargs.items():
      if v is None and k in cfg:
        v = cfg[k]

      setattr(self, k, v)

    return

  def _create_notification(
    self,
    notif,
    msg,
    info=None,
    stream_name=None,
    autocomplete_info=False,
    notif_code=None,
    notif_tag=None,
    ct=None,
    **kwargs
  ):
    body = {
      'MODULE': self.__class__.__name__
    }

    if hasattr(self, '__version__'):
      body['VERSION'] = self.__version__

    if autocomplete_info and info is None:
      err_info = self.log.get_error_info(return_err_val=True)
      trace_info = traceback.format_exc()
      if err_info[0] != '':
        info = "* Log error info:\n{}\n* Traceback:\n{}".format(
          err_info,
          trace_info,
        )
    # endif

    error_code = None
    if notif_code is not None and notif_code < 0:
      error_code = notif_code

    if notif_code is not None and notif_tag is None and ct is not None:
      notif_tag = ct.NOTIFICATION_CODES.TAGS.get(notif_code, None)
    if notif_tag is not None and notif_code is None and ct is not None:
      notif_code = ct.NOTIFICATION_CODES.CODES.get(notif_tag, None)

    body['NOTIFICATION_TYPE'] = notif
    body['NOTIFICATION'] = msg[:255]
    body['NOTIFICATION_CODE'] = notif_code
    body['NOTIFICATION_TAG'] = notif_tag
    body['INFO'] = info
    body['STREAM_NAME'] = stream_name
    body['TIMESTAMP'] = self.log.now_str(nice_print=True, short=False)
    body['ERROR_CODE'] = error_code
    body = {
      **body,
      **{k.upper(): v for k, v in kwargs.items()}
    }
    self._messages.append(body)
    return

  def get_notifications(self):
    lst = []
    while len(self._messages) > 0:
      lst.append(self._messages.popleft())
    return lst

  def get_cmd_handlers(self, update=False):
    if hasattr(self, 'COMMANDS') and isinstance(getattr(self, 'COMMANDS'), dict):
      COMMANDS = self.COMMANDS.copy()
    else:
      COMMANDS = {}
    for k in dir(self):
      if k.startswith('cmd_handler_'):
        cmd = k.replace('cmd_handler_', '').upper()
        if cmd not in COMMANDS:
          COMMANDS[cmd] = getattr(self, k)
    if update:
      self.COMMANDS = COMMANDS
    return COMMANDS

  def run_cmd(self, cmd, **kwargs):
    res = None
    cmd = cmd.upper()
    dct_cmds = self.get_cmd_handlers()
    if cmd in dct_cmds:
      func = dct_cmds[cmd]
      res = func(**kwargs)
    else:
      print("Received unk command '{}'".format(cmd))
    return res

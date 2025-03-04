from naeural_core.manager import Manager
# from naeural_core import constants as ct
from ratio1.io_formatter import IOFormatterWrapper

class IOFormatterManager(Manager):

  def __init__(self, log, **kwargs):
    self._io_formatter_wrapper = IOFormatterWrapper(log)
    self._formatter, self._formatter_name = None, None
    super(IOFormatterManager, self).__init__(
        log=log, prefix_log='[FMTM]', **kwargs)
    return

  def get_formatter(self):
    return self._formatter, self._formatter_name

  def get_default_formatter(self):
    return self._formatter, self._formatter_name

  def formatter_ready(self, name):
    return self._io_formatter_wrapper.formatter_ready(name)

  def get_formatter_by_name(self, name):
    return self._io_formatter_wrapper.get_formatter_by_name(name)

  def create_formatter(self, name):
    formatter = self._io_formatter_wrapper._create_formatter(name)
    self._formatter = formatter
    self._formatter_name = name
    return

  def get_required_formatter_from_payload(self, payload):
    return self._io_formatter_wrapper.get_required_formatter_from_payload(payload)

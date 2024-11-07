import abc

from datetime import datetime
from naeural_core import Logger
from naeural_core import DecentrAIObject

class BaseTestingPlugin(DecentrAIObject):

  def __init__(self, log : Logger, config=None, owner=None, **kwargs):
    self.y_hat = None
    self.exceptions = []
    self.config = config or {}
    self.owner = owner
    super(BaseTestingPlugin, self).__init__(log=log, **kwargs)
    return

  @abc.abstractmethod
  def _register_payload(self, payload):
    raise NotImplementedError()

  def register_payload(self, payload):
    try:
      self._register_payload(payload)
    except Exception as e:
      msg = "Could not register payload due to the following error:\n{}".format(e)
      self.exceptions.append(msg)
      self.P(msg, color='r')

    return


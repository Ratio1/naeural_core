import abc
import json
from collections import deque

from naeural_core.data.base.base_plugin_dct import DataCaptureThread
from ratio1 import Session

_CONFIG = {
  **DataCaptureThread.CONFIG,

  "URL": None,
  'RECONNECTABLE': True,


  'CAP_RESOLUTION' : 1,
  'STREAM_WINDOW' : 1_000,
  'LIVE_FEED' : False,
  'FILTER_WORKERS': None,

  'VALIDATION_RULES': {
    **DataCaptureThread.CONFIG['VALIDATION_RULES'],
  },
}


class BaseDecentraiConnectorDataCapture(DataCaptureThread):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(BaseDecentraiConnectorDataCapture, self).__init__(**kwargs)
    self.message_queue = deque(maxlen=1000)
    self._session = None
    return

  def _init(self):
    # create a session object and connect to ut
    self._session = Session(
      host=None,
      port=None,
      user=None,
      pwd=None,
      name=f'{self._device_id}_{self.cfg_name}',
      config=self.shmem['config_communication']['PARAMS'],
      filter_workers=self.cfg_filter_workers,
      log=self.log,
      on_heartbeat=self.heartbeat_callback,
      on_notification=self.notification_callback,
      on_payload=self.payload_callback,
      bc_engine=self.shmem[self.ct.BLOCKCHAIN_MANAGER],
    )
    return

  def heartbeat_callback(self, current_session, e2id, payload):
    self.message_queue.append({
      "TYPE": "heartbeat",
      "E2ID": e2id,
      "ADDRESS": payload["EE_SENDER"],
      "DATA": dict(payload),
    })

  def notification_callback(self, current_session, e2id, payload):
    self.message_queue.append({
      "TYPE": "notification",
      "E2ID": e2id,
      "ADDRESS": payload["EE_SENDER"],
      "DATA": dict(payload),
    })

  def payload_callback(self, current_session, e2id, session, signature, instance_id, payload):
    self.message_queue.append({
      "TYPE": "payload",
      "E2ID": e2id,
      "ADDRESS": payload["EE_SENDER"],
      # "SESSION": session,
      # "SIGNATURE": signature,
      # "INSTANCE_ID": instance_id,
      "DATA": dict(payload),
    })

  def _release(self):
    self._session.close(wait_close=True)
    return

  def _maybe_reconnect(self):
    return

  def _run_data_aquisition_step(self):
    if len(self.message_queue) == 0:
      return

    no_messages = len(self.message_queue)
    messages = []
    for _ in range(no_messages):
      messages.append(self.message_queue.popleft())
    # endfor

    lst_dct_output = []

    for msg in messages:
      dct_output = self.interpret_message(msg)
      if dct_output is not None:
        lst_dct_output.append(dct_output)
    # endfor

    if len(lst_dct_output) == 0:
      return

    self._add_struct_data_input(lst_dct_output)
    return

  @abc.abstractmethod
  def interpret_message(self, message) -> dict:
    """
    Interpret a message received from the network. The message is structured like this:
    ```
    {
      "TYPE": one of the following ["payload", "notification", "heartbeat"],
      "E2ID": the node name,
      "DATA": the message received,
    }
    ```

    To ignore the message, return `None`

    Parameters
    ----------
    message : dict
        the message received from the network

    Returns
    -------
    interpreted_message(optional) : dict | None
        a structured data point with information.
    """
    raise NotImplementedError

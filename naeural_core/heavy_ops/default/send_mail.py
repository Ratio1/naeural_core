
from naeural_core.heavy_ops.base import BaseHeavyOp
from naeural_core import constants as ct
from naeural_core.utils.emailer import send_email
import json

CONFIG = {
  'IDLE_THREAD_SLEEP_TIME' : 2,
}

class SendMailHeavyOp(BaseHeavyOp):
  """
  This op handles payloads that have _H_SEND_EMAIL set to True - and will send email
  as a threaded heavy op
  """

  def __init__(self, **kwargs):
    super(SendMailHeavyOp, self).__init__(**kwargs)
    return

  def startup(self):
    super().startup()
    assert self.comm_async

  def _register_payload_operation(self, payload):
    dct = payload.copy()
    payload.pop('_H_SEND_EMAIL', False)
    payload.pop('_H_EMAIL_CONFIG', None)
    payload.pop('_H_EMAIL_MESSAGE', None)
    payload.pop('_H_EMAIL_SUBJECT', None)
    return dct

  def _process_dct_operation(self, dct):
    bool_send_email = dct.pop('_H_SEND_EMAIL', False)
    email_config = dct.pop('_H_EMAIL_CONFIG', None)
    subject = dct.get('_H_EMAIL_SUBJECT', None)
    message = dct.get('_H_EMAIL_MESSAGE', None)

    if bool_send_email and email_config is not None and subject is not None:
      user = email_config.get(ct.EMAIL_NOTIFICATION.USER, ct.EMAIL_NOTIFICATION.DEFAULT_USER)
      pwd = email_config.get(ct.EMAIL_NOTIFICATION.PASSWORD, ct.EMAIL_NOTIFICATION.DEFAULT_PASSWORD)
      server = email_config.get(ct.EMAIL_NOTIFICATION.SERVER, ct.EMAIL_NOTIFICATION.DEFAULT_SERVER)
      port = email_config.get(ct.EMAIL_NOTIFICATION.PORT, ct.EMAIL_NOTIFICATION.DEFAULT_PORT)
      dest = email_config.get(ct.EMAIL_NOTIFICATION.DESTINATION)

      dct.pop('IMG', None)

      if message is None:
        message = json.dumps(dct, indent=4)

      if dest is not None:
        res, logs = send_email(
          log=self.log,
          server=server,
          port=port,
          user=user,
          password=pwd,
          destination=dest,
          subject=subject,
          msg=message,
          return_logs=True
        )

        for dl in logs:
          self._create_notification(
            notif='LOG',
            msg=dl['msg']
          )
        #endfor
      #endif

    return

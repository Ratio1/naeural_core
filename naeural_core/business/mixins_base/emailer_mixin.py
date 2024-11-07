from naeural_core import constants as ct

from naeural_core.utils.emailer import send_email

def email_string_to_dict(str_config):
  # parse in format user@origin:password@smtp_server:port/dest_name@dest_adr
  config = {}
  split1 = str_config.split('/')
  if len(split1) == 2:
    dest = split1[1]
    split2 = split1[0].split('@')
    if len(split2) == 3:
      split3 = split2[2].split(':') # server, port
      split4 = (split2[0] + '@' + split2[1]).split(':') # user, password
      if len(split3) == 2 and len(split4) == 2:
        server, port = split3
        user, pwd = split4
        config = {
          ct.EMAIL_NOTIFICATION.USER : user,
          ct.EMAIL_NOTIFICATION.PASSWORD : pwd,
          ct.EMAIL_NOTIFICATION.SERVER : server,
          ct.EMAIL_NOTIFICATION.PORT : port,
          ct.EMAIL_NOTIFICATION.DESTINATION : dest,
          }
  return config
      

class _EmailerMixin(object):
  """
  This mixin is configured using EMAIL_CONFIG key that can be either a dictionary/object
  or a simple string in the format user@origin:password@smtp_server:port/dest_name@dest_adr
  """
  def __init__(self):
    super(_EmailerMixin, self).__init__()
    return
  
  @property
  def cfg_email_config(self):
    config = self._instance_config.get(ct.EMAIL_NOTIFICATION.EMAIL_CONFIG, None)
    if isinstance(config, str):
      # now we parse the string 
      # in format user@origin:password@smtp_server:port/dest_name@dest_adr
      config = email_string_to_dict(config)
    return config

def send_email_notification(instance, message, subject=None):
  config = instance.cfg_email_config
  user = config.get(ct.EMAIL_NOTIFICATION.USER, ct.EMAIL_NOTIFICATION.DEFAULT_USER)
  pwd = config.get(ct.EMAIL_NOTIFICATION.PASSWORD, ct.EMAIL_NOTIFICATION.DEFAULT_PASSWORD)
  server = config.get(ct.EMAIL_NOTIFICATION.SERVER, ct.EMAIL_NOTIFICATION.DEFAULT_SERVER)
  port = config.get(ct.EMAIL_NOTIFICATION.PORT, ct.EMAIL_NOTIFICATION.DEFAULT_PORT)
  dest = config.get(ct.EMAIL_NOTIFICATION.DESTINATION)

  res = None
  if dest is not None:
    res = send_email(
      log=instance.log,
      server=server,
      port=port,
      user=user,
      password=pwd,
      destination=dest,
      subject=subject,
      msg=message
    )
  return res


if __name__ == '__main__':

  class Base:

    def __init__(self, log):
      self.log = log
      self._instance_config = {
        'INSTANCE_ID' : 'buc01',
        'POINTS' : [0,0,200,200],
        'EMAIL_CONFIG' : {
          'USER' : 'tmp@email.com',
          'PASSWORD' : 'PASSWORD',
          'SERVER' : 'SERVER',
          'PORT' : 587,
          'DESTINATION' : 'tmp@email.com'
        }
      }
      super(Base, self).__init__()
      return


  class Plugin(Base, _EmailerMixin):
    def __init__(self, log):
      super(Plugin, self).__init__(log=log)
      return

  from naeural_core import Logger
  log = Logger(lib_name='EMAILAL', base_folder='.', app_folder='_local_cache', TF_KERAS=False)
  p = Plugin(log)
  from time import time

  start = time()
  res_notif = send_email_notification(
    instance=p,
    message='Salut! Eu sunt DecentrAI',
    subject='First DecentrAI Alert'
  )
  end = time()
  print(end-start)

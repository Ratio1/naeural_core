__VER__ = '0.0.0.0'


class _NgrokMixinPlugin(object):
  class NgrokCT:
    NG_TOKEN = 'EE_NGROK_AUTH_TOKEN'
    # HTTP_GET = 'get'
    # HTTP_PUT = 'put'
    # HTTP_POST = 'post'

  """
  A plugin which exposes all of its methods marked with @endpoint through
  fastapi as http endpoints, and further tunnels traffic to this interface
  via ngrok.

  The @endpoint methods can be triggered via http requests on the web server
  and will be processed as part of the business plugin loop.
  """

  def get_setup_commands(self):
    try:
      super_setup_commands = super(_NgrokMixinPlugin, self).get_setup_commands()
    except AttributeError:
      super_setup_commands = []
    if self.cfg_use_ngrok or self.cfg_ngrok_enabled:
      return [self.__get_ngrok_auth_command()] + super_setup_commands
    else:
      return super_setup_commands

  def get_start_commands(self):
    try:
      super_start_commands = super(_NgrokMixinPlugin, self).get_start_commands()
    except AttributeError:
      super_start_commands = []
    if self.cfg_use_ngrok or self.cfg_ngrok_enabled:
      return [self.__get_ngrok_start_command()] + super_start_commands
    else:
      return super_start_commands

  def __get_ng_token(self):
    return self.os_environ.get(_NgrokMixinPlugin.NgrokCT.NG_TOKEN, None)

  def __get_ngrok_auth_command(self):
    return f"ngrok authtoken {self.__get_ng_token()}"

  def __get_ngrok_start_command(self):
    if self.cfg_ngrok_edge_label is not None:
      return f"ngrok tunnel {self.port} --label edge={self.cfg_ngrok_edge_label}"
    elif self.cfg_ngrok_domain is not None:
      return f"ngrok http {self.port} --domain={self.cfg_ngrok_domain}"
    else:
      raise RuntimeError("No domain/edge specified. Please check your configuration.")
    # endif

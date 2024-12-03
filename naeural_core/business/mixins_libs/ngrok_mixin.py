import ngrok
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

  @property
  def app_url(self):
    return None if self.ngrok_listener is None else self.ngrok_listener.url()

  def get_setup_commands(self):
    try:
      super_setup_commands = super(_NgrokMixinPlugin, self).get_setup_commands()
    except AttributeError:
      super_setup_commands = []
    if self.cfg_ngrok_use_api:
      # In this case the authentification will be made through the api in the actual code,
      # instead of the command line.
      return super_setup_commands
    # endif ngrok api used

    if self.cfg_ngrok_enabled:
      return [self.__get_ngrok_auth_command()] + super_setup_commands
    else:
      return super_setup_commands

  def maybe_init_ngrok(self):
    if self.cfg_ngrok_use_api and not self.ngrok_initiated:
      self.ngrok_initiated = True
      ngrok.set_auth_token(self.__get_ng_token())
      self.P(f"Ngrok initiated for {self.unique_identification}.")
    # endif ngrok api used
    return

  def get_ngrok_tunnel_kwargs(self):
    # Make the ngrok tunnel kwargs
    tunnel_kwargs = {}
    if self.cfg_ngrok_edge_label is not None:
      # In case of using edge label, the domain is not needed and the protocol is "labeled".
      tunnel_kwargs['labels'] = f'edge:{self.cfg_ngrok_edge_label}'
      tunnel_kwargs['proto'] = "labeled"
    # endif edge label
    elif self.cfg_ngrok_domain is not None:
      # In case of using domain, the domain is needed and the protocol is "http"(the default value).
      tunnel_kwargs['domain'] = self.cfg_ngrok_domain
    # endif domain
    # Specify the address and the authtoken
    tunnel_kwargs['addr'] = self.port
    tunnel_kwargs['authtoken'] = self._NgrokMixinPlugin__get_ng_token()
    return tunnel_kwargs

  def maybe_start_ngrok(self):
    # Maybe make this asynchronous?
    if self.cfg_ngrok_use_api and not self.ngrok_started:
      self.ngrok_started = True
      self.P(f"Ngrok starting for {self.unique_identification}...")
      self.ngrok_listener = ngrok.forward(**self.get_ngrok_tunnel_kwargs())
      self.P(f"Ngrok started at {self.app_url} for {self.unique_identification}.")
    # endif ngrok api used and not started
    return

  def get_start_commands(self):
    try:
      super_start_commands = super(_NgrokMixinPlugin, self).get_start_commands()
    except AttributeError:
      super_start_commands = []
    if self.cfg_ngrok_use_api:
      # In case of using the ngrok api, the tunnel will be started through the api
      return super_start_commands
    # endif ngrok api used

    if self.cfg_ngrok_enabled:
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

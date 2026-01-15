import threading
import ngrok
import asyncio

from naeural_core.business.mixins_base.tunnel_engine_mixin import _TunnelEngineMixin

__VER__ = '0.0.0.0'


NGROK_DEFAULT_PARAMETERS = {
  "NGROK_USE_API": True,
  "NGROK_DOMAIN": None,
  "NGROK_EDGE_LABEL": None,
  "NGROK_AUTH_TOKEN": None,
  "NGROK_PROTOCOL": None,
  "NGROK_PORT": None,
}


class _NgrokMixinPlugin(_TunnelEngineMixin):
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

  """NGROK UTILS METHODS"""
  if True:
    def __get_ngrok_auth_command(self):
      return f"ngrok authtoken {self.__get_ng_token()}"

    def _get_ngrok_start_command(self):
      edge_label = self.get_ngrok_edge_label()
      domain = self.get_ngrok_domain()
      proto = self.get_ngrok_protocol()
      domain_port = self.get_ngrok_port()
      if edge_label is not None:
        return f"ngrok tunnel {self.port} --label edge={edge_label}"
      if domain is not None:
        if proto == "tcp":
          if domain.endswith(".internal"):
            # Internal TCP endpoint: port in URL is required by ngrok for tcp internal.
            # If the user didn't put a port in NGROK_DOMAIN, fall back to the local port.
            return f"ngrok {proto} {self.port} --url {proto}://{domain}:{domain_port or self.port}"  # internal Agent Endpoint
          else:
            # Public TCP on reserved address
            if domain_port:
              # Bind a fixed public TCP address you reserved: tcp://<host>:<port>
              return f"ngrok tcp {self.port} --url tcp://{domain}:{domain_port}"
            return f"ngrok {proto} {self.port}"
        else:
          return f"ngrok http {self.port} --domain={domain}"
      # endif
      raise RuntimeError("No domain/edge specified. Please check your configuration.")

    def report_missing_authtoken(self):
      msg = "Ngrok token not found. Please set the environment variable `EE_NGROK_AUTH_TOKEN`"
      # Maybe have notif_code in the future
      self.P(msg, color='r')
      self._create_notification(
        msg=msg,
      )
      return

    def __get_ng_token(self):
      # TODO: At the moment multiple user auth tokens will not work on the same node
      #  if `NGROK_USE_API` is set to False.
      #  For the same node to use more than one auth token it needs to do so in separated
      #  processes. For the moment this is done only if the ngrok is started through
      #  CLI commands.
      configured_ng_token = self.get_tunnel_engine_parameters()["NGROK_AUTH_TOKEN"]
      environment_ng_token = self.os_environ.get(_NgrokMixinPlugin.NgrokCT.NG_TOKEN, None)
      if self.cfg_debug_web_app:
        self.P(f"Configured token: {configured_ng_token}, Environment token: {environment_ng_token}")
      return configured_ng_token if configured_ng_token is not None else environment_ng_token

    def get_ngrok_tunnel_kwargs(self):
      """
      Build kwargs for ngrok.forward(**kwargs) using only NGROK_DOMAIN.
      Accepted NGROK_DOMAIN forms:
        - "tcp://postgres.internal:5432"        -> internal TCP Agent Endpoint
        - "tcp://3.tcp.ngrok.io:26135"          -> public TCP on reserved address
        - "https://api.ngrok.app" or "api.ngrok.app" -> HTTP/S Agent Endpoint
      """
      tunnel_kwargs = {}
      valid = True

      # Extract host, optional scheme, and optional port from NGROK_DOMAIN
      # e.g. "tcp://3.tcp.ngrok.io:26135" -> ("3.tcp.ngrok.io", "tcp", "26135")
      domain_host, extracted_proto, extracted_port = self.get_ngrok_domain(with_protocol=True, with_port=True)

      # Compute final proto: explicit config wins; else derive from domain; default tcp
      proto = (self.get_tunnel_engine_parameters().get("NGROK_PROTOCOL") or extracted_proto or "tcp").lower()

      if proto:
        tunnel_kwargs["proto"] = proto
      if domain_host:
        if proto == "tcp":
          if extracted_port:
            # Case A: TCP endpoint with port specified in NGROK_DOMAIN
            tunnel_kwargs["remote_addr"] = f"{domain_host}:{extracted_port}"
          else:
            tunnel_kwargs["domain"] = domain_host
          # endif port specified
        else:
          # Case C: HTTP/S Agent Endpoint on a domain (public)
          tunnel_kwargs["domain"] = domain_host
          # For HTTP/S you don't need to pass proto; SDK handles http/https endpoints.
        # endif tcp protocol
      # endif domain_host specified

      # Always set the upstream (LOCAL) address and the authtoken
      tunnel_kwargs["addr"] = self.port  # forward to local service, e.g., 80 or 5432
      ng_token = self.__get_ng_token()
      if not ng_token:
        valid = False
        self.report_missing_authtoken()
      tunnel_kwargs["authtoken"] = ng_token

      return tunnel_kwargs, valid

    async def __maybe_async_stop_ngrok(self):
      try:
        self.P(f"Ngrok stopping...")
        self.ngrok_listener.close()
        self.tunnel_engine_started = False
        self.P(f"Ngrok stopped.")
      except Exception as exc:
        self.P(f"Error stopping ngrok: {exc}", color='r')
      return
  """END NGROK UTILS METHODS"""

  """RETRIEVE NGROK SPECIFIC CONFIGURATION PARAMETERS"""
  if True:
    def get_ngrok_edge_label(self):
      """
      Retrieve the ngrok edge label from the configuration.
      """
      # Since edge labels have been deprecated this will be disabled.
      return None
      return self.get_tunnel_engine_parameters()["NGROK_EDGE_LABEL"]

    def get_ngrok_domain(self, with_protocol=False, with_port=False):
      """
      Retrieve the ngrok domain from the configuration.
      """
      domain = self.get_tunnel_engine_parameters()["NGROK_DOMAIN"]
      edge_label = self.get_tunnel_engine_parameters()["NGROK_EDGE_LABEL"]
      res = domain or edge_label
      proto = None
      domain_port = None
      if isinstance(res, str) and "://" in res:
        proto = res.split("://")[0]
        res = res.split("://")[1]
      # endif
      if isinstance(res, str) and ":" in res:
        domain_port = res.split(":")[1]
        res = res.split(":")[0]
      # endif
      final_result = [res]
      if with_protocol:
        final_result.append(proto)
      if with_port:
        final_result.append(domain_port)
      return final_result[0] if len(final_result) == 1 else tuple(final_result)

    def get_ngrok_protocol(self):
      """
      Retrieve the ngrok protocol from the configuration.
      """
      domain, extracted_protocol = self.get_ngrok_domain(with_protocol=True)
      return self.get_tunnel_engine_parameters()["NGROK_PROTOCOL"] or extracted_protocol

    def get_ngrok_port(self):
      domain, extracted_port = self.get_ngrok_domain(with_port=True)
      return self.get_tunnel_engine_parameters().get("NGROK_PORT") or extracted_port

    def get_ngrok_use_api(self):
      """
      Check if the Ngrok Python SDK will be used
      """
      return self.cfg_tunnel_engine_enabled and self.get_tunnel_engine_parameters()["NGROK_USE_API"]
  """END RETRIEVE NGROK SPECIFIC CONFIGURATION PARAMETERS"""

  """BASE CLASS METHODS"""
  if True:
    def get_default_tunnel_engine_parameters_ngrok(self):
      return NGROK_DEFAULT_PARAMETERS

    def reset_tunnel_engine_ngrok(self):
      super(_NgrokMixinPlugin, self).reset_tunnel_engine()
      self.ngrok_listener = None
      return

    @property
    def app_url_ngrok(self):
      return None if self.ngrok_listener is None else self.ngrok_listener.url()

    @property
    def ngrok_listener(self):
      """
      The ngrok listener is the object that listens for incoming requests on the ngrok tunnel.
      """
      if hasattr(self, "_NgrokMixinPlugin__ngrok_listener"):
        return self.__ngrok_listener
      return None

    @ngrok_listener.setter
    def ngrok_listener(self, value):
      """
      Set the ngrok listener.
      """
      self.__ngrok_listener = value
      return

    def maybe_init_tunnel_engine_ngrok(self):
      if self.get_ngrok_use_api() and not self.tunnel_engine_initiated:
        self.tunnel_engine_initiated = True
        ng_token = self.__get_ng_token()
        if ng_token is None:
          self.report_missing_authtoken()
        else:
          ngrok.set_auth_token(ng_token)
          self.P(f"Ngrok initiated for {self.unique_identification}.")
        # endif ng_token present
      # endif ngrok api used
      return

    def maybe_start_tunnel_engine_ngrok(self):
      """

      TODO:
        - if no edge/domain is specified, the api should generate a url and return it while
          persisting the url in the instance local cache for future use. When the instance is
          restarted, the same url should be used.
      """
      # Maybe make this asynchronous?
      if self.get_ngrok_use_api() and not self.tunnel_engine_started:
        self.P(f"Ngrok starting for {self.unique_identification}...")
        tunnel_kwargs, valid = self.get_ngrok_tunnel_kwargs()
        if valid:
          if self.cfg_debug_web_app:
            self.P(f'Ngrok tunnel kwargs: {tunnel_kwargs}')
          self.ngrok_listener = ngrok.forward(**tunnel_kwargs)
          if self.app_url is not None:
            self.P(f"Ngrok started on URL `{self.app_url}` ({self._signature}).")
          else:
            edge = tunnel_kwargs.get('labels')
            domain = tunnel_kwargs.get('domain')
            str_tunnel = f"edge `{edge}`" if edge is not None else f"domain `{domain}`"
            self.P(f"Ngrok started on {str_tunnel} ({self._signature}).")
          # endif app_url
          self.tunnel_engine_started = True
        # endif valid tunnel kwargs
      # endif ngrok api used and not started
      return

    def maybe_stop_tunnel_engine_ngrok(self):
      if self.tunnel_engine_started and self.ngrok_listener is not None:
        self.P(f"Closing Ngrok listener...")

        threading.Thread(target=lambda: asyncio.run(self.__maybe_async_stop_ngrok()), daemon=True).start()
      # endif ngrok listener used
      return

    def get_setup_commands_ngrok(self):
      try:
        super_setup_commands = super(_NgrokMixinPlugin, self).get_setup_commands()
      except AttributeError:
        super_setup_commands = []
      if self.get_ngrok_use_api():
        # In this case the authentification will be made through the api in the actual code,
        # instead of the command line.
        return super_setup_commands
      # endif ngrok api used

      if self.cfg_tunnel_engine_enabled:
        super_setup_commands = [self.__get_ngrok_auth_command()] + super_setup_commands
      # endif ngrok enabled
      return super_setup_commands

    def get_start_commands_ngrok(self):
      super_start_commands = super(_NgrokMixinPlugin, self).get_start_commands()
      if self.get_ngrok_use_api():
        # In case of using the ngrok api, the tunnel will be started through the api
        return super_start_commands
      # endif ngrok api used

      if self.cfg_tunnel_engine_enabled:
        super_start_commands = super_start_commands + [self._get_ngrok_start_command()]
      # endif ngrok enabled
      return super_start_commands

    def check_valid_tunnel_engine_config_ngrok(self):
      """
      Check if the tunnel engine configuration is valid.
      """
      is_valid, msg = True, None
      auth_token = self.__get_ng_token()
      if auth_token is None:
        is_valid = False
        msg = "Ngrok authentication token is not set. Please set the environment variable `EE_NGROK_AUTH_TOKEN`."
      # endif auth token
      edge_label = self.get_ngrok_edge_label()
      domain = self.get_ngrok_domain()
      if (edge_label in [None, '']) or domain in [None, '']:
        is_valid = False
        msg = "Provide either NGROK_EDGE_LABEL (legacy) or NGROK_DOMAIN (public or .internal) in your configuration."
      # endif edge label
      return is_valid, msg

    def on_log_handler_ngrok(self, text, key=None):
      super(_NgrokMixinPlugin, self).on_log_handler(text, key)
      return
  """END BASE CLASS METHODS"""

  """NGROK ALIAS FOR BACKWARD COMPATIBILITY"""
  # NOTE: This section will be removed after integrating the new nomenclature in all plugins.
  if True:
    @property
    def ngrok_started(self):
      """
      ALIAS for ngrok tunnel engine started.
      """
      return self.tunnel_engine_started

    @ngrok_started.setter
    def ngrok_started(self, value):
      """
      ALIAS for ngrok tunnel engine started.
      """
      self.tunnel_engine_started = value
      return

    @property
    def ngrok_initiated(self):
      """
      ALIAS for ngrok tunnel engine initiated.
      """
      return self.tunnel_engine_initiated

    @ngrok_initiated.setter
    def ngrok_initiated(self, value):
      """
      ALIAS for ngrok tunnel engine initiated.
      """
      self.tunnel_engine_initiated = value
      return

    def _reset_ngrok(self):
      """
      Reset the ngrok state.
      """
      self.reset_tunnel_engine_ngrok()
      return

    def maybe_init_ngrok(self):
      """
      Initialize the ngrok if it is not already initialized.
      """
      self.maybe_init_tunnel_engine_ngrok()
      return

    def maybe_start_ngrok(self):
      """
      Start the ngrok if it is not already running.
      """
      self.maybe_start_tunnel_engine_ngrok()
      return

    def maybe_stop_ngrok(self):
      """
      Stop the ngrok if it is running.
      """
      self.maybe_stop_tunnel_engine_ngrok()
      return
  """END NGROK ALIAS FOR BACKWARD COMPATIBILITY"""












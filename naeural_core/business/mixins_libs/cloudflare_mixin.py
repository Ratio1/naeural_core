from naeural_core.business.mixins_base.tunnel_engine_mixin import _TunnelEngineMixin


CLOUDFLARE_DEFAULT_PARAMETERS = {
  "CLOUDFLARE_TOKEN": None,
  "CLOUDFLARE_PROTOCOL": "http",
}


class _CloudflareMixinPlugin(_TunnelEngineMixin):
  """
  A plugin which exposes all of its methods marked with @endpoint through
  fastapi as http endpoints, and further tunnels traffic to this interface
  via cloudflare.

  The @endpoint methods can be triggered via http requests on the web server
  and will be processed as part of the business plugin loop.
  """
  """CLOUDFLARE UTILS METHODS"""
  if True:
    def _get_cloudflare_start_command(self):
      token = self.get_cloudflare_token()
      protocol = self.get_cloudflare_protocol()
      if token is not None:
        return f"cloudflared tunnel --no-autoupdate run --token {token} --url {protocol}://127.0.0.1:{self.port}"
      return f"cloudflared tunnel --no-autoupdate --url {protocol}://127.0.0.1:{self.port}"
  """END CLOUDFLARE UTILS METHODS"""

  """RETRIEVE CLOUDFLARE SPECIFIC CONFIGURATION_PARAMETERS"""
  if True:
    def get_cloudflare_token(self):
      """
      Retrieve the Cloudflare token from the configuration parameters.
      If not set, it returns None.
      """
      # Here .get is used because init_endpoints goes through every method to find all
      # endpoints. Because this method is used by the property method app_url_cloudflare it was
      # called during the search
      return self.get_tunnel_engine_parameters().get("CLOUDFLARE_TOKEN")
    
    def get_cloudflare_protocol(self):
      """
      Retrieve the Cloudflare protocol from the configuration parameters.
      If not set, it returns "http".
      """
      protocol = self.get_tunnel_engine_parameters().get("CLOUDFLARE_PROTOCOL")
      if protocol is None:
        protocol = "http"
      return protocol
  """END RETRIEVE CLOUDFLARE SPECIFIC CONFIGURATION_PARAMETERS"""

  """BASE CLASS METHODS"""
  if True:
    @property
    def app_url_cloudflare(self):
      return None if self.get_cloudflare_token() is not None or not hasattr(self,
                                                                            '_CloudflareMixinPlugin__app_url') else self.__app_url
    def get_default_tunnel_engine_parameters_cloudflare(self):
      return CLOUDFLARE_DEFAULT_PARAMETERS

    def reset_tunnel_engine_cloudflare(self):
      """
      Reset the tunnel engine by stopping any existing tunnel and clearing the configuration.
      """
      super(_CloudflareMixinPlugin, self).reset_tunnel_engine()
      self.__app_url = None
      return

    def maybe_init_tunnel_engine_cloudflare(self):
      super(_CloudflareMixinPlugin, self).maybe_init_tunnel_engine()
      return

    def maybe_start_tunnel_engine_cloudflare(self):
      super(_CloudflareMixinPlugin, self).maybe_start_tunnel_engine()
      return

    def maybe_stop_tunnel_engine_cloudflare(self):
      super(_CloudflareMixinPlugin, self).maybe_stop_tunnel_engine()
      return

    def get_setup_commands_cloudflare(self):
      super(_CloudflareMixinPlugin, self).get_setup_commands()
      return

    def get_start_commands_cloudflare(self):
      super_start_commands = super(_CloudflareMixinPlugin, self).get_start_commands()

      if self.cfg_tunnel_engine_enabled:
        super_start_commands = super_start_commands + [self._get_cloudflare_start_command()]
      # endif tunnel engine enabled
      return super_start_commands

    def check_valid_tunnel_engine_config_cloudflare(self):
      """
      Check if the tunnel engine configuration is valid.
      If the Cloudflare token is not set, it raises an error.
      """
      is_valid, msg = True, None
      token = self.get_cloudflare_token()
      if token is None or token == "":
        msg = "Cloudflare token is not set."
        msg += "Please set the `CLOUDFLARE_TOKEN` parameter in your configuration."
        is_valid = False
      # endif token is None
      return is_valid, msg

    def on_log_handler_cloudflare(self, text, key=None):
      """
      Handle log messages from the Cloudflare tunnel.
      This method can be overridden in subclasses to handle logs differently.
      """
      super(_CloudflareMixinPlugin, self).on_log_handler(text, key)
      if hasattr(self, '_CloudflareMixinPlugin__app_url') and self.__app_url is not None:
        # URL already set, no need to process further
        return
      # Define a regular expression pattern to match the URL format
      url_pattern = r'https://[a-z0-9-]+\.trycloudflare\.com'
      match = self.re.search(url_pattern, text)

      if match:
        self.__app_url = match.group(0)
        self.P(f"Cloudflare tunnel started successfully on: {self.__app_url}", color="green")
      return
  """END BASE CLASS METHODS"""


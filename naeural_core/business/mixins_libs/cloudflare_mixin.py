from naeural_core.business.mixins_base.tunnel_engine_mixin import _TunnelEngineMixin


CLOUDFLARE_DEFAULT_PARAMETERS = {
  "CLOUDFLARE_TOKEN": None
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
    def __get_cloudflare_start_command(self):
      token = self.get_cloudflare_token()
      if token is not None:
        return f"cloudflared tunnel --no-autoupdate run --token {token} --url http://127.0.0.1:{self.port}"
      err_str = "No Cloudflare token provided in the configuration parameters."
      err_str += "Please check your configuration.\n"
      err_str += "You can set the token using the 'CLOUDFLARE_TOKEN' parameter in your configuration."
      raise RuntimeError(err_str)
  """END CLOUDFLARE UTILS METHODS"""

  """RETRIEVE CLOUDFLARE SPECIFIC CONFIGURATION_PARAMETERS"""
  if True:
    def get_cloudflare_token(self):
      """
      Retrieve the Cloudflare token from the configuration parameters.
      If not set, it returns None.
      """
      return self.get_tunnel_engine_parameters()["CLOUDFLARE_TOKEN"]
  """END RETRIEVE CLOUDFLARE SPECIFIC CONFIGURATION_PARAMETERS"""

  """BASE CLASS METHODS"""
  if True:
    def get_default_tunnel_engine_parameters_cloudflare(self):
      return CLOUDFLARE_DEFAULT_PARAMETERS

    def reset_tunnel_engine_cloudflare(self):
      """
      Reset the tunnel engine by stopping any existing tunnel and clearing the configuration.
      """
      super(_CloudflareMixinPlugin, self).reset_tunnel_engine()
      # No specific reset actions needed for Cloudflare
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
        super_start_commands += [self.__get_cloudflare_start_command()]
      # endif tunnel engine enabled
      return super_start_commands

    def maybe_tunnel_engine_ping_cloudflare(self):
      return

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
  """END BASE CLASS METHODS"""


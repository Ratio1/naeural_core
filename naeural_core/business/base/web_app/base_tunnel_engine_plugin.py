from naeural_core.business.base import BasePluginExecutor
from naeural_core.business.mixins_libs.ngrok_mixin import _NgrokMixinPlugin
from naeural_core.business.mixins_libs.cloudflare_mixin import _CloudflareMixinPlugin


_CONFIG = {
  **BasePluginExecutor.CONFIG,

  "TUNNEL_ENGINE": "ngrok",  # or "cloudflare"

  "VALIDATION_RULES": {
    **BasePluginExecutor.CONFIG['VALIDATION_RULES'],
  },
}

"""
This class is only made for backward compatibility.
"""

class BaseTunnelEnginePlugin(
  _NgrokMixinPlugin,
  _CloudflareMixinPlugin,
  BasePluginExecutor
):
  """
  Base class for tunnel engine plugins, which can be used to create plugins that
  expose methods as endpoints and tunnel traffic through ngrok or cloudflare.
  """
  CONFIG = _CONFIG

  def use_cloudflare(self):
    """
    Check if the plugin is configured to use Cloudflare as the tunnel engine.
    """
    return self.cfg_tunnel_engine.lower() == "cloudflare"

  @property
  def app_url(self):
    """
    Returns the URL of the application based on the tunnel engine being used.
    """
    if self.use_cloudflare():
      return self.app_url_cloudflare
    return self.app_url_ngrok

  def get_default_tunnel_engine_parameters(self):
    if self.use_cloudflare():
      return self.get_default_tunnel_engine_parameters_cloudflare()
    return self.get_default_tunnel_engine_parameters_ngrok()

  def reset_tunnel_engine(self):
    if self.use_cloudflare():
      return self.reset_tunnel_engine_cloudflare()
    return self.reset_tunnel_engine_ngrok()

  def maybe_init_tunnel_engine(self):
    if self.use_cloudflare():
      return self.maybe_init_tunnel_engine_cloudflare()
    return self.maybe_init_tunnel_engine_ngrok()

  def maybe_start_tunnel_engine(self):
    if self.use_cloudflare():
      return self.maybe_start_tunnel_engine_cloudflare()
    return self.maybe_start_tunnel_engine_ngrok()

  def maybe_stop_tunnel_engine(self):
    if self.use_cloudflare():
      return self.maybe_stop_tunnel_engine_cloudflare()
    return self.maybe_stop_tunnel_engine_ngrok()

  def get_setup_commands(self):
    if self.use_cloudflare():
      return self.get_setup_commands_cloudflare()
    return super(BaseTunnelEnginePlugin, self).get_setup_commands_ngrok()

  def get_start_commands(self):
    if self.use_cloudflare():
      return self.get_start_commands_cloudflare()
    return super(BaseTunnelEnginePlugin, self).get_start_commands_ngrok()

  def check_valid_tunnel_engine_config(self):
    if self.use_cloudflare():
      return self.check_valid_tunnel_engine_config_cloudflare()
    return self.check_valid_tunnel_engine_config_ngrok()

  def on_log_handler(self, text, key=None):
    if self.use_cloudflare():
      return self.on_log_handler_cloudflare(text, key)
    return self.on_log_handler_ngrok(text, key)

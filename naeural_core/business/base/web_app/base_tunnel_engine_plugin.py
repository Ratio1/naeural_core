import subprocess

from naeural_core.business.base import BasePluginExecutor
from naeural_core.business.mixins_libs.ngrok_mixin import _NgrokMixinPlugin
from naeural_core.business.mixins_libs.cloudflare_mixin import _CloudflareMixinPlugin


_CONFIG = {
  **BasePluginExecutor.CONFIG,

  "TUNNEL_ENGINE": "cloudflare",  # or "cloudflare"

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

  def on_init(self):

    self.dct_logs_reader = {}
    self.dct_err_logs_reader = {}

    super(BaseTunnelEnginePlugin, self).on_init()


  def run_tunnel_command(self, command):
    """
    Run a tunnel command in the background using LogReader like in base web app.
    This is a generic implementation for running tunnel engine commands.
    """
    if not command:
      return None
    
    try:
      self.P(f"Running tunnel command: {command}")
      process = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0  # this is important for real-time output
      )
      
      logs_reader = self.LogReader(process.stdout, size=100, daemon=None)
      err_logs_reader = self.LogReader(process.stderr, size=100, daemon=None)
      
      # Store the readers for later cleanup
      if not hasattr(self, 'dct_logs_reader'):
        self.dct_logs_reader = {}
      if not hasattr(self, 'dct_err_logs_reader'):
        self.dct_err_logs_reader = {}
      
      self.dct_logs_reader['tunnel'] = logs_reader
      self.dct_err_logs_reader['tunnel'] = err_logs_reader
      
      return process
    except Exception as e:
      self.P(f"Error running tunnel command: {e}")
      return None

  def stop_tunnel_command(self, process):
    """
    Stop a running tunnel command process and clean up LogReaders.
    """
    if process and process.poll() is None:
      try:
        process.terminate()
        process.wait(timeout=5)
      except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
      except Exception as e:
        self.P(f"Error stopping tunnel command: {e}")
    
    # Clean up LogReaders
    self._cleanup_tunnel_log_readers()
    return

  def _cleanup_tunnel_log_readers(self):
    """
    Clean up tunnel LogReaders like in base web app.
    """
    if hasattr(self, 'dct_logs_reader') and 'tunnel' in self.dct_logs_reader:
      logs_reader = self.dct_logs_reader.get('tunnel')
      if logs_reader is not None:
        logs_reader.stop()
        # Read any remaining logs
        logs = logs_reader.get_next_characters()
        if len(logs) > 0:
          self.on_log_handler(logs)
      # end if logs_reader
      self.dct_logs_reader.pop('tunnel', None)

    if hasattr(self, 'dct_err_logs_reader') and 'tunnel' in self.dct_err_logs_reader:
      err_logs_reader = self.dct_err_logs_reader.get('tunnel')
      if err_logs_reader is not None:
        err_logs_reader.stop()
        # Read any remaining error logs
        err_logs = err_logs_reader.get_next_characters()
        if len(err_logs) > 0:
          self.P(f"[stderr][tunnel]: {err_logs}")
      self.dct_err_logs_reader.pop('tunnel', None)
      # end if err_logs_reader
    return

  def read_tunnel_logs(self):
    """
    Read tunnel logs from LogReaders like in base web app.
    """
    if hasattr(self, 'dct_logs_reader') and 'tunnel' in self.dct_logs_reader:
      logs_reader = self.dct_logs_reader.get('tunnel')
      if logs_reader is not None:
        logs = logs_reader.get_next_characters()
        if len(logs) > 0:
          self.on_log_handler(logs)
      # end if logs_reader

    if hasattr(self, 'dct_err_logs_reader') and 'tunnel' in self.dct_err_logs_reader:
      err_logs_reader = self.dct_err_logs_reader.get('tunnel')
      if err_logs_reader is not None:
        err_logs = err_logs_reader.get_next_characters()
        if len(err_logs) > 0:
          self.P(f"[stderr][tunnel]: {err_logs}")
      # end if err_logs_reader
    return


  def run_tunnel_engine(self):
    """
    Run the tunnel engine start command based on the configured engine.
    This is a generic wrapper that delegates to the appropriate tunnel engine.
    """
    if self.use_cloudflare():
      cloudflare_command = self._get_cloudflare_start_command()
      if cloudflare_command:
        return self.run_tunnel_command(cloudflare_command)
    else:
      # For ngrok or other engines
      ngrok_command = self._get_ngrok_start_command()
      if ngrok_command:
        return self.run_tunnel_command(ngrok_command)
    
    return None

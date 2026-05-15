import os
import signal
import subprocess
import time

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
      popen_kwargs = dict(
        args=command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,  # this is important for real-time output
      )
      if os.name != "nt":
        popen_kwargs["start_new_session"] = True
      process = subprocess.Popen(**popen_kwargs)
      self._remember_process_group(process)
      
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

  def _remember_process_group(self, process):
    """
    Store the process group id for later tree termination.
    """
    if process is not None and os.name != "nt":
      try:
        process._r1_process_group_id = os.getpgid(process.pid)
      except Exception as exc:
        self.P(f"Could not record tunnel process group: {exc}", color='r')
    return process

  def _terminate_subprocess_tree(self, process, label="subprocess", terminate_timeout=5, kill_timeout=5):
    """
    Terminate a subprocess and, on POSIX, its process group.
    """
    if process is None:
      return True

    pgid = getattr(process, "_r1_process_group_id", None)

    def process_group_has_live_members():
      """
      Return True only when the process group still has non-zombie members.

      POSIX keeps zombie processes addressable until their parent reaps them.
      ``killpg(pgid, 0)`` therefore reports a group as alive even when the
      remaining members are defunct and cannot serve traffic or receive more
      useful signals. Treating zombie-only groups as stopped prevents shutdown
      cleanup from retrying forever on unreapable tunnel children.
      """
      proc_root = "/proc"
      if os.name == "nt" or pgid is None or not os.path.isdir(proc_root):
        return None
      try:
        pid_names = os.listdir(proc_root)
      except OSError as exc:
        self.P(f"Could not inspect {label} process group {pgid}: {exc}", color='r')
        return None

      found_member = False
      for pid_name in pid_names:
        if not pid_name.isdigit():
          continue
        try:
          with open(os.path.join(proc_root, pid_name, "stat"), "r") as fh:
            stat = fh.read()
          stat_tail = stat.rsplit(")", 1)[1].strip().split()
          state = stat_tail[0]
          member_pgid = int(stat_tail[2])
        except (FileNotFoundError, ProcessLookupError, IndexError, ValueError):
          continue
        except Exception as exc:
          self.P(f"Could not inspect {label} process {pid_name}: {exc}", color='r')
          continue
        if member_pgid != pgid:
          continue
        found_member = True
        # Stopped/traced members can resume later, so only zombies are treated as inert.
        if state != "Z":
          return True
      return False if found_member else None

    def is_process_group_alive():
      if os.name == "nt" or pgid is None:
        return False
      try:
        os.killpg(pgid, 0)
      except ProcessLookupError:
        return False
      except Exception as exc:
        self.P(f"Could not probe {label} process group {pgid}: {exc}", color='r')
        return True
      live_members = process_group_has_live_members()
      if live_members is not None:
        return live_members
      return True

    def wait_process_tree(timeout):
      deadline = time.monotonic() + timeout
      process_stopped = process.poll() is not None
      if not process_stopped:
        try:
          process.wait(timeout=timeout)
          process_stopped = True
        except subprocess.TimeoutExpired:
          process_stopped = False
        except Exception as exc:
          self.P(f"Error waiting for {label}: {exc}", color='r')
          process_stopped = process.poll() is not None

      if os.name == "nt" or pgid is None:
        return process_stopped

      while time.monotonic() < deadline:
        if not is_process_group_alive():
          return process_stopped
        time.sleep(0.05)
      return process_stopped and not is_process_group_alive()

    def send_signal(sig, fallback):
      if os.name != "nt" and pgid is not None:
        try:
          os.killpg(pgid, sig)
          return True
        except ProcessLookupError:
          return True
        except Exception as exc:
          self.P(f"Error signaling {label} process group {pgid}: {exc}", color='r')
      if process.poll() is None:
        try:
          fallback()
          return True
        except Exception as exc:
          self.P(f"Error signaling {label}: {exc}", color='r')
          return False
      return True

    if process.poll() is None or is_process_group_alive():
      if not send_signal(signal.SIGTERM, process.terminate):
        return False
    if wait_process_tree(terminate_timeout):
      return True

    self.P(f"{label} did not stop after terminate; killing it.", color='r')
    # Windows does not define SIGKILL. Select the POSIX signal only when it can
    # be used; otherwise fall back directly to Popen.kill().
    kill_signal = getattr(signal, "SIGKILL", None)
    if os.name != "nt" and kill_signal is not None:
      killed = send_signal(kill_signal, process.kill)
    else:
      killed = send_signal(None, process.kill)
    if not killed:
      return False
    if wait_process_tree(kill_timeout):
      return True
    self.P(f"{label} did not exit after kill; continuing shutdown.", color='r')
    return False

  def stop_tunnel_command(self, process):
    """
    Stop a running tunnel command process and clean up LogReaders.
    """
    process_stopped = True
    readers_stopped = True
    try:
      process_stopped = self._terminate_subprocess_tree(process, label="Tunnel command")
    finally:
      # LogReader cleanup must not be skipped when process termination fails.
      try:
        readers_stopped = self._cleanup_tunnel_log_readers()
      except Exception as exc:
        readers_stopped = False
        self.P(f"Error cleaning tunnel log readers: {exc}", color='r')
    return process_stopped and readers_stopped

  def _cleanup_tunnel_log_readers(self):
    """
    Clean up tunnel LogReaders like in base web app.
    """
    result = True
    if hasattr(self, 'dct_logs_reader') and 'tunnel' in self.dct_logs_reader:
      logs_reader = self.dct_logs_reader.get('tunnel')
      reader_stopped = True
      if logs_reader is not None:
        try:
          reader_stopped = logs_reader.stop()
          result = reader_stopped and result
          # Read any remaining logs
          logs = logs_reader.get_next_characters()
          if len(logs) > 0:
            self.on_log_handler(logs)
        except Exception as exc:
          reader_stopped = False
          result = False
          self.P(f"Error stopping tunnel stdout reader: {exc}", color='r')
      # end if logs_reader
      if reader_stopped:
        self.dct_logs_reader.pop('tunnel', None)

    if hasattr(self, 'dct_err_logs_reader') and 'tunnel' in self.dct_err_logs_reader:
      err_logs_reader = self.dct_err_logs_reader.get('tunnel')
      reader_stopped = True
      if err_logs_reader is not None:
        try:
          reader_stopped = err_logs_reader.stop()
          result = reader_stopped and result
          # Read any remaining error logs
          err_logs = err_logs_reader.get_next_characters()
          if len(err_logs) > 0:
            self.P(f"[stderr][tunnel]: {err_logs}")
        except Exception as exc:
          reader_stopped = False
          result = False
          self.P(f"Error stopping tunnel stderr reader: {exc}", color='r')
      if reader_stopped:
        self.dct_err_logs_reader.pop('tunnel', None)
      # end if err_logs_reader
    return result

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

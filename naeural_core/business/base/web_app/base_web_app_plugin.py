import os
import shutil
import subprocess
import tempfile

from jinja2 import Environment, FileSystemLoader

from naeural_core.business.base import BasePluginExecutor
from naeural_core.business.mixins_libs.ngrok_mixin import _NgrokMixinPlugin

__VER__ = '0.0.0.0'

_CONFIG = {
  **BasePluginExecutor.CONFIG,
  'ALLOW_EMPTY_INPUTS': True,
  'RUN_WITHOUT_IMAGE': True,
  'PROCESS_DELAY': 5,
  
  'GIT_REQUEST_DELAY' : 60 * 10, # 10 minutes

  'USE_NGROK': False,
  'NGROK_ENABLED': False,
  'NGROK_DOMAIN': None,
  'NGROK_EDGE_LABEL': None,

  'ASSETS': None,

  'SETUP_COMMANDS': [],
  'START_COMMANDS': [],
  'ENV_VARS': {},
  'AUTO_START': True,

  'PORT': None,

  'VALIDATION_RULES': {
    **BasePluginExecutor.CONFIG['VALIDATION_RULES']
  },
}


class BaseWebAppPlugin(_NgrokMixinPlugin, BasePluginExecutor):
  """
  A base plugin which will handle the lifecycle of a web application.
  Through this plugin, you can expose your business logic as a web application,
  using some implementation of a web server.

  You can also deploy your web application to the internet using ngrok.
  To do this, set the `USE_NGROK` flag to True in config and set the necessary
  environment variables.

  TODO: add ngrok necessary data in the config (after securing the configs)
  """

  CONFIG = _CONFIG
  
  def _on_init(self):

    self.__git_commit_hash = None
    self.__git_request_time = 0
    

    
    self.__allocate_port()

    self.prepared_env = None
    self.base_env = None

    self.script_temp_dir = tempfile.mkdtemp()

    self.assets_initialized = False
    self.failed = False    
    
    self.__setup_commands()

    self.can_run_start_commands = self.cfg_auto_start

    super(BaseWebAppPlugin, self)._on_init()
    return
  
  
  def __can_request_git(self):
    if self.time() - self.__git_request_time > self.cfg_git_request_delay:
      self.__git_request_time = self.time()
      return True
    return False


  # Port allocation
  def __check_port_valid(self):
    # Check the config as we're going to use it to start processes.
    if not isinstance(self.cfg_port, int):
      raise ValueError("Port not an int")
    if self.cfg_port < 0 or self.cfg_port > 65535:
      raise ValueError("Invalid port value {}".format(self.cfg_port))
    return

  def __allocate_port(self):
    with self.managed_lock_resource('USED_PORTS'):
      if 'USED_PORTS' not in self.plugins_shmem:
        self.plugins_shmem['USED_PORTS'] = {}
      dct_shmem_ports = self.plugins_shmem['USED_PORTS']
      used_ports = dct_shmem_ports.values()

      if self.cfg_port is not None:
        self.__check_port_valid()

        if self.cfg_port in used_ports:
          raise Exception("Port {} is already in use.".format(self.cfg_port))
        else:
          dct_shmem_ports[self.str_unique_identification] = self.cfg_port
      else:
        port = self.np.random.randint(49152, 65535)
        while port in used_ports:
          port = self.np.random.randint(49152, 65535)
        # endwhile
        dct_shmem_ports[self.str_unique_identification] = port
      # endif port
    # endwith lock
    return

  def __deallocate_port(self):
    port = self.port

    with self.managed_lock_resource('USED_PORTS'):
      if 'USED_PORTS' in self.plugins_shmem:
        self.plugins_shmem['USED_PORTS'].pop(self.str_unique_identification, None)
    # endwith lock

    self.P(f"Released port {port}")
    return

  def __prepare_env(self, assets_path):
    # pop all `EE_` keys
    prepared_env = dict(self.os_environ)
    to_pop_keys = []
    for key in prepared_env:
      if key.startswith('EE_'):
        to_pop_keys.append(key)
    # endfor all keys

    for key in to_pop_keys:
      prepared_env.pop(key)

    # add mandatory keys
    prepared_env["PWD"] = self.script_temp_dir
    prepared_env["PORT"] = str(self.port)

    self.base_env = prepared_env.copy()

    # add optional keys, found in `.env` file from assets
    env_file_path = self.os_path.join(assets_path, '.env')
    if self.os_path.exists(env_file_path):
      with open(env_file_path, 'r') as f:
        for line in f:
          if line.startswith('#') or len(line.strip()) == 0:
            continue
          key, value = line.strip().split('=', 1)
          prepared_env[key] = value
      # endwith

      # TODO: should we remove the .env file?
    # endif env file

    # add optional keys, found in config
    if self.cfg_env_vars is not None and isinstance(self.cfg_env_vars, dict):
      prepared_env.update(self.cfg_env_vars)

    self.prepared_env = prepared_env

    return prepared_env

  # process handling methods
  def __run_command(self, command, env=None, read_stdout=True, read_stderr=True):
    if isinstance(command, list):
      command_args = command
    elif isinstance(command, str):
      command_args = command.split(' ')
    else:
      raise ValueError("Command must be a string or a list of strings")

    process = subprocess.Popen(
      command_args,
      env=env,
      cwd=self.script_temp_dir,
      stdout=subprocess.PIPE if read_stdout else None,
      stderr=subprocess.PIPE if read_stderr else None,
    )
    logs_reader = self.LogReader(process.stdout) if read_stdout else None
    err_logs_reader = self.LogReader(process.stderr) if read_stderr else None
    return process, logs_reader, err_logs_reader

  def __wait_for_command(self, process, timeout):
    process_finished = False
    failed = False
    try:
      process.wait(timeout)
      failed = process.returncode != 0
      process_finished = True
    except subprocess.TimeoutExpired:
      pass

    return process_finished, failed

  def __maybe_kill_process(self, process, key):
    if process is None:
      return

    try:
      self.P(f"Forcefully killing process {key}")
      process.kill()
      self.__wait_for_command(
        process=process,
        timeout=3,
      )
      self.__maybe_print_key_logs(key)
      self.P(f"Killed process {key}")
    except Exception as exc:
      self.P(f'Could not kill process {key}. Reason: {exc}')

    return
  

  # logs handling methods
  def __maybe_print_all_logs(self, indent=35):
    for key, logs_reader in self.dct_logs_reader.items():
      if logs_reader is not None:
        logs = logs_reader.get_next_characters()
        if len(logs) > 0:
          indented_logs = self.indent_strings(logs, indent=indent)
          self.P(f"Showing stdout logs [{key}]:\n{indented_logs}")
          self.logs.append(f"[{key}]: {logs}")

    for key, err_logs_reader in self.dct_err_logs_reader.items():
      if err_logs_reader is not None:
        err_logs = err_logs_reader.get_next_characters()
        if len(err_logs) > 0:
          indented_err_logs = self.indent_strings(err_logs, indent=indent)
          self.P(f"Showing error logs [{key}]:\n{indented_err_logs}")
          self.err_logs.append(f"[{key}]: {err_logs}")
    return


  def __maybe_print_key_logs(self, key):
    logs_reader = self.dct_logs_reader.get(key)
    if logs_reader is not None:
      logs = logs_reader.get_next_characters()
      if len(logs) > 0:
        self.P(f"[{key}]: {logs}")
        self.logs.append(f"[{key}]: {logs}")

    err_logs_reader = self.dct_err_logs_reader.get(key)
    if err_logs_reader is not None:
      err_logs = err_logs_reader.get_next_characters()
      if len(err_logs) > 0:
        self.P(f"[{key}]: {err_logs}")
        self.err_logs.append(f"[{key}]: {err_logs}")
    return


  def __get_delta_logs(self):
    logs = list(self.logs)
    logs = "".join(logs)
    self.logs.clear()

    err_logs = list(self.err_logs)
    err_logs = "".join(err_logs)
    self.err_logs.clear()

    return logs, err_logs

  def __maybe_read_and_stop_key_log_readers(self, key):
    logs_reader = self.dct_logs_reader.get(key)
    if logs_reader is not None:
      logs_reader.stop()

    err_logs_reader = self.dct_err_logs_reader.get(key)
    if err_logs_reader is not None:
      err_logs_reader.stop()

    self.__maybe_print_key_logs(key)

    self.dct_logs_reader.pop(key, None)
    self.dct_err_logs_reader.pop(key, None)
    return

  def __maybe_read_and_stop_all_log_readers(self):
    log_keys = set(list(self.dct_logs_reader.keys()) + list(self.dct_err_logs_reader.keys()))
    for key in log_keys:
      self.__maybe_read_and_stop_key_log_readers(key)
    return

  # setup commands methods
  def __maybe_run_all_setup_commands(self):
    if self.failed:
      return

    has_finished_setup_commands = self.__has_finished_setup_commands()
    if has_finished_setup_commands:
      return
    
    self.P("Running setup commands (`has_finished_setup_commands: {}`)".format(has_finished_setup_commands))

    for idx in range(len(self.get_setup_commands())):
      self.__maybe_run_nth_setup_command(idx)
    return


  def __maybe_close_setup_commands(self):
    if not any(self.setup_commands_started):
      self.P("No setup commands were started. Skipping teardown.")
      return

    if all(self.setup_commands_finished):
      self.P("All setup commands have finished. Skipping teardown.")
      return

    for idx, process in enumerate(self.setup_commands_processes):
      if self.setup_commands_started[idx] and not self.setup_commands_finished[idx]:
        self.P(f"Setup command nr {idx} has not finished. Killing it.")
        self.__maybe_kill_process(process, f"setup_{idx}")
    # endfor all setup commands
    return


  def __maybe_run_nth_setup_command(self, idx, timeout=None):
    if self.failed:
      return

    if idx > 0 and not self.setup_commands_finished[idx - 1]:
      # Previous setup command has not finished yet. Skip this one.
      return

    if not self.setup_commands_started[idx]:
      self.P(f"Running setup command nr {idx}: {self.get_setup_commands()[idx]}")
      proc, logs_reader, err_logs_reader = self.__run_command(self.get_setup_commands()[idx], self.base_env)
      self.setup_commands_processes[idx] = proc
      self.dct_logs_reader[f"setup_{idx}"] = logs_reader
      self.dct_err_logs_reader[f"setup_{idx}"] = err_logs_reader

      self.setup_commands_started[idx] = True
      self.setup_commands_start_time[idx] = self.time()
    # endif setup command started

    if not self.setup_commands_finished[idx]:
      finished, failed = self.__wait_for_command(
        process=self.setup_commands_processes[idx],
        timeout=0.1,
      )

      self.setup_commands_finished[idx] = finished

      if finished and not failed:
        self.__maybe_read_and_stop_key_log_readers(f"setup_{idx}")
        self.add_payload_by_fields(
          command_type="setup",
          command_idx=idx,
          command_str=self.get_setup_commands()[idx],
          command_status="success"
        )
        self.P(f"Setup command nr {idx} finished successfully")
      elif finished and failed:
        self.__maybe_read_and_stop_key_log_readers(f"setup_{idx}")
        self.add_payload_by_fields(
          command_type="setup",
          command_idx=idx,
          command_str=self.get_setup_commands()[idx],
          command_status="failed"
        )
        self.P(f"ERROR: Setup command nr {idx} finished with exit code {self.setup_commands_processes[idx].returncode}")
        self.failed = True
      elif not finished and timeout is not None and timeout > 0:
        if self.time() - self.setup_commands_start_time[idx] > timeout:
          self.setup_commands_processes[idx].kill()
          self.__maybe_read_and_stop_key_log_readers(f"setup_{idx}")
          self.P(f"ERROR: Setup command nr {idx} timed out")
          self.add_payload_by_fields(
            command_type="setup",
            command_idx=idx,
            command_str=self.get_setup_commands()[idx],
            command_status="timeout"
          )
          self.failed = True
    # endif setup command finished
    return


  def __has_finished_setup_commands(self):
    return all(self.setup_commands_finished)


  # start commands methods
  def __maybe_run_all_start_commands(self):
    if self.failed:
      return

    if self.__has_finished_start_commands():
      return

    if not self.__has_finished_setup_commands():
      return

    if not self.can_run_start_commands:
      return
    
    self.P("Running START commands...")

    for idx in range(len(self.get_start_commands())):
      self.__maybe_run_nth_start_command(idx)
    return

  def __maybe_close_start_commands(self):
    if not any(self.start_commands_started):
      self.P("Server was never started. Skipping teardown.")
      return

    for idx, process in enumerate(self.start_commands_processes):
      self.__maybe_kill_process(process, f"start_{idx}")
    # endfor all start commands

  def __maybe_run_nth_start_command(self, idx, timeout=5):
    if self.failed:
      return

    if idx > 0 and not self.start_commands_finished[idx - 1]:
      # Previous start command has not finished yet. Skip this one.
      return

    if not self.start_commands_started[idx]:
      self.P(f"Running start command nr {idx}: {self.get_start_commands()[idx]}")
      proc, logs_reader, err_logs_reader = self.__run_command(self.get_start_commands()[idx], self.prepared_env)
      self.start_commands_processes[idx] = proc
      self.dct_logs_reader[f"start_{idx}"] = logs_reader
      self.dct_err_logs_reader[f"start_{idx}"] = err_logs_reader

      self.start_commands_started[idx] = True
      self.start_commands_start_time[idx] = self.time()
    # endif start command started

    if not self.start_commands_finished[idx]:
      finished, _ = self.__wait_for_command(
        process=self.start_commands_processes[idx],
        timeout=0.1,
      )

      self.start_commands_finished[idx] = finished

      if finished:
        self.__maybe_read_and_stop_key_log_readers(f"start_{idx}")
        self.add_payload_by_fields(
          command_type="start",
          command_idx=idx,
          command_str=self.get_start_commands()[idx],
          command_status="failed"
        )
        self.P(f"Start command nr {idx} finished unexpectedly. Please check the logs.")
        self.failed = True
      elif self.time() - self.start_commands_start_time[idx] > timeout:
        self.start_commands_finished[idx] = True
        self.add_payload_by_fields(
          command_type="start",
          command_idx=idx,
          command_str=self.get_start_commands()[idx],
          command_status="success"
        )
        self.P(f"Start command nr {idx} is running")
    # endif setup command finished
    return

  def __has_finished_start_commands(self):
    return all(self.start_commands_finished)
  
  
  def __reload_server(self):
    self.P("Initiating server reload...")
    self.__maybe_close_setup_commands()
    self.__maybe_close_start_commands()
    self.__maybe_read_and_stop_all_log_readers()

    self.assets_initialized = False
    self.failed = False

    self.setup_commands_started = [False] * len(self.get_setup_commands())
    self.setup_commands_finished = [False] * len(self.get_setup_commands())
    self.setup_commands_processes = [None] * len(self.get_setup_commands())
    self.setup_commands_start_time = [None] * len(self.get_setup_commands())

    self.start_commands_started = [False] * len(self.get_start_commands())
    self.start_commands_finished = [False] * len(self.get_start_commands())
    self.start_commands_processes = [None] * len(self.get_start_commands())
    self.start_commands_start_time = [None] * len(self.get_start_commands())

    self.P("Reloading server at next _maybe_init ...")  
    return  
  
  
  
  def __check_new_repo_version(self):
    result = False # return false by default including if no git url is provided
    # first check not to run this operation too many times
    if isinstance(self.cfg_assets, dict) and self.cfg_assets.get('operation') == 'clone':
      url = self.cfg_assets.get('url')
      username = self.cfg_assets.get('username')
      token = self.cfg_assets.get('token')
      if self.__git_commit_hash is not None:
        # check if we can request git based on a configured delay
        can_request_git = self.__can_request_git()
        if can_request_git:
          commit_hash = self.git_get_last_commit_hash(
            repo_url=url,
            user=username,
            token=token,
          )
          if commit_hash is None:
            self.P("Could not get the commit hash. Assuming no new version.")
            result = False
          elif commit_hash != self.__git_commit_hash:        
            self.P(f"New git assets available: local hash {self.__git_commit_hash} differs from git {commit_hash} . Server reloading procedure will be initiated...")
            result = True
          # endif commit hash
        # endif can request git
      else:
        # no previous git info found so we assume we need to perform setup
        self.P("No previous local git info found. Assuming new repo version and initializing...")
        result = True
    return result

  # assets handling methods
  def __maybe_download_assets(self):
    """
      self.cfg_assets = {
        "url": "https://example.com/assets.zip",
        "operation": "download",
      }
      self.cfg_assets = {
        "url": "https://github.com/user/repo",
        "username": "username",
        "token": "token",
        "operation": "clone",
      }
      self.cfg_assets = {
        "url": "https://github.com/user/repo",
        "username": null,
        "token": null,
        "operation": "clone",
      }
      self.cfg_assets = {
        "url": "/path/to/local/dir",
        "operation": "download",
      }
      self.cfg_assets = {
        "url"=[["base64_encoded_file", "encoded_file_name"], ...],
        "operation": "decode",
      }
    """
    # handle assets url: download, extract, then copy, then delete
    relative_assets_path = self.os_path.join('downloaded_assets', self.plugin_id, 'assets')

    operation = None

    assets_path = None

    # check if assets is a dict or a string
    # and extract the url and operation
    if isinstance(self.cfg_assets, dict):
      dct_data = self.cfg_assets
      operation = dct_data.get("operation", "download")
      assets_path = dct_data.get("url", None)

    elif isinstance(self.cfg_assets, str):
      assets_path = self.cfg_assets
      operation = "download"

    if assets_path is None:
      raise ValueError("No assets provided")

    # now download the assets there
    if operation == "clone":
      username = dct_data.get("username", None)
      token = dct_data.get("token", None)
      self.git_clone(
        repo_url=assets_path,
        repo_dir=relative_assets_path,
        target='output',
        user=username,
        token=token,
        pull_if_exists=True, # no need to pull if each time we delete the folder
      )
      # now we cache the commit hash
      commit_hash = self.git_get_local_commit_hash(
        repo_dir=self.os_path.join(self.get_output_folder(), relative_assets_path),
      )
      self.__git_commit_hash = commit_hash
      self.P("Finished cloning git repository. Current commit hash: {}".format(self.__git_commit_hash))
      #
    elif operation == "download":
      self.maybe_download(
        url=assets_path,
        fn=relative_assets_path,
        target='output'
      )
    elif operation == "decode":
      target_dir = self.os_path.join(self.get_output_folder(), relative_assets_path)
      for base64_encoded_file, encoded_file_name in assets_path:
        file_path = self.os_path.join(target_dir, encoded_file_name)
        os.makedirs(self.os_path.dirname(file_path), exist_ok=True)

        encoded_file = self.base64_to_str(base64_encoded_file, decompress=True)
        with open(file_path, 'w') as f:
          f.write(encoded_file)
        # endwith
      # endfor
    else:
      raise ValueError(f"Invalid operation {operation}")

    # now check if it is a zip file
    assets_path = self.os_path.join(self.get_output_folder(), relative_assets_path)
    if self.os_path.isfile(assets_path):
      if not assets_path.endswith('.zip'):
        os.rename(assets_path, assets_path + '.zip')
        assets_path += '.zip'

      relative_assets_path = self.os_path.join('downloaded_assets', self.plugin_id, 'unzipped')
      self.maybe_download(
        url=assets_path,
        fn=relative_assets_path,
        target='output',
        unzip=True
      )
      # remove zip file
      os.remove(assets_path)

    assets_path = self.os_path.join(self.get_output_folder(), relative_assets_path)
    return assets_path


  def __maybe_init_assets(self):
    if self.assets_initialized:
      # check if new git assets are available
      new_repo_version = self.__check_new_repo_version()
      if new_repo_version:
        self.P("New git assets available. Reloading server...")
        self.__reload_server()
      return
    
    self.P("Initializing assets (`assets_initialized: {}`)".format(self.assets_initialized))

    # download/clone/create/unzip assets
    assets_path = self.__maybe_download_assets()

    # prepare environment variables
    self.__prepare_env(assets_path=assets_path)

    # initialize assets -- copy them to the temp directory
    self.initialize_assets(
      src_dir=assets_path,
      dst_dir=self.script_temp_dir,
      jinja_args=self.jinja_args
    )

    self.assets_initialized = True
    return

  
  
  # other plugin default methods
  
  def __setup_commands(self):
    self.setup_commands_started = [False] * len(self.get_setup_commands())
    self.setup_commands_finished = [False] * len(self.get_setup_commands())
    self.setup_commands_processes = [None] * len(self.get_setup_commands())
    self.setup_commands_start_time = [None] * len(self.get_setup_commands())

    self.start_commands_started = [False] * len(self.get_start_commands())
    self.start_commands_finished = [False] * len(self.get_start_commands())
    self.start_commands_processes = [None] * len(self.get_start_commands())
    self.start_commands_start_time = [None] * len(self.get_start_commands())

    self.logs = self.deque(maxlen=1000)
    self.dct_logs_reader = {}

    self.err_logs = self.deque(maxlen=1000)
    self.dct_err_logs_reader = {}

    self.P(f"Port: {self.port}")
    self.P(f"Setup commands: {self.get_setup_commands()}")
    self.P(f"Start commands: {self.get_start_commands()}")
    return


  def _on_close(self):
    self.__maybe_close_setup_commands()
    self.__maybe_close_start_commands()

    # close all log readers that are still running
    # this should not have any effect, as the logs should have been
    # closed during the teardown of the setup and start processes
    self.__maybe_read_and_stop_all_log_readers()

    # cleanup the temp directory
    shutil.rmtree(self.script_temp_dir)

    self.__deallocate_port()

    super(BaseWebAppPlugin, self)._on_close()
    return

  def _on_command(self, data, delta_logs=None, full_logs=None, start=None, reload=None, **kwargs):
    super(BaseWebAppPlugin, self)._on_command(data, **kwargs)

    if (isinstance(data, str) and data.upper() == 'DELTA_LOGS') or delta_logs:
      logs, err_logs = self.__get_delta_logs()
      self.add_payload_by_fields(
        command_params=data,
        logs=logs,
        err_logs=err_logs,
      )
    if (isinstance(data, str) and data.upper() == 'FULL_LOGS') or full_logs:
      # TODO: Implement full logs
      self.add_payload_by_fields(
        command_params=data,
        logs=[]
      )

    if (isinstance(data, str) and data.upper() == 'START') or start:
      self.can_run_start_commands = True
      self.P("Starting server")

    if (isinstance(data, str) and data.upper() == 'RELOAD') or reload:
      self.__reload_server()

    return

  # Exposed methods
  @property
  def port(self):
    if 'USED_PORTS' not in self.plugins_shmem:
      return None
    if self.str_unique_identification not in self.plugins_shmem['USED_PORTS']:
      return None
    port = self.plugins_shmem['USED_PORTS'][self.str_unique_identification]
    return port

  @property
  def jinja_args(self):
    return {}

  def get_setup_commands(self):
    try:
      return super(BaseWebAppPlugin, self).get_setup_commands() + self.cfg_setup_commands
    except AttributeError:
      return self.cfg_setup_commands

  def get_start_commands(self):
    try:
      return super(BaseWebAppPlugin, self).get_start_commands() + self.cfg_start_commands
    except AttributeError:
      return self.cfg_start_commands

  def initialize_assets(self, src_dir, dst_dir, jinja_args):
    """
    Initialize and copy assets, expanding any jinja templates.
    All files from the source directory are copied copied to the
    destination directory with the following exceptions:
      - are symbolic links are ignored
      - files named ending with .jinja are expanded as jinja templates,
        .jinja is removed from the filename and the result copied to
        the destination folder.
    This maintains the directory structure of the source folder.

    Parameters
    ----------
    src_dir: str, path to the source directory
    dst_dir: str, path to the destination directory
    jinja_args: dict, jinja keys to use while expanding the templates

    Returns
    -------
    None
    """

    # now copy the assets to the destination
    self.P(f'Copying assets from {src_dir} to {dst_dir} with keys {jinja_args}')

    env = Environment(loader=FileSystemLoader('.'))
    # Walk through the source directory.
    for root, _, files in os.walk(src_dir):
      for file in files:
        src_file_path = self.os_path.join(root, file)
        dst_file_path = self.os_path.join(
          dst_dir,
          self.os_path.relpath(src_file_path, src_dir)
        )

        # If we have a symlink don't do anything.
        if self.os_path.islink(src_file_path):
          continue

        # Make sure the destination directory exists.
        os.makedirs(self.os_path.dirname(dst_file_path), exist_ok=True)

        is_jinja_template = False
        if src_file_path.endswith(('.jinja')):
          dst_file_path = dst_file_path[:-len('.jinja')]
          is_jinja_template = True
        if src_file_path.endswith(('.j2')):
          dst_file_path = dst_file_path[:-len('.j2')]
          is_jinja_template = True

        # If this file is a jinja template render it to a file with the .jinja suffix removed.
        # Otherwise just copy the file to the destination directory.
        if is_jinja_template:
          template = env.get_template(src_file_path)
          rendered_content = template.render(jinja_args)
          with open(dst_file_path, 'w') as f:
            f.write(rendered_content)
        else:
          shutil.copy2(src_file_path, dst_file_path)
        # endif is jinja template
      # endfor all files
    # endfor os.walk

    # write .env file in the target directory
    # environment variables are passed in subprocess.Popen, so this is not needed
    # but it's useful for debugging
    with open(self.os_path.join(self.script_temp_dir, '.env'), 'w') as f:
      for key, value in self.prepared_env.items():
        f.write(f"{key}={value}\n")

    with open(self.os_path.join(self.script_temp_dir, '.start_env_used'), 'w') as f:
      for key, value in self.prepared_env.items():
        f.write(f"{key}={value}\n")

    with open(self.os_path.join(self.script_temp_dir, '.setup_env_used'), 'w') as f:
      for key, value in self.base_env.items():
        f.write(f"{key}={value}\n")

    # now cleanup the output folder
    shutil.rmtree(self.os_path.join(self.get_output_folder(), 'downloaded_assets', self.plugin_id))

    self.P("Assets copied successfully")

    return


  def _process(self):  # Check: _process as opposed to process
    
    self.__maybe_init_assets() # Check: auto-updates point here?

    self.__maybe_run_all_setup_commands()

    self.__maybe_run_all_start_commands()

    self.__maybe_print_all_logs() # Check: must review 

    super(BaseWebAppPlugin, self)._process() # Check: why is this required
    return

import subprocess
import threading

from naeural_core.business.base.drivers.device import Device

__VER__ = '1.0.0'

_CONFIG = {
  **Device.CONFIG,

  'ALLOW_EMPTY_INPUTS': True,

  "PROCESS_DELAY": 10,

  "COOLDOWN": 3,  # seconds

  'VALIDATION_RULES': {
    **Device.CONFIG['VALIDATION_RULES'],
  },

  'DEVICE_DEBUG': False,
}


class EPDConstants:
  ARGUMENT_TYPE = "type"
  ARGUMENT_NAME = "name"
  ARGUMENT_TYPE_STATIC = "static"
  ARGUMENT_TYPE_DYNAMIC = "dynamic"
  ARGUMENT_TYPE_FILE = "file"
  ARGUMENT_FORCE = "force"
  ARGUMENT_VALUE = "value"


class ExternalProgramDevice(Device):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(ExternalProgramDevice, self).__init__(**kwargs)
    self.allowed_programs = ['ffmpeg']
    self.__last_operation_time = 0
    self.__process = None
    return

  def __validate_external_program_schema(self):
    """
    Validate the external program schema defined in the configuration

    Returns
    -------

    """
    if self.cfg_external_program_binary is None:
      raise ValueError("EXTERNAL_PROGRAM_BINARY not found in CONFIG")

    if self.cfg_external_program_allow_reentrant is None:
      raise ValueError("EXTERNAL_PROGRAM_ALLOW_REENTRANT not found in CONFIG")

    if self.cfg_external_program_arguments is None:
      raise ValueError("EXTERNAL_PROGRAM_ARGUMENTS not found in CONFIG")

    for arg in self.cfg_external_program_arguments:
      if EPDConstants.ARGUMENT_TYPE not in arg or arg[EPDConstants.ARGUMENT_TYPE] not in [
        EPDConstants.ARGUMENT_TYPE_STATIC, EPDConstants.ARGUMENT_TYPE_DYNAMIC, EPDConstants.ARGUMENT_TYPE_FILE]:
        raise ValueError("Invalid or missing type in argument schema")

      if arg[EPDConstants.ARGUMENT_TYPE] in [EPDConstants.ARGUMENT_TYPE_DYNAMIC,
                                             EPDConstants.ARGUMENT_TYPE_FILE] and EPDConstants.ARGUMENT_NAME not in arg:
        raise ValueError("Name not specified for dynamic or file type argument in schema")

    return

  def __prepare_files(self, **kwargs):
    """
    Prepare the files to be used as arguments for the external program
    Parameters
    ----------
    args

    Returns
    -------
    dict : file paths
    """
    file_paths = {}
    for arg in self.cfg_external_program_arguments:
      if arg[EPDConstants.ARGUMENT_TYPE] == EPDConstants.ARGUMENT_TYPE_FILE:
        file_url = kwargs.get(arg[EPDConstants.ARGUMENT_NAME])
        if file_url:
          filename = file_url.split("/")[-1].split(":")[-1]
          local_path = self.download(url=file_url, fn=filename, **kwargs)
          if local_path is None and arg[EPDConstants.ARGUMENT_FORCE] is True:
            raise ValueError(f"Error downloading file from URL: {file_url}")
          file_paths[arg[EPDConstants.ARGUMENT_NAME]] = local_path

    return file_paths

  def _process_dynamic_params(self, **args):
    """
    Process dynamic parameters, all derived classes must implement this method.
    Returns
    -------

    """
    raise NotImplementedError("Method not implemented")

  def _on_external_program_exited(self, exit_code=None, stdout=None, stderr=None):
    """
    This method is called when the external program has exited
    Parameters
    ----------
    exit_code
    stdout
    stderr

    Returns
    -------

    """
    raise NotImplementedError("Method not implemented")

  def __device_execute_program(self, program, args):
    """
    Execute the external program with the given arguments
    Parameters
    ----------
    program
    args

    Returns
    -------
    str : stdout of the program
    -------
    Raises
    -------
    Exception : if the program returns a non-zero exit code

    """
    if program not in self.allowed_programs:
      raise ValueError(f"Program {program} not allowed to run")

    full_command = [program] + args
    self.__process = subprocess.Popen(full_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if not self.cfg_external_program_allow_reentrant:
      stdout, stderr = self.__process.communicate()

      if self.__process.returncode == 0:
        return stdout.decode()
      else:
        raise Exception(f"Error executing program: {stderr.decode()}")

    return

  def device_run_external_program(self, **kwargs):
    """
    Run the external program with the given arguments
    Parameters
    ----------
    args

    Returns
    -------

    """
    self.__validate_external_program_schema()

    # Handle cooldown
    if self.__last_operation_time != 0 and (self.time() - self.__last_operation_time) < self.cfg_cooldown:
      raise ValueError("Operation is still in cooldown")

    try:
      files = self.__prepare_files(**kwargs)

      argument_list = []
      # check if the method is implemented in the derived class
      if hasattr(self, "_process_dynamic_params"):
        argument_list = self._process_dynamic_params(files=files, **kwargs)
      self.__device_execute_program(self.cfg_external_program_binary, argument_list)
    except Exception as e:
      message = f"Error running external program: {e}"
      if self.cfg_device_debug:
        self.P(message, color='red')
      self._create_error_payload(message)
      raise e
    finally:
      self.__last_operation_time = self.time()

    return

  def device_action_run_external_program(self, **kwargs):
    """
    Run the external program with the given arguments
    This method is called by the client using and instance command

    Parameters
    ----------
    args

    Returns
    -------

    """
    try:
      self.device_run_external_program(**kwargs)
      self._create_action_payload(
        action="RUN_EXTERNAL_PROGRAM", 
        message="command executed", 
        command_params=kwargs,
      )
      return
    except Exception as e:
      message = f"Error running external program: {e}"
      if self.cfg_device_debug:
        self.P(message, color='red')
      return

  def process(self):
    if self.cfg_external_program_allow_reentrant and self.__process is not None:
      # Poll the process to check if it has finished without blocking
      exit_code = self.__process.poll()
      # Process has finished, read the output and errors
      stdout, stderr = self.__process.communicate()

      if exit_code is not None:
        # Process has finished, call the on_external_program_exited method if it is implemented
        if hasattr(self, "_on_external_program_exited"):
          exit_code = self.__process.returncode
          self._on_external_program_exited(exit_code, stdout, stderr)
          return

        if exit_code != 0:
          message = f"Process finished with error: {stderr.decode()}"
          if self.cfg_device_debug:
            self.P(message, color='red')
          self._create_error_payload(message)
        else:
          message = f"Process completed successfully: {stdout.decode()}"
          if self.cfg_device_debug:
            self.P(message, color='green')
          self._create_action_payload(action="RUN_EXTERNAL_PROGRAM", message=message)

          # Reset the process attribute to None after handling completion
        self.__process = None
        # If exit_code is None, the process is still running, and we do nothing

        state = {
          "running": True if self.__process is not None else False,
          "last_run": self.__last_operation_time,
        }
        self._create_device_state_payload(state)
    return

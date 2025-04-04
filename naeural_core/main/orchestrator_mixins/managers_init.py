import traceback

from naeural_core import constants as ct

from naeural_core.data import CaptureManager
from naeural_core.comm import CommunicationManager
from naeural_core.config import ConfigManager
from naeural_core.serving import ServingManager
from naeural_core.business import BusinessManager
from naeural_core.io_formatters import IOFormatterManager
from naeural_core.heavy_ops import HeavyOpsManager
from naeural_core.remote_file_system import FileSystemManager
from naeural_core.bc import DefaultBlockEngine

from naeural_core.ipfs import R1FSEngine

class _ManagersInitMixin(object):
  def __init__(self):
    super(_ManagersInitMixin, self).__init__()
    return

  def _initialize_config_manager(self):
    header = "Initializing config manager ..."
    self.P(header, color='b', boxed=True)

    self._config_manager = ConfigManager(
      log=self.log,
      shmem=self.app_shmem,
      owner=self,
      subfolder_path=ct.CONFIG_MANAGER.DEFAULT_SUBFOLDER_PATH,
      fn_app_config=ct.CONFIG_MANAGER.DEFAULT_FN_APP_CONFIG,
      folder_streams_configs=ct.CONFIG_MANAGER.DEFAULT_FOLDER_STREAMS_CONFIGS
    )
    
    # maybe delete admin_pipeline
    
    if self.cfg_reset_admin_pipeline:
      self.P("Resetting admin pipeline...")
      self._config_manager._delete_stream_config('admin_pipeline')
    else:
      self.P("Preserving admin pipeline. Please make sure this is intended.", color='r', boxed=True)
    
    # first try to retrieve the config including the streams
    self.P("Initiating ConfigManager retrieve...")
    self._config_manager.retrieve(lst_config_retrieve=self.cfg_config_retrieve)
    try:
      # then load the config_app.txt or similar and then the streams
      self.P("Initiating ConfigManager load...")      
      self._config_manager.load()
    except Exception as e:
      self.P(str(e))
      raise e
    return

  def _initialize_comm_manager(self):
    avail_cmds = self.get_commands_list()
    header = "Initializing comm mgr ..."
    self.P(header, color='b', boxed=True)
    self.P(" Comm mgr commands: {}".format(avail_cmds))

    self._comm_manager = CommunicationManager(
      log=self.log,
      config=self._config_manager.cfg_app_communication,
      shmem=self.app_shmem,
      device_version=self.__version__,
      environment_variables=self.cfg_communication_environment,
      avail_commands=avail_cmds,
      DEBUG=self.DEBUG
    )
    self._comm_manager.validate(raise_if_error=True)
    self._comm_manager.start_communication()
    
    self.app_shmem["comm_manager"] = self._comm_manager
    return

  def _initialize_capture_manager(self):
    header = "Initializing capture manager ..."
    self.P(header, color='b', boxed=True)
    
    self._capture_manager = CaptureManager(
      log=self.log, 
      shmem=self.app_shmem, 
      owner=self,
      DEBUG=self.DEBUG,
      environment_variables=self.cfg_capture_environment,
    )

    self.app_shmem["capture_manager"] = self._capture_manager
    return

  def _initialize_serving_manager(self):
    header = "Initializing serving manager ..."
    self.P(header, color='b', boxed=True)

    self._serving_manager = ServingManager(
      log=self.log,
      shmem=self.app_shmem,
      DEBUG=self.DEBUG,
      full_debug=self.cfg_serving_environment.get('FULL_DEBUG', False),
      log_timeouts_period=self.cfg_serving_environment.get('LOG_TIMEOUTS_PERIOD', 3600),
      owner=self,
    )

    self.app_shmem["serving_manager"] = self._serving_manager
    return

  def _initialize_business_manager(self):
    header = "Initializing business manager ..."
    self.P(header, color='b', boxed=True)

    self._business_manager = BusinessManager(
      log=self.log, 
      owner=self,
      shmem=self.app_shmem,
      run_on_threads=self.cfg_plugins_on_threads,
      environment_variables=self.cfg_plugins_environment,
      DEBUG=self.DEBUG
    )

    self.app_shmem["business_manager"] = self._business_manager
    return

  def _initialize_io_formatter_manager(self):
    header = "Initializing IO formatter manager..."
    self.P(header, color='b', boxed=True)

    self._io_formatter_manager = IOFormatterManager(log=self.log, DEBUG=self.DEBUG)
    formatter_name = self.cfg_io_formatter
    if formatter_name != '':
      # create default (output) formatter
      self._io_formatter_manager.create_formatter(formatter_name)

    self._app_shmem['io_formatter_manager'] = self._io_formatter_manager
    return

  def _initialize_heavy_ops_manager(self):

    header = "Initializing Heavy Operations manager..."
    self.P(header, color='b', boxed=True)

    self._heavy_ops_manager = HeavyOpsManager(
      log=self.log, 
      shmem=self.app_shmem, 
      DEBUG=self.DEBUG,
    )
    self._app_shmem['heavy_ops_manager'] = self._heavy_ops_manager
    return

  def _initialize_file_system_manager(self):
    header = "Initializing File System manager..."
    self.P(header, color='b', boxed=True)

    self._file_system_manager = FileSystemManager(
      log=self.log,
      config=self._config_manager.cfg_app_file_upload,
      DEBUG=self.DEBUG
    )
    self._file_system_manager.validate(raise_if_error=True)
    self._file_system_manager.create_file_system()
    self._app_shmem['file_system_manager'] = self._file_system_manager
    return
  
  
  def _initialize_private_blockchain(self):    
    try:    
      self._blockchain_manager = DefaultBlockEngine(
        log=self.log,
        name=self.e2_id,
        config=self.cfg_blockchain_config,
        eth_enabled=ct.ETH_ENABLED,
      )
    except:
      raise ValueError("Failure in private blockchain setup:\n{}".format(traceback.format_exc()))

    self._app_shmem[ct.BLOCKCHAIN_MANAGER] = self._blockchain_manager
    return
  
  
  def _initialize_r1fs(self):
    try:
      self._r1fs_engine = R1FSEngine(
        logger=self.log,
        debug=self.debug_r1fs,
      )
    except:
      raise ValueError("Failure in r1fs setup:\n{}".format(traceback.format_exc()))
    # end try to initialize R1FS
    self._app_shmem[ct.R1FS_ENGINE] = self._r1fs_engine
    return
  

  @property
  def config_manager(self):
    return self._config_manager

  @property
  def comm_manager(self):
    return self._comm_manager

  @property
  def capture_manager(self):
    return self._capture_manager

  @property
  def serving_manager(self):
    return self._serving_manager

  @property
  def business_manager(self):
    return self._business_manager

  @property
  def file_system_manager(self):
    return self._file_system_manager
  
  @property
  def blockchain_manager(self) -> DefaultBlockEngine:
    return self._blockchain_manager

  @property
  def app_shmem(self):
    return self._app_shmem


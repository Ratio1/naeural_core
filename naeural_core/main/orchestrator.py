#global dependencies
import re
import os
import traceback
import numpy as np

from collections import deque
from time import perf_counter, sleep, time
from threading import Thread
from copy import deepcopy

#local dependencies
from naeural_core import constants as ct

from naeural_core.main.app_monitor import ApplicationMonitor
from naeural_core.main.net_mon import NetworkMonitor
from naeural_core.main.command_handlers import ExecutionEngineCommandHandlers
from naeural_core.main.orchestrator_mixins import (
  _ValidateConfigStartupMixin,
  _ManagersInitMixin,
  _OrchestratorUtils
)
from naeural_core.data import CaptureManager
from naeural_core.config import ConfigManager
from naeural_core.business import BusinessManager
from naeural_core.comm import CommunicationManager
from naeural_core.serving import ServingManager
from naeural_core.io_formatters import IOFormatterManager
from naeural_core.heavy_ops import HeavyOpsManager
from naeural_core.main.main_loop_data_handler import MainLoopDataHandler
from naeural_core.remote_file_system import FileSystemManager
from naeural_core.bc import DefaultBlockEngine

from naeural_core.ipfs import R1FSEngine

import json


from naeural_core.serving.ai_engines.utils import (
  get_serving_process_given_ai_engine,
  get_params_given_ai_engine
)

from naeural_core import DecentrAIObject
from naeural_core import Logger
from naeural_core.local_libraries import _ConfigHandlerMixin

from naeural_core.main.ver import __VER__ as __CORE_VER__

try:
  from ver import __VER__ as __APP_VER__
except:
  __APP_VER__ = None

SHUTDOWN_DELAY = 5

CHECK_AND_COMPLETE_ITERATIONS = 30 # how many iterations to wait for the dAuth completion
CHECK_AND_COMPLETE_SLEEP_PERIOD = 30 # how many seconds to wait between iterations
CHECK_AND_COMPLETE_TIMEOUT = CHECK_AND_COMPLETE_ITERATIONS * CHECK_AND_COMPLETE_SLEEP_PERIOD


SHUTDOWN_RESET_FILE = "/shutdown_reset"

class Orchestrator(DecentrAIObject, 
                   ExecutionEngineCommandHandlers,
                   _ValidateConfigStartupMixin,
                   _ManagersInitMixin,
                   _ConfigHandlerMixin,
                   _OrchestratorUtils,
                   ):

  def __init__(self, log : Logger, **kwargs):
    if __APP_VER__ is None:
      self.__version__ = __CORE_VER__
      # This is a bit ambiguous, but we want to use __version__ as the default 
      self.core_version = None
    else:
      self.__version__ = __APP_VER__
      self.core_version = __CORE_VER__

    self.__loop_stage = -1
    self.__main_loop_stopped = False
    self.__main_loop_stopped_count = 0 
    self.__main_loop_stopped_time = None
    self.__main_loop_stop_at_stage = None
    self.__main_loop_last_stage_change = time()
    self.__main_loop_stoplog = []
    self.__simulated_mlstops = 0
    self.__last_local_info_save = time()    
    self.__save_local_address_error_logged = False
    
    self.__is_supervisor_node = None
    self.__evm_network = None


    self._capture_manager : CaptureManager              = None
    self._comm_manager : CommunicationManager           = None
    self._config_manager : ConfigManager                = None
    self._business_manager : BusinessManager            = None
    self._serving_manager : ServingManager              = None
    self._io_formatter_manager : IOFormatterManager     = None
    self._heavy_ops_manager : HeavyOpsManager           = None
    self._file_system_manager : FileSystemManager       = None
    self._blockchain_manager : DefaultBlockEngine       = None
    
    self._r1fs_engine : R1FSEngine                      = None

    self._data_handler : MainLoopDataHandler = None

    self._app_shmem = {}
    self._app_monitor = None
    self._network_monitor = None
    self._last_checked_main_loop_iter = None

    self.__done = False
    self._last_heartbeat = -100000 # large value for 1st HB trigger
    self._recorded_first_object_tree = False
    self._main_loop_counts = {'ITER' : 0, 'VOID_ITER' : 0}
    self._last_main_loop_pass_time = perf_counter()
    self._heartbeat_counter = 0
    self._current_dct_config_streams = {}
    self._should_send_initial_log = False
    self._initial_log_sent = False
    self.loop_timings = deque(maxlen=3600)
    self._reset_timers = False
    self.__is_mlstop_dangerous = False
    self._last_timers_dump = time()
    self._last_node_status_warning = None

    self._payloads_count_queue = deque(maxlen=1000)
    self._payloads_count_queue.append(0)
    self._payloads_count_queue.append(0)

    self._return_code = None
    self._thread_async_comm = None
    self._in_shutdown = False
    self._non_business_payloads = deque(maxlen=100)

    super(Orchestrator, self).__init__(log=log, prefix_log='[MAIN]', **kwargs)
    return


  def set_loop_stage(self, stage, is_dangerous=True):
    with self.log.managed_lock_resource('set_loop_stage_for_logging'):
      self.__is_mlstop_dangerous = is_dangerous
      if not self.__loop_stage != stage:
        self.__main_loop_last_stage_change = time()
      # endif main loop stopped
      self.__loop_stage = stage
    # endwith lock
    return

  
  def P(self, s, color=None, **kwargs):
    if color is None or (isinstance(color,str) and color[0] not in ['e', 'r']):
      color = ct.COLORS.MAIN
    super().P(s, prefix=False, color=color, **kwargs)
    return  


  def startup(self):
    super().startup()
    self.P("Starting Execution Engine '{}' core v.{}".format(self.cfg_eeid, self.__version__))
    if self.runs_in_docker:
      self.P("Running in Docker container detected.")
    self._maybe_env_and_docker_setup()
    
    self.__admin_pipeline = None
    self.__get_admin_pipeline_config()
    
    self.log.start_timer(ct.TIMER_APP)
    
    self.validate()   
    # here we can run `_init_all_processes` or we can run it (better) in the main loop
    return
  
  def validate_config_startup(self):
    self.P("Running config validation...")
    ee_id = self.cfg_eeid
    # check if ee_id contains only letters, numbers, underscores and dashes
    if not self.log.is_url_friendly(ee_id):
      self.P("EE_ID '{}' contains invalid characters. Only letters, numbers, underscores and dashes are allowed.".format(ee_id), color='error')
    else:
      self.P("EE_ID '{}' is valid.".format(ee_id))
    seconds_heartbeat = self.config_data.get(ct.SECONDS_HEARTBEAT, 1e3)
    if seconds_heartbeat > ct.MAX_SECONDS_HEARTBEAT:
      self.P("WARNING: seconds_heartbeat is set to a very high value: {}s and will default to {}".format(
        seconds_heartbeat, ct.MAX_SECONDS_HEARTBEAT), color='r'
      )
    else:
      self.P("Seconds heartbeat set to: {}s".format(seconds_heartbeat))
    return
  
  ####### pre-init area
  
  @property
  def evm_network(self):
    return self.__evm_network
  
  
  @property
  def cfg_system_temperature_check(self):
    return self.config_data.get('SYSTEM_TEMPERATURE_CHECK', True)
  
  
  @property
  def runs_in_docker(self):
    # this must be same as in Dockerfile
    is_docker = str(os.environ.get('AINODE_DOCKER')).lower() in ["yes", "true"]
    return is_docker
  
  @property 
  def docker_env(self):
    docker_env = os.environ.get('AINODE_ENV')
    return docker_env

  @property 
  def docker_source(self):
    docker_source = os.environ.get('AINODE_DOCKER_SOURCE')
    return docker_source

  @property
  def debug_simulated_mlstop(self):
    return self.config_data.get('DEBUG_SIMULATED_MLSTOP', 0) or 0

  @property
  def debug_simulated_mlstop_start(self):
    # How many iterations to wait before simulating a main loop stop.
    return self.config_data.get('DEBUG_SIMULATED_MLSTOP_START', 0) or 0

  @property
  def debug_simulated_mlstop_count(self):
    return self.config_data.get('DEBUG_SIMULATED_MLSTOP_COUNT', 0) or 0

  def _maybe_env_and_docker_setup(self):    
    default_device = os.environ.get('EE_DEVICE')
    if default_device is not None:
      self.P("WARNING: Found DEFAULT_DEVICE overwrite in OS env: '{}'. Make sure this is as intended!".format(default_device), color='r')
      if 'SERVING_ENVIRONMENT' not in self.config_data:
        self.config_data['SERVING_ENVIRONMENT'] = {}      
      self.config_data['SERVING_ENVIRONMENT']['DEFAULT_DEVICE'] = default_device
    return
  
  def _check_and_complete_environment_variables(self):
    
    self.P(f"Node <{self.eth_address}> completing setup for network <{self.evm_network}>...")
    start_ts = time()
    done = False
    tries = 0
    while not done and (time() - start_ts) < CHECK_AND_COMPLETE_TIMEOUT:
      tries += 1
      dct_env_output = self.blockchain_manager.dauth_autocomplete(
        dauth_endp=None,  # get automatically
        add_env=True,
        debug=False,
        max_tries=5,
        sender_alias=self.cfg_eeid,
      )
      # Will be None in case of bad URL. In that case there is no need to reattempt it.
      done = (dct_env_output is None) or (isinstance(dct_env_output, dict) and len(dct_env_output) > 0)

      if not done:
        elapsed = time() - start_ts
        self.P(f'Retrying dAuth completion({tries} tries so far in {elapsed:.1f}s)...')
        sleep(
          CHECK_AND_COMPLETE_SLEEP_PERIOD / 2 + 
          np.random.randint(1, CHECK_AND_COMPLETE_SLEEP_PERIOD // 2)
        )
    # endwhile not done

    if not done:
      self.P(f'WARNING: <{self.eth_address}> could not retrieve dAuth in {CHECK_AND_COMPLETE_TIMEOUT}s. '
             f'Continuing with local environment...')

    if dct_env_output is not None and len(dct_env_output) > 0:
      self.P(f'Reloading config due to dAuth modification of following env vars: {list(dct_env_output.keys())}')
      self.log.reload_config()
      self.P("Config reloaded.")
    return
  
  ####### end pre-init area
  
  def _init_all_processes(self):
    self._data_handler = MainLoopDataHandler(log=self.log, owner=self, DEBUG=self.DEBUG)
    self._app_monitor = ApplicationMonitor(log=self.log, owner=self)

    self._initialize_private_blockchain()
    if self.e2_address is None:
      raise ValueError("Node address is `None`. Check your node configuration and network settings.")

    # set the EVM network
    self.__evm_network = self.blockchain_manager.evm_network
    # end set EVM network

    self.save_local_address()

    ### at this point we should check if the authentication information is available in the
    ### environment and if not we should pool the endpoint for the information    
    self._check_and_complete_environment_variables()
    # just after completed the dAuth we can check the supervisor status 
    _is_super = os.environ.get("EE_SUPERVISOR", False)
    self.__is_supervisor_node = self.log.str_to_bool(_is_super)
    self.P(f"SUPERVISOR = {self.is_supervisor_node} ({_is_super})", boxed=True)
    ### following the env update we can proceed with the managers initialization
    ### at this point the env can be considered complete and can have updates such as:
    ### - era information
    ### - external storages
    ### - list of whitelisted nodes <=== extremely important for new nodes that must accept supervisor based 
    ###   distributions of jobs
    
    
    self._network_monitor = NetworkMonitor(
      node_name=self.cfg_eeid, node_addr=self.e2_address,
      log=self.log, DEBUG=self.DEBUG,
      blockchain_manager=self._blockchain_manager,
    )

    self._app_shmem['network_monitor'] = self._network_monitor
    self._app_shmem['config_startup'] = self.config_data
    self._app_shmem['get_node_running_time'] = self.get_node_running_time
    self._app_shmem[ct.CALLBACKS.MAIN_LOOP_RESOLUTION_CALLBACK] = self._get_mean_loop_freq
    self._app_shmem[ct.CALLBACKS.INSTANCE_CONFIG_SAVER_CALLBACK] = self.save_config_pipeline_instance
    self._app_shmem[ct.CALLBACKS.PIPELINE_CONFIG_SAVER_CALLBACK] = self.save_config_pipeline


    self._initialize_managers()

    self.log.register_close_callback(self._maybe_gracefull_stop)

    self._thread_async_comm = Thread(
      target=self.asynchronous_communication,
      args=(),
      name=ct.THREADS_PREFIX + 'async_comm',
      daemon=True,
    )

    self._thread_async_comm.start()
    return
    
  def _initialize_managers(self):
    self.P("=================== Managers initialization procedure ===================")
    # io formatter and config manager should be initialized first time
    self._initialize_io_formatter_manager()
    self._initialize_config_manager()

    # the other managers can be initialized in any order
    self._initialize_r1fs()
    self._initialize_file_system_manager()
    self._initialize_heavy_ops_manager()
    self._initialize_comm_manager()
    self._initialize_capture_manager()
    self._initialize_serving_manager()
    self._initialize_business_manager()
    
    
    ## DEBUG SECTION BELOW
    # self._app_monitor.get_gpu_info()
    # self._app_monitor.get_basic_perf_info()
    ## END DEBUG SECTION
    sleep(4)
    msg = """Info:
==============================================================================
------------------------------------------------------------------------------

  All managers initialized:
      E2 name: {}
      E2 addr: {}
      {}
      
------------------------------------------------------------------------------
==============================================================================
    """.format(
      self.e2_id, self.e2_address, 
      "Node is SECURE" if self.is_secured else "Node is NOT SECURE",
    )
    self.log.P(msg, color='g' if self.is_secured else 'r')
    self.log.P("Heartbeats will {}contain pipeline data and will{} contain active plugins.".format(
      "" if self.cfg_hb_contains_pipelines else "NOT ",
      "" if self.cfg_hb_contains_active_plugins else "NOT ",
    ), color='r')
    self.save_local_address()
    return
  
  
  def _maybe_save_local_info(self):
    if (time() - self.__last_local_info_save) >= 2:
      self.save_local_address()
    return


  def save_local_address(self):
    try:
      folder = self.log.get_data_folder()
      ## cleanup
      CLEANUP_FILES = ["local_address.txt", "local_address.json"]
      for fn in CLEANUP_FILES:
        fpath = os.path.join(folder, fn)
        if os.path.exists(fpath):
          os.remove(fpath)
      ## end cleanup
      try:
        addr_file = os.path.join(folder, ct.LocalInfo.LOCAL_INFO_FILE)
        current_epoch = self._network_monitor.epoch_manager.get_current_epoch()
        current_epoch_avail = self._network_monitor.epoch_manager.get_current_epoch_availability()
        last_5_epochs = self._network_monitor.epoch_manager.get_node_last_n_epochs(
          node_addr=self.e2_addr, n=5, autocomplete=True, as_list=False,
        )
      except:
        current_epoch = None
        current_epoch_avail = None
        last_5_epochs = None
      #endif
      data = {
        ct.LocalInfo.K_ADDRESS : self.e2_address,
        ct.LocalInfo.K_ALIAS   : self.e2_id,
        ct.LocalInfo.K_ETH_ADDRESS : self.eth_address,
        ct.LocalInfo.K_VER_LONG : f"v{self.__version__} | core v{self.core_version} | SDK {self.log.version}",
        ct.LocalInfo.K_VER_SHORT : f"v{self.__version__}",
        ct.LocalInfo.K_INFO : {
          'whitelist' : self.whitelist_full,
          'current_epoch' : current_epoch,
          'current_epoch_avail' : current_epoch_avail,
          'last_epochs'       : last_5_epochs,
          'evm_network'       : self.evm_network,
        },
      }
      try:
        data[ct.LocalInfo.K_INFO]['r1fs_id'] = self.r1fs_id
        data[ct.LocalInfo.K_INFO]['r1fs_online'] = self.r1fs_started
        data[ct.LocalInfo.K_INFO]['comm_last_active'] = self._comm_manager.default_comm_last_active
      except Exception as exc:
        self.P(f"Failed to get local info: {exc}", color='r')
      with open(addr_file, 'w') as f:
        f.write(json.dumps(data, indent=2))
      self.__last_local_info_save = time()
    except Exception as e:
      if not self.__save_local_address_error_logged:
        self.P(f"Error saving local info: {e}\n{traceback.format_exc()}", color='r')
        self.__save_local_address_error_logged = True
    return


  def __get_admin_pipeline_config(self):
    """
    This method retrieves the admin pipeline configuration from the startup config then
    checks if all mandatory pipelines are present. If not, it adds them.
    """
    admin_pipeline = self.config_data.get('ADMIN_PIPELINE')  
    if admin_pipeline is None:
      self.P("admin_pipeline TEMPLATE not found in startup config. Defaulting to basecode.")
      admin_pipeline = ct.ADMIN_PIPELINE
    else:
      self.P("admin_pipeline TEMPLATE found in startup config: {}".format(list(admin_pipeline.keys())))
    # now check  (ct) defined admin pipelines to be contained in the property
    for k in ct.ADMIN_PIPELINE:
      if k not in admin_pipeline:
        self.P("  Mandatory `{}` not found in base admin pipeline TEMPLATE config. Adding...".format(k))
        admin_pipeline[k] = ct.ADMIN_PIPELINE[k]
      else:
        self.P(f'  Found `{k}` in admin pipeline config. Merging...')
        admin_pipeline[k] = {
          **ct.ADMIN_PIPELINE[k],
          **admin_pipeline[k],
        }
      # endif k not in admin_pipeline
    # endfor each mandatory pipeline
    self.__admin_pipeline = admin_pipeline
    return
  
  @property
  def admin_pipeline_template(self):
    return self.__admin_pipeline
  
  @property
  def e2_id(self):
    return self.cfg_eeid


  @property
  def e2_address(self):
    addr = None
    if self._blockchain_manager is not None:
      addr = self._blockchain_manager.address
    return addr
  
  @property
  def eth_address(self):
    addr = None
    if self._blockchain_manager is not None:
      addr = self._blockchain_manager.eth_address
    return addr  
  
  @property
  def whitelist(self):
    """ Will return the whitelist of the blockchain manager (if it exists) with the addresses of the allowed nodes and no prefixes"""
    lst_whitelist = []
    if self._blockchain_manager is not None:
      lst_whitelist = self._blockchain_manager.whitelist
    return lst_whitelist
  
  @property
  def whitelist_full(self):
    """ Will return the whitelist of the blockchain manager (if it exists) with the addresses of the allowed nodes and prefixes"""
    lst_whitelist = []
    if self._blockchain_manager is not None:
      lst_whitelist = self._blockchain_manager.whitelist
      lst_whitelist = [self._blockchain_manager._add_prefix(addr) for addr in lst_whitelist]
    return lst_whitelist
  
  @property
  def r1fs(self):
    return self._r1fs_engine
  
  
  @property
  def r1fs_id(self):
    if self.r1fs is not None:
      return self.r1fs.ipfs_id
    return None
  
  
  @property
  def r1fs_started(self):
    if self.r1fs is not None:
      return self.r1fs.ipfs_started
    return False
  
  
  @property
  def debug_r1fs(self):
    debug_r1fs = self.log.str_to_bool(
      self.config_data.get(
        'DEBUG_R1FS', 
        os.environ.get('EE_DEBUG_R1FS', False)
      )
    )
    return debug_r1fs
  
  
  @property
  def e2_addr(self):
    return self.e2_address
  
  
  @property
  def is_supervisor_node(self):
    return self.__is_supervisor_node
  
  @property
  def is_secured(self):
    return self.config_data.get(ct.CONFIG_STARTUP_v2.SECURED, False) 
  
  @property
  def check_ram_on_shutdown(self):
    return self.config_data.get(ct.CONFIG_STARTUP_v2.CHECK_RAM_ON_SHUTDOWN, False)

  
  @property
  def cfg_blockchain_config(self):
    return self.config_data.get('BLOCKCHAIN_CONFIG', {})
  
  @property
  def main_loop_stop_log(self):
    return self.__main_loop_stoplog[-10:]
  
  @property
  def running_time(self):
    return self.log.get_time_until_now(ct.TIMER_APP)
  
  @property
  def cfg_hb_contains_pipelines(self):
    # HB_CONTAINS_PIPELINES is now obsolete
    val = os.environ.get(ct.HB_CONTAINS_PIPELINES_ENV_KEY, True)
    return str(val).lower() in ['true', 'yes', '1']

  @property
  def cfg_hb_contains_active_plugins(self):
    # HB_CONTAINS_ACTIVE_PLUGINS is now obsolete
    val = os.environ.get(ct.HB_CONTAINS_ACTIVE_PLUGINS_ENV_KEY, True)
    return str(val).lower() in ['true', 'yes', '1']
    
  
  @property
  def cfg_critical_restart_low_mem(self):
    return self.config_data.get('CRITICAL_RESTART_LOW_MEM', 0.20)


  @property
  def cfg_min_avail_mem_thr(self):
    return self.config_data.get('MIN_AVAIL_MEM_THR', 0.09)
  
  @property
  def cfg_min_avail_disk_size(self):
    return self.config_data.get('MIN_AVAIL_DISK_SIZE_GB', 10)
  
  @property
  def cfg_min_avail_disk_thr(self):
    return self.cfg_min_avail_disk_size

  
  @property
  def cfg_extended_timers_dump(self):
    return self.config_data.get('EXTENDED_TIMERS_DUMP', True)
  
  @property
  def cfg_reset_admin_pipeline(self):
    return self.config_data.get('RESET_ADMIN_PIPELINE', True)
  
  
  @property
  def cfg_timers_dump_interval(self):
    return self.config_data.get('TIMERS_DUMP_INTERVAL', 650)

  @property
  def cfg_eeid(self):
    _id = self.config_data.get(ct.CONFIG_STARTUP_v2.K_EE_ID, '')[:ct.EE_ALIAS_MAX_SIZE]
    return _id
  
  @property
  def cfg_compress_heartbeat(self):
    return self.config_data.get('COMPRESS_HEARTBEAT', True)

  @property
  def cfg_config_retrieve(self):
    return self.config_data.get(ct.CONFIG_STARTUP_v2.K_CONFIG_RETRIEVE, [])

  @property
  def cfg_io_formatter(self):
    return self.config_data.get('IO_FORMATTER', '')

  @property
  def cfg_heartbeat_timers(self):
    return self.config_data.get('HEARTBEAT_TIMERS', False)

  @property
  def cfg_heartbeat_log(self):
    return self.config_data.get('HEARTBEAT_LOG', False)
  
  @property
  def cfg_app_seconds_heartbeat(self):
    return min(ct.MAX_SECONDS_HEARTBEAT, self.config_data.get(ct.SECONDS_HEARTBEAT, ct.DEFAULT_SECONDS_HEARTBEAT))

  @property
  def cfg_shutdown_no_streams(self):
    return self.config_data.get('SHUTDOWN_NO_STREAMS', False) 

  @property
  def cfg_main_loop_resolution(self):
    return self.config_data.get('MAIN_LOOP_RESOLUTION', 20)

  @property
  def cfg_sequential_streams(self):
    """
    if the "SEQUENTIAL_STREAMS" flag is activated, then DecentrAI will consume a stream at a time one by one;
    useful in testing mode when a chunk of streams are provided and one stream should be tested at a time.
    each stream must be finite and not reconnectable in order to let the following streams to be executed.
    """
    return self.config_data.get('SEQUENTIAL_STREAMS', False)

  @property
  def cfg_collect_telemetry(self):
    return self.config_data.get('COLLECT_TELEMETRY', False)

  @property
  def cfg_server_collector_timedelta(self):
    return self.config_data.get('SERVER_COLLECTOR_TIMEDELTA', None)

  @property
  def cfg_serving_environment(self):
    return self.config_data.get('SERVING_ENVIRONMENT', {})

  @property
  def cfg_capture_environment(self):
    return self.config_data.get('CAPTURE_ENVIRONMENT', {})

  @property
  def cfg_plugins_environment(self):
    return self.config_data.get('PLUGINS_ENVIRONMENT', {})

  @property
  def cfg_communication_environment(self):
    return self.config_data.get('COMMUNICATION_ENVIRONMENT', {})

  @property
  def cfg_plugins_on_threads(self):
    return self.config_data.get('PLUGINS_ON_THREADS', True)

  @property
  def cfg_default_email_config(self):
    return self.config_data.get('DEFAULT_EMAIL_CONFIG', None)

  @property
  def cfg_system_monitor(self):
    return self.config_data.get('SYSTEM_MONITOR', True)

  @property
  def avg_loop_timings(self):
    if len(self.loop_timings) > 0:
      return np.mean(self.loop_timings)
    return 0
  
  @property
  def real_main_loop_resolution(self):
    if len(self.loop_timings) > 0:
      return round(1 / np.mean(self.loop_timings), 1)
    return 0
  
  @property
  def in_process_serving(self):
    # default is in parallel
    # in-process only for debug purposes
    return self.cfg_serving_environment.get('SERVING_IN_PROCESS', False)
  
  @property
  def any_overloaded_business_plugins(self):
    return self._business_manager.any_overloaded_plugins
  
  @property
  def in_mlstop(self):
    return self.__main_loop_stopped
  
  
  def get_pipelines_view(self):
    return list(self._current_dct_config_streams.values())
  
  def get_node_running_time(self):
    return self.running_time

  def get_overloaded_business_plugins(self):
    return self._business_manager.get_overloaded_plugins()
    
  
  def check_shutdown_on_missing_streams(self):
    return self.cfg_shutdown_no_streams and not bool(self._config_manager.dct_config_streams)
  

  def _stop(self):    
    self.__done = True
    _thread_async_comm = vars(self).get('_thread_async_comm')
    if _thread_async_comm is not None and _thread_async_comm.is_alive():
      _thread_async_comm.join()
      self.P("Asynchronous communication thread joined.", color='y')
    return

  def _maybe_gracefull_stop(self):
    if self._in_shutdown:
      return
    self._in_shutdown = True
    self.P('Shutting down. Sending shutdown status heartbeat...', color='y')
    self._return_code = ct.CODE_SHUTDOWN
    self._maybe_send_heartbeat(
      status=ct.DEVICE_STATUS_SHUTDOWN,
      full_info=True, send_log=True, force=True,
    )
    # TODO: implement queue empty wait instead of sleep
    sleep(5)
    self._stop()
    return

  def _maybe_exception_stop(self):
    if self._in_shutdown:
      return
    self._in_shutdown = True
    self.P('Exception shutdown. Trying to send exception status heartbeat...', color='y')
    # send shutdown heartbeat message
    self._maybe_send_heartbeat(
      status=ct.DEVICE_STATUS_EXCEPTION,
      full_info=True, send_log=True, force=True,
    )
    # TODO: implement queue empty wait instead of sleep
    sleep(5)
    self._stop()
    return
  
  
  def save_config_pipeline_instance(self, pipeline_name, signature, instance_id, config_data, **kwargs):
    if self._config_manager is not None:
      self._config_manager.save_instance_modifications(
        pipeline_name=pipeline_name,
        signature=signature,
        instance_id=instance_id,
        config=config_data,
        **kwargs,
      )
    return
  
  
  def save_config_pipeline(self, pipeline_name, config_data, **kwargs):
    if self._config_manager is not None:
      self._config_manager.save_pipeline_modifications(
        pipeline_name=pipeline_name,
        pipeline_config=config_data,
        **kwargs,
      )
    return


  def _get_mean_loop_freq(self):
    main_loop_time = self.log.get_timer_mean(self._main_loop_timer_name)
    ml_time = 1e-3 if main_loop_time == 0 else main_loop_time
    result = min(self.cfg_main_loop_resolution, 1 / ml_time)
    return result

  def _get_last_timing(self):
    if len(self.loop_timings) > 0:
      return self.loop_timings[-1]
    return -1

  def _maybe_send_node_status(self):
    node_main_loop_time = self.log.get_timer_mean(self._main_loop_timer_name)
    uptime = self.running_time
    # wait 5 minutes from the start of the box in order to send abnormal functioning
    max_loop_time_threshold = 1.85
    warmup_time = 5 * 60
    warning_resend = 60 * 60 * 1 
    can_send = self._last_node_status_warning is None or (time() - self._last_node_status_warning) > warning_resend
    if node_main_loop_time > max_loop_time_threshold and uptime > warmup_time and can_send:
      str_msg = "WARNING: main loop time above {:.2f}s in EE '{}': loop time is {:.2f}".format(
        max_loop_time_threshold, self.cfg_eeid, node_main_loop_time,
      )
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING,
        msg=str_msg,
      )
      self._last_node_status_warning = time()
    #endif
    return
  
  def _maybe_send_inprocess_status(self):
    delay_periodic = 3600 # send every 1 hour
    if self.in_process_serving:
      last_timer = vars(self).get('_warning_inprocess_timer')      
      should_send = last_timer is None or (time() - last_timer) > delay_periodic
      if should_send:
        msg = "WARNING: '{}' uses in-process serving. This behaviour is debug-only and should not be enabled!".format(
          self.cfg_eeid,
        )
        self.P('*' * 80, color='r')
        self.P(msg, color='r')
        self.P('*' * 80, color='r')
        self._create_notification(
          notif=ct.NOTIFICATION_TYPE.STATUS_NORMAL, 
          msg=msg,
          displayed=True,
        )
        self._warning_inprocess_timer = time()
    #endif warning serving-in-process
    return
  
  def _maybe_send_periodic_notifications(self):
    """
    This method prepares various periodic notifications such as debug/config warnings
    and other similar notifications
    """
    # Perioding notifications 
    self._maybe_send_node_status()
    self._maybe_send_inprocess_status()
    #
    return
  
  
  def _check_for_other_issues(self):
    if self._serving_manager is not None:
      self._serving_manager.check_blocked_inprocess_servers()
    if self._business_manager is not None:
      self._business_manager.maybe_stop_finished_plugins()    
      self._payloads_count_queue.append(self._business_manager.get_total_payload_count())
    return
  
  
  def __log_main_loop_stop(self, end=None):
    cnt = self.__main_loop_stopped_count
    start = self.__main_loop_stopped_time      
    elapsed = (end if end is not None else time()) - start
    dct_info = {
      'NR'          : cnt,
      'FROM_START'  : self.log.elapsed_to_str(self.running_time, show_days=False),
      'WHEN'        : self.log.time_to_str(start),
      'STOP_STAGE'  : self.__main_loop_stop_at_stage,
      'CURR_STAGE'  : self.__loop_stage,
      'LAST_CHANGE' : self.__main_loop_last_stage_change,
      'RESUME'      : None if end is None else self.log.time_to_str(end),
      'DURATION'    : self.log.elapsed_to_str(elapsed, show_days=False),
      'ITER'        : self._main_loop_counts['ITER'],
    }
    if end is None:
      already_added = False
      for _log in self.__main_loop_stoplog:
        if _log['WHEN'] == dct_info['WHEN']:
          already_added = True
      if not already_added:
        self.__main_loop_stoplog.append(dct_info)
    else:
      if self.__main_loop_stoplog[-1]['WHEN'] == dct_info['WHEN']:
        self.__main_loop_stoplog[-1] = dct_info
    return
    
  def __get_system_version(self):
    well_defined_versions = [v for v in [__APP_VER__, __CORE_VER__, self.log.version] if v is not None]
    versions = '/'.join(['{}' for _ in well_defined_versions])
    versions = versions.format(*well_defined_versions)
    return "v{}".format(versions)


  def _maybe_send_heartbeat(self, status=None, full_info=True, send_log=False, force=False, initiator_id=None, session_id=None, **kwargs):
    
    if self._comm_manager is None:
      self.P("Cannont send heartbeat due to incomplete startup!")
      return
    
    if self._should_send_initial_log and not self._initial_log_sent:
      send_log = True
      force = True
      full_info = True
      
    elapsed_hb = time() - self._last_heartbeat
    if (elapsed_hb > self.cfg_app_seconds_heartbeat) or force:
      self._last_heartbeat = time()
      
      # START: internal node status & other functionality checks
      self._maybe_send_periodic_notifications()
      self._check_for_other_issues() # maybe inprocess issues or other stuff
      # now prepare status info
      hb_payload = self._app_monitor.get_status(
        status=status, full=full_info, send_log=send_log,
      )
      whitelist = hb_payload.get(ct.HB.EE_WHITELIST, [])
      nr_allowed = len(whitelist)
      # END: internal checks
      
      ## debug
      if False:
        if send_log:
          devicelog = hb_payload.get(ct.HB.DEVICE_LOG, [])
          msg = "MIME-Version: 1.0\nContent-Type: text/html\n\n<pre>"+"\n".join(
            devicelog
            ) + "\n\n</pre>"        
          self.P("Creating payload...", color='error')
          self._create_mail_payload(
            subject="'{}' full log".format(self.cfg_eeid), message=msg,
          )
      ## end debug
      
      hb_payload[ct.HB.INITIATOR_ID] = initiator_id
      n_inferences = hb_payload[ct.HB.NR_INFERENCES]
      n_comms = hb_payload[ct.HB.NR_PAYLOADS]
      n_upstream = hb_payload[ct.HB.NR_STREAMS_DATA]
      main_loop_iter = self._main_loop_counts['ITER']
      
      # TODO: add in payload upload-URL & upload date-time ???
      
      if not self.in_mlstop or (self.in_mlstop and (not self.__is_mlstop_dangerous)):
        # send HB only if not stopped or if stopped but NOT dangerous
        self._comm_manager.send(data=hb_payload, event_type=ct.HEARTBEAT)
        hb_sent = True
      else:
        hb_sent = False
      
      # TODO: remove from here and move to comm where such messages should be
      #       monitored in terms of time
      
      self._heartbeat_counter += 1
      if force:
        if initiator_id is not None:
          str_forced = "REQUESTED by '{}' ".format(initiator_id)
        else:
          str_forced = 'FORCED '
      else:
        str_forced = ''

      # START test if same main loop iter
      if main_loop_iter == self._last_checked_main_loop_iter:
        if self.__main_loop_stopped_count == 0:
          self.__main_loop_stopped_count = 1
        if not self.__main_loop_stopped:
          self.__main_loop_stopped_time = time()
          self.__main_loop_stop_at_stage = self.__loop_stage
          self.__log_main_loop_stop()
        self.__main_loop_stopped = True
        stage_last_change = time() - self.__main_loop_last_stage_change
        timer, elapsed = self.log.get_opened_timer()
        msg = "{} MLSTOP @ {} from start, stage stop '{}', now '{}', {} stops so far, {:.0f}s since stop, {:.0f}s since last, timer `{}={:.1f}s`,(E2 loop avg/last time {:.1f}s/{:.1f}s)".format(
          "WARNING:" if self.__is_mlstop_dangerous else "Normal (start/stop)", 
          self.log.elapsed_to_str(self.running_time), 
          self.__main_loop_stop_at_stage, self.__loop_stage,
          self.__main_loop_stopped_count, time() - self.__main_loop_stopped_time, 
          stage_last_change, timer, elapsed, self.avg_loop_timings, self._get_last_timing()
        )
        self.P(msg, color='error' if self.__is_mlstop_dangerous else 'r')
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING, msg=msg,
          is_alert=self.__is_mlstop_dangerous,
          displayed=True
        )
      elif self.__main_loop_stopped:
        self.__log_main_loop_stop(end=time())
        self.__main_loop_stopped = False
        msg = "{} main loop resumed after {:.0f}s from stage '{}', {} stop-resumes so far! (E2 loop avg/last {:.1f}s/{:.1f}s)".format(
          "WARNING:" if self.__is_mlstop_dangerous else "Normal (start/stop)", 
          time() - self.__main_loop_stopped_time, self.__main_loop_stop_at_stage,
          self.__main_loop_stopped_count, self.avg_loop_timings, self._get_last_timing()
        )
        self.__main_loop_stop_at_stage = None
        self.__main_loop_stopped_count += 1
        self.P(msg, color='error')
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING, msg=msg,
          displayed=True
        )
      self._last_checked_main_loop_iter = main_loop_iter
      # END same loop iter


      if ((self._heartbeat_counter % 2) == 0 or self._heartbeat_counter == 1 or force):
        if hb_sent:
          # show log
          extra = "Total {}/{}/{} data/inf/send".format(
            n_upstream, n_inferences, n_comms,
          )
          hb_msg = "{} hb:{} {}{}{}itr {} ({} void), Hz: {}/{}, {:.1f} hrs, New.pl.: {}, {}, wl:{}".format(
            "'{}' {}".format(self.cfg_eeid, self.__get_system_version()),
            self._heartbeat_counter,
            "with full log " if send_log else "", "status:'{}' ".format(status) if status is not None else '',
            str_forced, main_loop_iter, self._main_loop_counts['VOID_ITER'], self.real_main_loop_resolution, self.cfg_main_loop_resolution,
            self.running_time / 3600, self._payloads_count_queue[-1] - self._payloads_count_queue[-2],
            # self._app_monitor.get_basic_perf_info(),
            self._app_monitor.get_summary_perf_info(),
            nr_allowed,
          )
          if False:
            hb_msg = hb_msg + ', ' + extra
          self.P(hb_msg, color='g')        
        else:
          self.P("Heartbeat {} NOT sent!".format(self._heartbeat_counter), color='r')
        #endif hb was indeed sent or not
      #endif need to display info 
      
      if (not self._app_monitor.alert and (self._heartbeat_counter % 20) == 0) or not self._initial_log_sent:
        self._app_monitor.log_status()
      
      self._initial_log_sent = True
    #endif need to send heartbeat
    return
    
  
  def _maybe_send_startup_log(self):
    self._should_send_initial_log = True
    return


  def _maybe_delay_main_loop(self):
    self.log.start_timer('delay')
    wait_time = 1 / self.cfg_main_loop_resolution # max wait time
    current_cycle_time = perf_counter() - self._last_main_loop_pass_time # cycle time since last pass
    avg_comm_loop_timings = self._comm_manager.avg_comm_loop_timings
    if avg_comm_loop_timings is not None and avg_comm_loop_timings > wait_time:
      # increase wait time if need be
      wait_time = avg_comm_loop_timings 

    sleep(0.001) # no matter what go ahead and yield
    while current_cycle_time < wait_time:
      sleep(0.001)
      current_cycle_time = perf_counter() - self._last_main_loop_pass_time # recalc cycle time 

    self.loop_timings.append(current_cycle_time) # done after time extension if needed
    self._last_main_loop_pass_time = perf_counter() # reset
    self.log.end_timer('delay')
    return

  def _get_capture_manager_notifications(self):
    return self._capture_manager.get_manager_notifications() + self._capture_manager.get_all_subalterns_notifications()

  def _get_business_manager_notifications(self):
    return self._business_manager.get_manager_notifications() + self._business_manager.get_all_subalterns_notifications()

  def _get_comm_manager_notifications(self):
    return self._comm_manager.get_manager_notifications() + self._comm_manager.get_all_subalterns_notifications()

  def _get_config_manager_notifications(self):
    return self._config_manager.get_manager_notifications() + self._config_manager.get_all_subalterns_notifications()

  def _get_serving_manager_notifications(self):
    return self._serving_manager.get_manager_notifications() + self._serving_manager.get_all_subalterns_notifications()

  def _get_io_formatter_manager_notifications(self):
    return self._io_formatter_manager.get_manager_notifications() + self._io_formatter_manager.get_all_subalterns_notifications()

  def _get_heavy_ops_manager_notifications(self):
    return self._heavy_ops_manager.get_manager_notifications() + self._heavy_ops_manager.get_all_subalterns_notifications()

  def _get_all_notifications(self):
    all_notifications = self.log.flatten_2d_list([
      self.get_notifications(),
      self._get_capture_manager_notifications(),
      self._get_business_manager_notifications(),
      self._get_comm_manager_notifications(),
      self._get_config_manager_notifications(),
      self._get_serving_manager_notifications(),
      self._get_io_formatter_manager_notifications(),
      self._get_heavy_ops_manager_notifications(),
      
      self._app_monitor.get_notifications(), # finally get notifications from app monitor
    ])
    return all_notifications

  def _handle_all_notifications(self, filter_emails=False):
    all_notifications = self._get_all_notifications()
    title_printed = False

    for notif in all_notifications:

      module = notif[ct.MODULE]
      module += ' (EE core v{})'.format(__CORE_VER__)
      # we add ver in notif info
      notif['INFO'] = 'EE app v{}, core v{}, SDK v{}, info text: '.format(__APP_VER__, __CORE_VER__, self.log.version) + str(notif['INFO'])
      notif_msg = 'EE core v{}: '.format(__CORE_VER__) + str(notif['NOTIFICATION'])
      
      notif_type = notif['NOTIFICATION_TYPE']
      notif_code = notif['NOTIFICATION_CODE']
      notif_tag = notif['NOTIFICATION_TAG']
      notif_info = notif['INFO']
      notif_stream_name = notif['STREAM_NAME']
      # TODO: change from `displayed` to `to_print` (1-displayed)
      notif_displayed = notif.get('DISPLAYED', False) or notif.get('PRINTED', False)
      color = 'm'
      is_email = False
      email_config = None

      if notif_type in [ct.STATUS_TYPE.STATUS_EXCEPTION, ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING]:
        color = 'r'
        email_config = self.cfg_default_email_config
        self._create_mail_payload(
          subject="EE:'{}' exc. in `{}`: {}".format(self.cfg_eeid, module, notif_msg),
          message=notif_info,
          config=email_config,
        )
        is_email = True
      #endif

      if notif_type == ct.STATUS_TYPE.STATUS_EMAIL:
        subject = notif.get('_H_EMAIL_SUBJECT', None) or notif.get('EMAIL_SUBJECT', None) or "DecentrAI Notification in Module '{}'".format(module)
        message = notif.get('_H_EMAIL_MESSAGE', None) or notif.get('EMAIL_MESSAGE', None) or notif_msg
        email_config = notif.get('_H_EMAIL_CONFIG', None) or notif.get('EMAIL_CONFIG', None) or self.cfg_default_email_config
        self._create_mail_payload(
          subject=subject,
          message=message,
          config=email_config
        )
        is_email = True
      #endif

      str_notif = '[{}] TYPE:{} | CODE:{} | TAG: {} | EE: {} | STREAM: {} | EMAIL: {} | MSG: "{}" | INFO: {}'.format(
        module, notif_type, notif_code, notif_tag,
        self.cfg_eeid, notif_stream_name, 
        "Yes ({})".format("N/A" if email_config is None else "OK") if is_email else "No",
        notif_msg, notif_info,
      )
      if not notif_displayed:
        if not title_printed:
          self.P("===== Notifications =====", color='m')
          title_printed = True
        self.P("  NOTIF: {}".format(str_notif), color=color)
      #end not printed during collection
    #endfor each notification
    
    if filter_emails:
      server_notifications = list(filter(lambda x: x['NOTIFICATION_TYPE'] != ct.STATUS_TYPE.STATUS_EMAIL, all_notifications))
    else:
      server_notifications = list(all_notifications)      
    return server_notifications

  def _create_mail_payload(self, subject, message, config=None):
    if config is None:
      config = self.cfg_default_email_config
    self._non_business_payloads.append({
      '_H_SEND_EMAIL' : True,
      '_H_EMAIL_SUBJECT' : subject,
      '_H_EMAIL_MESSAGE' : message,
      '_H_EMAIL_CONFIG' : config,
    })
    return

  def close_main_loop(self):
    self.P('Closing connections and stopping threads...', color='m')
    self._maybe_gracefull_stop()
    if self._comm_manager is not None:
      self._comm_manager.close()
    if self._capture_manager is not None:
      self._capture_manager.close()
    if self._business_manager is not None:
      self._business_manager.close()
    if self._serving_manager is not None:
      self._serving_manager.stop_all_servers()
    return

  def refresh_business_plugins(self):
    """
    Main loop step:
      Calls the BusinessManager to start new plugins and kill the unused ones
    """
    self.__loop_stage = '2.bm.refresh.call_bm.update_streams'
    in_use_ai_engines = self._business_manager.update_streams(self._current_dct_config_streams)
    return in_use_ai_engines

  def choose_current_running_streams(self):
    """
    This method takes the all the pipelines configs from ConfigManager to copies
    them to the Orchestrator active streams

    Returns
    -------
    None.

    """
    with self.log.managed_lock_resource(ct.LOCK_CMD):

      try:
        if self.cfg_sequential_streams:
          streams = list(self._config_manager.dct_config_streams.keys())
          if len(streams) > 0:
            s = streams[0]
            self._current_dct_config_streams = {s : deepcopy(self._config_manager.dct_config_streams[s])}
          else:
            self._current_dct_config_streams = {}
          #endif
        else:
          if self._current_dct_config_streams != self._config_manager.dct_config_streams:
            self._current_dct_config_streams = deepcopy(self._config_manager.dct_config_streams)
        #endif
      except:
        msg = "CRITICAL error in `choose_current_running_streams`"      
        self.P(msg, color='r')
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_EXCEPTION, 
          msg=msg, autocomplete_info=True,
          displayed=True,
        )
      #end try-except
    # endwith lock
    return

  def collect_data(self):
    """
    Main loop step:
      Collects data from all streams
    """
    # update the streams to know which captures are done
    self.__loop_stage = '4.collect.update_streams'
    self._capture_manager.update_streams(self._current_dct_config_streams)
    
    self.__loop_stage = '4.collect.get_all_cap'
    dct_captures = self._capture_manager.get_all_captured_data()

    self.__loop_stage = '4.collect.add_data_info'
    self._app_monitor.add_data_info(val=len(dct_captures), stage=ct.NR_STREAMS_DATA)

    # after capturing, archive the streams that are completely finished (excluded those)    
    # that are keep-alive
    self.__loop_stage = '4.collect.get_finished_streams'
    finished_stream_names = self._capture_manager.get_finished_streams()
    
    self.__loop_stage = '4.collect.archive_streams'
    self._config_manager.archive_streams(finished_stream_names, initiator_id="SELF", session_id="MAIN_LOOP")
    
    if self.config_data.get('SEQUENTIAL_STREAMS', False) and len(finished_stream_names) > 0:
      self._reset_timers = True

    if len(dct_captures) == 0:
      self._main_loop_counts['VOID_ITER'] += 1

    return dct_captures

  def update_main_loop_data_handler(self, dct_captures):
    """
    Main loop step:
      Update the main loop data handler
    """
    self._data_handler.update(
      dct_captures=dct_captures,
      dct_instances_details=self._business_manager.dct_instances_details,
      dct_serving_processes_details=self._business_manager.dct_serving_processes_details,
    )
    return

  def append_captures_for_business_plugins(self):
    """
    Main loop step:
      Append captures to the corresponding plugins
    """
    self._data_handler.append_captures()
    return

  def aggregate_collected_data_for_serving_manager(self):
    """
    Main loop step:
      Aggregates collected data to be passed to the ServingManager
    """
    dct_servers_inputs = self._data_handler.aggregate_for_inference()
    return dct_servers_inputs

  def maybe_start_serving_processes(self, warmup=False, in_use_ai_engines=None):
    """
    Main loop step:
      Starts employed serving processes
    Args:
      warmup: bool - if True, the serving processes from auto_warmups are started
      in_use_ai_engines: set - set of AI engines that are currently in use
        - if None, nothing happens
        - if not None, some not in use AI engines are stopped

    """

    dct_serving_processes_startup_params = {}

    if warmup:
      auto_warmups = self.cfg_serving_environment.get('AUTO_WARMUPS', [])
      if isinstance(auto_warmups, list):
        dct_ai_engines = {x : {} for x in auto_warmups}
      else:
        dct_ai_engines = {k : v for k,v in auto_warmups.items()}
      if len(dct_ai_engines) > 0:
        self.P("Performing auto-warmup of AI Engines: {}".format(dct_ai_engines))
    else:
      dct_ai_engines = self._business_manager.dct_serving_processes_startup_params
    #endif

    for name, upstream_params in dct_ai_engines.items():
      # now we should prepare serving process config using biz plugin STARTUP_AI_ENGINE_PARAMS
      dct_upstream_config = {**upstream_params}
      # TODO:
      #   all ai_engine relatd stuff should be moved to Serving Manager
      server_name = get_serving_process_given_ai_engine(ai_engine=name)
      
      if False:
        # Ai Engine config should not be used here as this will invalidate the configuration
        # chain that is supposed to be default_serving_config -> ai_engine -> serving_env -> biz_plugin
        # **** this was left intentionaly for documentation purposes ****
        ai_engine_params = get_params_given_ai_engine(ai_engine=name)
        dct_upstream_config = {**ai_engine_params, **dct_upstream_config}
      # end ai-engine config params
      
      dct_serving_processes_startup_params[server_name] = dct_upstream_config
    #endfor
    servers = list(dct_serving_processes_startup_params.keys())
    for server_name in servers:
      upstream_params = dct_serving_processes_startup_params[server_name]
      self.__loop_stage = '3.serving.start.' + str(server_name)
      self._serving_manager.maybe_start_server(
        server_name,
        upstream_config=upstream_params,
        inprocess=self.in_process_serving,
      )
    #endfor
    if in_use_ai_engines is not None:
      server_names = self.serving_manager.get_active_server_names()
      in_use_ai_engines = [
        self.serving_manager.get_server_name(ai_engine_name) for ai_engine_name in in_use_ai_engines
      ]
      for server_name in server_names:
        if server_name not in in_use_ai_engines:
          self.__loop_stage = '3.serving.stop.' + str(server_name)
          self._serving_manager.maybe_stop_unused_server(server_name)
        # endif server not in use
      # endfor servers
    # endif in_use_ai_engines provided
    return

  def run_serving_manager(self, dct_servers_inputs):
    """
    Main loop step:
      Inference step - all needed serving processes are run
    """
    # the following line has been disabled - the running on empty inputs is the serving manager
    # decision - please do not remove below line for documentation purposes
    # dct_servers_inputs_filtered = {k:v for k,v in dct_servers_inputs.items() if len(v) > 0} 
    dct_servers_inputs_filtered = dct_servers_inputs

    dct_servers_outputs = self._serving_manager.predict_parallel(
      dct_servers_inputs_filtered,
      inprocess=self.in_process_serving, # default is paralel!
    )
    self.set_loop_stage('6.1.serve.run.add_data_info')
    self._app_monitor.add_data_info(val=len(dct_servers_outputs), stage=ct.NR_INFERENCES)
    return dct_servers_outputs

  def append_servers_outputs_for_business_plugins(self, dct_servers_outputs):
    """
    Main loop step:
      Append inferences (servers outputs) to the corresponding plugins
    """
    self._data_handler.append_inferences(dct_servers_outputs)
    dct_business_inputs = self._data_handler.dct_business_inputs
    return dct_business_inputs

  def run_business_manager(self, dct_business_inputs):
    """
    Main loop step:
      Plugins are executed
    """
    self._business_manager.execute_all_plugins(dct_business_inputs)
    return
  

  def communicate_recv_and_handle(self):
    """
    Main loop step:
      Receives commands from the server and executes them by using the 
      ExecutionEngineCommandHandlers "mixin" class where all the handlers are defined
      using the simple DecentrAIObject mechanism
    """
    received_commands = self._comm_manager.maybe_process_incoming()
    return_code, status = None, None
    for command_type, (command_content, sender_addr, initiator_id, session_id) in received_commands:
      with self.log.managed_lock_resource(ct.LOCK_CMD):
        try:
          if isinstance(command_content, dict):
            sender_addr = command_content.get(ct.COMMS.COMM_RECV_MESSAGE.K_SENDER_ADDR, None)
            initiator_id = command_content.get(ct.PAYLOAD_DATA.INITIATOR_ID)
            session_id = command_content.get(ct.PAYLOAD_DATA.SESSION_ID)
          #endif
          self.P("'{}' cmd from <{}:{}>".format(        
            command_type, initiator_id, sender_addr), color='y', boxed=True,
          )
          if True:
            self.P("  Command content: {}".format(str(command_content)[:250]), color='y')
          res = self.run_cmd(
            # command string
            cmd=command_type, 
            # **kwargs
            command_content=command_content, 
            initiator_id=initiator_id,
            session_id=session_id,
          )
          if res is not None:
            self.P("  Command '{}' returned: {}".format(command_type, res), color='y')
            return_code, status = res
        except:
          msg = "CRITICAL error in `communicate_recv_and_handle` for command: '{}' originating from {}:{}\nContent: {}".format(
            command_type, initiator_id, session_id, command_content)
          self.P(msg, color='r')
          self._create_notification(
            notif=ct.STATUS_TYPE.STATUS_EXCEPTION, msg=msg, autocomplete_info=True,
            initiator_id=initiator_id, session_id=session_id,
          )
        #end try-except
      # endwith lock
    #endfor

    return return_code, status

  def communicate_send(self, payloads, commands, status=None):
    """
    Main loop step:
      Sends all the messages and payloads collected in this main loop iteration;
    """    
    for cmd in commands:
      self._comm_manager.send(data=cmd, event_type='COMMAND')

    nr_payloads = 0
    for payload in payloads:
      # cleans the payloads in current thread and executes parallel heavy ops that do not modify the payload inplace
      self._heavy_ops_manager.run_all_comm_async(payload)
      if len(payload) > 0:
        nr_payloads += 1
        self._comm_manager.send(data=payload, event_type='PAYLOAD')

    self._app_monitor.add_data_info(val=nr_payloads, stage=ct.NR_PAYLOADS)

    for notif in self._handle_all_notifications():
      self._comm_manager.send(data=notif, event_type='NOTIFICATION')


    self._maybe_send_heartbeat(
      status=status,
      full_info=self.cfg_heartbeat_timers,
      send_log=self.cfg_heartbeat_log,
      )

    return
  

    

  def maybe_show_timers(self):
    threshold_no_show = 0.0001
    
    self.__loop_stage = "9.1.1"
    
    if False and not self._recorded_first_object_tree: # DISABLED for the moment
      sleep(5)
      self._recorded_first_object_tree = True
      self._save_object_tree(
        fn='obj_tree_startup.txt',
        save_top_only=True,
        top=100,
      )
        
    self.__loop_stage = "9.1.2"
        
    if (time() - self._last_timers_dump) > self.cfg_timers_dump_interval:
      self.P("Timer dump interval of {}s reached.".format(self.cfg_timers_dump_interval))
      self._last_timers_dump = time()
      self.log.show_timers(
        threshold_no_show=threshold_no_show, 
        color=ct.COLORS.TIMERS, 
        selected_sections=['main'],
        indent=4,
        obsolete_section_time=15 * 60 # 15 min obsolete sections
      )
      
      if self.cfg_extended_timers_dump:        
        self.__loop_stage = "9.1.3"
        self._app_monitor.log_sys_mon_info(color=ct.COLORS.STATUS)        
        self.__loop_stage = "9.1.4"
        self._serving_manager.get_active_servers(show=True, color='d')
  
      self.__loop_stage = "9.1.5"
      if False:
        # just for debug purposes -> set to True in order to save timers (together with the timers graph)
        # then investigate them a separate process by instantiating a Logger object and then setting
        # log.timers = data['timers']; log.timer_level = data['timer_level'] etc
        self.log.save_pickle_to_output(
          data={
            'timers' : self.log.timers,
            'timer_level' : self.log.timer_level,
            'opened_timers' : self.log.opened_timers,
            'timers_graph' : self.log.timers_graph,
            '_timer_error' : self.log._timer_error
          },
          fn='{}_timers.pickle'.format(self.log.file_prefix)
        )
      #endif
    #endif
    self.__loop_stage = "9.1.5.1"
    
    if self._reset_timers:
      self.log.show_timers(threshold_no_show=threshold_no_show, obsolete_section_time=3*60) 
      self.log.reset_timers()
      self.log.start_timer(ct.TIMER_APP)
      self._reset_timers = False
    #endif
    
    self.__loop_stage = "9.1.6"
    return

  def comm_manager_show_info(self):
    self._comm_manager.maybe_show_info()    
    return

  def _save_exception_main_loop_state(self, txt, **save_kwargs):
    fn = '{}_main_loop_exception'.format(self.log.now_str())
    self.log.save_pickle_to_output(data=save_kwargs, fn=fn + '.pickle', subfolder_path='main_loop_exceptions')
    with open(os.path.join(self.log.get_output_folder(), fn + '.txt'), 'w') as fhandle:
      fhandle.write(txt)
    return

  def asynchronous_communication(self):
    while not self.__done:
      start_it = time()
      self.log.start_timer('main_loop', section='asynchronous_communication')
      try:
        lst_payloads, lst_commands = [], []
        
        # Commands section 
        nr_commands = 0
        for instance_hash, deq in self._business_manager.comm_shared_memory['commands'].items():
          while len(deq) > 0:
            lst_commands += deq.popleft()
            nr_commands += 1
        #endfor
        
        self.communicate_send(
          payloads=[],
          commands=lst_commands,
          status=None,
        ) 
                
        if nr_commands > 0:
          sleep(0.02)  
     
        return_code, status = self.communicate_recv_and_handle()
              
        # Payload section
              
        while len(self._non_business_payloads) > 0:
          lst_payloads.append(self._non_business_payloads.popleft())
              
        for instance_hash, deq in self._business_manager.comm_shared_memory['payloads'].items():
          while len(deq) > 0:
            lst_payloads.append(deq.popleft())
        #endfor
        
        self.communicate_send(
          payloads=lst_payloads,
          commands=[],
          status=status
        ) 

        if return_code is not None:
          self._return_code = return_code
          self.P("Setting and executing return code {}".format(self._return_code), color='r')
      except Exception as e:
        self.P("CRITICAL ERROR {} in asynchoronous communication:\n{}\n{}".format(
          e, traceback.format_exc(), self.log.get_error_info(return_err_val=True))
        )
      self.log.end_timer('main_loop', section='asynchronous_communication')
      it_time = time() - start_it
      self._last_async_comm_sleep = max(1 / (2*self.cfg_main_loop_resolution) - it_time, 0.00001)
      sleep(self._last_async_comm_sleep)
    #endwhile
    return


  def _init_main_loop(self):
    self.P('Starting device main loop...', color='g')
    self._maybe_send_heartbeat(
      status=None,
      full_info=True,
      send_log=False,
    )
    self.maybe_start_serving_processes(warmup=True)
    return


  @property
  def _main_loop_timer_name(self):
    return ct.TIMER_MAIN_LOOP + '_{}'.format(self.cfg_main_loop_resolution)


  def forced_shutdown(self, code=ct.CODE_EXCEPTION):
    if self._app_monitor is not None: 
      self._app_monitor.shutdown()

    self.__done = True
    self._maybe_exception_stop()
    self.close_main_loop()
    return code

  def maybe_simulate_mlstop(self):
    main_loop_iters = self._main_loop_counts['ITER']
    if main_loop_iters < self.debug_simulated_mlstop_start:
      return
    if self.debug_simulated_mlstop > 0 and self.__simulated_mlstops < self.debug_simulated_mlstop_count:
      self.P(f"Simulated MLSTOP after {main_loop_iters} iterations({self.__simulated_mlstops + 1}/{self.debug_simulated_mlstop_count}).", color='r')
      sleep(self.debug_simulated_mlstop)
      self.__simulated_mlstops += 1
    return
  
  
  def check_shutdown_reset_by_file(self) -> bool:
    if os.path.exists(SHUTDOWN_RESET_FILE):
      os.unlink(SHUTDOWN_RESET_FILE)
      result = True
    else:
      result = False
    return result
  
  def shutdown_reset_erase(self):
    import shutil
    FOLDERS = [
      'network_monitor/'
    ]
    FILES = [
    ]
    # delete all FOLDERS folders and all files in FILES
    for folder in FOLDERS:
      full_path = self.log.get_data_subfolder(folder)
      self.P("Deleting folder '{}'".format(full_path), color='r')
      shutil.rmtree(full_path, ignore_errors=True)
    #endfor
    for file in FILES:
      fn = self.log.get_data_file(file)
      self.P("Deleting file '{}'".format(fn), color='r')
      os.unlink(fn)
    #endfor
    return


  def main_loop(self):
    """
    This is the main loop that orchestrates all the processes, managers and their worker threads.
    
    If the loop is stopped/locked at a particular `__loop_stage` check the first time it occured and
    go back on log to see where that particular stage progress stopped. Multiple layers of loop stage
    debug info have been added for debug purposes.

    """
    captures_data_metadata, dct_servers_inputs, dct_servers_outputs, dct_business_inputs, payloads = None, None, None, None, None
    check_ram_on_shutdown = self.check_ram_on_shutdown
    shutdown_reset_required = False
    try:
      return_code = None
      
      self._init_all_processes()      
      self._init_main_loop()
      
      while not self.__done:
        
        shutdown_reset_required = self.check_shutdown_reset_by_file()
        if shutdown_reset_required:
          self.__done = True
          self.P("SHUTDOWN-RESET REQUEST BY FLAG-FILE", color='r', boxed=True)
          continue
        
        self.__loop_stage = "0"
        self.log.start_timer(self._main_loop_timer_name)
        self._maybe_delay_main_loop()
        self._maybe_save_local_info()
        self._main_loop_counts['ITER'] += 1
        
        #1. Choose only the streams that should be run this step - copy from ConfigManager
        self.__loop_stage = "1.sel.run.pipe"
        self.choose_current_running_streams()

        # 1.1. Simulate MLSTOP
        self.__loop_stage = "1.1.sim.mlstop"
        self.maybe_simulate_mlstop()

        #2. Call the BusinessManager to start new business plugins and kill the unused ones
        self.__loop_stage = '2.bm.refresh.main'
        in_use_ai_engines = self.refresh_business_plugins()

        #3. Start the serving process. WARNING: potentially blocking if inprocess == True
        self.__loop_stage = '3.serving.start'
        self.maybe_start_serving_processes(in_use_ai_engines=in_use_ai_engines)

        #4. Collect data from all streams
        self.__loop_stage = '4.collect'
        dct_captures = self.collect_data()

        #5. Update the main loop data handler
        self.__loop_stage = '5.handle'
        self.update_main_loop_data_handler(dct_captures=dct_captures)

        #6. Inference step - all collected data are aggregated for inference and needed serving processes are run
        self.__loop_stage = '6.serve.data'
        dct_servers_inputs = self.aggregate_collected_data_for_serving_manager()
        
        #   WARNING: potentially blocking if inprocess == True
        self.__loop_stage = '6.1.serve.run'
        dct_servers_outputs = self.run_serving_manager(dct_servers_inputs=dct_servers_inputs)

        #7. Business plugins inputs preparation step
        #   - Append captures to the corresponding business plugins
        #   - Append inferences (servers outputs) to the corresponding business plugins
        self.__loop_stage = '7.bp.prep'
        self.append_captures_for_business_plugins() ### the business inputs at this point are stored internally in DataAggregationManager
                
        self.__loop_stage = '7.1.bp.serve'
        dct_business_inputs = self.append_servers_outputs_for_business_plugins(dct_servers_outputs=dct_servers_outputs)

        #8. Business Plugins are executed
        self.__loop_stage = '8.bp.run'
        self.run_business_manager(dct_business_inputs=dct_business_inputs)

        #9. Comm info, timers, ... - later we gonna check for total comm failures
        self.__loop_stage = '9.logs'
        self.comm_manager_show_info()


        self.log.stop_timer(self._main_loop_timer_name)
        
        self.__loop_stage = '9.1.log.tm'
        self.maybe_show_timers() # show timers after main timer is closed
        # now we signal we should send the startup log
        
        self.__loop_stage = '9.2.sendlog'
        self._maybe_send_startup_log()
        self.__is_mlstop_dangerous = True # first iter surely done

        return_code = self._return_code

        self.__loop_stage = '10.checks'
        if self.comm_manager.has_failed_comms:
          self.P("Shutdown initiated due to multiple failure in communication!", color='r')
          return_code = ct.CODE_EXCEPTION

        if self.check_shutdown_on_missing_streams():
          self.P("Shutdown initiated due NO streams and SHUTDOWN_NO_STREAMS==True!", color='r')
          return_code = ct.CODE_SHUTDOWN
          
        if self._app_monitor.critical_alert:          
          msg = "************** Restart in {}s initiated due to LOW MEMORY! **************".format(SHUTDOWN_DELAY)
          self.P('**************************************************************************', color='r')
          self.P(msg, color='r')
          self.P('**************************************************************************', color='r')
          self._create_notification(
            notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
            msg=msg,
            displayed=True,
          )
          self.__is_mlstop_dangerous = False 
          sleep(SHUTDOWN_DELAY) # allow notification to be send!
          return_code = ct.CODE_EXCEPTION
          check_ram_on_shutdown = False
        #endif critical alert
        
        if return_code is not None:
          self.__loop_stage = '11.break'
          self.P("WARNING: Exiting MLOOP({}).".format(return_code), color='r')
          self.__is_mlstop_dangerous = False 
          break
      #enwhile
      # post loop exit save object tree
      if check_ram_on_shutdown:
        self._save_object_tree(
          fn='obj_mem_shutdown.txt',
          save_top_only=False,
          top=100,
        )
    except:
      return_code = ct.CODE_EXCEPTION
      exception_text = traceback.format_exc()
      self.P('Exception in main loop: {}'.format(exception_text), color='r')
      if check_ram_on_shutdown:
        self._save_object_tree(
          fn='obj_mem_mainloop_exception.txt',
          save_top_only=False,
          top=100,
        )      
      self._maybe_exception_stop()
      self._save_exception_main_loop_state(
        exception_text,
        captures_data_metadata=captures_data_metadata,
        dct_servers_inputs=dct_servers_inputs,
        dct_servers_outputs=dct_servers_outputs,
        dct_business_inputs=dct_business_inputs,
        payloads=payloads,
        dct_config_streams=self._config_manager.dct_config_streams if self._config_manager is not None else None,
      )      
    finally:
      self.close_main_loop()
    #end try-except-finally

    try:
      self.P('Returning from main loop with return code: {}'.format(return_code), color='r')
      self.log.stop_timer(ct.TIMER_APP, skip_first_timing=False)
      self.log.show_timers()
      self._app_monitor.shutdown()
    except Exception as e:
      self.P('Exception in finalization of main loop: {}'.format(e), color='r')
    
    if shutdown_reset_required:
      self.P("Initiating SHUTDOWN-RESET data erase", color='r')
      self.shutdown_reset_erase()

    return return_code
  
    

# -*- coding: utf-8 -*-
"""
  
  
TODO:
  - on every error deallocate parallel serving process
  - use faster & non blocking communication  
  - Raise warning when preprocessing on a large amount of images !
  
  - model cloning for same definition but DIFFERENT weights

windows: set PYTHONPATH=%PYTHONPATH%;C:\work\ 
  


"""

import os
import threading
import traceback


from time import sleep, time, strftime, gmtime
from collections import OrderedDict, defaultdict

# os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

from naeural_core import constants as ct
from naeural_core.utils.system_shared_memory import NumpySharedMemory, replace_ndarray_with_shm, replace_shm_with_ndarray

from naeural_core.manager import Manager
from naeural_core.serving.base.serving_utils import comm_generator

_FULL_DEBUG = True

class SMConst:
  PCOUNT = 'PCOUNT'
  PTIME = 'PTIME'
  CREATED_DATE = 'CREATED_DATE'
  DATA = 'DATA'
  TYPE = 'TYPE'
  RESULT = 'RESULT'
  TIMERS = 'TIMERS'
  ABNORMAL_EXIT = 'ABNORMAL_EXIT'
  PREDICT = 'PREDICT'
  READY = 'READY'
  GET_CONFIG = 'GET_CONFIG'
  STOP = 'STOP'
  SERVER_NAME = 'SERVER_NAME'
  
  COMM = 'COMM'
  SERVER = 'SERVER'
  PID = 'PID'
  COVERED_BY = 'COVERED_BY'
  SHM = 'SHM'
  DEFAULT_COMM_METHOD = 'shm'
  
  CMD_ID = 'CMD_ID'
  TIMESTAMP = 'TIMESTAMP'
  
  BASE_PROPS = [COMM, SERVER, PID]
  
  SERVING_ENVIRONMENT = 'SERVING_ENVIRONMENT'
  MAX_WAIT_TIME_DEFAULT = 5
  MAX_WAIT_TIME_KEY = 'MAX_WAIT_TIME'
  
  MAX_WAIT_TIME_MULTIPLIER_DEFAULT = max(2, 100 // MAX_WAIT_TIME_DEFAULT) # this is used only for startups
  
  MAX_INPROCESS_TIME = 12000
  
  SERVING_MAX_TRIES = "SERVING_MAX_TRIES"
  SERVING_MAX_TRIES_DEFAULT = 5
  
  
def get_raw_server(log, server_name):
  """
  Simple utility function that enables quick experimentation of serving process without Serving Manager

  Parameters
  ----------
  log : Logger

  server_name : str


  """
  from naeural_core.manager import Manager
  mgr = Manager(log=log)
  _, class_name, _Class, _default_config = mgr._get_module_name_and_class(
      locations=ct.PLUGIN_SEARCH.LOC_SERVING_PLUGINS,
      name=server_name,
      suffix=ct.PLUGIN_SEARCH.SUFFIX_SERVING_PLUGINS,
      safe_locations=ct.PLUGIN_SEARCH.SAFE_LOC_SERVING_PLUGINS,
      safe_imports=ct.PLUGIN_SEARCH.SERVING_SAFE_IMPORTS,
      safety_check=True,
    )  
  
  _server = _Class(
      server_name=server_name,
      comm_eng=None,
      inprocess=True,
      default_config=_default_config,
      log=log,
    )  
  _server.predict_server_name = server_name.upper()
  return _server
    
  

class ServingManager(Manager):
  def __init__(self,
               shmem,
               server_names=None,
               owner=None,
               full_debug=None,
               log_timeouts_period=3600,
               monitor_each_k_inferences=10,
               monitor_callback=None,
               **kwargs):
    """
    The main ServingManager

    Parameters
    ----------
    server_names : list[str]
      the initial list of servers (better just empty list).
      
    full_debug : TYPE, optional
      show "extreme" debug info. The default is False.
            
      
    **kwargs : TYPE
      must pass Logger instance.


    Usage/tools:
      
      `start_server(server)`                      : will start a serving process based on the given name
        
      `stop_server(name)`                         : gracefully stop the server
      
      `stop_all_servers()`                        : gracefully stop all servers
      
      `get_server_config(name, key)`              : returns full serving process config or just a key
      
      `predict(name, inputs)`                     : run prediction using inputs (can be anything)
      
      `predict_parallel(dct)`                     : run all given {server:input} pairs in parallel
      
      `get_predict_time(name)`                    : get mean timing for the given serving process - depends on
                                                    individual calls and individual batch sizes
      
      `get_predict_parallel_time()`               : returns mean parallel predict time depending of all jobs 
                                                    that have been given
                                          
      `keep_active_servers(active_server_names)`  : will keep running only the requested server names.
                                                    will close any started server that is not found in active_server_names
                                                    will start any un-started server that is found in active_server_names

    """
    self.shmem = shmem
    # TODO: maybe add SharedMemoryManager to each serving process instance under category=ct.SHMEM.SERVING
    # however keep in mind that it will not work for simple dicts!!!
      
    if server_names is None:
      server_names = []

    if full_debug is None:
      full_debug = False

    self.owner = owner
      
    self._monitor_each_k_inferences = monitor_each_k_inferences
    self._monitor_counter = 0
    self._monitor_callback = monitor_callback

    self.full_debug = full_debug
    self.log_timeouts_last_ts = None
    self.log_timeouts_period = log_timeouts_period
    self._servers = {}
    self._timeouts = {}
    self._collector_call_counter = 0
    self.__cmd_count = 0
    self.__server_failures = defaultdict(lambda : 0)
    self._server_names = server_names
    self.inprocess_predict = None
    self._main_tid = threading.get_ident()
    prefix_log = kwargs.pop('prefix_log','[SMGR]')
    super(ServingManager, self).__init__(prefix_log=prefix_log, **kwargs)
    return
  
  def P(self, s, color=None, **kwargs):
    s = "" + s # maybe think a extra prefix
    super().P(s, color=color, **kwargs)
    return
  
  def get_server_name(self, server_name):
    """
    Returns the stringified server name in uppercase
    Args:
      server_name: str or list[str] or tuple[str] - the server name(s)
    Returns:
      res : str - the stringified server name in uppercase
    """
    str_id = None
    if isinstance(server_name, str):
      str_id = server_name.upper()
    elif isinstance(server_name, (list, tuple)):
      str_id = '_'.join([x.upper() for x in server_name])
    else:
      self.P("Unknown server name format {}".format(server_name), color='error')
    return str_id
  
  def startup(self):
    super().startup()
    self.P("Initialising {} with following environment:\n{}".format(
      self.__class__.__name__,
      self.log.dict_pretty_format(self.cfg_serving_environment)
    ), color='b')
    self._create_servers()
    return

  @property
  def serving_pids(self):
    pids = []
    for s, dct_s in self._servers.items():
      pids.append(dct_s[SMConst.PID])
    return list(set(pids))

  @property
  def total_timeouts(self):
    return sum(self._timeouts.values()) if len(self._timeouts.keys()) > 0 else 0

  @property
  def cfg_serving_environment_dict(self):
    return self.log.config_data.get(SMConst.SERVING_ENVIRONMENT, {})
  
  @property
  def cfg_serving_environment(self):
    return self.cfg_serving_environment_dict

  @property
  def default_cuda(self):
    return self.cfg_serving_environment.get(ct.DEFAULT_CUDA, 'cpu')
  
  @property
  def _server_collector_timedelta(self):
    return self.cfg_serving_environment.get(ct.SERVING.SERVER_COLLECTOR_TIMEDELTA, 3600)
    
  @property
  def _server_collector_delay(self):
    return self.cfg_serving_environment.get(ct.SERVING.SERVER_COLLECTOR_DELAY, 5)


  @property
  def must_check_blocked_inprocess_serving(self):
    return self.cfg_serving_environment.get(ct.SERVING.CHECK_BLOCKED_INPROCESS_SERVING, True)
  
  @property
  def max_wait_time_multiplier(self):
    return self.cfg_serving_environment.get(ct.SERVING.MAX_WAIT_TIME_MULTIPLIER, SMConst.MAX_WAIT_TIME_MULTIPLIER_DEFAULT)
  
  ###
  ### PRIVATE/PROTECTED
  ###

  def get_timeouts(self, server_name):
    if server_name not in self._timeouts:
      self._timeouts[server_name] = 0
    return self._timeouts[server_name]

  def increment_timeouts(self, server_name):
    if server_name not in self._timeouts:
      self._timeouts[server_name] = 0
    self._timeouts[server_name] += 1
    return self._timeouts[server_name]

  def _assign_server(self, replaced_server, main_server):
    assert main_server in self._servers
    if replaced_server not in self._servers:
      self._servers[replaced_server] = {}
    for prop in SMConst.BASE_PROPS:
      self._servers[replaced_server][prop] = self._servers[main_server][prop]
    self._servers[replaced_server][SMConst.COVERED_BY] = main_server
    self._reset_usage(replaced_server)
    return
  
  
  def _check_equal_servers(self, replaced_server, main_server):
    result = True
    for prop in SMConst.BASE_PROPS:
      if self._servers[replaced_server][prop] != self._servers[main_server][prop]:
        result = False
    return result
  
  
  def _maybe_complete_servers(self, server_name, similar_servers, covered_servers, force_stop_alias=True): 
    server_name = self.get_server_name(server_name)
    similar_servers = [self.get_server_name(x) for x in similar_servers]
    covered_servers = [self.get_server_name(x) for x in covered_servers]
    if server_name not in self._servers:
      return
    self.P("Model '{}' parents & coverage: ".format(server_name), color='y')
    for similar_server in similar_servers:  
      if similar_server in covered_servers:
        # this similar server has been declared in covered servers
        if similar_server not in self._servers:
          # not loaded then just alias
          self.P("  Parent '{}' => will be automatically served due to COVERED_SERVERS".format(similar_server), color='m')
          self._assign_server(replaced_server=similar_server, main_server=server_name)
        else:
          # seems already loaded
          if not self._check_equal_servers(replaced_server=similar_server, main_server=server_name):
            # check if already loaded is not actually the main server (probably assigned elsewhere)
            # then (usually) stop and alias
            self.P("  WARNING: '{}' covers '{}' although '{}' is already running, please verify this behavior".format(
              server_name, similar_server, similar_server), color='r')
            if force_stop_alias and (self._servers[similar_server][SMConst.SERVER].server_name in similar_servers):
              self.stop_server(similar_server)
              self._assign_server(replaced_server=similar_server, main_server=server_name)
              # now check if no other aliases of the similar should be re-assigned to the current server
              for svr in self._servers:
                if self._servers[svr].get(SMConst.COVERED_BY) == similar_server:
                  self.P("Found '{}' already covered by '{}'. Covering '{}' by '{}'".format(
                    svr, similar_server, svr, server_name,
                    ), color='m')
                  self._assign_server(replaced_server=svr, main_server=server_name)
          else:
            self.P("  Server '{}' already covered by '{}'".format(similar_server, server_name), color='m')
        # endif similar_server in self._servers

      else:
        self.P("  Parent '{}' not in COVERED_SERVERS. No aliast added.".format(similar_server), color='y')

    for covered_server in covered_servers:
      if covered_server not in self._servers:
        # this is covered and not loaded - just make an alias
        self.P("  Model '{}' not in '{}' parents will be covered by '{}'".format(
          covered_server, server_name, server_name
          ), color='m')
        self._assign_server(replaced_server=covered_server, main_server=server_name) 
      elif covered_server not in similar_servers: 
        # this is covered and isn't a parent
        if not self._check_equal_servers(replaced_server=covered_server, main_server=server_name):
          # if main server is NOT already same as the covered server then stop & alias
          self.P("  WARNING: '{}' covers '{}' although '{}' is already running, please verify this behavior!".format(
            server_name, covered_server, covered_server), color='r')
          if force_stop_alias and (self._servers[covered_server][SMConst.SERVER].server_name in covered_servers):
            self.stop_server(covered_server)
            self._assign_server(replaced_server=covered_server, main_server=server_name)        
        else:
          self.P("  Server '{}' already covered by '{}'".format(covered_server, server_name), color='m')          
    return
  
  def _download_and_load_config(self, url, server_name, force_download=True):
    fn_custom_model_definition = server_name + '_def.txt'
    self.log.maybe_download_model(
      url, fn_custom_model_definition, force_download=force_download
    )
    dct_config = self.log.load_models_json(fn_custom_model_definition)
    return dct_config

  def get_comm_method(self):
    """
    Get the communication method used by the serving processes.
    Options:
    - 'default': will send messages from manager to serving processes without
                storing the ndarrays in shared memory
    - 'shm': will send messages from manager to serving processes in the same manner,
            but will replace any ndarrays from the message with references to shared memory
    Returns
    -------

    """
    return self.cfg_serving_environment.get(ct.SERVING.COMM_METHOD, SMConst.DEFAULT_COMM_METHOD)

  def _server_wait_time(self, server_name):
    server_name = self.get_server_name(server_name)
    if server_name not in self._servers:
      return SMConst.MAX_WAIT_TIME_DEFAULT
    _server = self._servers[server_name][SMConst.SERVER]
    # TODO: move this to received config 
    wait_time = _server.CONFIG[SMConst.MAX_WAIT_TIME_KEY]
    return wait_time

  def _create_server(self, server_name, server_class_name=None, upstream_config=None, inprocess=False):
    upstream_config = upstream_config or {}
    if server_name in self._servers:
      self.P("Serving process '{}' already created".format(server_name), color='r')
      return server_name
    if server_class_name is None:
      server_class_name = server_name
    self.P("Creating {}serving process '{}'({})...".format(
      "INPROCESS " if inprocess else "PARALLEL ",
      server_name, server_class_name))
    self.log.start_timer('create_server')
    _module, class_name, _Class, _default_config = self._get_module_name_and_class(
      locations=ct.PLUGIN_SEARCH.LOC_SERVING_PLUGINS,
      name=server_class_name,
      safety_check=True,
      suffix=ct.PLUGIN_SEARCH.SUFFIX_SERVING_PLUGINS,
      safe_locations=ct.PLUGIN_SEARCH.SAFE_LOC_SERVING_PLUGINS,     
      safe_imports=ct.PLUGIN_SEARCH.SERVING_SAFE_IMPORTS,
    )
    if _Class is None:
      self.P("Failed to load model server '{}'({}). Please define a specific class in inference/serving_plugins!".format(
        server_name, server_class_name), color='error')
      self.log.stop_timer('create_server')
      return None
    
    self.P("Loaded model serving class '{}' {}".format(class_name, _Class), color='g')

    # maybe create inter-process communication
    if not inprocess:
      comm_type = self.cfg_serving_environment.get('COMM_ENGINE', 'queue')
      comm_server, comm_client = comm_generator(engine=comm_type)
      log = None
    else:
      comm_server, comm_client = None, None
      log = self.log
    #endif
    
    # before creating the process check if the serving process is a CUSTOM_DOWNLOADABLE_MODEL
    if isinstance(_default_config, dict) and _default_config.get(ct.CUSTOM_DOWNLOADABLE_MODEL, False):
      # now load the config from the server and add it to 
      config_url = upstream_config.get(ct.CUSTOM_DOWNLOADABLE_MODEL_URL, None)
      if config_url is None:
        self.P("CUSTOM_DOWNLOADABLE_MODEL '{}' is missing CUSTOM_DOWNLOADABLE_MODEL_URL from its upstream config".format(
          server_name), color='error')
        self.log.stop_timer('create_server')
        return None
      dct_custom_config = self._download_and_load_config(config_url, server_name)
      # now merge the dicts
      for k,v in dct_custom_config.items():
        _default_config[k] = v
        
    # end CUSTOM_DOWNLOADABLE_MODEL

    # create serving process
    _version = "0.0"
    if isinstance(_default_config, dict):
      _version = _default_config.get("MODULE_VERSION", "0.0.0")
    self.log.start_timer('create_server_new()')
    max_img_shape = self.cfg_serving_environment.get('SHM_MAX_IMAGE_SHAPE', ct.SERVING.SHM_IMG_MAX_SHAPE)
    comm_method = self.get_comm_method()
    if comm_method == 'pipe':
      self.P(f"Attempting to use pipe communication for server '{server_name}'")
    npy_shm_kwargs = {
      'mem_name': self.get_server_name(server_name),
      'mem_size': 0,  # irrelevant for buffer mode
      'np_shape': max_img_shape,
      'np_type': 'uint8',
      'is_buffer': True,
      'maxlen': self.cfg_serving_environment.get('SHM_MAX_LEN', ct.SERVING.SHM_MAX_LEN),
    }
    self.owner.set_loop_stage('3.serving.start.{}.shm_starting'.format(server_name))
    self.log.start_timer('create_server_shm_start()')
    npy_shm = None if comm_method == 'pipe' else NumpySharedMemory(**npy_shm_kwargs, create=True, log=self.log)
    if npy_shm is not None and not npy_shm.initialized:
      self.P(
        f"Failed to initialize shared memory for server '{server_name}'. Defaulting to pipe communication",
        color='error'
      )
      comm_method = 'pipe'
      npy_shm = None
    # endif initializing shared memory failed
    self.log.stop_timer('create_server_shm_start()')
    _server = _Class(
      server_name=server_name,
      comm_eng=comm_client,  # None if inprocess
      full_debug=self.full_debug,
      inprocess=inprocess,
      default_config=_default_config,
      upstream_config=upstream_config,
      log=log,
      environment_variables=self.cfg_serving_environment,
      version=_version,
      npy_shm_kwargs=npy_shm_kwargs,
      comm_method=comm_method,
    )
    self.log.stop_timer('create_server_new()')

    success = True
    if _server.inprocess:
      self.P("Model '{}' forced startup done".format(server_name), color='y')
      self.P("  Model '{}' serving process will NOT be running in parallel".format(server_name), color='y')
      self.P("  All logs will be deferred to current one.", color='y')
      self._servers[server_name] = {
        SMConst.SERVER: _server,
        # no need for comm but gonna put it anyway
        SMConst.COMM: comm_server,
        SMConst.PID : os.getpid(),
        SMConst.CREATED_DATE : self.log.now_str(nice_print=True, short=True),
      }
    else:
      self.owner.set_loop_stage('3.serving.start.{}.starting'.format(server_name))
      self.log.start_timer('create_server_start()')
      self.P("Running process {}...".format(_server))
      _server.daemon = True # TODO: must see if all servings work ok
      _server.start()
      # sleep 1-2 second to allow subprocess to "breath"
      sleep(2)
      _exit_code = _server.exitcode
      _pid = _server.pid
      self.owner.set_loop_stage('3.serving.start.{}.postsleep'.format(server_name))
      if _exit_code is not None:
        self.P("Serving process '{}' ended with code {}".format(server_name, _exit_code), color='r')
        comm_server.close()
        success = False
        if npy_shm is not None:
          npy_shm.shutdown()
      else:
        self.P("Serving process '{}' is running with PID {}".format(server_name, _pid))
        # now we wait for "READY" status
        self.P("  Waiting until '{}' responds".format(server_name))
        self.owner.set_loop_stage('3.serving.start.{}.wait'.format(server_name), is_dangerous=False)
        result = self._wait_for_result(
          server_name=server_name, 
          comm_eng=comm_server, 
          max_wait_time=float('inf'),
        )
        self.owner.set_loop_stage('3.serving.start.{}.received'.format(server_name))
        msg_type, msg_data = None, None
        if result is not None:          
          msg_type, msg_data = result
        if isinstance(msg_type, str) and msg_type.upper() == SMConst.READY:
          self.P("Received '{}' from '{}': {}".format(
            msg_type, server_name, msg_data), color='g')
          dct_server = {
            SMConst.SERVER: _server,
            SMConst.COMM: comm_server,
            SMConst.SHM: npy_shm,
            SMConst.PID: _pid,
            SMConst.CREATED_DATE : self.log.now_str(nice_print=True, short=False),
          }
          self._servers[server_name] = dct_server
          # now copy locally the remote configuration just to make sure all is good
          self._servers[server_name][SMConst.SERVER].config_model = self.get_server_config(server_name)
          self._servers[server_name][SMConst.SERVER].config_data = self._servers[server_name][SMConst.SERVER].config_model
        else:
          self._show_serving_error(server_name, result)
          self.P("Serving process '{}' FAILED. Closing pipes and process...".format(server_name), color='error')
          comm_server.close()
          _server.terminate()
          if npy_shm is not None:
            npy_shm.shutdown()
          self.P("  Serving process '{}' terminated: {}".format(server_name, _server), color='r')
          success = False
        # end if-else READY
      # end if-else exit_code is None
      self.log.stop_timer('create_server_start()')
    # end running in parallel

    if success:
      self.log.start_timer('create_server_parents()')
      # now lets populate similar "lower-level" servers if not already loaded
      similar_processes = _server.get_server_parents()
      covered_servers = _default_config.get(ct.COVERED_SERVERS, [])
      if server_class_name != server_name:
        covered_servers.append(server_class_name)
      self._maybe_complete_servers(server_name, similar_processes, covered_servers)
  
      # finally lets initialize server usage
      self._reset_usage(server_name)
      self.P("Server '{}' succesfully created.".format(server_name), color='g')
      self.show_servers()
      self.log.stop_timer('create_server_parents()')
      
    self.log.stop_timer('create_server')
    return server_name if success else None
  
  def _show_serving_error(self, server_name, result):
    # TODO: on every error deallocate parallel serving process
    server_name = self.get_server_name(server_name)
    if result is not None:
      if isinstance(result, dict):
        msg_data = result[SMConst.DATA]
        msg_type = result[SMConst.TYPE]            
      elif isinstance(result, tuple):
        msg_type, msg_data = result
      else:
        msg_type = "UNKNOWN ERROR"
        msg_data = str(result)
    else:
      msg_type = "Unknown status/error (NULL received)"
      msg_data = "Unknown status/error (NULL received) from " + server_name
    msg = "ServingManager: Received '{}' from model serving process '{}'".format(
      msg_type, 
      server_name
    )
    info = "  '{}' details:\n{}".format(msg_type, msg_data)
    self._create_notification(
      notif=ct.NOTIFICATION_TYPE.STATUS_EXCEPTION, 
      msg=msg, 
      info=info,
    )
    return


  def _create_servers(self):
    started = []
    for server_name in self._server_names:
      server_name = self.get_server_name(server_name)
      online = self._create_server(server_name) ### TODO upstream
      started.append((server_name, online))
    # done for each server
    return started

  def _send_message(self, server_name, command, inputs):
    server_name = self.get_server_name(server_name)
    self.__cmd_count += 1
    if self._servers[server_name].get(SMConst.SHM) is not None:
      self.log.start_timer('replace_ndarray_with_shm')
      processed_inputs = replace_ndarray_with_shm(inputs, self._servers[server_name][SMConst.SHM], debug=self.full_debug)
      self.log.stop_timer('replace_ndarray_with_shm')
    else:
      processed_inputs = inputs
    dct_cmd = {
      SMConst.TYPE : command,
      SMConst.DATA: processed_inputs,
      SMConst.SERVER_NAME : server_name,
      SMConst.CMD_ID : self.__cmd_count,
      SMConst.TIMESTAMP: time()
      }
    comm_eng = self._servers[server_name][SMConst.COMM]
    try:      
      if self.full_debug or command != SMConst.PREDICT:
        self.P('Sending {}:{} to {} on {}'.format(command, self.__cmd_count, server_name, comm_eng), color='y')
      self.log.start_timer('pipe_send_message')
      comm_eng.send(dct_cmd)
      self.log.stop_timer('pipe_send_message')
      if self.full_debug or command != SMConst.PREDICT:
        self.P('  Command {}:{} sent to {}'.format(command, self.__cmd_count, server_name), color='y')
        
    except:
      self.log.stop_timer('pipe_send_message') # double stop due to re-raise
      self.P("Error while sending command {} to server {}:{}".format(
         command, server_name, traceback.format_exc()), color='r')
      self.show_servers()
      raise ValueError('Comm pipe exception!')
    return
  
  
  def _wait_for_comm_message(self, server_name, comm_eng=None, max_wait_time=None):
    assert max_wait_time is not None, "ERROR: max_wait_time != None is required"
    server_name = self.get_server_name(server_name)
    if comm_eng is None:
      comm_eng = self._servers[server_name][SMConst.COMM]
    if max_wait_time is None:
      # we wait until we receive the answer
      payload = comm_eng.recv()
    else:
      # now we wait only `max_wait_time` seconds for the result
      has_payload = comm_eng.poll(max_wait_time)
      payload = None
      if has_payload:
        payload = comm_eng.recv()
    return payload
  
      
  def _cleanup_server(self, server_name, wait=False, terminate=False):    
    # cleanup and maybe even kill
    server_name = self.get_server_name(server_name)
    if server_name not in self._servers:
      return
    
    covered_by = self._servers[server_name].get(SMConst.COVERED_BY)
    if covered_by is not None:
      self.P("Attempted to cleanup the server '{}' covered by '{}'".format(
        server_name, covered_by), color='r'
      )
      return
    
    _server = self._get_server(server_name)
    self.P("Proceeding to serving process '{}' cleanup...".format(server_name), color='m')
    if _server.inprocess:
      self.P("  Parallel serving disabled. Cannot totally stop serving module '{}'".format(server_name), color='r')
      _server.shutdown()
      del _server
    else:
      if terminate and _server.is_alive():
        self.P("  Forcing '{}' termination: {}...".format(server_name, _server), color='m')
        _server.terminate()
      if wait:
        self.P("  Waiting for serving process '{}' ...".format(server_name), color='m')
        _server.join(5)
        if _server.exitcode is None:
          self.P("  Serving process '{}' did not respond to join()".format(server_name), color='r')
          _server.kill()
        #endif exitcode is None
      #endif wait  
      self.P("  Serving process status: {}".format(_server), color='m'
      )
      self.P("  Closing comm pipe for for serving process '{}' ...".format(server_name), color='m')
      self._servers[server_name][SMConst.COMM].close()
      if self._servers[server_name].get(SMConst.SHM) is not None:
        self._servers[server_name][SMConst.SHM].shutdown()
    
    del self._servers[server_name]
    remaining_servers = list(self._servers.keys())
    for svr in remaining_servers:
      if self._servers[svr].get(SMConst.COVERED_BY) == server_name:
        self.P("  Deleting covered server '{}' due to main server shutdown".format(svr), color='m')
        del self._servers[svr]
    return


  def _get_server(self, server_name):
    server_name = self.get_server_name(server_name)
    return self._servers[server_name][SMConst.SERVER] if server_name in self._servers else None

  def _close_server_on_null(self, server_name, max_wait_time=None):
    if max_wait_time is None:
      max_wait_time = self._server_wait_time(server_name)
    msg = "Server '{}' error: no message returned from server (maybe timeout) in over {}s.".format(
      server_name, max_wait_time)
    self.P(msg, color='r')
    self._create_notification(
      notif=ct.NOTIFICATION_TYPE.STATUS_EXCEPTION, 
      msg="'{}' server timeout".format(server_name), 
      info=msg,
      displayed=True,
    )
    self._cleanup_server(server_name, wait=True, terminate=True)
    return
  
  
  def _wait_for_result(self, server_name, message_callback=None, comm_eng=None, max_wait_time=None):
    """
    This method should ALWAYS be called with a default timeout. If the server does not send any information
    in the given time then something is wrong (including bad coding on the server side)
    """
    
    server_name = self.get_server_name(server_name)
    
    if self.full_debug:
      self.P(f'Waiting for data from {server_name}[timeouts: {self.get_timeouts(server_name)}| total: {self.total_timeouts}]', color='y')
      start_wait = time()
    #endif fulldebug

    if max_wait_time is None:
      max_wait_time = self._server_wait_time(server_name)

    result = self._wait_for_comm_message(
      server_name=server_name, comm_eng=comm_eng, max_wait_time=max_wait_time
    )

    if result is None:
      # something is clearly wrong so we cleanup and raise alarms    
      self._close_server_on_null(server_name=server_name, max_wait_time=max_wait_time)
      self.increment_timeouts(server_name=server_name)
      return None
    elif self.full_debug:
      msg_ts = result.get(SMConst.TIMESTAMP)
      elapsed_str = f'[Elapsed: {round(time() - msg_ts, 3)}]' if msg_ts is not None else ''
      self.P(f'  Received data {result.get(SMConst.CMD_ID)} from {server_name}{elapsed_str}', color='y')
      self.P(f'  Wait time: {round(time() - start_wait, 3)}', color='y')
    # end serving process problem
    
    # result is not none so we can get its data    
    msg_type = result[SMConst.TYPE].upper()  
    msg_data = result[SMConst.DATA]
    
    if SMConst.TIMERS in result and isinstance(result[SMConst.TIMERS], tuple):
      # we process timers if sent
      dct_timers, dct_timers_graph = result[SMConst.TIMERS]
      if dct_timers is not None and dct_timers_graph is not None:
        self.log.import_timers_section(
          dct_timers=dct_timers,
          dct_timers_graph=dct_timers_graph,
          section=str(server_name) + '_remote_main',
          overwrite=True,
        )
        self.P("Received timers from '{}' and updated remote section info".format(server_name), color='m')
    #endif received extra info --- timers
    
    # maybe during predict we might want to send intermediate messages before 'RESULT'
    # such as the ones that could be sent during training process
    # for this feature we will uses the `message_callback` for all non-result messages

    while msg_type != SMConst.RESULT:
      if msg_type == SMConst.ABNORMAL_EXIT:        
        self._show_serving_error(server_name, result)
        # if the serving process crashed then we wait for the process to finish
        # we can later try to re-create one
        self._cleanup_server(server_name, wait=True)
        return None
      else:
        self.P("Received '{}': from '{}' with data: '{}'".format(msg_type, server_name, msg_data), color='y')
        # now we can use message_callback in order to to custom processing on the received message if needed
        if message_callback is not None:
          message_callback(msg_data)
        else:
          # if no callback available and the message was not RESULT then we move on...
          break        
        # endif call the callback
      #endif abnormal or other message
      
      # periodic messages are multiplied by 2
      max_wait_time = self._server_wait_time(server_name) * 2
      result = self._wait_for_comm_message(server_name, comm_eng=comm_eng, max_wait_time=max_wait_time)
      if result is None:
        self._close_server_on_null(server_name=server_name, max_wait_time=max_wait_time)
        return None
      else:
        msg_type = result[SMConst.TYPE].upper()
        msg_data = result[SMConst.DATA]
      #endif valid result or not
    #endwhile not received the result
    return msg_type, msg_data

  def _increment_usage(self, server_name):
    server_name = self.get_server_name(server_name)
    if server_name not in self._servers:
      return None    
    dct_server = self._servers[server_name]
    if SMConst.PCOUNT not in dct_server:
      dct_server[SMConst.PCOUNT] = 0
    dct_server[SMConst.PCOUNT] += 1
    dct_server[SMConst.PTIME] = time()
    return dct_server[SMConst.PCOUNT]
  
  def _reset_usage(self, server_name):
    server_name = self.get_server_name(server_name)
    if server_name not in self._servers:
      return None    
    dct_server = self._servers[server_name]
    dct_server[SMConst.PCOUNT] = 0
    dct_server[SMConst.PTIME] = time()
    if SMConst.CREATED_DATE not in dct_server:
      dct_server[SMConst.CREATED_DATE] = self.log.now_str(nice_print=True, short=True)
    return
    
  
  
  #########################
  ###                   ###
  ###       PUBLIC      ###
  ###                   ###
  #########################
  
  def maybe_start_server(self, server_name, upstream_config=None, inprocess=False):
    result, server_class_name = None, None
    if isinstance(server_name, (list, tuple)):
      if len(server_name) != 2:
        self.P("Providing list/tuple instead of server name must be in (CLASS, NAME) format", color='error')
        return None
      server_class_name, _ = server_name # this extracts the actual serving process name
    server_name = self.get_server_name(server_name) # this constructs the instance name that can be <SERVING>_<INSTANCE>
    if server_name not in self._servers:
      self.P("`maybe_start_server`: '{}' server NOT available. Attempting to start server...".format(
        server_name), color='y')
      result = self._create_server(
        server_name=server_name,  # full name
        server_class_name=server_class_name, # just the serving process
        upstream_config=upstream_config,
        inprocess=inprocess
        )
      if result is None or result not in self._servers:
        self.__server_failures[server_name] += 1
        if self.__server_failures[server_name] > self.cfg_serving_environment.get(SMConst.SERVING_MAX_TRIES, SMConst.SERVING_MAX_TRIES_DEFAULT):
          msg = "FATAL ERROR: Serving process creation failed for {} multiple times!".format(server_name)
          self.P(msg, color='r')
          self._create_notification(
            notif=ct.STATUS_TYPE.STATUS_EXCEPTION,          
            notif_code=ct.NOTIFICATION_CODES.SERVING_START_FATAL_FAIL,
            failed_ai_server=server_name,
            msg=msg,
            displayed=True,
            ct=ct,
          )
          self.P("Serving failure will generate a system shutdown!", color='r', boxed=True)
          raise ValueError(msg)        
        msg = "Serving process creation failed for {}!".format(server_name)
        self.P(msg, color='r')
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_EXCEPTION,     
          notif_code=ct.NOTIFICATION_CODES.SERVING_START_FAILED,     
          failed_ai_server=server_name,
          msg=msg,
          displayed=True,
        )
        return None
      #endif

      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_NORMAL,
        notif_code=ct.NOTIFICATION_CODES.SERVING_START_OK,
        msg='Serving process {} creation succeeded'.format(result),
        ct=ct,
      )
    else:
      result = server_name
    #endif
    return result

  def is_covered_by(self, server_name):
    server_name = self.get_server_name(server_name)
    return self._servers[server_name].get(SMConst.COVERED_BY)

  def close_if_unused(self, server_name):
    server_name = self.get_server_name(server_name)
    if server_name not in self._servers:
      return False
    return self._servers[server_name][SMConst.SERVER].config_model.get('CLOSE_IF_UNUSED', False)

  def maybe_stop_unused_server(self, server_name):
    """
    This method will check if the server should be closed due to being unused.
    For this to have any effect the server must have
    Args:
      server_name: str or tuple, the server name to check

    Returns:

    """
    server_name = self.get_server_name(server_name)
    if server_name not in self._servers:
      return
    if self.is_covered_by(server_name) is not None:
      return
    if self.close_if_unused(server_name):
      self.P("Server '{}' is not used and should be stopped. Stopping...".format(server_name), color='y')
      self.stop_server(server_name)
    return

  def is_parallel(self, server_name):
    server_name = self.get_server_name(server_name)
    return not self._servers[server_name][SMConst.SERVER].inprocess
  
  def is_avail(self, server_name):
    server_name = self.get_server_name(server_name)
    return server_name in self._servers

  def get_server_config(self, server_name, key=None, local=False):
    """
    this function returns the serving process configuration. Will return current process
    retained config is local==True or the actual config if local==False
    
    
    """
    msg_type, msg_data, _config = None, None, None
    
    server_name = self.get_server_name(server_name)
    if server_name not in self._servers:
      self.P("Serving process '{}' is not active".format(server_name), color='r')
      return None

    _svr = self._servers[server_name][SMConst.SERVER]
    if _svr.inprocess:
      self.P("'{}' is running in current process. Local config is valid.".format(server_name))
      local = True
      
    if local:      
      _config = _svr.get_config()
    else:
      self._send_message(
        server_name=server_name, 
        command=SMConst.GET_CONFIG, 
        inputs=None
        )
      result = self._wait_for_result(server_name=server_name)      
      if result is not None:
        msg_type, msg_data = result
      # check if valid config message received
      if msg_type == SMConst.GET_CONFIG:
        _config = msg_data
      else:
        self._show_serving_error(server_name, result)
    #endif local or not
    if key is not None and _config is not None:
      return _config.get(key)
    return _config
  
  
  def start_server(self, server_name, upstream_config=None, inprocess=False):
    return self.maybe_start_server(
      server_name=server_name, 
      upstream_config=upstream_config,
      inprocess=inprocess
      )
  
  def stop_server(self, server_name):
    server_name = self.get_server_name(server_name)
    _server = self._get_server(server_name)
    if _server is None:
      # already stopped
      return
    covered_by = self.is_covered_by(server_name)
    if covered_by is not None:
      self.P("Serving process {} is covered by {}. Aborting shutdown".format(
        server_name,
        covered_by,
      ), color='m')
      return
    if _server is None:
      self.P("Serving process {} does not exist. Probably already stopped".format(server_name), color='r')
      return
    if not _server.inprocess:
      self.P("  Stopping serving process '{}' (PID:{}) ...".format(server_name, _server.pid), color='y')
      self._send_message(
        server_name=server_name, 
        command=SMConst.STOP, 
        inputs=None
      )
    # run cleanup no matter what
    self._cleanup_server(server_name, wait=True)
    if not _server.inprocess:
      self.P("  Serving process {} has been gracefully stopped.".format(server_name), color='y')
    return
  
  def stop_all_servers(self):
    self.P("Stopping all serving processes...", color='y')
    self.get_active_servers(show=True)
    gpu_info1 = self.log.gpu_info()
    if gpu_info1 is not None and len(gpu_info1) > 0:
      self.P("GPU0 free mem before shutdown: {} GB".format(gpu_info1[0]['FREE_MEM']), color='g')
    server_names = list(self._servers.keys())
    for server_name in server_names:      
      self.stop_server(server_name)
    gpu_info2 = self.log.gpu_info()
    if gpu_info2 is not None and len(gpu_info2) > 0:
      self.P("GPU0 free mem after shutdown: {} GB".format(gpu_info2[0]['FREE_MEM']), color='g')
    self.P("All servers stopped.", color='y')
    return
  
  def cleanup_stopped_servers(self):
    server_names = list(self._servers.keys())
    for server_name in server_names:
      _server = self._get_server(server_name)
      if _server.exitcode is not None:
        self.P("Serving process '{}' ended with exit code {}.".format(server_name, _server.exitcode))
        self._cleanup_server(server_name)
    return
  
  def get_plugin_default_config(self, signature):
    _module_name, _class_name, _cls_def, _config_dict = self._get_module_name_and_class(
      locations=ct.PLUGIN_SEARCH.LOC_SERVING_PLUGINS,
      name=signature,
      verbose=0,
      safety_check=True, # TODO: should we do this?
      suffix=ct.PLUGIN_SEARCH.SUFFIX_SERVING_PLUGINS,
      safe_locations=ct.PLUGIN_SEARCH.SAFE_LOC_SERVING_PLUGINS,     
      safe_imports=ct.PLUGIN_SEARCH.SERVING_SAFE_IMPORTS,
    )

    return _config_dict
  
  
  def get_predict_time(self, server_name):
    server_name = self.get_server_name(server_name)
    _server = self._get_server(server_name)
    if _server is not None:
      if _server.inprocess:
        timer_key = 'local_pred_' + server_name
      else:
        timer_key = 'remote_pred_' + server_name
    else:
      # if server is not active we assume it was remote and it stopped
      timer_key = 'remote_pred_' + server_name
    dct_timer = self.log.timers.get(timer_key)
    if dct_timer is not None:
      return dct_timer['MEAN']
    return None


  def simple_predict(self, server_name, inputs):
    res = None
    server_name = self.get_server_name(server_name)
    self.inprocess_predict = server_name
    self.inprocess_predict_start = time()
    self.log.start_timer('local_pred_' + server_name)
    try:
      _server = self._get_server(server_name)
      res = _server._in_process_predict(inputs)
    except Exception as e:
      info = str(e)
      self.P("Exception in '{}': {}".format(server_name, info), color='error')
      self._create_notification(
        notif=ct.NOTIFICATION_TYPE.STATUS_EXCEPTION,
        msg="Serving Manager exception for '{}' server.".format(server_name),
        info=info,
        displayed=True,
      )
      self.stop_server(server_name)
    self.log.stop_timer('local_pred_' + server_name)
    self.inprocess_predict = None
    return res
  
  
  def predict(self, server_name, inputs, message_callback=None, inprocess=False):
    """
    Single server predict

    Parameters
    ----------
    server_name : str or tuple
      name of the serving process.
      
    inputs : ndarray, dict, list, ...
      input data for the serving process.
      
    message_callback : func, optional
      callback that will be called when the server sends predict response message. The default is None.
    
    inprocess : bool
      if the serving process is not started then it will be started with inprocess or not

    Returns
    -------
    msg_data : list, dict, etc
      the result of the prediction/inference.

    """
    
    server_name = self.maybe_start_server(server_name, inprocess=inprocess)
    
    if server_name is None:
      return None
    
    self._increment_usage(server_name)
    
    msg_data = None # actually redundant
    if not self.is_parallel(server_name):      
      msg_data = self.simple_predict(server_name=server_name, inputs=inputs)
    else:
      # send the PREDICT message
      self.log.start_timer('remote_pred_' + server_name)
      self._send_message(
        server_name=server_name, # if this name is just a alias then the receiving process will figure that out
        command=SMConst.PREDICT,
        inputs=inputs,
        )
      # wait for the RESULT answer
      res = self._wait_for_result(server_name, message_callback=message_callback)
      self.log.stop_timer('remote_pred_' + server_name)
      if res is None:
        return None
      else:
        msg_type, msg_data = res
      if self.full_debug:
        self.P("Received from '{}' predict message '{}': {}".format(
          server_name, msg_type, type(msg_data)))
    
    self.collect_and_stop_idle_servers()
    return msg_data
  
  
  def server_runs_on_empty_input(self, server_name):
    svr = self._get_server(server_name)
    if svr is not None:
      # using svr.cfg_... is triky as the svr is initiated on different process
      # so we use the copy of config date received from parent
      runs_on_empty_input = svr.config_data['RUNS_ON_EMPTY_INPUT']
    else:
      runs_on_empty_input = None
      self.P("Attempted to call a inexisting server '{}'".format(
        self.get_server_config(server_name)
      ), color='r')
    return runs_on_empty_input
  
  def maybe_run_monitor(self):
    self._monitor_counter += 1
    if self._monitor_counter >= self._monitor_each_k_inferences:
      if self._monitor_callback is not None:
        self._monitor_callback()
      self._monitor_counter = 0
    return
  
  def check_blocked_inprocess_servers(self):
    """
    This method is designed for inprocesses only.
    Will monitor the `.predict` and force-throw a exception in main thread forcing 
    problematic models and simulating terminating a parallel process.
    """
    if self.inprocess_predict is None:
      return
    if not self.must_check_blocked_inprocess_serving:
      return
    elapsed = time() - self.inprocess_predict_start
    if elapsed > SMConst.MAX_INPROCESS_TIME:
      msg = "Forced inprocess server shutdown initiated for '{}' - predict blocked for {:.1}s > {:.1f}s".format(
        self.inprocess_predict,
        elapsed, SMConst.MAX_INPROCESS_TIME,
      )
      self.P(msg, color='error')
      self._create_notification(
        notif=ct.NOTIFICATION_TYPE.STATUS_EXCEPTION, 
        msg=msg,
        displayed=True,
      )
      ## Do the shutdown
      from naeural_core.utils.thread_raise import ctype_async_raise
      ctype_async_raise(self._main_tid, ValueError)
    # end must shutdown
    return
  
  
  def predict_parallel(self, dct_servers_inputs, inprocess=False):
    """
    Multi server predict

    Parameters
    ----------
    dct_servers_inputs : TYPE
      `server` : `inputs` dict.
      
      {
        'th_yolo_movenet' : [...]
        'th_iqa' : [...]
        ('th_second_stage', 'bascheti') : [...]
        ('th_second_stage', 'schiuri') : [...]
      }

    inprocess : TYPE, optional
      if a particular server is not running then start it with give `inprocess`. The default is False.

    Returns
    -------
    dct_resp : TYPE
      similar with the input, ie. a dict of `server:results` where results is a list (batch) of outputs 
      one output for each input (stream).

    """
    if self.log_timeouts_last_ts is None or time() - self.log_timeouts_last_ts > self.log_timeouts_period:
      self.log_timeouts_last_ts = time()
      msg_str = f"Serving servers timeouts: {self.total_timeouts}"
      details_str = '\n'.join([f"  {s_name}: {t_cnt}" for s_name, t_cnt in self._timeouts.items()])
      msg_str = msg_str + '\n' + details_str if len(details_str) > 0 else msg_str
      self.P(msg_str, color='y')
    #  endif log timeouts
    dct_resp = {}
    if not isinstance(dct_servers_inputs, dict):
      self.P("WARNING: parallel predict must receive a dict with at least one server and the associated inputs", color='r')
      return dct_resp

    ### disabled warnings in this case because in the main loop we can receive many warnings if no data is collected from the streams
    if len(dct_servers_inputs) == 0:
      self.collect_and_stop_idle_servers()
      return dct_resp

    self.log.start_timer('parallel_pred')
    
    self.log.start_timer('parallel_pred_filter_inputs')
    # this filtering has been moved from orchestrator >> {k:v for k,v in dct_servers_inputs.items() if len(v) > 0}
    dct_servers_inputs = {
      k:v for k,v in dct_servers_inputs.items() 
      if len(v) > 0 or self.server_runs_on_empty_input(k)
    }
    self.log.stop_timer('parallel_pred_filter_inputs')
    
    self.log.start_timer('parallel_pred_restart')

    self.owner.set_loop_stage('6.1.serve.run.parallel_pred_restart')
    # check if servers are online
    avail_servers = []
    for server_name in dct_servers_inputs:
      started = self.maybe_start_server(
        server_name,
        inprocess=inprocess
      )
      if started is not None:
        # while `started` can be different from `server_name` as in
        # `server_name == ('TH_MODEL','ONE')` and `started == 'TH_MODE_ONE'`
        # we must add upstream idenfier for upstream dict matching
        avail_servers.append(server_name)
        self._increment_usage(server_name)
    
    self.log.stop_timer('parallel_pred_restart')
    
    # start predicts: at this point we have to be carefull as if a server is (somehow) 
    # called more than once (separate entries in dct_servers_inputs and avail_servers)
    # we may have a crash at the first call that will invalidate the second call
    # thus we must make sure that we do not receive garabage from orchestrator !!!
    self.log.start_timer('parallel_pred_requests')

    for server_name in avail_servers:
      self.owner.set_loop_stage('6.1.serve.run.parallel_pred_requests.{}'.format(server_name))
      inputs = dct_servers_inputs[server_name]
      if self.is_parallel(server_name):
        self._send_message(
          server_name=server_name,
          command=SMConst.PREDICT,
          inputs=inputs,
        )
      else:
        self.log.start_timer('inprocess_pred_' + self.get_server_name(server_name))
        dct_resp[server_name] = self.simple_predict(
          server_name=server_name,
          inputs=inputs,
        )
        self.log.stop_timer('inprocess_pred_' + self.get_server_name(server_name))
    self.log.stop_timer('parallel_pred_requests')
    
    self.owner.set_loop_stage('6.1.serve.run.maybe_run_monitor')
    self.maybe_run_monitor()

    # collect answers if servers are not inprocess
    self.log.start_timer('parallel_pred_collect')
    self.owner.set_loop_stage('6.1.serve.run.parallel_pred_collect')
    for server_name in avail_servers:
      if not self.is_avail(server_name):
        # not available anymore!
        self.P("Serving process '{}' closed during predict".format(server_name), color='r')
        dct_resp[server_name] = None
        continue
      if self.is_parallel(server_name):
        # if parallel then sync check (wait) for result
        self.owner.set_loop_stage('6.1.serve.run.parallel_pred_collect.' + self.get_server_name(server_name))
        max_wait_time = self._server_wait_time(server_name)
        res = self._wait_for_result(
          server_name=server_name, 
          max_wait_time=max_wait_time,
        )
        dct_resp[server_name] = res[1] if res is not None else None
      else:
        # do nothing if not parallel as the result is already collected
        pass 
    self.log.stop_timer('parallel_pred_collect')
    # done all predicts
    
    self.owner.set_loop_stage('6.1.serve.run.stop_idle_servers')
    self.collect_and_stop_idle_servers(avail_servers)

    self.log.stop_timer('parallel_pred')
    return dct_resp
  
  
  def get_predict_parallel_time(self):
    if 'parallel_pred' in self.log.timers:
      return self.log.timers['parallel_pred']['MEAN']
    return None
  
  
  
  def collect_and_stop_idle_servers(self, last_servers_run=None, force_collection=False):
    if not force_collection:
      if self._collector_call_counter < self._server_collector_delay:
        self._collector_call_counter += 1
        return
      else:
        self._collector_call_counter = 0
      
    self.log.start_timer('idle_shutdown')
    current_servers = list(self._servers.keys())
    for server_name in current_servers:
      if server_name not in self._servers:
        # already deleted within the loop :)
        continue
      if self._servers[server_name].get(SMConst.COVERED_BY) is not None:
        # this is a alias server covered by someone else
        continue
      count = self._servers[server_name][SMConst.PCOUNT]
      last_time = self._servers[server_name][SMConst.PTIME]
      delay = time() - last_time
      if delay > self._server_collector_timedelta:
        msg1 = "'{}' server forced shutdown initiated".format(server_name)          
        msg2 = "WARNING: Serving process '{}' idle {:.1f}s > {}s (usage: {}). {}Shutting down...".format(
          server_name, delay, self._server_collector_timedelta, count,
          "Last `parallel_predict` on {}. ".format(self.log.time_to_str(last_time)), # if last_servers_run is not None else '',
          )
        self.P(msg2, color='r')
        if self._server_collector_timedelta < 3600:
          msg1 = msg1 + ' - SERVER_COLLECTOR_TIMEDELTA:{} may be too low!'.format(self._server_collector_timedelta)
          self.P("  " + msg1, color='r')
        self._create_notification(
          notif=ct.NOTIFICATION_TYPE.STATUS_EXCEPTION, 
          msg=msg1, 
          info=msg2,
        )
        self.get_active_servers(show=True)
        self.stop_server(server_name)
        self.show_servers('Post server-cleanup serving process status:')
    self.log.stop_timer('idle_shutdown')
    return


  def keep_active_servers(self, active_server_names, inprocess=True):
    active_server_names = [self.get_server_name(x) for x in active_server_names]
    
    lst_in_use = self.get_active_server_names()
    lst_start = list(set(active_server_names) - set(lst_in_use))
    lst_stop = list(set(lst_in_use) - set(active_server_names))
    
    if lst_start:
      self.P('Found {} new graphs to start: {}'.format(len(lst_start), lst_start))
      for name in lst_start:
        self.start_server(
          server_name=name,
          inprocess=inprocess
          )
      
    if lst_stop:
      self.P('Found {} graphs to deallocate: {}'.format(len(lst_stop), lst_stop))
      for name in lst_stop:
        self.stop_server(name)
    return
  
  def get_active_server_names(self):
    return list(self._servers.keys())

  def get_active_servers(self, show=True, color='b'):
    res = []
    lines = []
    if show:
      self.P("Analysing current active serving processes...", color=color)
      if len(self._servers) == 0:
        self.P("  -- No active servers running --", color=color)
      else:
        lines = ["==== List of active serving processes ===="]
    
    server_names = list(self._servers.keys())    
    for server_name in server_names:
      count = self._servers[server_name].get(SMConst.PCOUNT, -1)
      covered_by = self._servers[server_name].get(SMConst.COVERED_BY)
      last_time = self._servers[server_name].get(SMConst.PTIME, 0)
      when_created = self._servers[server_name].get(SMConst.CREATED_DATE, "")
      str_last_run_gmt = strftime('%H:%M:%S', gmtime(last_time))
      str_last_run_local = self.log.time_to_str(last_time)
      str_last_run = '{} / GMT: {}'.format(str_last_run_local, str_last_run_gmt)
      idle_time = (time() - last_time) if last_time > 0 else -1
      svr_name = self._get_server(server_name).__class__.__name__
      if covered_by is None:
        res.append(OrderedDict({
          'Name (ID)' : server_name,
          'Server'    : svr_name,
          'Created'   : when_created,
          'Last run'  : str_last_run_local,
          'Idle (s)'  : round(idle_time, 1),
          'Run count' : count,
          'Inprocess' : self._get_server(server_name).inprocess,
          })
        )
      if show:
        lines.append("Server '{}':{}".format(
          server_name, " covered by {}:{}".format(
            covered_by, 
            self._servers[covered_by][SMConst.SERVER] if covered_by in self._servers else '<deallocated>'
            ) if covered_by is not None else '',
          ))
        lines.append("  Process:  {}".format(svr_name))
        lines.append("  Created:  {}".format(when_created))
        lines.append("  Last run: {}".format(str_last_run))
        lines.append("  Run cnt:  {}".format(count))
        lines.append("  Idle/Max: {:.0f}s/{:.0f}s".format(idle_time, self._server_collector_timedelta))
        self.P("\n".join(lines), color=color)
        lines = []
    return res
  
  def show_servers(self, title="Current servers:", color='b'):
    lst_log = [title]
    if len(self._servers) == 0:
      lst_log.append("  -- No servers running to show --")
    for svr in self._servers:
      lst_log.append("  {}:".format(svr))
      for k,v in self._servers[svr].items():
        lst_log.append("    {:<15}{}".format(k + ':', v))
    full_log = "\n".join(["    " + x for x in lst_log])
    self.P(full_log.lstrip(), color=color)
    return


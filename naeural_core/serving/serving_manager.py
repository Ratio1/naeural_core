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
import shutil
import threading
import traceback
from uuid import uuid4


from time import sleep, time, strftime, gmtime, monotonic
from collections import OrderedDict, defaultdict, deque

# os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

from naeural_core import constants as ct
from naeural_core.utils.system_shared_memory import NumpySharedMemory, replace_ndarray_with_shm, replace_shm_with_ndarray
from naeural_core.utils import shm_diagnostics as shm_diag

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
    # Allocation identities must survive neither a worker restart nor a new
    # manager session. Logical model names remain unchanged for routing.
    self._shm_session = uuid4().hex
    self._shm_generation = 0
    self._shm_created = 0
    self._shm_cleanup_attempts = 0
    self._shm_failures = deque(maxlen=32)
    self._shm_failure_count = 0
    self._shm_failure_log_ts = None
    self._shm_legacy_names = OrderedDict()
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
    """Initialize serving and its nonblocking, process-local diagnostics.

    Returns
    -------
    None
      Start the diagnostic sink before workers or inference can submit events.
    """
    super().startup()
    shm_diag.start(self.log)
    self.P("Initialising {} with following environment:\n{}".format(
      self.__class__.__name__,
      self.log.dict_pretty_format(self.cfg_serving_environment)
    ), color='b')
    # Observe leftovers even when startup has no requested physical workers.
    self.P('\n'.join(self._shm_inventory_lines()), color='b')
    self._create_servers()
    return

  def get_diagnostic_server_name(self, server_name):
    """Resolve an existing handle to the actual worker for observation only.

    Parameters
    ----------
    server_name : str or tuple
      Existing serving handle; routing and inference keys are unchanged.

    Returns
    -------
    str
      Actual registered worker name, or the canonical requested handle.
    """
    requested = self.get_server_name(server_name)
    entry = self._servers.get(requested, {})
    return shm_diag.serving_target(requested, entry.get(SMConst.SERVER),
                                  entry.get(SMConst.COVERED_BY))

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
    configured_cuda = self.cfg_serving_environment.get(ct.DEFAULT_CUDA, 'cuda:0')
    gpu_info = self.log.gpu_info()
    if gpu_info is None or len(gpu_info) == 0:
      return 'cpu'
    return configured_cuda
  
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
    """Resolve and load a custom downloadable serving configuration.

    Parameters
    ----------
    url : str
        Source location for the configuration payload. Supports the existing
        logger-managed schemes plus ``r1fs:<CID>``.
    server_name : str
        Serving signature used to derive the cached local definition filename.
    force_download : bool, optional
        Preserved logger download flag for non-R1FS sources.

    Returns
    -------
    dict
        Parsed serving configuration dictionary.
    """

    fn_custom_model_definition = server_name + '_def.txt'
    if isinstance(url, str) and url.lower().startswith("r1fs:"):
      cid = url.split(":", 1)[1].strip()
      r1fs_engine = self.shmem.get(ct.R1FS_ENGINE)
      if r1fs_engine is None:
        raise RuntimeError("R1FS engine missing from shared memory for custom model definition download")

      source_path = r1fs_engine.get_file(cid=cid)
      if source_path is None or not os.path.isfile(source_path):
        raise RuntimeError("Failed to resolve R1FS custom model definition '{}'".format(url))

      target_path = os.path.join(self.log.get_models_folder(), fn_custom_model_definition)
      target_dir = os.path.dirname(target_path)
      if target_dir != "":
        os.makedirs(target_dir, exist_ok=True)

      # The serving manager consumes the fetched definition immediately, and
      # `get_file()` recreates the CID folder on the next access, so relocating
      # the artifact avoids keeping duplicate copies of the same file.
      shutil.move(source_path, target_path)
    else:
      model_zoo_kwargs = self.cfg_serving_environment.get(ct.MODEL_ZOO_CONFIG) or {}
      self.log.maybe_download_model(
        url, fn_custom_model_definition, force_download=force_download,
        **model_zoo_kwargs
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

  def _record_shm_failure(self, server_name, phase, error_type, segment=None):
    """Retain bounded lifecycle evidence without replacing a runtime failure.

    Parameters
    ----------
    server_name, phase, error_type : str
      Logical worker, failed operation and exception class; never raw payloads.
    segment : str or None
      Assigned allocation identity, when known. Repeated failures are summarized
      immediately at most once per ten seconds and at the normal model dump.
    """
    record = dict(server=str(server_name)[:96], phase=phase,
                  error_type=error_type, segment=segment, time=time())
    self._shm_failures.append(record)
    self._shm_failure_count += 1
    now = monotonic()
    if self._shm_failure_log_ts is None or now - self._shm_failure_log_ts >= 10:
      self._shm_failure_log_ts = now
      try:
        self.P("SHM lifecycle failure: {} (total={})".format(
          record, self._shm_failure_count), color='error')
      except Exception:
        # A failed diagnostic sink must not prevent the remaining cleanup.
        pass

  def _shm_summary_lines(self, npy_shm, context):
    """Describe one existing allocation using metadata, never image contents.

    Parameters
    ----------
    npy_shm : NumpySharedMemory
      The physical worker's allocation; this function never opens a segment.
    context : str
      ``startup``, ``after_collection`` or a non-quiescent ``snapshot``.

    Returns
    -------
    list[str]
      Compact log-only fields. Nonzero occupancy is suspicious only after
      collection; neither the flag count nor this sample proves ownership.
    """
    occupied = shm_diag.occupancy_observation(npy_shm)
    free = None if occupied is None else npy_shm.maxlen - occupied
    data_bytes = getattr(npy_shm._shm, 'size', None)
    control_bytes = getattr(npy_shm._shm_additional, 'size', None)
    expected_control = npy_shm.maxlen * (1 + npy_shm.shape_offset) * 4
    residual = occupied if context == 'after_collection' else 'unchecked'
    return [
      "  SHM: initialized={} role={} segment={} control={}_additional".format(
        npy_shm.initialized, npy_shm.instance_type, npy_shm._mem_name, npy_shm._mem_name),
      "  SHM slots: capacity={} occupied={} free={} context={} suspected_residual_slots={}".format(
        npy_shm.maxlen, occupied, free, context, residual),
      "  SHM bytes (capacity, not RSS): data={}/{} control={}/{} (actual/expected)".format(
        data_bytes, int(npy_shm._np_bytes), control_bytes, expected_control),
    ]

  def _shm_inventory_lines(self):
    """Inspect a bounded Linux namespace without attaching or reclaiming it.

    Returns
    -------
    list[str]
      Lifecycle counts, recent failures and at most sixteen possible leftover
      names. At most 256 directory entries are inspected. Unknown names may
      belong to another live manager; incomplete visibility is explicit.
    """
    owned = set()
    for entry in self._servers.values():
      shm = entry.get(SMConst.SHM)
      if shm is not None:
        owned.update((shm._mem_name, shm._mem_name + '_additional'))
    legacy = set(self._shm_legacy_names)
    legacy.update(name + '_additional' for name in self._shm_legacy_names)
    candidates, count, total_bytes, truncated, errors = [], 0, 0, False, 0
    status = 'complete'
    try:
      with os.scandir('/dev/shm') as entries:
        for index, entry in enumerate(entries):
          if index == 256:
            truncated = True
            break
          if entry.name in owned or not (entry.name.startswith('r1s_') or entry.name in legacy):
            continue
          try:
            info = entry.stat(follow_symlinks=False)
          except OSError:
            errors += 1
            continue
          count += 1
          total_bytes += info.st_size
          if len(candidates) < 16:
            candidates.append("{}:{}B".format(entry.name, info.st_size))
    except OSError as exc:
      status = 'unavailable:' + type(exc).__name__
    if status == 'complete' and (truncated or errors):
      status = 'incomplete'
    # A failed directory read is unknown visibility, never a healthy zero.
    visible_count = count if not status.startswith('unavailable:') else 'unknown'
    visible_bytes = total_bytes if not status.startswith('unavailable:') else 'unknown'
    lines = [
      "SHM lifecycle: created_pairs={} cleanup_attempts={} failures={} retained={} dropped={}".format(
        self._shm_created, self._shm_cleanup_attempts, self._shm_failure_count,
        len(self._shm_failures), max(0, self._shm_failure_count - len(self._shm_failures))),
      "SHM namespace: status={} scanned_limit=256 truncated={} stat_errors={} possible_leftovers={} sampled_bytes={} shown={}".format(
        status, truncated, errors, visible_count, visible_bytes, len(candidates)),
    ]
    if candidates:
      lines.append("  Possible leftovers (ownership unproven): " + ', '.join(candidates))
    for failure in list(self._shm_failures)[-8:]:
      lines.append("  Recent SHM failure: {}".format(failure))
    return lines

  def _create_serving_shm(self, server_name, inprocess, comm_method):
    """Allocate a fresh worker generation or choose pipe before child startup.

    Parameters
    ----------
    server_name : str
      Logical model identity, including its configured instance.
    inprocess : bool
      Local models do not need or own IPC image storage.
    comm_method : str
      Existing configured transport choice.

    Returns
    -------
    tuple
      Allocation, exact child attachment kwargs, and effective transport.
      Only name collisions receive up to three fresh-identity attempts.
    """
    self._shm_legacy_names[server_name] = None
    self._shm_legacy_names.move_to_end(server_name)
    if len(self._shm_legacy_names) > 64:
      self._shm_legacy_names.popitem(last=False)
    if inprocess or comm_method == 'pipe':
      return None, None, comm_method
    self.P('\n'.join(self._shm_inventory_lines()))
    kwargs = dict(mem_size=0,
                  np_shape=self.cfg_serving_environment.get('SHM_MAX_IMAGE_SHAPE', ct.SERVING.SHM_IMG_MAX_SHAPE),
                  np_type='uint8', is_buffer=True,
                  maxlen=max(ct.SERVING.SHM_MAX_LEN,
                             self.cfg_serving_environment.get('SHM_MAX_LEN', ct.SERVING.SHM_MAX_LEN)))
    model = ''.join(c if c.isascii() and c.isalnum() else '_' for c in server_name)[:48]
    for _ in range(3):
      self._shm_generation += 1
      kwargs['mem_name'] = 'r1s_{}_{}_{}'.format(self._shm_session, self._shm_generation, model)
      shm = NumpySharedMemory(**kwargs, create=True, log=self.log)
      try:
        if shm.initialized:
          self._shm_created += 1
          occupied = shm_diag.occupancy_observation(shm)
          expected = (int(shm._np_bytes), shm.maxlen * (1 + shm.shape_offset) * 4)
          actual = (shm._shm.size, shm._shm_additional.size)
          # POSIX objects keep their declared file lengths; Windows may round
          # mapping sizes to page granularity. Neither backend may be undersized.
          size_valid = actual == expected if os.name == 'posix' else all(
            observed >= required for observed, required in zip(actual, expected))
          if occupied == 0 and size_valid:
            self.P('\n'.join(self._shm_summary_lines(shm, 'startup')))
            return shm, kwargs, comm_method
          error_type = 'UnexpectedStartupSize' if not size_valid else 'UnexpectedStartupOccupancy'
        else:
          error_type = shm.error_type
      except BaseException:
        self._release_server_resources(server_name, None, (), shm)
        raise
      self._record_shm_failure(server_name, 'create', error_type, kwargs['mem_name'])
      self._release_server_resources(server_name, None, (), shm)
      if error_type != 'FileExistsError':
        break
    self.P("SHM unavailable for '{}'; using pipe before worker startup".format(server_name), color='error')
    return None, None, 'pipe'

  def _release_server_resources(self, server_name, server, comm_engs, npy_shm,
                                wait=False, terminate=False):
    """Attempt independent cleanup steps and retain the first runtime error.

    Parameters
    ----------
    server_name : str
      Logical owner of these resources.
    server : serving process or None
      May be a constructed process that has not started yet.
    comm_engs : iterable
      Parent-owned communication endpoints to close.
    npy_shm : NumpySharedMemory or None
      Only this allocation can be closed/unlinked by the utility's owner rules.
    wait, terminate : bool
      Existing bounded process-stop behavior; cleanup proceeds if it fails.

    Returns
    -------
    BaseException or None
      First failure for the caller to propagate, unless it is already handling
      an earlier error. Subsequent errors remain visible in bounded diagnostics.
    """
    first_error = None
    segment = None if npy_shm is None else npy_shm._mem_name
    if server is not None:
      try:
        if server.inprocess:
          server.shutdown()
        elif server.pid is not None:
          if terminate and server.is_alive():
            server.terminate()
          if wait:
            server.join(5)
            if server.exitcode is None:
              server.kill()
              server.join(5)
              if server.is_alive():
                raise RuntimeError('Serving process remains alive after kill')
      except BaseException as exc:
        first_error = exc
        self._record_shm_failure(server_name, 'process_cleanup', type(exc).__name__, segment)
    for comm in comm_engs:
      if comm is None:
        continue
      try:
        comm.close()
      except BaseException as exc:
        if first_error is None:
          first_error = exc
        self._record_shm_failure(server_name, 'comm_close', type(exc).__name__, segment)
    if npy_shm is not None:
      self._shm_cleanup_attempts += 1
      previous_errors = len(npy_shm.cleanup_errors) + npy_shm.cleanup_errors_dropped
      try:
        npy_shm.shutdown()
      except BaseException as exc:
        if first_error is None:
          first_error = exc
        self._record_shm_failure(server_name, 'shm_shutdown', type(exc).__name__, segment)
      for error in npy_shm.cleanup_errors:
        self._record_shm_failure(server_name, error['operation'], error['exception_type'], error['segment'])
      # Historical failed closes remain diagnostic evidence even if this retry
      # succeeded; they must not turn a successful retry into a fresh failure.
      current_errors = len(npy_shm.cleanup_errors) + npy_shm.cleanup_errors_dropped
      if current_errors > previous_errors and first_error is None:
        first_error = RuntimeError('Shared memory cleanup incomplete')
    return first_error

  def _server_wait_time(self, server_name):
    server_name = self.get_server_name(server_name)
    if server_name not in self._servers:
      return SMConst.MAX_WAIT_TIME_DEFAULT
    _server = self._servers[server_name][SMConst.SERVER]
    # TODO: move this to received config 
    wait_time = _server.CONFIG[SMConst.MAX_WAIT_TIME_KEY]
    return wait_time

  def _create_server(self, server_name, server_class_name=None, upstream_config=None, inprocess=False):
    """Start and register a worker while retaining ownership until success.

    Parameters
    ----------
    server_name, server_class_name : str
      Logical instance identity and optional implementation class.
    upstream_config : dict or None
      Existing model startup overrides.
    inprocess : bool
      Run locally without allocating an IPC image buffer.

    Returns
    -------
    str or None
      Registered identity on success, or None for a failed worker handshake.
      Runtime exceptions propagate after all owned cleanup has been attempted.
    """
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
      max_tries = 3
      sleep_period = 5
      download_success = False
      current_exception = None
      dct_custom_config = None
      for try_idx in range(max_tries):
        try:
          dct_custom_config = self._download_and_load_config(config_url, server_name)
          download_success = dct_custom_config is not None
        except Exception as e:
          current_exception = traceback.format_exc()
        # endtry-except
        if not download_success:
          err_msg = (
            f"Failed to load CUSTOM_DOWNLOADABLE_MODEL '{server_name}' from URL '{config_url}'. "
            f"\nMaybe check URL. Attempt {try_idx + 1} of {max_tries}."
          )
          if current_exception is not None:
            err_msg += f"\nException: {current_exception}"
          if try_idx < max_tries - 1:
            err_msg += f"\nRetrying in {sleep_period} seconds..."
          self.P(err_msg, color='r')
          sleep(sleep_period)
        else:
          download_success = True
          break
      # endfor try_idx
      if not download_success:
        self.log.stop_timer('create_server')
        return None
      # endif download_success

      # now merge the dicts
      for k,v in dct_custom_config.items():
        _default_config[k] = v
    # end CUSTOM_DOWNLOADABLE_MODEL

    # The manager owns startup resources until the entire registration path
    # succeeds. Every failure, including constructor/start/READY/config errors,
    # reaches the same independent cleanup steps.
    npy_shm, _server = None, None
    startup_complete, failed_with_exception = False, False
    try:
      _version = _default_config.get("MODULE_VERSION", "0.0.0") if isinstance(_default_config, dict) else "0.0"
      self.log.start_timer('create_server_new()')
      self.owner.set_loop_stage('3.serving.start.{}.shm_starting'.format(server_name))
      self.log.start_timer('create_server_shm_start()')
      npy_shm, npy_shm_kwargs, comm_method = self._create_serving_shm(
        server_name, inprocess, self.get_comm_method())
      self.log.stop_timer('create_server_shm_start()')
      _server = _Class(
        server_name=server_name,
        comm_eng=comm_client,
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

      if _server.inprocess:
        self.P("Model '{}' running in the current process without SHM".format(server_name), color='y')
        self._servers[server_name] = {
          SMConst.SERVER: _server,
          SMConst.COMM: comm_server,
          SMConst.PID: os.getpid(),
          SMConst.CREATED_DATE: self.log.now_str(nice_print=True, short=True),
        }
      else:
        self.owner.set_loop_stage('3.serving.start.{}.starting'.format(server_name))
        self.log.start_timer('create_server_start()')
        self.P("Running process {}...".format(_server))
        _server.daemon = True
        _server.start()
        sleep(2)
        self.owner.set_loop_stage('3.serving.start.{}.postsleep'.format(server_name))
        if _server.exitcode is not None:
          self.P("Serving process '{}' ended with code {}".format(server_name, _server.exitcode), color='r')
          self._record_shm_failure(server_name, 'worker_startup', 'EarlyWorkerExit',
                                   None if npy_shm is None else npy_shm._mem_name)
          return None
        self.P("Serving process '{}' is running with PID {}".format(server_name, _server.pid))
        self.owner.set_loop_stage('3.serving.start.{}.wait'.format(server_name), is_dangerous=False)
        result = self._wait_for_result(
          server_name=server_name, comm_eng=comm_server, max_wait_time=float('inf'))
        self.owner.set_loop_stage('3.serving.start.{}.received'.format(server_name))
        msg_type, msg_data = result if result is not None else (None, None)
        if not isinstance(msg_type, str) or msg_type.upper() != SMConst.READY:
          self._show_serving_error(server_name, result)
          self._record_shm_failure(server_name, 'worker_startup', 'WorkerNotReady',
                                   None if npy_shm is None else npy_shm._mem_name)
          return None
        self.P("Received '{}' from '{}': {}".format(msg_type, server_name, msg_data), color='g')
        self._servers[server_name] = {
          SMConst.SERVER: _server,
          SMConst.COMM: comm_server,
          SMConst.SHM: npy_shm,
          SMConst.PID: _server.pid,
          SMConst.CREATED_DATE: self.log.now_str(nice_print=True, short=False),
        }
        _server.config_model = self.get_server_config(server_name)
        _server.config_data = _server.config_model
        self.log.stop_timer('create_server_start()')

      self.log.start_timer('create_server_parents()')
      similar_processes = _server.get_server_parents()
      covered_servers = _default_config.get(ct.COVERED_SERVERS, [])
      if server_class_name != server_name:
        covered_servers.append(server_class_name)
      self._maybe_complete_servers(server_name, similar_processes, covered_servers)
      self._reset_usage(server_name)
      self.P("Server '{}' successfully created.".format(server_name), color='g')
      self.show_servers()
      self.log.stop_timer('create_server_parents()')
      self.log.stop_timer('create_server')
      startup_complete = True
      return server_name
    except BaseException as exc:
      failed_with_exception = True
      self._record_shm_failure(server_name, 'worker_startup', type(exc).__name__,
                               None if npy_shm is None else npy_shm._mem_name)
      raise
    finally:
      if not startup_complete:
        cleanup_error = self._release_server_resources(
          server_name, _server, (comm_server, comm_client), npy_shm,
          wait=True, terminate=True)
        # A failed config/alias setup may already have installed registry entries.
        # Remove only entries belonging to this failed physical worker.
        for name, entry in list(self._servers.items()):
          if _server is not None and entry.get(SMConst.SERVER) is _server:
            del self._servers[name]
        shm_diag.clear_requests(server_name)
        if cleanup_error is not None and not failed_with_exception:
          raise cleanup_error

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
    """Submit the existing request and retain bounded SHM admission evidence.

    Parameters
    ----------
    server_name : str or tuple
      Existing serving handle.
    command : str
      Existing IPC command type.
    inputs : object
      Request data; diagnostic counters are local and never enter the envelope.

    Raises
    ------
    ValueError
      Existing send failure; conversion errors retain their original type.
    """
    server_name = self.get_server_name(server_name)
    self.__cmd_count += 1
    shm = self._servers[server_name].get(SMConst.SHM)
    target = self.get_diagnostic_server_name(server_name)
    trace = shm_diag.request_observation(inputs, shm)
    if trace is not None:
      trace["requested_server"] = server_name
      entry = self._servers[server_name]
      configuration_entry = self._servers.get(target, entry)
      shm_diag.configuration_observation(self.log, target, server_name,
                                         configuration_entry.get(SMConst.SERVER),
                                         configuration_entry.get(SMConst.SHM),
                                         configuration_entry.get(SMConst.COMM))
    if self._servers[server_name].get(SMConst.SHM) is not None:
      self.log.start_timer('replace_ndarray_with_shm')
      try:
        processed_inputs = replace_ndarray_with_shm(inputs, shm, debug=self.full_debug, trace=trace)
      except BaseException as exc:
        if trace is not None:
          shm_diag.observe(self.log, target, "send", self.__cmd_count,
                           **trace, attempted=False, completed=False,
                           exception_type=type(exc).__name__)
        raise
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
      if trace is not None:
        # Count slots only after an already-observed admission failure. Healthy
        # requests reuse conversion counters instead of rescanning shared flags.
        if trace.get('missing'):
          trace['occupied_after'] = shm_diag.occupancy_observation(shm)
        shm_diag.remember_request(target, self.__cmd_count, command, server_name, inputs,
                                  observation=trace)
      comm_eng.send(dct_cmd)
      self.log.stop_timer('pipe_send_message')
      if trace is not None:
        shm_diag.observe(self.log, target, "send", self.__cmd_count,
                         **trace, attempted=True, completed=True)
      if self.full_debug or command != SMConst.PREDICT:
        self.P('  Command {}:{} sent to {}'.format(command, self.__cmd_count, server_name), color='y')
        
    except BaseException as exc:
      if trace is not None:
        shm_diag.clear_requests(target, self.__cmd_count)
        shm_diag.observe(self.log, target, "send", self.__cmd_count,
                         **trace, attempted=True, completed=False,
                         exception_type=type(exc).__name__)
      self.log.stop_timer('pipe_send_message') # double stop due to re-raise
      self.P("Error while sending command {} to server {}:{}".format(
         command, server_name, traceback.format_exc()), color='r')
      self.show_servers()
      raise ValueError('Comm pipe exception!')
    return
  
  
  def _wait_for_comm_message(self, server_name, comm_eng=None, max_wait_time=None):
    """Poll/receive once and observe returned result cardinality.

    Parameters
    ----------
    server_name : str or tuple
      Existing serving handle.
    comm_eng : object or None
      Existing transport, or resolve it from the server registry.
    max_wait_time : float
      Required existing poll timeout; an empty poll is not diagnosed here.

    Returns
    -------
    object or None
      Original IPC envelope, unchanged. Transport exceptions are re-raised.
    """
    assert max_wait_time is not None, "ERROR: max_wait_time != None is required"
    server_name = self.get_server_name(server_name)
    target = self.get_diagnostic_server_name(server_name)
    if comm_eng is None:
      comm_eng = self._servers[server_name][SMConst.COMM]
    try:
      if max_wait_time is None:
        # we wait until we receive the answer
        payload = comm_eng.recv()
      else:
        # now we wait only `max_wait_time` seconds for the result
        has_payload = comm_eng.poll(max_wait_time)
        payload = None
        if has_payload:
          payload = comm_eng.recv()
    except BaseException as exc:
      shm_diag.observe(self.log, target, "result", correlated=False,
                       requested_server=server_name, exception_type=type(exc).__name__)
      raise
    try:
      # Even the observation predicate can invoke user-defined equality;
      # isolate it so diagnostics cannot change the original result path.
      if not isinstance(payload, dict) or payload.get(SMConst.TYPE) != SMConst.RESULT:
        return payload
      fields = shm_diag.result_observation(target, payload, server_name)
      shm_diag.observe(
        self.log, target, "result", payload.get(SMConst.CMD_ID),
        requested_server=server_name, **fields,
      )
    except Exception:
      shm_diag.observe(self.log, target, "result", observation_error=True, correlated=False,
                       requested_server=server_name)
    return payload
  
      
  def _cleanup_server(self, server_name, wait=False, terminate=False):
    """Retire one physical worker and attempt all of its owned cleanup steps.

    Parameters
    ----------
    server_name : str
      Physical worker identity; covered aliases cannot own cleanup.
    wait, terminate : bool
      Preserve the existing bounded process stop policy. Communication and SHM
      cleanup still run when process teardown raises, and the first error is
      propagated after retiring the registry entries.
    """
    server_name = self.get_server_name(server_name)
    entry = self._servers.get(server_name)
    if entry is None:
      return
    covered_by = entry.get(SMConst.COVERED_BY)
    if covered_by is not None:
      self.P("Attempted to cleanup '{}' covered by '{}'".format(server_name, covered_by), color='r')
      return
    self.P("Proceeding to serving process '{}' cleanup...".format(server_name), color='m')
    error = self._release_server_resources(
      server_name, entry[SMConst.SERVER], (entry.get(SMConst.COMM),),
      entry.get(SMConst.SHM), wait=wait, terminate=terminate)
    del self._servers[server_name]
    shm_diag.clear_requests(server_name)
    for name, candidate in list(self._servers.items()):
      if candidate.get(SMConst.COVERED_BY) == server_name:
        del self._servers[name]
    if error is not None:
      raise error

  def _get_server(self, server_name):
    server_name = self.get_server_name(server_name)
    return self._servers[server_name][SMConst.SERVER] if server_name in self._servers else None

  def _close_server_on_null(self, server_name, max_wait_time=None):
    """Keep existing response-timeout cleanup and add an uncorrelated record.

    Parameters
    ----------
    server_name : str or tuple
      Timed-out serving handle.
    max_wait_time : float or None
      Existing response deadline; None resolves the configured value.
    """
    requested = self.get_server_name(server_name)
    target = self.get_diagnostic_server_name(requested)
    try:
      entry = self._servers.get(requested, {})
      worker, shm = entry.get(SMConst.SERVER), entry.get(SMConst.SHM)
      fields = shm_diag.pending_observation(target, requested)
      fields.update(shm_diag.request_observation(None, shm))
      occupied = shm_diag.occupancy_observation(shm)
      fields["occupied_before"] = occupied
      fields.update(requested_server=requested, worker_pid=entry.get(SMConst.PID),
                    worker_alive=worker.is_alive() if worker is not None else None,
                    worker_exitcode=getattr(worker, "exitcode", None),
                    free_slots=shm.maxlen - occupied if occupied is not None else None)
      shm_diag.observe(self.log, target, "timeout", correlated=False,
                       reason="response_timeout", **fields)
    except Exception:
      shm_diag.observe(self.log, target, "timeout", correlated=False,
                       reason="response_timeout", observation_error=True,
                       requested_server=requested)
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
    shm_diag.clear_requests(target)
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
          # TODO: should this be a fatal error or should the node keep working?
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
    """Stop a physical worker without allowing a failed STOP to bypass cleanup.

    Parameters
    ----------
    server_name : str
      Serving identity. A STOP failure is preserved after cleanup; secondary
      cleanup failures are retained separately in the bounded SHM summary.
    """
    server_name = self.get_server_name(server_name)
    server = self._get_server(server_name)
    if server is None:
      return
    covered_by = self.is_covered_by(server_name)
    if covered_by is not None:
      self.P("Serving process {} is covered by {}. Aborting shutdown".format(
        server_name, covered_by), color='m')
      return
    try:
      if not server.inprocess:
        self.P("Stopping serving process '{}' (PID:{})...".format(server_name, server.pid), color='y')
        self._send_message(server_name=server_name, command=SMConst.STOP, inputs=None)
    except BaseException as exc:
      self._record_shm_failure(server_name, 'stop_send', type(exc).__name__)
      try:
        self._cleanup_server(server_name, wait=True, terminate=True)
      except BaseException:
        # Cleanup records its own failure; preserve the original STOP exception.
        pass
      raise
    else:
      self._cleanup_server(server_name, wait=True)
    if not server.inprocess:
      self.P("Serving process '{}' stopped and resources cleaned.".format(server_name), color='y')

  def stop_all_servers(self):
    """Attempt every physical worker's cleanup before propagating failure.

    Raises
    ------
    BaseException
      The first worker teardown failure, after all remaining workers receive
      their own cleanup attempt. Covered aliases never own extra resources.
    """
    self.P("Stopping all serving processes...", color='y')
    self.get_active_servers(show=True)
    gpu_info1 = self.log.gpu_info()
    if gpu_info1 is not None and len(gpu_info1) > 0:
      self.P("GPU0 free mem before shutdown: {} GB".format(gpu_info1[0]['FREE_MEM']), color='g')
    server_names = [name for name, entry in self._servers.items()
                    if entry.get(SMConst.COVERED_BY) is None]
    first_error = None
    for server_name in server_names:
      try:
        self.stop_server(server_name)
      except BaseException as exc:
        # stop_server has already attempted this worker's owned cleanup. A
        # failed STOP or close must not strand the next worker's allocation.
        if first_error is None:
          first_error = exc
    if first_error is not None:
      raise first_error
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

  def get_active_servers(self, show=True, color='b', shm_context='snapshot'):
    """Return unchanged heartbeat rows and optionally print serving/SHM state.

    Parameters
    ----------
    show : bool
      Only printed summaries inspect SHM metadata and the bounded namespace.
      The asynchronous heartbeat path passes False and performs neither scan.
    color : str
      Existing logger color for the summary.
    shm_context : str
      ``after_collection`` is supplied only by the orchestrator's post-inference
      timer dump. Other callers cannot infer leaks from in-flight occupancy.

    Returns
    -------
    list[OrderedDict]
      Existing public heartbeat schema, one row per physical worker. Additional
      diagnostics remain log-only and never inspect image contents or issue IPC.
    """
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
        if covered_by is not None:
          lines.append("  SHM: covered handle; request transport=pipe; allocation reported under {}".format(covered_by))
        elif self._get_server(server_name).inprocess:
          lines.append("  SHM: transport=inprocess; no allocation")
        else:
          shm = self._servers[server_name].get(SMConst.SHM)
          if shm is None:
            lines.append("  SHM: transport=pipe; no allocation")
          else:
            lines.extend(self._shm_summary_lines(shm, shm_context))
        self.P("\n".join(lines), color=color)
        lines = []
    if show:
      self.P('\n'.join(self._shm_inventory_lines()), color=color)
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

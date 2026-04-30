import json
import gc
import os
import sys

import traceback

from collections import OrderedDict

from time import time, sleep, perf_counter
from threading import Event, Lock, Thread
from queue import Queue, Empty, Full
from copy import deepcopy
from naeural_core import constants as ct
from naeural_core import Logger
from naeural_core.manager import Manager

from collections import deque, defaultdict

class BusinessManager(Manager):

  def __init__(self, log : Logger, owner, shmem, environment_variables=None, run_on_threads=True, **kwargs):
    """Initialize business-plugin runtime state.

    Parameters
    ----------
    log : Logger
      Runtime logger.
    owner : object
      Owning orchestrator-like object.
    shmem : dict
      Shared-memory dictionary used across managers and plugins.
    environment_variables : dict or None, optional
      Environment variables exposed to business plugins.
    run_on_threads : bool, optional
      Whether business plugins execute on dedicated threads.
    **kwargs
      Additional manager initialization arguments.
    """
    self.shmem = shmem
    self.shmem['get_active_plugins_instances'] = self.get_active_plugins_instances
    self.plugins_shmem = {}
    self.owner = owner
    self.__netmon_instance = None
    self._dct_config_streams = None
    self.__dauth_hash = None
    self.is_supervisor_node = self.owner.is_supervisor_node
    self.__evm_network = self.owner.evm_network
    self.shmem['is_supervisor_node'] = self.is_supervisor_node
    self.shmem['__evm_network'] = self.__evm_network
    self.comm_shared_memory = {
      'payloads' : {},
      'commands' : {},
    }
    self._run_on_threads = run_on_threads
    self._environment_variables = environment_variables

    self.dct_serving_processes_startup_params = None
    self.dct_serving_processes_details = None
    
    
    ### each business plugin will be kept having key a hash created based on (stream_name, signature, config_instance)
    ### so whenever a param changes from config_instance we know to deallocate the old business plugin and initialize a new one
    self._dct_current_instances = {}
    self._dct_hash_mappings = {}
    self._dct_stop_timings = {}
    self._dct_instance_hash_log = OrderedDict()

    self._admin_dispatch_queue = None
    self._admin_dispatch_thread = None
    self._admin_dispatch_stop = Event()
    self._admin_dispatch_lock = Lock()
    self._admin_instance_hashes = set()
    self._admin_dispatch_counters = {
      "enqueued": 0,
      "dispatched": 0,
      "dropped_missing_plugin": 0,
      "dropped_queue_full": 0,
    }
    self._admin_dispatch_last_loop_ts = None
    self._admin_dispatch_last_progress_ts = None
    self._admin_dispatch_last_warning_ts = 0
    self._admin_dispatch_consecutive_failures = 0

    self._graceful_stop_instances = defaultdict(lambda: 0)
    super(BusinessManager, self).__init__(log=log, prefix_log='[BIZM]', **kwargs)
    return
  
  def _str_to_bool(self, s):
    result = False
    if isinstance(s, bool):
      result = s
    if s is None:
      result = False
    if isinstance(s, int):
      result = bool(s)
    if isinstance(s, str):
      s = s.lower()
      result = s == 'true'
    return result
  

  def startup(self):
    """Start the business manager and admin async dispatch lane.

    Returns
    -------
    None
      Initializes manager state, maps current instances to subalterns, and logs
      plugin timing diagnostics when enabled.
    """
    super().startup()
    self._dct_current_instances = self._dct_subalterns # this allows usage of `self.get_subaltern(instance_hash)`
    self._initialize_admin_async_dispatch()
    if self.config_data.get('PLUGINS_DEBUG_LOAD_TIMINGS', True):
      self.P(
        "Plugin timing env: python={} dont_write_bytecode={} env_PYTHONDONTWRITEBYTECODE={}".format(
          sys.version.split()[0],
          getattr(sys, 'dont_write_bytecode', None),
          os.environ.get('PYTHONDONTWRITEBYTECODE'),
        ),
        color='b',
      )
    return

  @property
  def current_instances(self):
    """Return currently active business plugin instances.

    Returns
    -------
    dict
      Mapping from instance hash to plugin instance.
    """
    return self._dct_current_instances

  @property
  def cfg_admin_pipeline_async_dispatch(self):
    """Return whether admin-pipeline inputs use the async dispatch lane.

    Returns
    -------
    bool
        `True` by default unless explicitly disabled in runtime config.
    """
    return self.config_data.get("ADMIN_PIPELINE_ASYNC_DISPATCH", True)

  @property
  def cfg_admin_pipeline_dispatch_poll_seconds(self):
    """Return the async admin dispatcher queue polling interval.

    Returns
    -------
    float
        Queue polling interval in seconds.
    """
    return self.config_data.get("ADMIN_PIPELINE_DISPATCH_POLL_SECONDS", 0.05)

  @property
  def cfg_admin_pipeline_queue_maxlen(self):
    """Return the maximum async admin dispatch queue length.

    Returns
    -------
    int
        Queue capacity used when creating the dispatcher queue.
    """
    return self.config_data.get("ADMIN_PIPELINE_QUEUE_MAXLEN", 1024)

  @property
  def cfg_admin_pipeline_stall_warning_seconds(self):
    """Return the health-check threshold for dispatcher stalls.

    Returns
    -------
    float
        Seconds without progress before the dispatcher is considered stalled.
    """
    # Keep the dispatcher and orchestrator collection lane on the same
    # operator-facing stall threshold unless they are intentionally split later.
    return self.config_data.get(
      "ADMIN_PIPELINE_STALL_WARNING_SECONDS",
      max(1.0, 10 * self.cfg_admin_pipeline_dispatch_poll_seconds),
    )

  def _initialize_admin_async_dispatch(self):
    """
    Initialize the optional async delivery lane for `admin_pipeline` plugins.

    Returns
    -------
    None
        Starts or disables the dispatcher according to configuration.

    Raises
    ------
    ValueError
        If async dispatch is enabled while plugins are configured to run inline.

    Notes
    -----
    The dispatcher only owns pre-serving capture/control-plane deliveries. It
    does not execute plugins directly and it is intentionally unsupported when
    plugin execution is configured to run inline on the main thread.
    """
    if not self.cfg_admin_pipeline_async_dispatch:
      self._stop_admin_dispatch_thread()
      self._admin_dispatch_queue = None
      return

    if not self._run_on_threads:
      raise ValueError("ADMIN_PIPELINE_ASYNC_DISPATCH requires PLUGINS_ON_THREADS=true.")

    with self._admin_dispatch_lock:
      if self._admin_dispatch_queue is None:
        self._admin_dispatch_queue = Queue(maxsize=self.cfg_admin_pipeline_queue_maxlen)
      self._admin_dispatch_stop.clear()
      self._start_admin_dispatch_thread()
    return

  def _start_admin_dispatch_thread(self):
    """Start the async admin dispatch thread when it is not alive.

    Returns
    -------
    None
        Creates and starts the daemon dispatcher thread.
    """
    if self._admin_dispatch_thread is not None and self._admin_dispatch_thread.is_alive():
      return

    now = time()
    self._admin_dispatch_last_loop_ts = now
    self._admin_dispatch_last_progress_ts = now
    self._admin_dispatch_consecutive_failures = 0
    self._admin_dispatch_thread = Thread(
      target=self._admin_dispatch_loop,
      name="admin_pipeline_dispatcher",
      daemon=True,
    )
    self._admin_dispatch_thread.start()
    self.P("Started admin pipeline async dispatcher thread.", color='b')
    return

  def _stop_admin_dispatch_thread(self):
    """Stop the async admin dispatch thread if it exists.

    Returns
    -------
    None
        Signals the thread to stop and joins it with a bounded timeout.
    """
    thread = self._admin_dispatch_thread
    if thread is None:
      return

    self._admin_dispatch_stop.set()
    thread.join(timeout=max(1.0, 4 * self.cfg_admin_pipeline_dispatch_poll_seconds))
    if thread.is_alive():
      self.P("Admin pipeline async dispatcher thread did not stop before timeout.", color='r')
    else:
      self.P("Admin pipeline async dispatcher thread joined.", color='b')
    self._admin_dispatch_thread = None
    return

  def _admin_dispatch_loop(self):
    """Run the async admin input delivery loop.

    Returns
    -------
    None
        Exits when the dispatcher stop event is set or the queue is removed.
    """
    while not self._admin_dispatch_stop.is_set():
      if self._admin_dispatch_queue is None:
        return
      self._admin_dispatch_last_loop_ts = time()
      try:
        queue_item = self._admin_dispatch_queue.get(timeout=self.cfg_admin_pipeline_dispatch_poll_seconds)
      except Empty:
        continue
      try:
        self._dispatch_admin_queue_item(queue_item)
        self._admin_dispatch_last_progress_ts = time()
        self._admin_dispatch_consecutive_failures = 0
      except Exception:
        self._admin_dispatch_consecutive_failures += 1
        self.P(
          "Exception in admin pipeline async dispatcher loop:\n{}".format(traceback.format_exc()),
          color='r',
        )
    return

  def _ensure_admin_async_dispatch_health(self):
    """Ensure the async admin dispatcher is configured and healthy.

    Returns
    -------
    None
        Restarts the dispatcher when it is missing, dead, or stalled.
    """
    if not self.cfg_admin_pipeline_async_dispatch:
      return

    with self._admin_dispatch_lock:
      # This intentionally mirrors the orchestrator-side admin collection
      # health checks, but it also owns queue lifecycle and queue-specific
      # stall context, so we keep the logic local instead of forcing a shared
      # abstraction prematurely.
      if self._admin_dispatch_queue is None:
        self._admin_dispatch_queue = Queue(maxsize=self.cfg_admin_pipeline_queue_maxlen)
      if self._admin_dispatch_thread is None or not self._admin_dispatch_thread.is_alive():
        self.P("Admin pipeline async dispatcher thread is not alive. Restarting.", color='y')
        self._admin_dispatch_stop.clear()
        self._start_admin_dispatch_thread()
        return

    now = time()
    last_progress = self._admin_dispatch_last_progress_ts or self._admin_dispatch_last_loop_ts or now
    queue_depth = self._admin_dispatch_queue.qsize() if self._admin_dispatch_queue is not None else 0
    if queue_depth == 0 and self._admin_dispatch_consecutive_failures == 0:
      self._admin_dispatch_last_progress_ts = now
      return

    if (
      now - last_progress >= self.cfg_admin_pipeline_stall_warning_seconds and
      now - self._admin_dispatch_last_warning_ts >= self.cfg_admin_pipeline_stall_warning_seconds
    ):
      self.P(
        "Admin pipeline async dispatcher appears stalled: no progress for {:.1f}s, queue_depth={}, consecutive_failures={}.".format(
          now - last_progress,
          queue_depth,
          self._admin_dispatch_consecutive_failures,
        ),
        color='y',
      )
      self._admin_dispatch_last_warning_ts = now
    return

  def _dispatch_admin_queue_item(self, queue_item):
    """Deliver one queued admin-pipeline input snapshot to its plugin.

    Parameters
    ----------
    queue_item : tuple[str, dict]
        Pair of plugin instance hash and input snapshot.

    Returns
    -------
    None
        Drops stale entries for missing plugins and updates dispatcher counters.
    """
    instance_hash, inputs = queue_item
    plugin = self.get_subaltern(instance_hash)
    if plugin is None:
      self._admin_dispatch_counters["dropped_missing_plugin"] += 1
      self.P("Dropping queued admin delivery for missing plugin instance '{}'.".format(instance_hash), color='y')
      return

    if inputs is not None and self._should_filter_network_inputs(plugin, inputs):
      inputs = self._filter_network_inputs(plugin, inputs)
      if inputs is None:
        return

    plugin.add_inputs(inputs)
    self._admin_dispatch_counters["dispatched"] += 1
    return

  def _refresh_admin_instance_hashes(self, current_instances):
    """
    Refresh the set of currently active `admin_pipeline` instance hashes.

    Parameters
    ----------
    current_instances : list[str]
        Active instance hashes produced by the normal business-manager refresh.

    Returns
    -------
    None
        Updates the in-memory admin instance hash set.
    """
    admin_hashes = set()
    for instance_hash in current_instances:
      stream_name, _, _ = self._dct_hash_mappings.get(instance_hash, (None, None, None))
      if stream_name == ct.CONST_ADMIN_PIPELINE_NAME:
        admin_hashes.add(instance_hash)
    with self._admin_dispatch_lock:
      self._admin_instance_hashes = admin_hashes
    return

  def dispatch_admin_pipeline_inputs(self, dct_business_inputs):
    """
    Enqueue snapshot-owned admin inputs for async delivery.

    Parameters
    ----------
    dct_business_inputs : dict
        Capture-derived business inputs keyed by plugin instance hash.

    Returns
    -------
    int
        Number of admin deliveries successfully queued.
    """
    if not self.cfg_admin_pipeline_async_dispatch:
      return 0

    with self._admin_dispatch_lock:
      queue = self._admin_dispatch_queue
      admin_instance_hashes = frozenset(self._admin_instance_hashes)

    if queue is None:
      return 0

    enqueued = 0
    for instance_hash in admin_instance_hashes:
      inputs = dct_business_inputs.get(instance_hash)
      if not inputs:
        continue
      snapshot = deepcopy(inputs)
      try:
        queue.put_nowait((instance_hash, snapshot))
      except Full:
        self._admin_dispatch_counters["dropped_queue_full"] += 1
        self.P("Admin pipeline async dispatcher queue is full. Dropping delivery for '{}'.".format(instance_hash), color='r')
        continue
      self._admin_dispatch_counters["enqueued"] += 1
      enqueued += 1
    return enqueued

  def build_admin_capture_inputs(self, dct_captures):
    """
    Build capture-derived business inputs for the currently active
    `admin_pipeline` plugin instances.

    Parameters
    ----------
    dct_captures : dict
        Capture snapshot keyed by stream name.

    Returns
    -------
    dict
        Capture-only business inputs keyed by admin plugin instance hash.
    """
    dct_business_inputs = {}
    for instance_hash in self._admin_instance_hashes:
      stream_name, _, _ = self._dct_hash_mappings.get(instance_hash, (None, None, None))
      stream_capture = dct_captures.get(stream_name, {})
      inputs = stream_capture.get("INPUTS")
      if not isinstance(inputs, list) or len(inputs) == 0:
        continue
      normalized_inputs = [inp for inp in inputs if isinstance(inp, dict)]
      if len(normalized_inputs) == 0:
        continue
      stream_name = stream_capture.get("STREAM_NAME")
      if stream_name is None:
        continue
      dct_business_inputs[instance_hash] = {
        "STREAM_NAME": stream_name,
        "STREAM_METADATA": stream_capture.get("STREAM_METADATA") or {},
        "INPUTS": normalized_inputs,
      }
    return dct_business_inputs

  def update_streams(self, dct_config_streams):
    """Refresh business plugin instances from stream configuration.

    Parameters
    ----------
    dct_config_streams : dict
      Current stream configuration map.

    Returns
    -------
    Any
      AI engine usage information returned by `fetch_ai_engines`.
    """
    self._ensure_admin_async_dispatch_health()
    self.owner.set_loop_stage('2.bm.refresh.entry_update_streams')
    self._dct_config_streams = dct_config_streams
    self.owner.set_loop_stage('2.bm.refresh._check_instances')
    current_instances = self._check_instances()
    self._refresh_admin_instance_hashes(current_instances)
    self.owner.set_loop_stage('2.bm.refresh._deallocate_unused_instances')
    self._deallocate_unused_instances(current_instances)
    self.owner.set_loop_stage('2.bm.refresh.fetch_ai_engines')
    in_use_ai_engines = self.fetch_ai_engines()
    return in_use_ai_engines

  def bootstrap_admin_pipeline_instances(self, dct_config_streams):
    """
    Start or refresh only `admin_pipeline` business instances during early startup.

    Parameters
    ----------
    dct_config_streams : dict
        Full stream configuration map.

    Returns
    -------
    list[str]
        Current admin-pipeline instance hashes after refresh.
    """
    self._ensure_admin_async_dispatch_health()
    self.owner.set_loop_stage('2.bm.bootstrap_admin.entry_update_streams')
    self._dct_config_streams = dct_config_streams
    self.owner.set_loop_stage('2.bm.bootstrap_admin._check_instances')
    current_instances = self._check_instances(stream_names={ct.CONST_ADMIN_PIPELINE_NAME})
    self._refresh_admin_instance_hashes(current_instances)
    return current_instances
    

  def get_active_plugins_instances(self, as_dict=True):
    active = []
    instances = list(self._dct_current_instances.keys())
    for instance in instances:
      plg = self._dct_current_instances.get(instance)
      if plg is None:
        continue
      sid, sign, iid, apr, it, et, lct, fet, let, owh, cei, cpi, lpt, tpc = [None] * 14
      info = None
      
      try:
        # this section MUST be protected as it will call plugin code
        sid = plg._stream_id
        sign = plg._signature
        iid = plg.cfg_instance_id
        
        pdl = plg.cfg_process_delay

        apr = plg.actual_plugin_resolution
        it = plg.init_timestamp
        et = plg.exec_timestamp
        lct = plg.last_config_timestamp
        fet = plg.first_error_time
        let = plg.last_error_time
        owh = plg.is_outside_working_hours # modified within the plugin loop - DO NOT use `plg.outside_working_hours`
        cei = plg.current_exec_iteration
        cpi = plg.current_process_iteration
        lpt = plg.last_payload_time_str
        tpc = plg.total_payload_count
      except Exception as exc:
        info = "Error while retrieving data: {}".format(exc)
      #end try


      if as_dict:
        plg_info = {
          ct.HB.ACTIVE_PLUGINS_INFO.STREAM_ID                  : sid,
          ct.HB.ACTIVE_PLUGINS_INFO.SIGNATURE                  : sign,
          ct.HB.ACTIVE_PLUGINS_INFO.INSTANCE_ID                : iid,
          
          ct.HB.ACTIVE_PLUGINS_INFO.PROCESS_DELAY              : pdl,

          ct.HB.ACTIVE_PLUGINS_INFO.FREQUENCY                  : apr,
          ct.HB.ACTIVE_PLUGINS_INFO.INIT_TIMESTAMP             : it,
          ct.HB.ACTIVE_PLUGINS_INFO.EXEC_TIMESTAMP             : et, # this is the last exec timestamp. It is updated at the end of the exec
          ct.HB.ACTIVE_PLUGINS_INFO.LAST_CONFIG_TIMESTAMP      : lct,
          ct.HB.ACTIVE_PLUGINS_INFO.FIRST_ERROR_TIME           : fet,
          ct.HB.ACTIVE_PLUGINS_INFO.LAST_ERROR_TIME            : let,
          ct.HB.ACTIVE_PLUGINS_INFO.OUTSIDE_WORKING_HOURS      : owh,
          ct.HB.ACTIVE_PLUGINS_INFO.CURRENT_PROCESS_ITERATION  : cpi,
          ct.HB.ACTIVE_PLUGINS_INFO.CURRENT_EXEC_ITERATION     : cei,
          ct.HB.ACTIVE_PLUGINS_INFO.LAST_PAYLOAD_TIME          : lpt,
          ct.HB.ACTIVE_PLUGINS_INFO.TOTAL_PAYLOAD_COUNT        : tpc,

          ct.HB.ACTIVE_PLUGINS_INFO.INFO                       : info,
          }
      else:
        plg_info = (
          sid, sign, iid,
          apr, it, et, lct, fet, let, owh, cpi, cei, lpt, tpc,
          info
        )
      #endif dict or tuple
      active.append(plg_info)
    # end for
    return active


  def get_total_payload_count(self):
    instances = list(self._dct_current_instances.keys())
    total = 0
    for instance in instances:
      plg = self._dct_current_instances.get(instance)
      if plg is None:
        continue
      tpc = plg.total_payload_count
      total += tpc
    return total

  def set_loop_stage(self, s):
    self.owner.set_loop_stage(s)
    return


  def get_business_instance_identification(self, instance_hash):
    return self._dct_hash_mappings.get(instance_hash)

  def get_business_instance_hash(self, stream_name, signature, instance_id):
    dct_inv = {v : k for k,v in self._dct_hash_mappings.items()}
    return dct_inv.get((stream_name, signature, instance_id))

  def get_current_jobs(self, stream_names=None):
    """Build plugin instance jobs from the current stream configuration.

    Parameters
    ----------
    stream_names : set[str] or None, optional
        Optional stream-name filter used for admin-only bootstrap.

    Returns
    -------
    list[tuple]
        Plugin startup/update jobs with initiator, session, stream, signature,
        instance id, and upstream config data.
    """
    current_pipeline_names = list(self._dct_config_streams.keys())
    if stream_names is not None:
      allowed_streams = set(stream_names)
      current_pipeline_names = [name for name in current_pipeline_names if name in allowed_streams]
    # now prioritize the "admin_pipeline" (ct.CONST_ADMIN_PIPELINE_NAME) to be the first one
    if ct.CONST_ADMIN_PIPELINE_NAME in current_pipeline_names:
      current_pipeline_names.remove(ct.CONST_ADMIN_PIPELINE_NAME)
      current_pipeline_names.insert(0, ct.CONST_ADMIN_PIPELINE_NAME)   
    #endif prioritize admin pipeline 
    jobs = []
    for pipeline_name in current_pipeline_names:
      pipeline_config = self._dct_config_streams[pipeline_name]

      initiator_addr = pipeline_config.get(ct.CONFIG_STREAM.K_INITIATOR_ADDR, None) 
      initiator_id = pipeline_config.get(ct.CONFIG_STREAM.K_INITIATOR_ID, None)

      modified_by_addr = pipeline_config.get(ct.CONFIG_STREAM.K_MODIFIED_BY_ADDR, None)
      modified_by_id = pipeline_config.get(ct.CONFIG_STREAM.K_MODIFIED_BY_ID, None)

      lst_config_plugins = pipeline_config[ct.CONFIG_STREAM.K_PLUGINS]

      if pipeline_name == ct.CONST_ADMIN_PIPELINE_NAME:
        # Netmon plugin should be the first one in the admin pipeline.
        # This is because the netmon will toggle supervisor node status for every other plugin instance.
        netmon_config_idx, netmon_config = None, None
        for idx, config_plugin in enumerate(lst_config_plugins):
          signature = config_plugin[ct.CONFIG_PLUGIN.K_SIGNATURE]
          if signature == ct.ADMIN_PIPELINE_NETMON:
            netmon_config_idx = idx
            netmon_config = config_plugin
            break
        # endfor plugin_configs
        # Now move the netmon plugin to the first position.
        if netmon_config is not None:
          lst_config_plugins.pop(netmon_config_idx)
          lst_config_plugins.insert(0, netmon_config)
        # endif netmon_config found
      # endif admin pipeline

      session_id = pipeline_config.get(ct.CONFIG_STREAM.K_SESSION_ID, None)
      
      for config_plugin in lst_config_plugins:
        lst_config_instances = config_plugin[ct.CONFIG_PLUGIN.K_INSTANCES]
        signature = config_plugin[ct.CONFIG_PLUGIN.K_SIGNATURE]
        for config_instance in lst_config_instances:
          instance_id = config_instance[ct.CONFIG_INSTANCE.K_INSTANCE_ID]
          jobs.append((initiator_addr, initiator_id, modified_by_addr, modified_by_id, session_id, pipeline_name, signature, instance_id, config_instance))
    return jobs

  def __maybe_register_special_plugin_instance_hash(self, instance_hash, signature):
    if signature.upper() == ct.ADMIN_PIPELINE_DAUTH.upper():
      self.__dauth_hash = instance_hash
    return

  def __maybe_shutdown_special_instances(self):
    """
    This method will check if the special instances are still running and if not, it will shutdown them.
    """
    if self.__dauth_hash is not None:
      self.P("Closing dAuth plugin instance {}...".format(self.__dauth_hash), color='y')
      self._send_stop_signal_to_plugin(self.__dauth_hash, forced=True)
    #endif
    return

  def _check_instances(self, stream_names=None):
    """
    IMPORTANT: this code section is critical wrt overall main loop functioning!

    Parameters
    ----------
    stream_names : set[str] or None, optional
        Optional stream-name filter limiting which pipeline instances are
        checked or started.

    Returns
    -------
    list[str]
        Instance hashes that should remain active after this refresh.
    """
    current_instances = []
    self.set_loop_stage('2.bm.refresh._check_instances.get_current_jobs')
    all_jobs = self.get_current_jobs(stream_names=stream_names)
    n_all_jobs = len(all_jobs)
    debug_load_timings = self.config_data.get('PLUGINS_DEBUG_LOAD_TIMINGS', True)
    total_start = perf_counter()
    for idx_job, (initiator_addr, initiator_id, modified_by_addr, modified_by_id, session_id, stream_name, signature, instance_id, upstream_config) in enumerate(all_jobs):
      if debug_load_timings:
        iter_start = perf_counter()
        get_class_s = 0.0
        instantiate_s = 0.0
        start_thread_s = 0.0
        update_config_s = 0.0
      is_new_instance = False
      try:
        obj_identification = (stream_name, signature, instance_id)
        instance_hash = self.log.hash_object(obj_identification, size=5)
        self._dct_hash_mappings[instance_hash] = obj_identification
        current_instances.append(instance_hash)
        if instance_hash not in self._dct_current_instances:
          is_new_instance = True
          self._dct_instance_hash_log[instance_hash] = {
            ct.PAYLOAD_DATA.INITIATOR_ID  : initiator_id,
            ct.PAYLOAD_DATA.SESSION_ID    : session_id,
            ct.PAYLOAD_DATA.SIGNATURE     : signature,
            ct.PAYLOAD_DATA.STREAM_NAME   : stream_name,
            ct.PAYLOAD_DATA.INSTANCE_ID   : instance_id,
          }

          self.P(
            " * * * * Init biz plugin #{}/{} {}:{} * * * *".format(
              idx_job + 1, n_all_jobs, signature, instance_id
            ),
            color='b'
          )
          self.set_loop_stage('2.bm.refresh.get_class.{}:{}'.format(signature,instance_id))
          if 'update_monitor' in signature.lower():
            print('debug')
          if debug_load_timings:
            get_class_start = perf_counter()
          _module_name, _class_name, _cls_def, _config_dict = self._get_module_name_and_class(
            locations=ct.PLUGIN_SEARCH.LOC_BIZ_PLUGINS,
            name=signature,
            suffix=ct.PLUGIN_SEARCH.SUFFIX_BIZ_PLUGINS,
            verbose=0,
            safety_check=True,  # perform safety check on custom biz plugins
            safe_locations=ct.PLUGIN_SEARCH.SAFE_BIZ_PLUGINS,
            safe_imports=ct.PLUGIN_SEARCH.SAFE_BIZ_IMPORTS
          )
          if debug_load_timings:
            get_class_s = perf_counter() - get_class_start

          self.set_loop_stage('2.bm.refresh.check_class.{}:{}'.format(signature,instance_id))
          
          if _cls_def is None:
            self._dct_current_instances[instance_hash] = None
            msg = "Error loading business plugin <{}:{}> - No code/script defined.".format(signature, instance_id)
            self.P(msg + " on stream {}".format(stream_name), color='r')
            self._create_notification(
              notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
              msg=msg,
              stream_name=stream_name,
              info="No code/script defined for business plugin '{}' in {} or plugin is invalid (node {})".format(
                signature, ct.PLUGIN_SEARCH.LOC_BIZ_PLUGINS, "is currently SECURED" if self.owner.is_secured else "is currently UNSECURED!"
              )
            )
            continue
          #endif

          self.comm_shared_memory['payloads'][instance_hash] = deque(maxlen=1000)
          self.comm_shared_memory['commands'][instance_hash] = deque(maxlen=1000)

          try:
            self.set_loop_stage('2.bm.refresh.call_class.{}:{}:{}'.format(stream_name, signature, instance_id))
            self.shmem['__set_loop_stage_func'] = self.set_loop_stage
            # debug when configuring a plugin
            debug_config_changes = self.config_data.get('PLUGINS_DEBUG_CONFIG_CHANGES', False) # Ugly but needed
            # end debug
            
            _module_version = _config_dict.get('MODULE_VERSION', '0.0.0')
            
            if debug_load_timings:
              instantiate_start = perf_counter()
            plugin = _cls_def(
              log=self.log,
              global_shmem=self.shmem, # this SHOULD NOT be used for inter-plugin mem access
              plugins_shmem=self.plugins_shmem,
              stream_id=stream_name,
              signature=signature,
              default_config=_config_dict,
              upstream_config=upstream_config,
              environment_variables=self._environment_variables,
              initiator_id=initiator_id,
              initiator_addr=initiator_addr,
              session_id=session_id,
              threaded_execution_chain=self._run_on_threads,
              payloads_deque=self.comm_shared_memory['payloads'][instance_hash],
              commands_deque=self.comm_shared_memory['commands'][instance_hash],
              ee_ver=self.owner.__version__,
              runs_in_docker=self.owner.runs_in_docker,
              docker_branch=self.owner.docker_source,
              debug_config_changes=debug_config_changes,
              version=_module_version,
              pipelines_view_function=self.owner.get_pipelines_view,
              pipeline_use_local_comms_only=self._dct_config_streams[stream_name].get(ct.CONFIG_STREAM.K_USE_LOCAL_COMMS_ONLY, False),
            )
            if debug_load_timings:
              instantiate_s = perf_counter() - instantiate_start
            if plugin.cfg_runs_only_on_supervisor_node:
              if not self.is_supervisor_node:
                self.P(
                  "Plugin {}:{} runs ONLY on supervisor node. Skipping.".format(signature, instance_id), 
                  color='r', boxed=True,
                )
                plugin = None
                # continue
              else:
                self.P("Plugin {}:{} runs only on supervisor node. Running.".format(signature, instance_id), color='g')
            # endif runs only on supervisor node
            self.set_loop_stage('2.bm.refresh.new_instance_done: {}:{}:{}'.format(stream_name, signature, instance_id))
          except Exception as exc:
            plugin = None
            trace = traceback.format_exc()
            msg = "Plugin init FAILED for business plugin {} instance {}".format(signature, instance_id)
            info = str(exc)
            if "validating" not in info:
              info += '\n' + trace
            self.P(msg + ': ' + info, color='r')
            self._create_notification(
              notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
              msg=msg,
              signature=signature,
              instance_id=instance_id,
              stream_name=stream_name,
              info=info,
              displayed=True,
            )
          #end try-except

          self._dct_current_instances[instance_hash] = plugin
          self.__maybe_register_special_plugin_instance_hash(instance_hash=instance_hash, signature=signature)

          if plugin is None:
            continue

          self.P("New plugin instance {} added for exec.".format(plugin), color='g')
          if self._run_on_threads:
            if debug_load_timings:
              start_thread_start = perf_counter()
            plugin.start_thread()
            if debug_load_timings:
              start_thread_s = perf_counter() - start_thread_start
          #endif new instance
        else:
          # I do have the instance, I just need to modify the config
          plugin = self._dct_current_instances[instance_hash]
          if plugin is not None:
            # next we need to check if the config has changed and handle also the particular
            # case when the plugin just received a INSTANCE_COMMAND
            if debug_load_timings:
              update_config_start = perf_counter()
            plugin.maybe_update_instance_config(
              upstream_config=upstream_config,
              session_id=session_id,
              modified_by_addr=modified_by_addr,
              modified_by_id=modified_by_id,
            )
            if debug_load_timings:
              update_config_s = perf_counter() - update_config_start
            self.set_loop_stage('2.bm.refresh.maybe_update_instance_config.DONE: {}:{}:{}'.format(stream_name, signature, instance_id))
          #endif
        #endif
      finally:
        if is_new_instance and debug_load_timings:
          iter_total_s = perf_counter() - iter_start
          total_elapsed_s = perf_counter() - total_start
          other_s = iter_total_s - (get_class_s + instantiate_s + start_thread_s + update_config_s)
          self.P(
            " START Plugin {}/{} {}:{} new={} total={:.2f}s other={:.2f}s get_class={:.2f}s init={:.2f}s start_thread={:.2f}s update_cfg={:.2f}s (ALL={:.2f}s)".format(
              idx_job + 1,
              n_all_jobs,
              signature,
              instance_id,
              is_new_instance,
              iter_total_s,
              other_s,
              get_class_s,
              instantiate_s,
              start_thread_s,
              update_config_s,
              total_elapsed_s,
            ),
            boxed=True
          )
        # endif debug_load_timings
      # end try-finally

    return current_instances

  def _stop_plugin(self, instance_hash):
    plugin = self._dct_current_instances[instance_hash]

    if plugin is None:
      return

    if self._run_on_threads:
      plugin.stop_thread()

    plugin = self._dct_current_instances.pop(instance_hash)
    self._dct_stop_timings.pop(instance_hash, None)
    del plugin
  
    sleep(0.1)
    gc.collect()
    return

  def maybe_stop_finished_plugins(self):
    """
    This method will check if any plugins that have been marked for deletion are
    are still working and forces the closing
    """
    max_stop_lag_time = self.log.config_data.get('MAX_PLUGIN_STOP_TIME', 60)
    instances = list(self._dct_stop_timings.keys())
    for instance_hash in instances:
      if instance_hash in self._dct_stop_timings:
        stop_init_time = self._dct_stop_timings[instance_hash]
        elapsed = time() - stop_init_time
        plugin = self._dct_current_instances.get(instance_hash)
        if elapsed > max_stop_lag_time and plugin is not None:
          self.P(ct.BM_PLUGIN_END_PREFIX + "Stopping lagged ({:.1f}s/{:.1f}s) pugin {}".format(
            elapsed, max_stop_lag_time, plugin
            ), color='r'
          )
          self._stop_plugin(instance_hash)
    return

  def _send_stop_signal_to_plugin(self, instance_hash, forced=False):
    plugin = self._dct_current_instances.get(instance_hash)
    if plugin is None:
      self.P("Received STOP for unvail plugin instance {}".format(instance_hash), color='r')
      if instance_hash in self._dct_current_instances:
        self.P("  Deleting {} from current instances".format(instance_hash), color='r')
        del self._dct_current_instances[instance_hash]
      if instance_hash in self._graceful_stop_instances:
        self.P("  Deleting {} from instances marked for graceful stop".format(instance_hash), color='r')
        del self._graceful_stop_instances[instance_hash]
      return

    if plugin.done_loop:
      self.P("Received STOP for already stopped {}:{}".format(instance_hash, plugin), color='r')
      # TODO: maybe add a sleep so that we do not spam this thing
      self._graceful_stop_instances.pop(instance_hash, 0)
      return

    if instance_hash not in self._dct_stop_timings:
      self._dct_stop_timings[instance_hash] = time()

    nr_inputs = len(plugin.upstream_inputs_deque)

    if (nr_inputs == 0) or forced:
      self.P(ct.BM_PLUGIN_END_PREFIX + "Stopping {}".format(plugin), color='y')
      self._stop_plugin(instance_hash)
      nr_graceful_tries = self._graceful_stop_instances.pop(instance_hash, 0)
      self.P("Stopped `{}` (pend.inp:{}, forced:{}, graceful cnt.:{})".format(repr(plugin), nr_inputs, forced, nr_graceful_tries), color='y')
    else:
      self._graceful_stop_instances[instance_hash] += 1
      if self._graceful_stop_instances[instance_hash] == 1:
        self.P(ct.BM_PLUGIN_END_PREFIX + "Gracefull marking for stopping {}".format(plugin), color='y')
        self.P("Marked `{}` to be gracefully stopped as it has {} pending inputs. Waiting to consume all pending inputs.".format(
          repr(plugin), nr_inputs),
          color='r'
        )
    #endif

    return

  def close(self):
    self.P("Stopping all business plugins...", color='y')
    self._stop_admin_dispatch_thread()
    # First, shutdown special instances
    self.__maybe_shutdown_special_instances()
    # Now, we can shutdown normal instances
    keys = list(self._dct_current_instances.keys())
    for _hash in keys:
      self._send_stop_signal_to_plugin(instance_hash=_hash, forced=True)
    self.P("Done stopping all business plugins.", color='y')
    return

  def _deallocate_unused_instances(self, current_instances):
    keys = list(self._dct_current_instances.keys())
    for _hash in keys:
      if _hash not in current_instances:
        self._send_stop_signal_to_plugin(instance_hash=_hash, forced=False)

    keys = list(self._graceful_stop_instances.keys())
    for _hash in keys:
      self._send_stop_signal_to_plugin(instance_hash=_hash, forced=False)
    return

  @property
  def dct_instances_details(self):
    dct_instances_details = {}
    for instance_hash, plugin in self._dct_current_instances.items():
      if plugin is None:
        continue
      dct_instances_details[instance_hash] = (
        plugin.get_stream_id(),
        plugin.get_signature(),
        plugin.get_instance_config()
      )
    return dct_instances_details

  def fetch_ai_engines(self):
    self.dct_serving_processes_details = {}
    self.dct_serving_processes_startup_params = {}
    currently_used_ai_engines = set()

    for instance_hash, (stream_id, signature, instance_config) in self.dct_instances_details.items():
      plugin = self._dct_current_instances[instance_hash]
      ai_engine = plugin.cfg_ai_engine
      if ai_engine is None:
        continue

      # this config params go into serving process overall inputs
      inference_ai_engine_params = instance_config.get('INFERENCE_AI_ENGINE_PARAMS', {})

      # this is used only at model startup
      startup_ai_engine_params = instance_config.get('STARTUP_AI_ENGINE_PARAMS', {})

      ### for 'AI_ENGINE' there can be either single value or multiple values (list)
      ### For the second case we expect that for 'INFERENCE_AI_ENGINE_PARAMS' and 'STARTUP_AI_ENGINE_PARAMS'
      ### to (maybe) have params for each model serving process in the list.
      assert isinstance(ai_engine, (str, list))
      if not isinstance(ai_engine, list):
        ai_engine = ai_engine.lower()
        tmp_inference_ai_engine_params = {}
        tmp_startup_ai_engine_params = {}
        if ai_engine not in inference_ai_engine_params:
          tmp_inference_ai_engine_params[ai_engine] = inference_ai_engine_params
          inference_ai_engine_params = tmp_inference_ai_engine_params
        #endif

        if ai_engine not in startup_ai_engine_params:
          tmp_startup_ai_engine_params[ai_engine] = startup_ai_engine_params
          startup_ai_engine_params = tmp_startup_ai_engine_params
        #endif not in startup

        ai_engine = [ai_engine]
      #endif is just a string

      for _ai_engine in ai_engine:
        _ai_engine = _ai_engine.lower()
        inference_params = inference_ai_engine_params.get(_ai_engine, {})
        startup_params = startup_ai_engine_params.get(_ai_engine, {})
        t = (stream_id, json.dumps(inference_params))
        model_instance_id = startup_params.get('MODEL_INSTANCE_ID', None)
        if model_instance_id is not None:
          key = (_ai_engine, model_instance_id)
        else:
          key = _ai_engine

        if key not in self.dct_serving_processes_details:
          self.dct_serving_processes_details[key] = {}
        if t not in self.dct_serving_processes_details[key]:
          self.dct_serving_processes_details[key][t] = []
        if key not in currently_used_ai_engines:
          currently_used_ai_engines.add(key)

        self.dct_serving_processes_details[key][t].append(instance_hash)

        # TODO check current startup_params overwrite other startup_params
        # for _model_serving, which is a case of misconfiguration
        self.dct_serving_processes_startup_params[key] = startup_params
      #endfor
    #endfor

    return currently_used_ai_engines


  @property
  def any_overloaded_plugins(self):
    overloaded_plugins = self.get_overloaded_plugins()
    return len(overloaded_plugins) > 0

  def get_overloaded_plugins(self):
    all_instance_hashes = list(self._dct_current_instances)
    lst_overloaded = []
    for instance_hash in all_instance_hashes:
      plugin = self._dct_current_instances.get(instance_hash)
      if plugin is not None and plugin.is_queue_overflown:
        overflow = plugin.is_queue_overflown
        qsize = plugin.input_queue_size 
        qsizemax = plugin.cfg_max_inputs_queue_size
        status = "{}/{}".format(qsize, qsizemax)
        lst_overloaded.append(
          (plugin.get_stream_id(), plugin.get_signature(), plugin.get_instance_id(), overflow, status)
        )
    return lst_overloaded

  def get_plugin_default_config(self, signature):
    _module_name, _class_name, _cls_def, _config_dict = self._get_module_name_and_class(
      locations=ct.PLUGIN_SEARCH.LOC_BIZ_PLUGINS,
      name=signature,
      suffix=ct.PLUGIN_SEARCH.SUFFIX_BIZ_PLUGINS,
      verbose=0,
      safety_check=True, # perform safety check on custom biz plugins # TODO: should we do this?
      safe_locations=ct.PLUGIN_SEARCH.SAFE_BIZ_PLUGINS,
      safe_imports=ct.PLUGIN_SEARCH.SAFE_BIZ_IMPORTS
    )

    return _config_dict


  def execute_all_plugins(self, dct_business_inputs):
    self.log.start_timer('execute_all_business_plugins')
    all_instance_hashes = list(dct_business_inputs.keys())
    for instance_hash in all_instance_hashes:
      if self.cfg_admin_pipeline_async_dispatch and instance_hash in self._admin_instance_hashes:
        continue
      inputs = dct_business_inputs.get(instance_hash)
      plugin = self.get_subaltern(instance_hash) # this or `self._dct_current_instances[instance_hash]`
      if plugin is None:
        dct_info = self._dct_instance_hash_log.get(instance_hash, {})
        stream_name = dct_info.get(ct.PAYLOAD_DATA.STREAM_NAME)
        signature = dct_info.get(ct.PAYLOAD_DATA.SIGNATURE)
        instance_id = dct_info.get(ct.PAYLOAD_DATA.INSTANCE_ID)
        initiator_id = dct_info.get(ct.PAYLOAD_DATA.INITIATOR_ID)
        session_id = dct_info.get(ct.PAYLOAD_DATA.SESSION_ID)
        msg = "Biz plugin instance execution error"
        info = "Biz plugin instance '{}' was deleted yet the pipeline has not safely cleaned the dataflow. Deleted instance data: {}".format(
          instance_hash, dct_info,
        )
        self.P(info, color='r')
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
          msg=msg,
          info=info,
          stream_name=stream_name,
          signature=signature,
          instance_id=instance_id,
          initiator_id=initiator_id,
          session_id=session_id,
          displayed=True,
        )
        continue

      if not self._run_on_threads:
        # postponing stuff
        if plugin.is_process_postponed:
          # if process needs postponing just do not add new inputs to process
          # and if it runs in parallel the loop will check this
          continue

        if plugin.is_outside_working_hours:
          # if process is outside working hours again do not add inpus
          # and if it runs in parallel the loop will check this
          continue
        # end postponing stuff
        if inputs is not None and self._should_filter_network_inputs(plugin, inputs):
          inputs = self._filter_network_inputs(plugin, inputs)
          if inputs is None:
            continue
        plugin.add_inputs(inputs)

        plugin.execute()
      else:
        # if the process is running (default) on thread we just need to add
        # data to its inputs queue
        if inputs is not None and self._should_filter_network_inputs(plugin, inputs):
          inputs = self._filter_network_inputs(plugin, inputs)
          if inputs is None:
            continue
        plugin.add_inputs(inputs)
      #endif

    #endfor
    self.log.stop_timer('execute_all_business_plugins', skip_first_timing=False)
    return

  def _should_filter_network_inputs(self, plugin, inputs):
    """
    Determine whether handler-based routing should filter the incoming inputs.

    Parameters
    ----------
    plugin : BasePluginExecutor
        Plugin instance considered for routing.
    inputs : dict
        Input payload dict produced by the data handler.

    Returns
    -------
    bool
        True if filtering should be applied, False otherwise.
    """
    if not plugin.cfg_network_route_by_handler:
      return False
    if not hasattr(plugin, "get_registered_payload_signatures"):
      return False
    if not isinstance(inputs, dict):
      return False
    if "INPUTS" not in inputs:
      return False
    if not isinstance(inputs["INPUTS"], list):
      return False
    return True

  def _filter_network_inputs(self, plugin, inputs):
    """
    Filter structured payloads by handler signature.

    Parameters
    ----------
    plugin : BasePluginExecutor
        Plugin instance that owns the handlers.
    inputs : dict
        Input payload dict with an `INPUTS` list.

    Returns
    -------
    dict or None
        A filtered inputs dict, or None if nothing remains after filtering.
    """
    dct_inputs = inputs
    lst_inputs = dct_inputs.get("INPUTS", [])
    if not lst_inputs:
      return None

    handlers = plugin.get_registered_payload_signatures()
    if not handlers:
      return None

    filtered = []
    for item in lst_inputs:
      if not isinstance(item, dict):
        filtered.append(item)
        continue
      if item.get("TYPE") != "STRUCT_DATA":
        filtered.append(item)
        continue
      payload = item.get("STRUCT_DATA")
      if not isinstance(payload, dict):
        filtered.append(item)
        continue
      payload_path = payload.get(ct.PAYLOAD_DATA.EE_PAYLOAD_PATH, None)
      if not isinstance(payload_path, list) or len(payload_path) < 3:
        continue
      signature = payload_path[2]
      if isinstance(signature, str) and signature.upper() in handlers:
        filtered.append(item)
        continue
    if len(filtered) == 0:
      return None
    if len(filtered) == len(lst_inputs):
      return dct_inputs
    dct_inputs = dict(dct_inputs)
    dct_inputs["INPUTS"] = filtered
    return dct_inputs

import json
import gc

import traceback

from collections import OrderedDict

from time import time, sleep
from naeural_core import constants as ct
from naeural_core import Logger
from naeural_core.manager import Manager

from collections import deque, defaultdict

class BusinessManager(Manager):

  def __init__(self, log : Logger, owner, shmem, environment_variables=None, run_on_threads=True, **kwargs):
    self.shmem = shmem
    self.plugins_shmem = {}
    self.owner = owner
    self.__netmon_instance = None
    self._dct_config_streams = None
    self.is_supervisor_node = False
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


    self._graceful_stop_instances = defaultdict(lambda: 0)
    super(BusinessManager, self).__init__(log=log, prefix_log='[BIZM]', **kwargs)
    return

  def startup(self):
    super().startup()
    self._dct_current_instances = self._dct_subalterns # this allows usage of `self.get_subaltern(instance_hash)`
    return

  @property
  def current_instances(self):
    return self._dct_current_instances

  def update_streams(self, dct_config_streams):
    self.owner.set_loop_stage('2.bm.refresh.entry_update_streams')
    self._dct_config_streams = dct_config_streams
    self.owner.set_loop_stage('2.bm.refresh._check_instances')
    current_instances = self._check_instances()
    self.owner.set_loop_stage('2.bm.refresh._deallocate_unused_instances')
    self._deallocate_unused_instances(current_instances)
    self.owner.set_loop_stage('2.bm.refresh.fetch_ai_engines')
    in_use_ai_engines = self.fetch_ai_engines()
    return in_use_ai_engines
  

  def get_active_plugins_instances(self, as_dict=True):
    active = []
    instances = list(self._dct_current_instances.keys())
    for instance in instances:
      plg = self._dct_current_instances.get(instance)
      if plg is None:
        continue
      sid, sign, iid, apr, it, et, lct, fet, let, owh, cei, cpi, lpt, tpc = [None] * 14
      info = None
      
      # setup special plugins area
      if plg._signature.upper() == ct.ADMIN_PIPELINE_NETMON.upper():
        self.is_supervisor_node = plg.cfg_supervisor
        self.shmem['is_supervisor_node'] = self.is_supervisor_node
        self.__netmon_instance = plg
      # end setup special plugins area
      
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

  def get_current_jobs(self):
    current_pipeline_names = list(self._dct_config_streams.keys())
    jobs = []
    for pipeline_name in current_pipeline_names:
      pipeline_config = self._dct_config_streams[pipeline_name]

      initiator_addr = pipeline_config.get(ct.CONFIG_STREAM.K_INITIATOR_ADDR, None) 
      initiator_id = pipeline_config.get(ct.CONFIG_STREAM.K_INITIATOR_ID, None)

      modified_by_addr = pipeline_config.get(ct.CONFIG_STREAM.K_MODIFIED_BY_ADDR, None)
      modified_by_id = pipeline_config.get(ct.CONFIG_STREAM.K_MODIFIED_BY_ID, None)

      lst_config_plugins = pipeline_config[ct.CONFIG_STREAM.K_PLUGINS]
      session_id = pipeline_config.get(ct.CONFIG_STREAM.K_SESSION_ID, None)
      
      for config_plugin in lst_config_plugins:
        lst_config_instances = config_plugin[ct.CONFIG_PLUGIN.K_INSTANCES]
        signature = config_plugin[ct.CONFIG_PLUGIN.K_SIGNATURE]
        for config_instance in lst_config_instances:
          instance_id = config_instance[ct.CONFIG_INSTANCE.K_INSTANCE_ID]
          jobs.append((initiator_addr, initiator_id, modified_by_addr, modified_by_id, session_id, pipeline_name, signature, instance_id, config_instance))
    return jobs

  def _check_instances(self):
    """
    IMPORTANT: this code section is critical wrt overall main loop functioning!
    """
    current_instances = []
    self.set_loop_stage('2.bm.refresh._check_instances.get_current_jobs')
    all_jobs = self.get_current_jobs()
    for idx_job, (initiator_addr, initiator_id, modified_by_addr, modified_by_id, session_id, stream_name, signature, instance_id, upstream_config) in enumerate(all_jobs):
      obj_identification = (stream_name, signature, instance_id)
      instance_hash = self.log.hash_object(obj_identification, size=5)
      self._dct_hash_mappings[instance_hash] = obj_identification
      current_instances.append(instance_hash)
      if instance_hash not in self._dct_current_instances:
        self._dct_instance_hash_log[instance_hash] = {
          ct.PAYLOAD_DATA.INITIATOR_ID  : initiator_id,
          ct.PAYLOAD_DATA.SESSION_ID    : session_id,
          ct.PAYLOAD_DATA.SIGNATURE     : signature,
          ct.PAYLOAD_DATA.STREAM_NAME   : stream_name,
          ct.PAYLOAD_DATA.INSTANCE_ID   : instance_id,
        }

        self.P(" * * * * Init biz plugin {}:{} * * * *".format(signature, instance_id), color='b')
        self.set_loop_stage('2.bm.refresh.get_class.{}:{}'.format(signature,instance_id))
        if 'update_monitor' in signature.lower():
          print('debug')
        _module_name, _class_name, _cls_def, _config_dict = self._get_module_name_and_class(
          locations=ct.PLUGIN_SEARCH.LOC_BIZ_PLUGINS,
          name=signature,
          suffix=ct.PLUGIN_SEARCH.SUFFIX_BIZ_PLUGINS,
          verbose=0,
          safety_check=True, # perform safety check on custom biz plugins
          safe_locations=ct.PLUGIN_SEARCH.SAFE_BIZ_PLUGINS,
          safe_imports=ct.PLUGIN_SEARCH.SAFE_BIZ_IMPORTS
        )

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

        self.comm_shared_memory['payloads'][instance_hash] = deque(maxlen=100)
        self.comm_shared_memory['commands'][instance_hash] = deque(maxlen=100)

        try:
          self.set_loop_stage('2.bm.refresh.call_class.{}:{}:{}'.format(stream_name, signature, instance_id))
          self.shmem['__set_loop_stage_func'] = self.set_loop_stage
          # debug when configuring a plugin
          debug_config_changes = self.config_data.get('PLUGINS_DEBUG_CONFIG_CHANGES', False) # Ugly but needed
          # end debug
          
          _module_version = _config_dict.get('MODULE_VERSION', '0.0.0')
          
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

        if plugin is None:
          continue

        self.P("New plugin instance {} added for exec.".format(plugin), color='g')
        if self._run_on_threads:
          plugin.start_thread()
      #endif new instance
      else:
        # I do have the instance, I just need to modify the config
        plugin = self._dct_current_instances[instance_hash]
        if plugin is not None:
          # next we need to check if the config has changed and handle also the particular
          # case when the plugin just received a INSTANCE_COMMAND
          plugin.maybe_update_instance_config(
            upstream_config=upstream_config,
            session_id=session_id,
            modified_by_addr=modified_by_addr,
            modified_by_id=modified_by_id,
          )
          self.set_loop_stage('2.bm.refresh.maybe_update_instance_config.DONE: {}:{}:{}'.format(stream_name, signature, instance_id))
        #endif
      #endif

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
        
        plugin.add_inputs(inputs)

        plugin.execute()
      else:
        # if the process is running (default) on thread we just need to add
        # data to its inputs queue
        plugin.add_inputs(inputs)
      #endif

    #endfor
    self.log.stop_timer('execute_all_business_plugins', skip_first_timing=False)
    return


#global dependencies
import json
import re
import abc
import traceback
from collections import OrderedDict
from multiprocessing import Process
from time import time, sleep

#local dependencies
from naeural_core import constants as ct
from naeural_core import Logger
from naeural_core.local_libraries import _ConfigHandlerMixin
from naeural_core.serving.mixins_base import (
  _InferenceUtilsMixin,
)
from naeural_core.utils.system_shared_memory import NumpySharedMemory, replace_shm_with_ndarray, replace_ndarray_with_shm

# from naeural_core.serving.base.serving_utils import CommEngine


_CONFIG = {

  "SERVING_TIMERS_IDLE_DUMP"              : 3601,
  "SERVING_TIMERS_IDLE_DUMP_DEFAULT"      : 1801,
  "SERVING_TIMERS_PREDICT_DUMP"           : 901,
  "SERVING_TIMERS_PREDICT_DUMP_DEFAULT"   : 601,

  "CLOSE_IF_UNUSED"                       : False,
  
  "MAX_WAIT_TIME"                         : 5,
  
  "MODEL_ZOO_CONFIG"                      : {},
  "CUSTOM_DOWNLOADABLE_MODEL"             : False,
  "CUSTOM_DOWNLOADABLE_MODEL_URL"         : None,
  "PICKED_INPUT"                          : "IMG",
  "RUNS_ON_EMPTY_INPUT"                   : False,
  "MODEL_INSTANCE_ID"                     : None,
  

  ## SERIALIZATION & PERSISTENCE
  'LOAD_PREVIOUS_SERIALIZATION'   : False,
  'SERIALIZATION_SIGNATURE'       : None,
  ## END SERIALIZATION & PERSISTENCE
  
  
  
  'VALIDATION_RULES' : {

  },
}

class ModelServingProcess(
  _ConfigHandlerMixin,
  _InferenceUtilsMixin,
  Process,
):
  """
  This is the basic model serving process class. 
  
  The following rules must be complied for subclassing:
    
    `startup`         : define a one time model setup method
    
    `on_init`         : define a one time model setup method executed after `startup`
    
    `pre_process`     : define a function that prepares (if needed) input for `_predict`. Just
                        return the inputs if not required.
    
    `predict`         : define a predict function that receives `_pre_process_inputs` result
    
    `post_process`    : define a function that post-processes (if needed) the `_predict`
                        output. Just return the function input if no post-proc required
  """

  CONFIG = _CONFIG
  __metaclass__ = abc.ABCMeta

  def __init__(self, 
               server_name : str, 
               comm_eng, 
               inprocess: bool,
               default_config,
               version : str = "0.0.0",
               upstream_config=None,
               full_debug=False, 
               log=None,
               try_predict=True,
               environment_variables=None,
               npy_shm_kwargs=None,
               comm_method=None,
               **kwargs
               ):
    self._done = False
    self.log = log
    self.inprocess = inprocess
    self._full_debug = full_debug
    self._environment_variables = environment_variables
    self.server_name = server_name
    
    # `predict_server_name` initially defaults to actual server and can be overwritten
    # in `predict` with a given covered server    
    self.predict_server_name = server_name 
    # this must be correctly configured by each individual serving process
    # and is redundant for a lot of models
    self._has_second_stage_classifier = False    
    # end `predict_server_name` config
    
    _name = self.server_name if len(self.server_name) < 10 else Logger.name_abbreviation(self.server_name)
    self.__prefix_name = _name
    self.prefix = '[' + _name + ']'
    self.comm_eng = comm_eng
    self.__version__ = version
    self.version = self.__version__
    self._default_config = default_config
    self._upstream_config = upstream_config or {}
    self.config_model = None
    self._startup_failure = False
    self._inprocess_startup_done = False
    self._displayed_first_predict_coverage = False
    self._parents = self.get_server_parents()
    self.TRY_PREDICT = try_predict
    self._stream_index_mapping = None

    self.inputs = None
    self.prep_inputs = None
    self.preds = None
    self.npy_shm_kwargs = npy_shm_kwargs
    self.npy_shm = None
    self.comm_method = comm_method

    super(ModelServingProcess, self).__init__()
    if self.inprocess:
      self.inprocess_startup()
    return
  
  @property
  def _cache_folder(self):
    return ct.CACHE_SERVING
    
  @property
  def plugin_id(self):
    return self.sanitize_name(self.server_name)
  
  @property
  def json(self):
    return json

  @property
  def has_second_stage_classifier(self):
    return self._has_second_stage_classifier and self.server_name == self.predict_server_name

  @property
  def serving_timers_idle_dump(self):
    return max(self.cfg_serving_timers_idle_dump, self.cfg_serving_timers_idle_dump_default)

  @property
  def serving_timers_predict_dump(self):
    return max(self.cfg_serving_timers_predict_dump, self.cfg_serving_timers_predict_dump_default)

  @property
  def close_if_unused(self):
    return self.cfg_close_if_unused if self.ready_cfg_handlers else False

  def sleep(self, sec):
    sleep(sec)

  def get_serving_param(self, key, inference_upstream_config):
    val = inference_upstream_config.get(key, None)
    if val is None:
      val = self.config_model[key]
    return val
  
  def download(self, url, fn):
    kwargs = self.cfg_model_zoo_config 
    return self.log.maybe_download_model(
      url=url,
      model_file=fn,
      **kwargs,
    )

  def get_server_parents(self):
    parents = self.__class__.mro()
    parent_servers = []
    for parent in parents:
      parent_name = parent.__name__
      subnames = re.findall('[A-Z][^A-Z]*', parent_name)
      parent_server = "_".join(subnames).upper()
      if parent_server == 'MODEL_SERVING_PROCESS':
        break
      parent_servers.append(parent_server)
    return parent_servers[1:]
  
  def _start_timer(self, tmr):
    self.log.start_timer(self.server_name + '_' + tmr)
    return
  
  def _stop_timer(self, tmr, periodic=False):
    self.log.stop_timer(self.server_name + '_' + tmr, periodic=periodic)
    return

  def start_timer(self, tmr):
    self.log.start_timer(self.server_name + '_' + tmr)
    return
  
  def stop_timer(self, tmr, periodic=False):
    self.log.stop_timer(self.server_name + '_' + tmr, periodic=periodic)
    return

  def end_timer(self, tmr, periodic=False):
    self.log.stop_timer(self.server_name + '_' + tmr, periodic=periodic)
    return


  def get_config(self):
    return self.config_model
  
  def _get_message(self, wait=False):
    payload, msg_type, msg_data, msg_server_name, msg_id, msg_ts = None, None, None, None, None, None
    if not wait:
      sleep(0.0001)
    if self._full_debug:
      # sleep(1)
      self.P("Running poll...")
    res = self.comm_eng.poll(0.5)
    if wait or res:
      if self._full_debug and res:
        self.P("{} has data and will `recv`".format(self.__class__.__name__), color='y')
      payload = self.comm_eng.recv()
        
    if payload is not None:
      if self._full_debug:
        self.P(f'Received payload with keys: {payload.keys()}')
        self.P(f'payload[`SERVER_NAME`]={payload.get("SERVER_NAME")}')
      msg_type = payload['TYPE']
      msg_data = replace_shm_with_ndarray(payload['DATA'], self.npy_shm, debug=self._full_debug)
      msg_server_name = payload['SERVER_NAME']
      msg_id = payload['CMD_ID']
      msg_ts = payload['TIMESTAMP']
      elapsed = self.time() - msg_ts
      if self._full_debug:
        self.P("  {}:{} received comand {}:{} and data type '{}'[Elapsed: {}s]".format(
          self.__class__.__name__, msg_server_name, msg_type, msg_id,
          msg_data.__class__.__name__, round(elapsed, 3)), color='y'
        )
    return msg_type, msg_data, msg_server_name, msg_id, msg_ts
  
  
  def get_name(self):
    return self.__class__.__name__, self.server_name, self.name
  
  
  def _send_message(self, command, inputs, send_timers=False, cmd_id=None):
    timers_val = self.log.export_timers_section() if send_timers else None
    if timers_val is not None:
      self.P("Sending timers (len {})".format(len(timers_val[0])))
    dct_msg = {
      'TYPE'    : command,
      'DATA'    : inputs,
      'TIMERS'  : timers_val,
      'CMD_ID'  : cmd_id,
      'TIMESTAMP': self.time(),
    }
    if self._full_debug:
      self.P("Sending '{}' for cmd_id:{} to manager".format(command, cmd_id))
    self.comm_eng.send(dct_msg)
    if self._full_debug:
      self.P("  '{}' for cmd_id:{} sent to manager".format(command, cmd_id))
    return
  
  def _dump_error(self, data):
    str_err = traceback.format_exc()
    fn_input = self.log.save_pickle(
      data=data, 
      fn='{}_predict_error.pkl'.format(self.__class__.__name__),
      folder='output',
      use_prefix=True,
      )
    fn_text = self.log.get_output_folder() + "/{}_{}_predict_error.txt".format(
      self.log.file_prefix,
      self.__class__.__name__,
      )
    with open(fn_text, "wt") as fp:
      fp.write(str_err)
    return fn_input, fn_text
  
  def _create_logger(self):
    self.log = Logger(
      lib_name=self.__prefix_name,
      base_folder='.',
      app_folder=ct.LOCAL_CACHE,
      TF_KERAS=False,
      default_color=ct.COLORS.SERVING,
    )      
    return

  def create_numpy_shared_memory(self):
    """
    Create a numpy shared memory object for accessing data loaded by the serving manager.
    Returns
    -------

    """
    if self.npy_shm_kwargs is not None and self.comm_method != 'pipe':
      self.npy_shm = NumpySharedMemory(
        **self.npy_shm_kwargs,
        create=False,
        log=self.log
      )
    return

  def inprocess_startup(self):
    if self._inprocess_startup_done:
      self.P("inprocess_startup` already called. Bypassing.", color='y')
      return
    if self.inprocess:
      # now we prepare config
      self._setup_config()
      _ = self._startup() 
      self._inprocess_startup_done = True
    else:
      raise ValueError('`inprocess_startup` called without actual inprocess serving process!')
    return

  def _pack_results(self, results):
    res = OrderedDict()
    # TODO: should the additional be added after the rest?
    meta = OrderedDict()
    additional_meta = self.get_additional_metadata()
    if isinstance(additional_meta, dict):
      meta.update(additional_meta)
    else:
      meta['ADDITIONAL_META'] = additional_meta
    # endif additional meta
    
    if results is None or len(results) == 0:
      meta[ct.ERROR] = 'No inputs received for inference'
      results = [[]]

    meta[ct.SYSTEM_TIME] = self.log.now_str(nice_print=True, short=False)
    meta[ct.VER] = self.__version__
    meta['PICKED_INPUT'] = self.cfg_picked_input
    res['INFERENCES_META'] = meta
    
    if len(self._stream_index_mapping) == 0:
      res[ct.INFERENCES] = results
    else:
      res[ct.INFERENCES] = []
      for stream_name, indexes in self._stream_index_mapping.items():
        crt_stream_results = []
        for i in indexes:
          crt_stream_results.append(results[i])
        res[ct.INFERENCES].append(crt_stream_results)
      #endfor
    #endif
    
    return res

  def _input_validator(self):
    pass ###TODO

  def _pick_inference_inputs(self, inputs, data_type):
    """
    Inputs standard:
    [
      {
        "STREAM_NAME" : "xxxxx",
        "STREAM_METADATA" : {"k1" : v1, "k2" : v2, ...},
        "INPUTS" : [
          {
            "IMG" : np.ndarray,
            "STRUCT_DATA" : None,
            "INIT_DATA" : None,
            "METADATA" : {"k1" : v1, "k2" : v2, ...},
            "TYPE" : "IMG"
          }
        ],
        "SERVING_PARAMS" : {...},
      }
    ]

    - `inputs` is a batch (list) of stream collected data (because the inference is done batchified on all streams that use the same model)
    - each element of `inputs` is a dictionary having the following keys:
      * STREAM_NAME
      * STREAM_METADATA
      * INPUTS
      * SERVING_PARAMS

    - `inputs[i]['SERVING_PARAMS']` defines the upstream configuration for the current serving plugin 

    - `inputs[i]['INPUTS']` is also a list (a stream can collect data from multiple sources - multi-modal or meta-stream).
      Each element in this list is basically a sub-stream.
      When we have to deal with simple cases (a stream collects a video frame and that'a all), `inputs[i]['INPUTS']` is a list of 1 element.

    - each element of `inputs[i]['INPUTS']` is a dictionary having the following keys:
      * IMG - if the sub-stream returned an image
      * STRUCT_DATA - if the sub-stream returned a chuck of structured data
      * INIT_DATA - if the sub-stream returned some initial data
      * METADATA - sub-stream metadata
      * TYPE: "IMG" or "STRUCT_DATA" (they are mutual exclusive and cannot co-exist, i.e. a sub-stream returns either IMG, or STRUCT_DATA)

    """

    dct_picked = {
      'DATA' : [],
      'INIT_DATA' : [],
      'SERVING_PARAMS' : [],
      'STREAM_NAME' : [],
      'EMPTY' : False
    }

    for stream_input in inputs:
      stream_name = stream_input.get('STREAM_NAME')
      if stream_name is None:
        continue
      self._stream_index_mapping[stream_name] = []
      lst_sub_stream_inputs = stream_input.get('INPUTS') or []
      _serving_params = stream_input.get('SERVING_PARAMS', {})

      for _input in lst_sub_stream_inputs:
        input_type = _input.get('TYPE')
        if input_type == data_type:
          picked_data = _input.get(data_type)
          if picked_data is None:
            continue
          dct_picked['DATA'].append(picked_data)
          dct_picked['SERVING_PARAMS'].append(_serving_params)
          dct_picked['INIT_DATA'].append(_input.get('INIT_DATA'))
          dct_picked['STREAM_NAME'].append(stream_name)
          self._stream_index_mapping[stream_name].append(len(dct_picked['DATA'])-1)
        #endif
      #endfor
    #endfor

    if len(dct_picked['STREAM_NAME']) == 0:
      dct_picked['EMPTY'] = True

    return dct_picked

  def maybe_log_phase(self, phase, start_time, done=True):
    if self._full_debug:
      prefix = 'Done' if done else 'Started'
      self.P(f'[DEBUG]{prefix} {phase} for {self.server_name}[Elapsed: {round(time() - start_time, 3)}]')
    return

  def __predict(self, inputs, **kwargs):
    """
    Parameters:
    -----------
    inputs: dict, mandatory
      pass
    """
    start_time = time()
    self.inputs = inputs
    if inputs.pop('EMPTY', False):
      outputs = []
    else:
      # PRE-PROCESS
      self.maybe_log_phase('_pre_process', start_time, done=False)
      self._start_timer(ct.TIMER_PRE_PROCESS)
      prep_inputs = self._pre_process(inputs)
      self.prep_inputs = prep_inputs
      self._stop_timer(ct.TIMER_PRE_PROCESS)
      self.maybe_log_phase('_pre_process', start_time)

      # INFERENCE/PREDICTION
      self.maybe_log_phase('_predict', start_time, done=False)
      self._start_timer(ct.TIMER_RUN_INFERENCE)
      preds = self._predict(prep_inputs)
      self.preds = preds  # TODO: review why this is here
      self._stop_timer(ct.TIMER_RUN_INFERENCE)
      self.maybe_log_phase('_predict', start_time)

      # POST-PROCESS
      self._start_timer(ct.TIMER_POST_PROCESS)
      outputs = self._post_process(preds)
      self._stop_timer(ct.TIMER_POST_PROCESS)
      self.maybe_log_phase('_post_process', start_time)
    #endif

    # TO DICT
    self._start_timer(ct.TIMER_PACK_RESULTS)
    results = self._pack_results(outputs)
    self._stop_timer(ct.TIMER_PACK_RESULTS)
    self.maybe_log_phase('_pack_results', start_time)

    return results
  
  
  def _in_process_predict(self, inputs, **kwargs):
    return self.__loop_predict(inputs, **kwargs)
  
  def __loop_predict(self, inputs, **kwargs):
    """
    Parameters:
    ----------
    inputs: list[object], mandatory
      List of all inference inputs.
      If the list contain dictionaries, it means that the inputs are collected and prepared in Orchestrator's main loop;
        thus they should be "picked" by `self._pick_inference_inputs`
      If the list contain other types, it means that the inputs are raw.
    """
    assert isinstance(inputs, list) and len(inputs) >= 1 or self.cfg_runs_on_empty_input

    self._start_timer(ct.TIMER_PREDICT)
    self._stream_index_mapping = {}
    self.kwargs = kwargs
    
    self.predict_server_name = kwargs.get('server_name', self.server_name)
    if not self._displayed_first_predict_coverage:
      self._displayed_first_predict_coverage = True
      if self.predict_server_name != self.server_name:
        self.P("Running '{}' for covered server '{}'".format(self.server_name, self.predict_server_name))

    dct_picked = {
      'DATA': inputs,
      'EMPTY': False
    }
    if isinstance(inputs[0], dict):
      dct_picked = self._pick_inference_inputs(inputs, self.cfg_picked_input)
    #endif
    if self.cfg_runs_on_empty_input:
      dct_picked['EMPTY'] = False
    # endif empty input allowed

    if self.TRY_PREDICT:
      try:
        results = self.__predict(dct_picked, **kwargs)
      except:
        res = self._dump_error(inputs)
        msg = "Error raised in {}.predict(). Dump prepared. Please see error detailed info here: {}".format(self.__class__.__name__, res)
        raise ValueError(msg)
    else:
      results = self.__predict(dct_picked, **kwargs)

    self._stop_timer(ct.TIMER_PREDICT)
    return results
  
  
  def P(self, msg, color=None, **kwargs):
    if self.inprocess:
      prefix = self.prefix + f'[{self.pid}] '
    else:
      prefix = f'[{self.pid}] '

    if color not in ['e', 'r']:
      color = ct.COLORS.SERVING
    self.log.P(prefix + msg, color=color, **kwargs)
    return
  
  def _predict_iter_debug_handle(self, curr_iter, n_thr=100):
    TEST = None # sleep, crash or None
    if curr_iter >= n_thr and isinstance(TEST, str):
      TEST = TEST.lower()      
      self.P("DEBUG predict iter {}: executing TEST='{}'".format(
        curr_iter, TEST), color='r'
      )
      if TEST == 'sleep':
        sleep_time = 120
        self.P("  Sleeping for {} sec to annoy the SM and kill the current process".format(
          sleep_time), color='r'
        )
        sleep(sleep_time)
      elif TEST == 'crash':
        raise ValueError("Exceptionen!")            
      else:
        self.P("  Unknown TEST='{}'".format(TEST), color='r')
      # end tests
    # end debug threshold
    return
  
  def run(self):
    if self.inprocess:
      raise ValueError("Something is wrong: `{}.run` called for a inprocess serving process".format(
        self.__class__.__name__))
    try:
      # all following code is "protected" in one big exception catching block, thus any
      # major exception will stop the process and announce the Serving Manager
      # so we do not necesarely need higher levels of exception handling unless 
      # those errors are minor and we can work around them. 
      n_predicts = 0
      self._create_logger() # uses only server_name
      self.create_numpy_shared_memory()
      # prepare config
      self._setup_config() 
      
      # startup: finally this is where the model (should be) is created in theory
      self._startup_failure = True # we assume anything can go wrong
      self._start_timer('startup')
      _msg = self.__startup() 
      self.__on_init()
      self._stop_timer('startup')
      self._startup_failure = False # all passed
      
      
      tm_last_timers_dump = None
      if _msg is None:
        _msg = "{} ({}) `_startup` done.".format(self.server_name, self.name)
      self._send_message(command='READY', inputs=_msg)
      self.P("{} is waiting for commands...".format(self.server_name), color='g')
      must_send_timers = False
      while not self._done:
        cmd, data, server_name, cmd_id, ts = self._get_message(wait=False)
        # now we parse the commands
        if cmd is not None:
          if cmd.upper() == 'STOP':
            self.P("Received 'STOP' for {}.".format(self.server_name))
            self._done = True
          # end STOP
          elif cmd.upper() == 'PREDICT':
            if self._full_debug:
              self.P(f'[DEBUG]Received PREDICT for {self.server_name}[Elapsed: {round(self.time() - ts, 3)}]')
            res = self.__loop_predict(data, server_name=server_name)
            if self._full_debug:
              self.P(f'[DEBUG]Done PREDICT for {self.server_name}[Elapsed: {round(self.time() - ts, 3)}]')
            n_predicts += 1
            if tm_last_timers_dump is None or (time() - tm_last_timers_dump) > self.serving_timers_predict_dump:
              self.P("Predict timers dump time {}s reached. Dumping timers and sending to Serving Manager".format(self.serving_timers_predict_dump))
              tm_last_timers_dump = time()
              self.log.show_timers(color='d')
              must_send_timers = True
            # now we can write a run-each-n-predicts debug section
            self._predict_iter_debug_handle(curr_iter=n_predicts) # DEBUG method
            # send message with the result if managed so far
            self._send_message(
              command='RESULT', 
              inputs=res,
              send_timers=must_send_timers,
              cmd_id=cmd_id,
            )
            if self._full_debug:
              self.P(f'[DEBUG]Sent RESULT for {self.server_name}[Elapsed: {round(self.time() - ts, 3)}]')
            must_send_timers = False
          # end PREDICT
          elif cmd.upper() == 'GET_CONFIG':
            self.P("Sending config to the serving manager...")
            self._send_message(
              command='GET_CONFIG',
              inputs=self.get_config(),
              send_timers=False,
            )
          # end GET_CONFIG
          else:
            self.P("Received unknown command '{}'".format(cmd.upper()), color='r')
          # endif select cmd type
        else:
          # TODO: add on_idle method here
          # now we are on IDLE - no cmd received so we can do quick internal kitchen
          if tm_last_timers_dump is None or (time() - tm_last_timers_dump) > self.serving_timers_idle_dump:
            self.P("Idle timers dump {}s reached. Dumping timers".format(self.serving_timers_idle_dump))
            tm_last_timers_dump = time()
            self.log.show_timers(color='d')
            must_send_timers = True
        #endif command or none
      #endwhile main loop
      self.P("Gracefully closing Model Server '{}'".format(self.server_name), color='r')
    except:
      str_except = traceback.format_exc()
      if self._startup_failure:
        _error = 'STARTUP_FAILURE'
      else:
        _error = 'ABNORMAL_EXIT'
      self.P("Exception in {}:\n{}".format(
                self.__class__.__name__, 
                str_except
              ),
              color="r",
            )
      self._send_message(command=_error, inputs=str_except)
    #end try-except
    self.P("Closing Serving Process execution loop...")
    # TODO: cleanup stuff
    self.shutdown(from_parallel=True)
    self.log.show_timers()
    return
      
  def shutdown(self, from_parallel=False):
    if self.inprocess:
      self.P("Shutting down inprocess server...")
    elif not from_parallel:
      self.P("WARNING: called shutdown on a parallel process without explicit knowledge!")
    else:
      self.P("Shutting down parallel process!")
    if self.npy_shm is not None:
      self.npy_shm.shutdown()
    self._shutdown()
    return

  def __on_init(self):
    self._on_init()
    return
  
  def __startup(self):
    return self._startup()    
  # START: SUCCLASS MUST IMPLEMENET
    
  
  def startup(self):
    """
    This method is called at startup. It can be used to prepare the model.
    """
    return
  
  def _startup(self):
    return self.startup()
    
  
  def on_init(self):
    """
    This method is called post startup. It can be used for additional prep if needed.
    """
    return
    
  def _on_init(self):
    self.on_init()
    return

  def get_additional_metadata(self):
    """
    This method is called to add metadata to what is sent to the serving manager.
    Returns
    -------

    dict
        The additional metadata to be sent to the serving manager
    """
    return {}

  @abc.abstractmethod
  def pre_process(self, inputs):
    """
    This method is called before the actual prediction. It can be used to prepare the inputs.
    The implementation is specific for each serving process.
    """
    raise NotImplementedError()
  
  def _pre_process(self, inputs):
    return self.pre_process(inputs)


  @abc.abstractmethod
  def predict(self, prep_inputs):
    """
    This method is called to perform the actual prediction. It can be used to perform the prediction.
    The implementation is specific for each serving process.
    """
    raise NotImplementedError()
      
  def _predict(self, prep_inputs):
    return self.predict(prep_inputs)


  @abc.abstractmethod
  def post_process(self, preds):
    """
    This method is called after the actual prediction. It can be used to post-process the predictions.
    The implementation is specific for each serving process.
    """
    raise NotImplementedError()
  
  def _post_process(self, preds):
    return self.post_process(preds)
  # END: SUCCLASS MUST IMPLEMENET


  # START: SUBCLASS CAN OVERWRITE

  def _shutdown(self):
    return

  def _setup_config(self):
    """
    if subclassed, mandatory call super()._setup_config() in first line
    """
    dct_cfg = None
    dct_env = self._environment_variables # here we can use self._environment_variables["SERVING_PROCESS_CONFIG"]
    if isinstance(dct_env, dict) and len(dct_env) > 0:
      # update default config with environment
      self.P("Updating model config with 'SERVING_ENVIRONMENT' config...")
      dct_cfg = self._merge_prepare_config(delta_config=self._environment_variables)
    else:
      self.P("No 'SERVING_ENVIRONMENT' config update.")
    # now update dct_cfg with upstream
    self.P("Updating model config with upstream config...")
    self.config_model = self._merge_prepare_config(
      default_config=dct_cfg,
      delta_config=self._upstream_config,
    )

    self.setup_config_and_validate(self.config_model)

    return
  # END: SUBCLASS CAN OVERWRITE

  def validate_custom_downloadable_model(self):
    if self.cfg_custom_downloadable_model:
      if self.cfg_custom_downloadable_model_url is None:
        self.add_error("Attempted to create a CUSTOM_DOWNLOADABLE_MODEL without a definition URL")

    return
"""

"""
# global dependencies
import os
import inspect
import numpy as np
import traceback
import json

from time import time, sleep
from copy import deepcopy
from collections import deque, OrderedDict
from functools import partial
from threading import Thread

# local dependencies
from naeural_core import constants as ct
from naeural_core.business.mixins_base.threading import _ThreadingAPIMixin
from naeural_core.main.net_mon import NetworkMonitor

from naeural_core.business.base.base_plugin_biz_loop import _BasePluginLoopMixin
from naeural_core.business.base.base_plugin_biz_api import _BasePluginAPIMixin

from naeural_core.business.mixins_libs import (
  _TimeBinsMixin, _PoseAPIMixin,
  _AlertTrackerMixin
)


from naeural_core import Logger
from naeural_core import DecentrAIObject
from naeural_core.local_libraries import _ConfigHandlerMixin
from naeural_core.business.mixins_base import (
  _AlerterMixin, _ShmMixin,
  _EmailerMixin, _DailyIntervalMixin,
  _DataAPIMixin, _WorkingHoursMixin,
  _IntervalAggregationMixin,
  _UploadMixin, _CmdAPIMixin, _ExecutionAuditMixin,
  _GenericUtilsApiMixin, _DiskAPIMixin, _DeAPIMixin,
  _DatasetBuilderMixin, _StateMachineAPIMixin,
)

from naeural_core.utils.mixins.code_executor import _CodeExecutorMixin

from naeural_core.data_structures import GeneralPayload
from naeural_core.utils.config_utils import get_now_value_from_time_dict
from naeural_core.business.test_framework.testing_manager import TestingManager
from naeural_core.business.test_framework.scoring_manager import ScoringManager

from naeural_core.utils.plugins_base.bc_wrapper import BCWrapper

_CONFIG = {

  'INSTANCE_COMMAND': {},  # one time command-trigger
  'INSTANCE_COMMAND_LAST': {},  # data witness of the last INSTANCE_COMMAND
  'INSTANCE_COMMAND_LOG': True,  # log the instance command - default TRUE maybe FALSE in future ?

  'COPY_IN_MAIN_THREAD': False,  # flag to switch between copying data in main thread on in plugin instance thread
  'ENCRYPT_PAYLOAD': False,  # flag to independent toggle payload encryption
  "USE_LOCAL_COMMS_ONLY": False, # flag to switch to local comms only for a particular plugin instance

  'RESEND_LAST_STATUS_ON_IDLE': 0,

  'FORCED_PAUSE': False,
  

  # set this to 1 for real time processing (data will be lost and only latest data be avail)
  # when PROCESS_DELAY is used this should be either bigger than 1 if we want to have previous data
  # but (more likely) set it to 1 just to "see" current data ("real time at processing time")

  # 'REQUIRES_INPUTS_LOCAL_COPY' TODO: implement safe plugins only - default True for user plugins

  'MAX_INPUTS_QUEUE_SIZE': 1,
  'QUEUE_OVERFLOW_SLEEP_TIME': 0.01,

  'LOG_ON_BLOB': False,

  # "WORKING_HOURS"
  #  this should be one of the following options:
  #     - list of lists (as is)
  #     - dict with "weekday" : list of lists
  #     - dict with "label" : dict( containing 'INTERVALS' : list-of-lists, weekday, params...) dictionary {"i1" : {"INTERVAL" : [start, end], "PARAM1": ...}}
  # ==>> must modify only `working_hours_current_interval` and must add `params_current_interval`
  #
  'WORKING_HOURS_TIMEZONE': None,
  'WORKING_HOURS': [],
  'IGNORE_WORKING_HOURS': False,

  'PLUGIN_LOOP_RESOLUTION': 20,   # proposed loop res
  'FORCED_LOOP_SLEEP': None,  # use predefined sleep time

  'CLOSE_PIPELINE_WHEN_DONE': False,

  'CONFIDENCE_THRESHOLD': 0.2,

  'RESTART_ALERTERS_ON_CONFIG': True,
  'RESTART_SHMEM_ON_CONFIG': True,
  'THREAD_SAFE_DRAWING': True,

  'NO_WITNESS': False,

  'AI_ENGINE': [],
  "INFERENCE_AI_ENGINE_PARAMS": {
  },
  "STARTUP_AI_ENGINE_PARAMS": {
  },

  # alerter zone
  "ALERT_RAISE_CONFIRMATION_TIME": 0,
  "ALERT_LOWER_CONFIRMATION_TIME": 1,
  "ALERT_DATA_COUNT": 2,
  "ALERT_RAISE_VALUE": 0.5,
  "ALERT_LOWER_VALUE": 0.4,
  "ALERT_MODE": 'mean',
  "ALERT_MODE_LOWER": 'max',
  "ALERT_REDUCE_VALUE": False,
  "AUTO_FORCE_LOWER": 0,
  # end alerter zone


  # if ALLOW_EMPTY_INPUTS is set to true the on-idle will trigger continously the process
  # default is False, set to True if we want to process empty inputs
  'ALLOW_EMPTY_INPUTS': False,

  'SIMPLE_WITNESS': False,  # only the simple picture is returned

  'PROCESS_DELAY': 0,  # minimum time between two consecutive process calls
  'MAX_PROCESS_DELAY': None,  # maximum time between two consecutive process calls


  'ORIGINAL_FRAME': True,
  'RESIZE_WITNESS': [],



  # debug & testing
  'SIMULATE_CRASH_EXEC_ITER': None,  # set this to a nr to simulate a exec crash at that process iter
  'SIMULATE_CRASH_EXEC_SINGLE': True,  # just one crash
  'SIMULATE_CRASH_LOOP': False,  # set this to a nr to simulate a loop crash at that process iter

  'DEBUG_PAYLOADS': False,  # TO BE CLARIFIED

  'DEBUG_MODE': False,


  'DEBUG_SAVE_IMG': False,  # saves in _output/[STREAM]__[SIGN]__[INST] the witness & originals
  'DEBUG_SAVE_IMG_ARCHIVE': 30,     # no file tuples to archive & delete


  # debug save upload migrated from heavy ops
  'DEBUG_SAVE_PAYLOAD': False,  # basic saving of payload as csv in GeneralPayload object

  # next two are not currently implemented
  'DEBUG_SAVE_ARCHIVE_M': 60,  # How often the debug upload should be performed (in minutes)
  'DEBUG_SAVE_UPLOAD': False,

  'TESTING': {},

  'DEBUG_SAVE': False,  # this is left here for legacy/history SaveImageHeavyOps


  'SECONDS_EXPORT': 0,        # REVIEW:: musth check
  # end debug and testing

  # Timebins
  'WEEKDAY_NAMES': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'],
  'REPORT_DEFAULT_EMPTY_VALUE': None,
  'PER_DAY_OF_WEEK_TIMESLOT': True,   # True if we want to report day and hour vs hour
  'DAILY_INTERVALS': 144,  # split the day in equal intervals
  'WARMUP_ANOMALY_MODELS': 10,  # how many datapoins required for anomaly model inference
  # end Timebins

  # plugin level data availability control
  'ALERT_ON_NO_DATA': False,
  'ALERT_ON_NO_DATA_INTERVAL': 30,
  'ALERT_ON_NO_DATA_REPEAT': 60,
  # end plugin level data availability control


  # couple of keys for custom/rest plugins manifest sending (and any other plugins that send manifests)
  'SEND_MANIFEST_EACH': 300,
  'LOG_MANIFEST_SEND': False,



  'ENABLE_AUDIT': False,

  'TAGS': '',
  'ID_TAGS': [],
  'SAVE_INTERVAL': 5 * 60,   # REVIEW: must check
  'MAX_IDLE_TIME': 5,        # REVIEW: must check

  'LINKED_INSTANCES': [],

  "DATASET_BUILDER": None,

  # SERIALIZATION & PERSISTENCE
  'LOAD_PREVIOUS_SERIALIZATION': False,
  'SERIALIZATION_SIGNATURE': None,
  # END SERIALIZATION & PERSISTENCE


  'VALIDATION_RULES': {
    'FORCED_PAUSE': {
      'DESCRIPTION': 'Will STOP the instance execution until is not `True` anymore',
      'TYPE': 'bool',
    },

    'AUTO_FORCE_LOWER': {
      'DESCRIPTION': 'If non zero will force alerter from Raised to Lowered after the given amount of seconds. WARNING: While this is a generic and universal approach, some plugins have own specific internal lowering mechanics',
      'TYPE': 'float'
    },

    'RESEND_LAST_STATUS_ON_IDLE': {
      'DESCRIPTION': 'If above 0 will resend the instance status (last payload) each given number of seconds from last idle',
      'TYPE': 'float',
    },

    'WORKING_HOURS': {
      'DESCRIPTION': 'Define working hours intervals. Default is full time, otherwise list of [[start, end], [start, end]] strings',
      'TYPE': ['list', 'dict']
    },

    'MAX_INPUTS_QUEUE_SIZE': {
      'DESCRIPTION': "Advanced setting: Controls the queue size of the plugin instance. set this to 1 for real time & overwrite (for example when PROCESS_DELAY is used and we need to process only the 'live' data)",
      'TYPE': 'int',
      'MIN_VAL': 1,
      'MAX_VAL': 512,
    },

    'PROCESS_DELAY': {
      'DESCRIPTION': 'Imposes a specific delay in seconds between business plugin execution cycles - i.e. 5 means cycles are executed each 5 seconds',
      'TYPE': 'float',
      'MIN_VAL': 0,
      'MAX_VAL': 24 * 3600 + 1,  # max delay is one day
    },

    'ALERT_ON_NO_DATA': {
      'DESCRIPTION': 'Automatic alert is raised if the business plugin does not receive data for `ALERT_ON_NO_DATA_INTERVAL` seconds',
      'TYPE': 'bool',
    },

    'ALERT_ON_NO_DATA_INTERVAL': {
      'DESCRIPTION': "Maximal time accepted for a biz plugin to 'work' without data - only valid if `ALERT_ON_NO_DATA = true`",
      'TYPE': 'int',
    },

    'AI_ENGINE': {
      'DESCRIPTION': "AI Engine that will process data upstream from biz plugin. String name or list of strings if multiple AI engines are required.",
      'TYPE': ['str', 'list'],
    },

    'STARTUP_AI_ENGINE_PARAMS': {
      'TYPE': 'dict',
      'DESCRIPTION': 'This configuration dict will be send as startup params during Serving Process creation, thus can overwrite various things such as "DEFAULT_DEVICE"',
    },

    'INFERENCE_AI_ENGINE_PARAMS': {
      'TYPE': 'dict',
      'DESCRIPTION': 'This configuration dict will be added for each individual inference run in "SERVING_PARAMS" key beside "DATA" key during eache `predict`',
    },

    'CLOSE_PIPELINE_WHEN_DONE': {
      'DESCRIPTION': "User flag that can be used to call a `cmdapi_archive_pipeline()`",
      'TYPE': 'bool',
    },

    'SEND_MANIFEST_EACH': {
      "DESCRIPTION": "Idle time that will trigger manifest generation for certain plugins that require this",
      "TYPE": "float"
    },

    'WEEKDAY_NAMES': {
      "DESCRIPTION": "Name of week days, used for the formatted report",
      "TYPE": "list"
    },
    # 'REPORT_DEFAULT_EMPTY_VALUE' : {
    #   # TODO(AID): help decide on this
    #   "DESCRIPTION": "Default value for aggregation function, used for the formatted report when timebins contain no elements",
    #   "TYPE": "obj",
    # },
    'PER_DAY_OF_WEEK_TIMESLOT': {
      "DESCRIPTION": "Controlls if the timebins report contains weekdays or not (on false it only reports intervals, on true it reports intervals by days)",
      "TYPE": "bool"
    },
    'DAILY_INTERVALS': {
      "DESCRIPTION": "Number of intervals in which to split a day (24 means intervals of one hour, 144 means intervals of 10 minutes)",
      "TYPE": "int"
    },
    'WARMUP_ANOMALY_MODELS': {
      "DESCRIPTION": "Number of timesteps required for anomaly model warmup. Defaults to 10",
      "TYPE": "int"
    },

    'LINKED_INSTANCES': {
      "DESCRIPTION": "List of tuple/lists [<STREAM_NAME>,<INSTANCE_ID>] that identify all the 'subordinates' of the current instances",
      "TYPE": "list",
    }
  }
}


class BasePluginExecutor(
  DecentrAIObject,
  _ConfigHandlerMixin,
  _AlerterMixin,
  _ShmMixin,
  _EmailerMixin,
  _DataAPIMixin,
  _DeAPIMixin,
  _ThreadingAPIMixin,
  _WorkingHoursMixin,
  _IntervalAggregationMixin,
  _DailyIntervalMixin,
  _UploadMixin,
  _CmdAPIMixin,
  _ExecutionAuditMixin,
  _GenericUtilsApiMixin,
  _DiskAPIMixin,
  _TimeBinsMixin,
  _CodeExecutorMixin,
  _DatasetBuilderMixin,
  _AlertTrackerMixin,
  _PoseAPIMixin,
  _BasePluginLoopMixin,
  _BasePluginAPIMixin,
  _StateMachineAPIMixin,
):
  CONFIG = _CONFIG

  def __init__(self, log: Logger,
               global_shmem,
               plugins_shmem,
               stream_id,
               signature,
               default_config,
               upstream_config,
               environment_variables=None,
               version="0.0.0",
               initiator_id=None,
               initiator_addr=None,
               session_id=None,
               threaded_execution_chain=True,
               payloads_deque=None,
               commands_deque=None,
               ee_ver=None,
               runs_in_docker=False,
               docker_branch='main',
               debug_config_changes=False,
               pipeline_use_local_comms_only=False,
               **kwargs):
    self.__version__ = version

    self.__global_shmem = global_shmem  # global access to various engines - not allowed in plugin code

    self.__blockchain_manager = global_shmem[ct.BLOCKCHAIN_MANAGER]  # blockchain manager

    self.bc = BCWrapper(self.__blockchain_manager)  # blockchain wrapper (for encryption/decryption

    self.__plugins_shmem = plugins_shmem  # plugins shared memory

    self.__debug_config_changes = debug_config_changes

    self._painter = None
    self.__ee_ver = ee_ver
    self.__runs_in_docker = runs_in_docker
    self.__docker_branch = docker_branch
    # TODO: change all to PRIVATE

    self.__stream_id = stream_id
    self.__signature = signature
    self._default_config = default_config
    self._upstream_config = upstream_config
    self._environment_variables = environment_variables
    self.__initiator_id = initiator_id
    self.__initiator_addr = initiator_addr
    self.__modified_by_id = self.__initiator_id  # default modified by is the initiator
    self.__modified_by_addr = self.__initiator_addr  # default modified by is the initiator
    self._session_id = session_id
    self._signature_hash = None
    self._threaded_execution_chain = threaded_execution_chain
    self._timers_section = None

    self._instance_config = None
    
    self.__pipeline_use_local_comms_only = pipeline_use_local_comms_only

    log.P("Init {} v{} <{}:{}> SUPER:{}".format(
      self.__class__.__name__, self.__version__,
      self.initiator_id, self.initiator_addr, self.is_supervisor_node,
    ), color=ct.COLORS.BIZ
    )

    # error handling vars

    self._last_data_input = time()  # last time the loop encountered data
    self._last_alert_on_no_data = time()
    self.no_data_alerts_count = 0

    self.lost_inputs_count = 0
    self.queue_full_warning = False
    self.queue_full_delays_count = 0
    self._last_logged_queue_full_count = 0
    self._last_logged_queue_full_time = time()
    # end error handling vars

    # standard payload & process delay variables
    self.__first_process_time = None       # time of the first process call
    self.__last_process_time = None        # tune of the last process call
    self.__last_payload_process_time = 0  # this is the time of last payload creation
    self.__last_payload_resend_time = 0
    self.__last_payload_nonresend_time = 0
    self.__last_saved = time()
    self.__start_time = time()  # time counter since the plugin is alive, usefull for statefull plugins
    self.__total_payloads = 0
    # end delay vars

    # execution vars
    self.done_loop = False

    self._was_stopped_last_iter = False  # for `FORCED_PAUSE`

    self._plugin_loop_in_exec = False

    self.__dct_last_payload = None  # cache of the last payload, starts with nothing

    self._pre_process_outputs = None
    self._payload = None
    self._commands = []  # see cmdapi mixin
    self.payloads_deque = payloads_deque
    self.commands_deque = commands_deque

    if self.payloads_deque is None:
      self.payloads_deque = deque(maxlen=1000)
    if self.commands_deque is None:
      self.commands_deque = deque(maxlen=1000)

    self.__default_payload_data = {}
    # end execution vars

    # END Execution/loop vars

    self.__upstream_inputs_deque = None  # the variable holding the queue
    self.__inputs = None  # the current inputs

    self._testing_manager: TestingManager = None
    self._scoring_manager: ScoringManager = None

    self._dataset_builder_info = None

    self.config_changed_since_last_process = None

    self.thread = None
    super(BasePluginExecutor, self).__init__(log=log, prefix_log='[PLEX]', **kwargs)
    self.init_timestamp = self.log.now_str(nice_print=True, short=False)
    return

  @property
  def ee_ver(self):
    return self.__ee_ver

  @property
  def runs_in_docker(self):
    return self.__runs_in_docker

  @property
  def docker_branch(self):
    return self.__docker_branch

  @property
  def _stream_id(self):
    return self.__stream_id

  @property
  def _signature(self):
    return self.__signature

  @property
  def global_shmem(self):
    return self.__global_shmem

  @property
  def plugins_shmem(self):
    return self.__plugins_shmem

  @property
  def is_supervisor_node(self):
    """
    Returns `True` if the plugin is running on the supervisor node and `False` otherwise.
    Warning: This property is not safe/trusted while the plugin is initializing due to the 
    fact that the supervisor node may not be set yet within the NET_MON plugin.
    """
    return self.global_shmem.get('is_supervisor_node', False)

  def __repr__(self):
    s = "<stream='{}' sign='{}' inst='{}'>".format(self._stream_id, self._signature, self.cfg_instance_id)
    return s

  def _reset_plugin_instance_data(self, **kwargs):
    # define logic in inherited class
    self.P("  Plugin instance data reset request received", color='m')
    # REVIEW: basic cleanup
    # ...
    return

  def _reset_plugin_instance_alerters(self):
    self.P("  Plugin instance alerters reset request received", color='m')
    self.alerter_hard_reset_all()
    return

  def reset_plugin_instance(self, **kwargs):
    self.P("Resetting {}".format(self), color='m')
    self._reset_plugin_instance_data(**kwargs)
    self._reset_plugin_instance_alerters()
    return

  def _get_methods(self, cls, include_parent=False):
    return self.log.get_class_methods(cls, include_parent=include_parent)

  def reset_default_plugin_vars(self):
    self.__default_payload_data = {}
    return

  def get_default_plugin_vars(self):
    return self.__default_payload_data

  def payload_set_data(self, key, val):
    self.__default_payload_data[key] = val
    return

  def P(self, s, color=None, **kwargs):
    if color is None or (isinstance(color, str) and color[0] not in ['e', 'r']):
      color = ct.COLORS.BIZ
    super().P(s, prefix=True, color=color, **kwargs)
    return

  def __set_loop_stage(self, s, prefix='2.bm.refresh._chkinst.'):
    str_id = '{}:{}:{}-'.format(self._stream_id, self._signature, self._default_config.get('INSTANCE_ID'))
    self.global_shmem['__set_loop_stage_func'](prefix + str_id + s)
    return

  def startup(self):
    super().startup()

    if False:
      # following code has been left here for legacy reasons
      netmon_class_functions = self.log.get_class_methods(self.network_monitor.__class__, include_parent=False)
      for method_name, func in netmon_class_functions:
        if 'network_' in method_name:
          # make aliases to the network_monitor's methods
          setattr(self, method_name, partial(func, self=self.network_monitor))
      # endif netmon disabled approach

    self.__name__ = self.log.name_abbreviation(self._signature)

    self._instance_init()  # will be moved to thread init
    self.maybe_init_ds_builder_saved_stats()
    return

  def _instance_init(self):
    self.__set_loop_stage('_update_instance_config')
    # now update instance config
    self._update_instance_config()
    # end upteda instance condif
    self.__name__ = 'BP:{}>{}>{}'.format(
      self._stream_id,  # leave it like this for clear logging when receiving parallel jobs
      self.log.name_abbreviation(self._signature),  # can be abbreviated
      self.cfg_instance_id,  # maybe better not abbreviated
    )
    if self._threaded_execution_chain:
      self._timers_section = '{}->{}->{}'.format(self._stream_id, self._signature, self.cfg_instance_id)
    else:
      raise ValueError(
        "Non-threaded plugin instance execution is not supported anymore. Supress temporaly this exception with care")

    instance_full_id = (self._stream_id, self._signature, self.cfg_instance_id)
    self._signature_hash = self.log.hash_object(instance_full_id, size=4)

    if (self.cfg_debug_save or self.cfg_debug_save_img) and not os.path.exists(self.save_path):
      os.makedirs(self.save_path, exist_ok=True)

    if bool(self.cfg_testing):
      self.__set_loop_stage('TestingManager')
      self._testing_manager = TestingManager(log=self.log, owner=self)
      self._scoring_manager = ScoringManager(log=self.log, owner=self)

      try:
        self._testing_manager.create_tester(
          name=self.testing_tester_name,
          config=self.testing_tester_config
        )
      except Exception as e:
        self._testing_manager = None
        self.P("Exception when creating tester:\n{}".format(e), color='r')
        self.P("The plugin will switch from testing mode to normal mode.", color='r')

      try:
        self._scoring_manager.maybe_create_scorer(
          name=self.testing_tester_name,
          y_true_src=self.testing_tester_y_true_src
        )
      except Exception as e:
        self._scoring_manager = None
        self.P("Exception when creating scorer:\n{}".format(e), color='r')
    # endif TESTING == True
    self.__upstream_inputs_deque = deque(maxlen=self.cfg_max_inputs_queue_size)
    self.reset_exec_counter_after_config()
    return

  @property
  def instance_hash(self):
    return self._signature_hash

  @property
  def last_payload_time(self):
    return self.__last_payload_process_time

  @property
  def last_payload_time_str(self):
    return self.log.time_to_str(self.__last_payload_process_time)

  @property
  def total_payload_count(self):
    return self.__total_payloads

  @property
  def input_queue_size(self):
    """
    Returns the size of the input queue that is consumed iterativelly
    """
    return len(self.upstream_inputs_deque)

  # TODO: TO BE REMOVED
  @property
  def cfg_collect_payloads_until_seconds_export(self):
    bool_collect = self._instance_config.get('COLLECT_PAYLOADS_UNTIL_SECONDS_EXPORT', True)
    if self.cfg_seconds_export == 0:
      bool_collect = False
    if bool(self.cfg_testing):
      # the testing plugins should register each individual payload
      bool_collect = False
    return bool_collect

  @property
  def cfg_demo_mode(self):
    if self.cfg_debug_mode:
      return True
    return self._instance_config.get('DEMO_MODE', False)

  @property
  def use_local_comms_only(self):
    return self.cfg_use_local_comms_only or self.__pipeline_use_local_comms_only

  @property
  def is_debug_mode(self):
    return self.cfg_debug_mode

  @property
  def is_demo_mode(self):
    return self.cfg_demo_mode

  def get_plugin_loop_resolution(self):
    # plugin must be faster than main loop (or configured as needed)
    return max(
      self.cfg_plugin_loop_resolution,
      3 * self.global_shmem[ct.CALLBACKS.MAIN_LOOP_RESOLUTION_CALLBACK]()
    )

  @property
  def testing_tester_name(self):
    return self.cfg_testing.get('NAME', None)

  @property
  def testing_tester_y_true_src(self):
    return self.cfg_testing.get('Y_TRUE_SRC', None)

  @property
  def testing_tester_config(self):
    return self.cfg_testing.get('TESTER_CONFIG', {})

  @property
  def testing_scorer_config(self):
    return self.cfg_testing.get('SCORER_CONFIG', {})

  @property
  def testing_upload_result(self):
    return self.cfg_testing.get('UPLOAD_RESULT', False)

  @property
  def cfg_cancel_witness(self):
    return self.cfg_no_witness

  def __maybe_reconfigure_process_delay(self):
    max_process_delay = self.cfg_max_process_delay
    curr_process_delay = self.cfg_process_delay
    if max_process_delay is not None:  # for faster checking
      if isinstance(max_process_delay, (int, float)) and curr_process_delay > max_process_delay:
        self.P("WARNING: PROCESS_DELAY {:.1f} > MAX_PROCESS_DELAY {:.1f} - setting to MAX_PROCESS_DELAY".format(
          curr_process_delay, max_process_delay), color='r',
        )
        self.config_data['PROCESS_DELAY'] = max_process_delay
      # endif
    return

  @property
  def is_process_postponed(self):
    self.__maybe_reconfigure_process_delay()
    _elapsed = self.time_from_last_process
    if _elapsed is None or self.current_process_iteration == 0:
      # if elapsed is none (no __last_process_time recorded) OR no actual process has ever occured then allow run
      return False
    # endif

    if _elapsed < self.cfg_process_delay:
      return True
    # endif elapsed < delay thus wait more until elapsed >= delay
    return False

  @property
  def is_plugin_temporary_stopped(self):
    msg = None
    status = None
    notif_code = None
    if self._was_stopped_last_iter and not self.cfg_forced_pause:
      # just received "resume"
      msg = "WARNING: Plugin will now RESUME due to `FORCED_PAUSE` change to {}".format(self.cfg_forced_pause)
      status = "RESUMING"
      notif_code = ct.NOTIFICATION_CODES.PLUGIN_RESUME_OK
    elif not self._was_stopped_last_iter and self.cfg_forced_pause:
      # just received "stop"
      msg = "WARNING: Plugin will now STOP indefinitelly due to `FORCED_PAUSE` change to {}".format(
        self.cfg_forced_pause)
      status = "PAUSING"
      notif_code = ct.NOTIFICATION_CODES.PLUGIN_PAUSE_OK
      if self.cfg_ignore_working_hours:
        msg_working_hours_conflict = "The following ERROR was detected: IGNORE_WORKING_HOURS is set to True and FORCED_PAUSE is set to True. Although FORCED_PAUSE is above WORKING_HOURS this might be a configuration error."
        msg += ". " + msg_working_hours_conflict
        self.P(msg_working_hours_conflict, color='r')
      # endif working hours conflict
    # endif resume or new stop
    if msg is not None:
      self.P(msg, color='r')
      self._create_notification(
        msg=msg,
        displayed=True,
        forced_pause=self.cfg_forced_pause,
        status=status,
        notif_code=notif_code,
      )
      self.add_payload_by_fields(
        info=msg,
        forced_pause=self.cfg_forced_pause,
        status=status,
        notif_code=notif_code,
      )
    # end if process just resumed or just stopped
    self._was_stopped_last_iter = self.cfg_forced_pause
    return self.cfg_forced_pause

  @property
  def is_plugin_stopped(self):
    return self.cfg_forced_pause or self.is_outside_working_hours

  @property
  def instance_relative_path(self):
    return '__'.join(self.unique_identification)

  @property
  def save_path(self):
    return os.path.join(self.log.get_output_folder(), self.instance_relative_path)

  @property
  def plugin_output_path(self):
    return self.save_path

  @property
  def last_process_time(self):
    return self.__last_process_time

  @property
  def time_from_last_process(self):
    if self.__last_process_time is None:
      return
    return time() - self.__last_process_time

  @property
  def time_alive(self):
    return time() - self.__start_time

  @property
  def start_time(self):
    return self.__start_time

  @property
  def first_process_time(self):
    return self.__first_process_time

  @property
  def _cache_folder(self):
    return ct.CACHE_PLUGINS

  @property
  def unique_identification(self):
    return self._stream_id, self._signature, self.cfg_instance_id

  @property
  def plugin_id(self):
    str_name = "{}__{}__{}".format(
      self._stream_id, self._signature, self.cfg_instance_id
    )
    return self.sanitize_name(str_name)

  @property
  def str_unique_identification(self):
    return str(self.unique_identification)

  # set and get inputs
  @property
  def inputs(self):
    return self.__inputs

  @inputs.setter
  def inputs(self, inp):
    self.__inputs = inp
  # end set and get inputs

  @property
  def _device_id(self):
    device_id = self.log.config_data.get(ct.CONFIG_STARTUP_v2.K_EE_ID, '')
    return device_id

  @property
  def eeid(self):
    return self._device_id

  @property
  def ee_id(self):
    return self.eeid

  @property
  def node_id(self):
    return self.eeid

  @property
  def e2_id(self):
    return self._device_id

  @property
  def e2_addr(self):
    return self.__blockchain_manager.address

  @property
  def ee_addr(self):
    return self.e2_addr

  @property
  def node_addr(self):
    return self.e2_addr

  @property
  def network_monitor(self) -> NetworkMonitor:
    return self.global_shmem['network_monitor']

  @property
  def netmon(self):
    return self.network_monitor

  @property
  def net_mon(self):
    return self.network_monitor

  @property
  def upstream_inputs_deque(self):
    return self.__upstream_inputs_deque

  @property
  def is_queue_overflown(self):
    # queue is overflown only if max size is bigger than one ("real time") and its at limit
    return (self.input_queue_size >= self.cfg_max_inputs_queue_size) and (self.cfg_max_inputs_queue_size > 1)

  @property
  def time_with_no_data(self):
    return time() - self._last_data_input

  @property
  def modified_by_addr(self):
    return self.__modified_by_addr

  @property
  def initiator_addr(self):
    return self.__initiator_addr

  @property
  def initiator_id(self):
    return self.__initiator_id

  @property
  def modified_by_id(self):
    return self.__modified_by_id

  def get_node_running_time(self):
    """
    Returns the time since the node was started in seconds
    """
    return self.global_shmem['get_node_running_time']()

  def get_node_running_time_str(self):
    """
    Returns the time since the node was started pretty stringyfied
    """
    val = self.global_shmem['get_node_running_time']()
    if val < 600:
      return "{:.0f}s".format(val)
    elif val < 3600:
      return "{:.1f}m".format(round(val / 60, 1))
    else:
      return "{:.1f}h".format(round(val / 3600, 1))

  def add_to_inputs_deque(self, data):
    self.upstream_inputs_deque.append(data)
    return

  def copy_simple_data(self, dct_data):
    dct_res = {}
    for k, v in dct_data.items():
      nl1 = not isinstance(v, (list, str, np.ndarray))
      nl2 = isinstance(v, list) and len(v) > 0 and isinstance(v[0], str) and len(v[0]) < 1000
      nl3 = isinstance(v, (str, np.ndarray)) and len(v) < 1000
      if nl1 and nl2 and nl3:
        dct_res[k] = v
    return deepcopy(dct_res)

  def _cache_last_payload(self, dct_payload):
    self.__dct_last_payload = self.copy_simple_data(dct_payload)
    return

  def __process_payload(self, payload=None):
    """
    This private method wraps the payload-to-dict method and any other stuff such
    as saving the last payload if needed as dict.
    """
    if payload is None:
      payload = self._payload
    if not vars(payload).get('IS_NO_DATA_ALERT', False):
      self._maybe_ds_builder_gather_after_process(payload=payload)
      self._maybe_ds_builder_save()
    dct_res = payload.to_dict()
    self._cache_last_payload(dct_payload=dct_res)
    self.__last_payload_process_time = time()
    return dct_res

  def _maybe_alert_on_no_data(self):
    # this method checks if plugin did not receive data if this plugin so requires
    # and will generate a direct payload with this issue
    if self.cfg_alert_on_no_data:
      if self.time_with_no_data > self.cfg_alert_on_no_data_interval:
        if (time() - self._last_alert_on_no_data) > self.cfg_alert_on_no_data_repeat:
          self.no_data_alerts_count += 1
          self.P("Sending NO DATA alert {}.".format(self.no_data_alerts_count), color='r')
          self._last_alert_on_no_data = time()
          # now we deliver the payload !
          payload = self._create_payload(
            status="No data alert {} - Plugin {} did not receive any data for past {:.0f}s".format(
              self.no_data_alerts_count, self, self.time_with_no_data),
            is_no_data_alert=True,
            no_data_alerts_count=self.no_data_alerts_count,
          )
          self.add_payload(payload)
      # endif interval passed so we maybe raise
    # endif needs to check
    return

  def _maybe_resend_last_payload(self):
    if self.get_last_payload_data() is None:
      return
    if self.cfg_resend_last_status_on_idle > 0:
      if (time() - self.__last_payload_process_time) > self.cfg_resend_last_status_on_idle:
        if self.__last_payload_resend_time != self.__last_payload_process_time:
          # if last process differs last resend then it was not a resend but a normal send so just write it over for below calcs
          self.__last_payload_nonresend_time = self.__last_payload_process_time
        status_payload = self._create_payload(
          status='STATUS PAYLOAD RESEND',
          is_resend=True,
          idle_seconds=round(time() - self.__last_payload_nonresend_time, 2),
          initial_send_time=self.time_to_str(self.__last_payload_nonresend_time),
          resend_last_status_on_idle=self.cfg_resend_last_status_on_idle,
          **self.get_last_payload_data()
        )
        self.add_payload(status_payload)
        self.__last_payload_resend_time = self.__last_payload_process_time
    return

  def _maybe_exec_auto_processes(self):
    """
    This private method executes any required "AUTO_..." processing or other similar stuff.
    Should call mixins if required

    Returns
    -------
    None.

    """
    if self.cfg_auto_force_lower > 0:
      elapsed = self.alerter_time_from_last_change()
      was_reset = self.alerter_maybe_force_lower(max_raised_time=self.cfg_auto_force_lower)
      if was_reset:
        self.add_payload(
          self._create_payload(
            auto_forced_lower=True,
            elapsed_while_raised=round(elapsed, 1),
            status='FORCED LOWER executed after {:.1f}s'.format(elapsed)
              ))
    # end if auto force lower is needed
    return

  def get_last_payload_data(self):
    return self.__dct_last_payload

  def get_plugin_used_memory(self, return_tree=False):
    self.start_timer('get_plugin_memory')
    size_o = self.log.get_obj_size(
      obj=self,
      return_tree=return_tree,
      excluded_obj_props=[
        '_painter', 'thread', 'log', 'owner', 'global_shmem',
        # why excluding - to clarify
      ],
      exclude_obj_props_like=[
        'upstream_inputs_deque'
      ]
    )
    self.end_timer('get_plugin_memory')
    return size_o

  def get_plugin_queue_memory(self):
    self.start_timer('get_plugin_queue_memory')
    size_q = self.log.get_obj_size(obj=self.upstream_inputs_deque)
    self.end_timer('get_plugin_queue_memory')
    return size_q

  def mainthread_wait_for_plugin(self):
    # now we introduce a yield loop to allow the thread to consume if possible
    # WARNING: this approach is bad for the main loop and should NOT be used
    wait_start = time()
    min_decrease = int(self.actual_plugin_resolution)
    wait_time = self.cfg_queue_overflow_sleep_time
    max_wait_time = wait_time * 5
    while self.input_queue_size >= (self.cfg_max_inputs_queue_size - min_decrease):
      sleep(wait_time)
      if (time() - wait_start) > max_wait_time:
        break
    # log post yield-loop status
    if not self.queue_full_warning:
      msg = "    Post sleep queue: {}/{}".format(
        self.input_queue_size,
        self.cfg_max_inputs_queue_size,
      )
      self.P(msg, color='r')
      self.queue_full_warning = True
    return

  def add_inputs(self, inp):
    # RUNNING IN MAIN THREAD (MAIN LOOP)
    if self.is_outside_working_hours:
      # if outside working hours do not add data just skip it
      return

    if not hasattr(self, '_BasePluginExecutor__upstream_inputs_deque'):
      return
    if inp is None:
      return
    if not bool(inp):
      return

    # first things first we have to make a instance specific copy of the original data
    # coming from upstream so that we do not modify data such as numpy by mistake across
    # different parallel instances that use same inputs
    if self.cfg_copy_in_main_thread:
      self.log.start_timer('mainthr_dcopy')  # this is run in main thread
      inp = deepcopy(inp)
      self.log.stop_timer('mainthr_dcopy')

    if self.is_queue_overflown:
      # the following code is executed in the main thread thus it should NOT impose any delays
      # WARNING: if this code is executed then it means there is a problem with the safety measures
      #          implemented within the DCT Manager

      # minimal queue protection triggered only for plugins that queue data and
      # excluding those that need to process just the current timestep (MAX_INPUTS_QUEUE_SIZE = 1)
      self.queue_full_delays_count += 1
      efll = time() - self._last_logged_queue_full_time  # elapsed_from_last_log
      if (not self.queue_full_warning or
              (self._last_logged_queue_full_count != self.queue_full_delays_count and efll > 60)):
        # now we log the issue for maintenance!
        self._last_logged_queue_full_count = self.queue_full_delays_count
        self._last_logged_queue_full_time = time()
        # now we measure queue size
        queue_size_gb = self.get_plugin_queue_memory() / (1024**3)
        plugin_size_gb = self.get_plugin_used_memory() / (1024**3)
        # end measure queue size
        try:
          info = "Queue ovrflw {} (q:{}), Lps {:.1f} c/cfg: {:.1f}/{:.0f}, Q/P: {:.3f}/{:.3f} GB, lost: {}".format(
            self.queue_full_delays_count, self.cfg_max_inputs_queue_size,
            self.actual_plugin_resolution, self.get_plugin_loop_resolution(), self.cfg_plugin_loop_resolution,
            queue_size_gb, plugin_size_gb,
            self.lost_inputs_count,
          )
        except:
          info = "Queue ovrflw {} (q:{}), Lps {} c/cfg: {}/{}, Q/P: {}/{} GB, lost: {}".format(
            self.queue_full_delays_count, self.cfg_max_inputs_queue_size,
            self.actual_plugin_resolution, self.get_plugin_loop_resolution(), self.cfg_plugin_loop_resolution,
            queue_size_gb, plugin_size_gb,
            self.lost_inputs_count,
          )

        self.P(info, color='r')
        PRC_QUEUE_EXCEED_TOTAL_MEMORY = 0.01
        if queue_size_gb > PRC_QUEUE_EXCEED_TOTAL_MEMORY * self.log.total_memory:
          self.P("WARNING: queue exceeds {:.0f}% of total memory size".format(
            PRC_QUEUE_EXCEED_TOTAL_MEMORY * 100), color='r')
        msg = 'Queue overload in {}'.format(self)
        self._create_abnormal_notification(
          msg=msg,
          info=info,
          displayed=True,
        )
      # endif queue-is-full warning required

      # a this point initially a `self.mainthread_wait_for_plugin()` was ran but that imposed main loop unwanted delay
      # as a result the "waiting has been moved to the data acquisition area
    # endif queue is overflown

    # if the queue is full we have to report the lost input
    if self.is_queue_overflown:
      self.lost_inputs_count += 1
    # end record lost inputs
    self.add_to_inputs_deque(inp)
    return

  def start_thread(self):
    self.thread = Thread(
      target=self.plugin_loop,
      args=(),
      name=ct.THREADS_PREFIX + 'plg_' + '>'.join(self.unique_identification),
      daemon=True,
    )
    self.thread.start()
    return

  def stop_thread(self):
    TIMEOUT = 15
    self.P(ct.BM_PLUGIN_END_PREFIX +
           "[main-thr] Received STOP {} thread command...".format(self.instance_hash), color='b')
    self.done_loop = True
    self.thread.join(timeout=TIMEOUT)
    is_alive = self.thread.is_alive()
    self.plugins_shared_mem.clean_instance()
    if not is_alive:
      self.P(ct.BM_PLUGIN_END_PREFIX + "[main-thr] thread {} stopped/joined ok.".format(self.instance_hash), color='b')
    else:
      self.P(ct.BM_PLUGIN_END_PREFIX +
             "[main-thr] Cannot gracefully stop thread {} after {}s".format(self.instance_hash, TIMEOUT), color='r')
    return

  def get_stream_id(self):
    return self._stream_id

  def get_signature(self):
    return self._signature

  def get_instance_id(self):
    return self.cfg_instance_id

  def get_instance_config(self):
    return self._instance_config

  def get_upstream_config(self):
    return self._upstream_config

  def get_payload_after_exec(self):
    """Gets the payload and resets the internal _payload protected variable

    Returns
    -------
    payload : GenericPayload
        returns the current payload
    """
    p = self._payload
    self._payload = None
    return p

  def get_commands_after_exec(self):
    c = self._commands
    self._cmdapi_refresh()
    return c

  def start_timer(self, tmr_id):
    if self.log.is_main_thread:
      tmr_id = tmr_id + '_{}'.format(self._signature_hash)

    self.log.start_timer(tmr_id, section=self._timers_section)
    return

  def end_timer(self, tmr_id, skip_first_timing=False, periodic=False):
    if self.log.is_main_thread:
      tmr_id = tmr_id + '_{}'.format(self._signature_hash)

    return self.log.end_timer(tmr_id, skip_first_timing=skip_first_timing, section=self._timers_section, periodic=periodic)

  def stop_timer(self, tmr_id, skip_first_timing=False, periodic=False):
    self.end_timer(tmr_id=tmr_id, skip_first_timing=skip_first_timing, periodic=periodic)
    return

  def maybe_update_instance_config(self, upstream_config, session_id=None, modified_by_addr=None, modified_by_id=None):
    """
    This method is called by the plugin manager when the instance config has changed.
    IMPORTANT: it runs on the same thread as the BusinessManager so it should not block!

    For the particular case when only INSTANCE_COMMAND is modified then the plugin should not reset its state
    """
    # first cleanup all the non-actionable keys
    for k in self.ct.NON_ACTIONABLE_INSTANCE_CONFIG_KEYS:
      upstream_config.pop(k, None)
      self._upstream_config.pop(k, None)
    
    if upstream_config == self._upstream_config:
      return
    
    # if is just a INSTANCE_COMMAND then bypass the full config update including
    # `is_instance_command_only` check from the `_update_instance_config`
    # first set _upstream_config

    if session_id is not None and session_id != self._session_id:
      self.P("  Changing session_id from '{}' to '{}'".format(self._session_id, session_id), color='y')
      self._session_id = session_id
    if modified_by_id is not None and modified_by_id != self.__modified_by_id:
      self.P("  Changing modified-by-id from '{}' to '{}'".format(self.__modified_by_id, modified_by_id), color='y')
      self.__modified_by_id = modified_by_id
      self.__modified_by_addr = modified_by_addr
    # endif session_id

    # check if is just a command
    if upstream_config.get('INSTANCE_COMMAND') not in [None, '', [], {}]:
      # if the command is not empty then we just set the command and return
      # TODO(FIXME): when a plugin instance `process` method takes some time,
      # this text will be spammed in the logs. also, there is the risk of 
      # a new command overwriting the previous one before it is processed. 
      self.P("Command '{}' received for plugin {}".format(upstream_config['INSTANCE_COMMAND'], self))
      self.config_data['INSTANCE_COMMAND'] = upstream_config['INSTANCE_COMMAND']
      # reset the command for the upstream config
      self._upstream_config['INSTANCE_COMMAND'] = {}  
      upstream_config['INSTANCE_COMMAND'] = {}
      return
    # endif is just a command

    self._upstream_config = upstream_config

    self.P("Config {} on request {}:{}".format(self, modified_by_id, modified_by_addr), color='b')
    # DEBUG
    # self.log.P("Current:\n{}".format(json.dumps(self._upstream_config, indent=4)), color='d')
    # self.log.P("New:\n{}".format(json.dumps(upstream_config, indent=4)), color='d')
    # END DEBUG
    self.config_changed_since_last_process = True

    
    # now while changing config we must stop loop exec
    self.__set_loop_stage(s='maybe_update_instance_config.wait', prefix='2.bm.refresh.{}'.format(self.cfg_instance_id))
    wait_start = self.time()
    CONFIG_WAIT_FOR_EXEC = 10
    while self._plugin_loop_in_exec:
      sleep(0.001)
      if (self.time() - wait_start) >= CONFIG_WAIT_FOR_EXEC:
        msg = "Forced update config will be executed. Plugin is taking too long in `_plugin_loop_in_exec`"
        self.P(msg, color='error')
        self._create_error_notification(
          msg=msg,
          displayed=True,
        )
        break
      # endif need to break the loop
    # end wait loop
    self.__set_loop_stage(s='maybe_update_instance_config.done-wait',
                          prefix='2.bm.refresh.{}'.format(self.cfg_instance_id))

    # pause the plugin loop while updating the config
    self.loop_paused = True  # can also use self.pause_loop() but is safer like this as here we have getter and setter (harder to overwrite)
    last_config = deepcopy(self.config_data)
    try:
      self.__set_loop_stage(s='maybe_update_instance_config._update_instance_config',
                            prefix='2.bm.refresh.{}'.format(self.cfg_instance_id))
      self._update_instance_config()
      # call on-config handler
      self.__set_loop_stage(s='maybe_update_instance_config._on_config',
                            prefix='2.bm.refresh.{}'.format(self.cfg_instance_id))
      self._on_config()
      # resume loop
      self.__set_loop_stage(s='maybe_update_instance_config.reset_exec_counter_after_config',
                            prefix='2.bm.refresh.{}'.format(self.cfg_instance_id))
      self.reset_exec_counter_after_config()
      self.loop_paused = False  # in the rare case that someone or something overwrote resume_loop()

    except Exception as exc:
      # rollback
      self._instance_config = last_config
      self.config_data = last_config
      msg = "Exception occured while updating the instance config: '{}' Rollback to the last good config.".format(exc)
      info = traceback.format_exc()
      self.P(msg + '\n' + info, color='r')

      # self.__save_full_config()
      # Instead of saving the full config, we only need to rollback the keys that were changed
      # which are in the upstream_config
      # We also need to exclude all the keys that are not in config data, as they are not used by anyone
      self.__save_config(keys=[k for k in upstream_config.keys() if k in self.config_data])

      self._create_error_notification(
        msg=msg,
        info=info,
        displayed=True,
      )
      # resume loop even on exception
      self.loop_paused = False
    return

  def _create_notification(self, msg, info=None, notif=ct.PAYLOAD_CT.STATUS_TYPE.STATUS_NORMAL, use_local_comms_only=False, **kwargs):
    return super()._create_notification(
      notif=notif, msg=msg, info=info,
      stream_name=self._stream_id,
      # next special info for backend
      signature=self._signature,
      instance_id=self.cfg_instance_id,
      initiator_id=self.__initiator_id,
      session_id=self._session_id,
      ct=ct,
      use_local_comms_only=use_local_comms_only or self.use_local_comms_only,
      **kwargs
    )

  def _create_error_notification(self, msg, info=None, **kwargs):
    return super()._create_notification(
      notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
      msg=msg, info=info,
      autocomplete_info=True,
      stream_name=self._stream_id,

      # next special info for backend
      signature=self._signature,
      instance_id=self.cfg_instance_id,
      initiator_id=self.__initiator_id,
      session_id=self._session_id,
      ct=ct,
      **kwargs
    )

  def _create_abnormal_notification(self, msg, info=None, **kwargs):
    return super()._create_notification(
      notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING,
      msg=msg, info=info,
      autocomplete_info=True,
      stream_name=self._stream_id,
      # next special info for backend
      signature=self._signature,
      instance_id=self.cfg_instance_id,
      initiator_id=self.__initiator_id,
      session_id=self._session_id,
      ct=ct,
      **kwargs
    )

  def __save_config(self, keys):
    # writes the config for a set of particular keys on the local cache!
    save_config_fn = self.global_shmem[ct.CALLBACKS.INSTANCE_CONFIG_SAVER_CALLBACK]
    try:
      config = {k: self.config_data[k] for k in keys}
      save_config_fn(self._stream_id, self._signature, self.cfg_instance_id, config_data=config)
      # the save will trigger a update config when the BusinessManager will get the new config from
      # the ConfigManager and will pass it as upstream_config to the plugin
      # so we already update the upstream_config to avoid this
      for k in keys:
        self._upstream_config[k] = self.config_data[k]
    except Exception as exc:
      self.P("Error '{}' while saving keys {}".format(exc, keys))
      raise exc
    return

  def __save_full_config(self):
    # writes the config on the local cache!
    save_config_fn = self.global_shmem[ct.CALLBACKS.INSTANCE_CONFIG_SAVER_CALLBACK]
    try:
      save_config_fn(self._stream_id, self._signature, self.cfg_instance_id, config_data=self.config_data)
      # the save will trigger a update config when the BusinessManager will get the new config from
      # the ConfigManager and will pass it as upstream_config to the plugin
      # so we already update the upstream_config to avoid this
      self._upstream_config = deepcopy(self.config_data)
    except Exception as exc:
      self.P("Error '{}' while saving full config".format(exc))
      raise exc
    return

  def _reset_last_process_time(self):
    self.__last_process_time = time()
    return

  def reset_first_process(self):
    self.__last_process_time = None
    return

  def need_refresh(self):
    idle_time = time() - self.__last_payload_process_time
    return idle_time > self.cfg_max_idle_time

  # TODO: TO BE REMOVED

  def maybe_save_data(self, data, single_file=False):
    """
    DEPRECATED - please use the persistence API
    """
    saved_fn = None
    if (time() - self.__last_saved) > self.cfg_save_interval:
      self.P("[DEPRECATED - please use the persistence API] {} saving its data (SAVE_INTERVAL={}s).".format(
        self.__class__.__name__, self.cfg_save_interval), color='y')
      saved_fn = "{}_{}".format(self._signature, self.log.now_str()) if not single_file else self._signature
      self.log.save_pickle_to_output(
        data=data,
        fn=saved_fn
      )
      self.__last_saved = time()
    return saved_fn

  # Section 0 - methods that provides main functionality of a plugin
  if True:

    def archive_config_keys(self, keys: list, defaults=None):
      """
      Method that allows resetting of a list of keys and saving the current value as `_LAST` keys

      Parameters
      ----------
      keys : list
        List of keys to be archived.

      defaults: list
        List of default values for all keys. Default is None

      Returns
      -------
      None.

      """
      if defaults is None:
        defaults = [None] * len(keys)
      assert len(defaults) == len(keys), "Default values must be provided for all keys"
      all_keys = keys.copy()
      archived_keys = []
      for i, key in enumerate(keys):
        archive_key = key + '_LAST'
        self.config_data[archive_key] = self.config_data[key]
        self.config_data[key] = defaults[i]
        all_keys.append(archive_key)
      # endfor
      self.save_config_keys(all_keys)
      return

    def save_config_keys(self, keys: list):
      """
      Method that allows saving the local config in local cache in order to update a
      specific set of given keys that might have been modified during runtime

      Parameters
      ----------
      keys : list
        List of keys to be saved.

      Returns
      -------
      None.

      """
      EXCEPTIONS = ['INSTANCE_ID']
      result = False
      all_exist = all([k in self.config_data for k in keys])
      all_accepted = all([k not in EXCEPTIONS for k in keys])
      if all_exist and all_accepted:
        self.__save_config(keys)
        result = True
      return result

    def add_payload(self, payload):
      """
      Adds a payload in the plugin instance output queue. If used inside plugins
      plese do NOT return the payload from _process as the payload will be duplicated

      Parameters
      ----------
      payload : GeneralPayload or dict
        the payload

      Returns
      -------
      None.

      """
      if payload is not None:
        # if the payload is added from normal process output then is already __process_payload-ed from high_level_execution_chain
        if isinstance(payload, GeneralPayload):
          # TODO: some things may be missing (cvpluginexecutor._add_plugin_identifiers_to_payload)
          # self._add_plugin_identifiers_to_payload(payload=payload)
          payload = self.__process_payload(payload)
        self.payloads_deque.append(payload)
        self.__total_payloads += 1
      return payload

    def add_payload_by_fields(self, **kwargs):
      """
      Adds a payload in the plugin instance output queue based on fields rather than
      on a already created payload object.
      If used inside plugins plese do NOT return the payload from _process as the payload
      will be duplicated

      Parameters
      ----------
      **kwargs : dict

      Returns
      -------
      None.

      """
      payload = self._create_payload(**kwargs)
      self.add_payload(payload)
      return

    def create_and_send_payload(self, **kwargs):
      """
      Creates a payload and sends it to the output queue.
      If used inside plugins plese do NOT return the payload from _process as the payload
      will be duplicated

      Parameters
      ----------
      **kwargs : dict

      Returns
      -------
      None.

      """
      payload = self._create_payload(**kwargs)
      self.add_payload(payload)
      return

    def get_next_avail_input(self):
      result = None
      if self.input_queue_size > 0:
        data = self.upstream_inputs_deque.popleft()
        if not self.cfg_copy_in_main_thread:
          self.start_timer('thread_dcopy')
          result = deepcopy(data)
          self.stop_timer('thread_dcopy')
        else:
          result = data
      return result

    def pre_process_wrapper(self):
      self.start_timer(ct.TIMER_PRE_PROCESS)
      self._pre_process_outputs = self._pre_process()
      self.stop_timer(ct.TIMER_PRE_PROCESS)
      return

    def process_wrapper(self):
      self.start_timer('process_wrapper')

      if self.__first_process_time is None:
        self.__first_process_time = time()

      self._recalc_plugin_resolution()

      self._reset_last_process_time()  # reset time as we call it next
      self.start_timer('process')
      self._payload = self._process()
      self.stop_timer('process')
      self.stop_timer('process_wrapper')
      return

    def post_process_wrapper(self):
      self.start_timer(ct.TIMER_POST_PROCESS)
      self._post_process()

      if self._payload is not None:
        pass

      self.stop_timer(ct.TIMER_POST_PROCESS)
      return

    # TESTING / BENCHMARKING

    def _maybe_register_payload_for_tester(self, payload=None):
      # this is most likely redundant - should be evaluated for deletion
      if self._testing_manager is None:
        return

      payload = payload or self._payload

      self._prepare_payload_for_testing_registration(payload)
      self._testing_manager.tester.register_payload(payload)
      self._payload = None  # during testing there is no need to communicate the payloads
      return

    def _maybe_generate_testing_results_payload(self, payload):
      if self._testing_manager is None:
        return

      payload = self._prepare_payload_for_generating_testing_results(payload)

      if payload is not None:
        payload['STREAM'] = self.dataapi_stream_name()
        payload['SIGNATURE'] = self.get_signature()
        payload['INSTANCE_ID'] = self.cfg_instance_id
        payload['VERSION'] = self.__version__
        payload['BOX_SESSION_ID'] = self.log.file_prefix
        payload['TESTER_NAME'] = self.testing_tester_name

        if self._scoring_manager is not None:
          dct_score = self._scoring_manager.score(
            y_true_src=self.testing_tester_y_true_src,
            payload=payload,
            config=self.testing_scorer_config
          )

          payload['Y_TRUE'] = self._scoring_manager.get_y_true(
            y_true_src=self.testing_tester_y_true_src,
            name=payload['TESTER_NAME']
          )
          payload['TOTAL_FRAMES_PROCESSED'] = self.current_process_iteration
          payload['SCORE'] = dct_score
        # endif

        file_name = '{}__{}__{}__{}.json'.format(
          payload['BOX_SESSION_ID'],
          payload['STREAM'],
          payload['SIGNATURE'],
          payload['INSTANCE_ID']
        )
        file_path = self.log.save_output_json(
          payload,
          file_name,
          subfolder_path='testing'
        )

        if self.testing_upload_result:
          target_path = '/TESTING/{}'.format(file_name)

          url, _ = self.upload_file(
            file_path=file_path,
            target_path=target_path,
            force_upload=True,
            verbose=1
          )

          payload['URL'] = url

          os.remove(file_path)
        # endif
      # endif
      return

  # endif

  # Section A - methods that can be overwritten in subclass.
  # When reimplementing, pay double attention if you want to overwrite the entire functionality;
  # otherwise, first line should be parent_result = super().method_name(...)
  if True:

    def _create_payload(self, **kwargs):
      if 'plugin_category' not in kwargs:
        kwargs['plugin_category'] = 'general'

      kwargs[self.ct.PAYLOAD_DATA.EE_IS_ENCRYPTED] = self.cfg_encrypt_payload

      payload = GeneralPayload(
        owner=self,
        _p_dataset_builder_used=False,
        **kwargs
      )
      return payload

    def _create_output(self, **kwargs):
      # _create_payload alias
      return self._create_payload(**kwargs)

    def _create_result(self, **kwargs):
      # _create_payload alias
      return self._create_payload(**kwargs)

    def _create_response(self, **kwargs):
      # _create_payload alias
      return self._create_payload(**kwargs)

    def _prepare_payload_for_testing_registration(self, payload):
      return payload

    def _prepare_payload_for_generating_testing_results(self, payload):
      self._payload = None
      return payload

    def _check_delta_config(self, delta_config):
      # see what keys differ from current config - particularly useful for INSTANCE_COMMAND
      updates = []
      for k, v in delta_config.items():
        if (
          (self._instance_config is None) or
          (k not in self._instance_config) or
          (v != self._instance_config[k])
        ):
          updates.append(k)
      return updates

    def _update_instance_config(self):
      if self._instance_config is not None:
        reconfig = True
        self.P("* * * * Reconfiguring plugin {} * *".format(self), color='b')
      else:
        self.P("* * * * * Initial config of plugin `{}` * * *".format(self.__class__.__name__), color='m')
        reconfig = False

      if self._environment_variables is not None and len(self._environment_variables) > 0:
        if self.__debug_config_changes:
          self.P("Updating instance config with node environment config....", color='m')
        # initially this call did not have `default_config = self._instance_config` that led
        # to wrongly using at each update the _default_config
        self.__set_loop_stage('_update_instance_config._environment_variables')
        self._instance_config = self._merge_prepare_config(
          default_config=self._instance_config,
          delta_config=self._environment_variables,
          debug=self.__debug_config_changes,
        )
      # now normal delta config from upstream
      self.__set_loop_stage('_update_instance_config._default_config')

      updates = self._check_delta_config(self._upstream_config)  # check if there are any updates
      is_instance_command_only = len(updates) == 1 and 'INSTANCE_COMMAND' in updates

      self._instance_config = self._merge_prepare_config(
        default_config=self._instance_config,
        debug=self.__debug_config_changes,
      )

      self.__set_loop_stage('_update_instance_config.setup_config_and_validate')

      # if we do not run the next line then the config will not be updated for each of the cfg_* handlers
      # as they will point to the old config_data dict
      self.setup_config_and_validate(self._instance_config)  # here _instance_config will be copyed to config_data

      self.__set_loop_stage('_update_instance_config._print_warnings')
      self._print_warnings()

      # now for the second part of the config: reset/set stuff
      if is_instance_command_only:
        self.P(">>>> INSTANCE_COMMAND received, skipping all resets and initializations... <<<<")
      else:
        # now maybe restart alerters
        if (self.cfg_restart_alerters_on_config and reconfig) or not reconfig:
          # either create if reset is needed or first config (not reconfig)
          # TODO: make sure you set RESTART_ALERTERS_ON_CONFIG to False for plugins
          #       that should NOT restart alerters
          self.__set_loop_stage('_update_instance_config._create_alert_state_machine')
          self._create_alert_state_machine()

        # Create time bins mixin default # TODO: refactor similar with alerters
        self.__set_loop_stage('_update_instance_config.timebins_create_bin')
        self.timebins_create_bin()

        # now maybe re-init plugin shmem if uptate or new initialization
        if (self.cfg_restart_shmem_on_config and reconfig) or not reconfig:
          # `global_shmem` here is WRONG: we should NOT allow  plugins to access global shmem!
          #  so instead we use `plugins_shmem` initiated by the BizMgr
          self.__set_loop_stage('_update_instance_config.init_plugins_shared_memory')
          self.init_plugins_shared_memory(self.plugins_shmem)

      self.last_config_timestamp = self.log.now_str(nice_print=True, short=False)
      if self.is_plugin_stopped:
        self._create_notification(
          msg='Plugin configured while being stopped',
          notif_code=ct.NOTIFICATION_CODES.PLUGIN_CONFIG_IN_PAUSE_OK,
        )
      self.__set_loop_stage('_update_instance_config.EXIT_update_instance_config')
      return

    def _prepare_debug_save_payload(self):
      # this method is "connected" with "debug_save" heavy ops plugin
      return

    def _get_alerter_reduce_threshold(self):
      return self._get_confidence_threshold()

    def _print_warnings(self):
      str_warnings = ""

      # TODO: Maybe remove
      if self.cfg_debug_payloads:
        str_warnings += "- sends payloads with increased frequency for DEBUG purposes."
      ####
      ####

      if not hasattr(self, '_draw_witness_image'):
        str_warnings += "\n- does not have a `_draw_witness_image` method to call from `get_witness_image`"

      _pre_process_ow = inspect.getsource(self._pre_process) != inspect.getsource(BasePluginExecutor._pre_process)
      _process_ow = inspect.getsource(self._process) != inspect.getsource(BasePluginExecutor._process)
      _post_process_ow = inspect.getsource(self._post_process) != inspect.getsource(BasePluginExecutor._post_process)

      if not _process_ow:
        str_warnings += "\n- does not provide high level '_process' functionality. Make sure that _on_data and _on_idle to be overwritten!"

      if not _pre_process_ow:
        str_warnings += "\n- does not provide high level '_pre_process' functionality."

      if not _post_process_ow:
        str_warnings += "\n- does not provide high level '_post_process' functionality"

      if str_warnings != "":
        str_warnings = "{} WARNINGS:".format(self.__class__.__name__) + str_warnings
        self.P(str_warnings, color='y')

      return

    def _witness_prepare(self, img=None, **kwargs):
      img_witness = None
      if img is not None:
        _H, _W = img.shape[:2]
        if self.cfg_original_frame:
          img_witness = img[:, :, ::-1]
          img_witness = np.ascontiguousarray(img_witness, dtype=np.uint8)
        else:
          img_witness = np.zeros((_H, _W, 3), dtype=np.uint8)
      # endif
      return img_witness

    def _witness_pre_process(self, img_witness, **kwargs):
      return img_witness

    def _witness_post_process(self, img_witness, **kwargs):
      return img_witness

    def _add_alert_info_to_payload(self, payload):
      vars(payload)[ct.IS_ALERT] = self.alerter_is_alert()
      vars(payload)[ct.STATUS_CHANGED] = self.alerter_status_changed()
      return

    def _get_confidence_threshold(self):
      """
      Function to generate confidence threshold

      Returns
      -------
      conf_thr : float (0-1)
      """
      conf_thr = self.cfg_confidence_threshold
      if conf_thr is not None:
        if isinstance(conf_thr, dict):
          # assume conf thr is hourly based and keys are "HH:MM-HH:MM" format
          dct_hours = conf_thr
          conf_thr = get_now_value_from_time_dict(dct_hours)
      return conf_thr

    def maybe_start_thread_safe_drawing(self, name='_draw_witness_image'):
      if self.cfg_thread_safe_drawing:
        self._thread_safe_drawing_lock_name = name
        self.log.lock_resource(self._thread_safe_drawing_lock_name)
      return

    def maybe_stop_thread_safe_drawing(self):
      if self.cfg_thread_safe_drawing:
        self.log.unlock_resource(self._thread_safe_drawing_lock_name)
      return

    def get_witness_image(self, img=None,
                          prepare_witness_kwargs=None,
                          pre_process_witness_kwargs=None,
                          draw_witness_image_kwargs=None,
                          post_process_witness_kwargs=None):
      """
      This is the wrapper function that should be called from any plug-in.
      It contains the channel reversing and the cv2 required numpy magic and it
      will call the `_draw_witness_image` plug-in specific method

      definition of: _draw_witness_image(img_witness, **kwargs)

      Parameters
      ----------
      img: np.ndarray
        The starting image. Can be None

      prepare_witness_kwargs : dict
        anything we need in _witness_prepare (passed as **prepare_witness_kwargs)

      pre_process_witness_kwargs : dict
        anything we need in _witness_pre_process (passed as **pre_process_witness_kwargs)

      draw_witness_image_kwargs : dict
        anything we need in _draw_witness_image (passed as **draw_witness_image_kwargs)

      post_process_witness_kwargs : dict
        anything we need in _witness_post_process (passed as **post_process_witness_kwargs)

      Returns
      -------
      img_witness : ndarray
        The returned image will be in RGB format.

      """
      if self.cfg_cancel_witness:
        return

      prepare_witness_kwargs = prepare_witness_kwargs or {}
      pre_process_witness_kwargs = pre_process_witness_kwargs or {}
      draw_witness_image_kwargs = draw_witness_image_kwargs or {}
      post_process_witness_kwargs = post_process_witness_kwargs or {}

      if img is not None:
        if not isinstance(img, np.ndarray):
          self.P("`get_witness_image` called having a non numpy img {}. Returning no witness".format(
            repr(self), type(img)), color='error')
          return

      uses_BGR = False

      self.start_timer(ct.TIMER_GET_WITNESS_IMAGE)
      img_witness = self._witness_prepare(img, **prepare_witness_kwargs)
      if img_witness is not None:
        img_witness = self._witness_pre_process(img_witness, **pre_process_witness_kwargs)
        uses_BGR = True

      if hasattr(self, '_draw_witness_image'):
        if not self.cfg_simple_witness:
          self.maybe_start_thread_safe_drawing()
          img_witness = self._draw_witness_image(
            img=img_witness,
            **draw_witness_image_kwargs
          )
          self.maybe_stop_thread_safe_drawing()

      if img_witness is not None:
        img_witness = self._witness_post_process(img_witness, **post_process_witness_kwargs)
        # maybe we need to resize the whole thing so that is respects the model input size
        if len(self.cfg_resize_witness) == 2:
          h, w = self.cfg_resize_witness
          img_witness = self.log.center_image2(
            np_src=img_witness,
            target_h=h,
            target_w=w
          )

        # because we flipped channels in `_witness_prepare` to BGR in order to draw using cv2; now we come back to RGB..
        if uses_BGR:
          img_witness = img_witness[:, :, ::-1]
      # endif

      self.stop_timer(ct.TIMER_GET_WITNESS_IMAGE)
      return img_witness
  # endif

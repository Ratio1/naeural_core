#global dependencies
import os
import numpy as np
import abc

from time import sleep, time
from threading import Thread
from collections import deque
from datetime import datetime

#local dependencies
from naeural_core import constants as ct

from naeural_core.data_structures import MetadataObject
from naeural_core.data.base import BaseDataCapture
from naeural_core.data.mixins_base import _AquisitionOnIntervalsMixin, _DCTUtilsMixin



RUN_ON_THREAD = True
REGISTER_TIMERS = True

_CONFIG = {
  **BaseDataCapture.CONFIG,
  
  'IS_THREAD' : True,
  
  'CAP_RESOLUTION'        : 1,
  
  'FORCE_CAP_RESOLUTION'  : -1, # if -1, this parameter is ignored
  
  'RECONNECTABLE'         : 'YES',
  
  
  'SLEEP_TIME_ON_ERROR'   : 10,
  'MAX_RETRIES'           : 10,
  'MAX_DEQUE_LEN'         : 128, # 1 if 'LIVE_FEED': true
  'STREAM_WINDOW'         : 1, # if 'LIVE_FEED': true, this has no effect
  
  'LIVE_FEED'             : True, # default true
  
  'NR_SKIP_FRAMES'        : None, 
  
  ## SERIALIZATION & PERSISTENCE
  'LOAD_PREVIOUS_SERIALIZATION'   : False,
  'SERIALIZATION_SIGNATURE'       : None,
  ## END SERIALIZATION & PERSISTENCE
  
  
  'VALIDATION_RULES': {
    **BaseDataCapture.CONFIG['VALIDATION_RULES'],

    'CAP_RESOLUTION': {
      'TYPE': 'float',
      'MIN_VAL': 0.0003, 
      'MAX_VAL': 250,
      'DESCRIPTION': 'Sets the frequency of the DCT plugin where the minimum is once every hour and the maximum is 250 times per second.'
    },
    
    'RECONNECTABLE': {
      'TYPE': ['str', 'bool'],
    
      'DESCRIPTION': """
      Sets behaviour for connection/data stopping events: 
      - "YES"/true will force reconnecting when connection is lost, 
      - "NO"/false will finish the data capture and the included underlying pipeline with all its plugins if the data has finished or the connection is lost,
      - "KEEPALIVE" when the connection is lost or data has finished, the pipeline will enter a zombie state where no data is produced yet the business 
      plugins are still runnning and the pipeline can only be closed with an external command
      """
    }
  }
}

class DataCaptureThread(BaseDataCapture, 
                        _AquisitionOnIntervalsMixin,
                        _DCTUtilsMixin,
                        ):
  CONFIG = _CONFIG
  def __init__(self, **kwargs):
    self._phase = None
    self._deque = None
    self._thread = None
    self._stop = False
    self._register_timers = REGISTER_TIMERS
    self._DPS = -1
    
    self.__last_get_data_capture_timestamp = 0
    self.__get_data_capture_laps = deque(maxlen=1000)

    self.thread_stopped = False
    self.has_connection = False
    self.has_finished_acquisition = False
    self.__lap_times = deque(maxlen=100)
    self.__process_times = deque(maxlen=100)
    self.__sleep_times = deque(maxlen=100)

    self.__has_received_data = False
    self._start_time = time()
    self._timers_section = None
    self.__last_add_input_timestamp = time()

    self._metadata = MetadataObject(
      current_interval=None,
      payload_context=None,
      use_local_comms_only=False,
    )

    super(DataCaptureThread, self).__init__(**kwargs)
    return
  
  
  @property
  def _cache_folder(self):
    return ct.CACHE_DATA_CAPTURE
    
  @property
  def plugin_id(self):
    str_type = self.cfg_type
    str_name = self.cfg_name
    str_id = "{}__{}".format(str_type, str_name)
    return self.sanitize_name(str_id)

    
  def sleep(self, s):
    sleep(s)
    return
  

  def startup(self):
    super().startup()

    self._timers_section = 'STREAM_' + self.cfg_name
    maxlen = 1 if self.cfg_live_feed else max(self.cfg_max_deque_len, self.cfg_stream_window)
    self._deque = deque(maxlen=maxlen)

    crt_log = "Starting {} -> {} with following options:".format(self._signature, self.cfg_name)
    crt_log += "\n  Queue size:      {}".format(maxlen)
    crt_log += "\n  Capture res:     {} d/s".format(self.cap_resolution)
    crt_log += "\n  Is reconn.:      {}".format(self.is_reconnectable)
    crt_log += "\n  Skip data:       {}".format(self.cfg_nr_skip_frames)
    for key, val in self.config.items():
      if key not in [ct.PLUGINS, ct.CAP_RESOLUTION, ct.NR_SKIP_FRAMES, "VALIDATION_RULES"] and val is not None:
        crt_log += "\n  {:<15} {}".format(key + ':', val)

    crt_warn = ''
    if not self.cfg_live_feed and not self.is_reconnectable:
      crt_warn += '\n[Warning] Be aware that stream will be closed when all the data in buffer is consumed!'

    if self.cfg_live_feed and self.cfg_stream_window > 1:
      crt_warn += '\n[Warning] Be aware that using a stream window in live feed does not affect anything'

    self.P(crt_log, color='y') 
    if len(crt_warn) > 0:
      self.P(crt_warn, color='r')
    return
  

  def _populate_stream_metadata_from_config(self):
    """
    This protected method populates the `_stream_metadata` object with basic information
    at initiation time. Part of the information is also added in capture getter and should 
    be moved.
    
    Should be populated in derived classes.

    Returns
    -------
    None.

    """
    super()._populate_stream_metadata_from_config()    
    self._stream_metadata.url = self.cfg_url
    return
  

  @property
  def is_reconnectable(self):
    return (
      (isinstance(self.cfg_reconnectable, str) and  self.cfg_reconnectable.upper() in ['YES','Y', 'T', 'TRUE']) or 
      (isinstance(self.cfg_reconnectable, bool) and self.cfg_reconnectable)
    )
    
  
  @property
  def is_keepalive(self):
    return isinstance(self.cfg_reconnectable, str) and  self.cfg_reconnectable.upper() in ['KEEPALIVE', 'KEEP_ALIVE']
  
    
  def get_cap_or_forced_resolution(self):
    cap_resolution = self.cfg_cap_resolution
    force_cap_resolution = self.cfg_force_cap_resolution
    if force_cap_resolution > 0:
      cap_resolution = force_cap_resolution
    return cap_resolution

  
  @property
  def has_cap_resolution_config(self):
    cap = self.config.get(ct.CAP_RESOLUTION, None)
    is_valid = cap is not None and isinstance(cap, (int, float)) and cap > 0
    return is_valid
  
  
  @property
  def heuristic_cap(self):
    if hasattr(self, '_heuristic_cap_resolution'):
      if self._heuristic_cap_resolution is not None and self._heuristic_cap_resolution > 0:
        return round(self._heuristic_cap_resolution, 2)
    return None


  @property
  def cap_resolution(self):
    """
    Use this property-function if you need to use the heuristics / auto-calculation of DPS
    otherwise you can use directly get_proposed_cap_resolution

    Returns
    -------
    TYPE: float
      required number of stream reads per second.

    """
    heuristic_cap = self.heuristic_cap
    default_cap = self.get_cap_or_forced_resolution()  
    if heuristic_cap is not None:
      return heuristic_cap
    else:
      return default_cap


  @property
  def generate_resolution(self):
    """
    Data generation resolution is the number of times the data is generated per second for
    downstream by the get_data_capture method. This is different from the cap_resolution
    """
    if len(self.__get_data_capture_laps) > 0:
      return round(1 / np.mean(self.__get_data_capture_laps), 2)
    else:
      return -1
    


  @property
  def can_delete(self):
    # return True if stream can reconnect or someone else needs to destroy it
    return self.thread_stopped and not (self.is_reconnectable or self.is_keepalive)


  @property
  def is_thread(self):
    return True


  @property
  def actual_dps(self):
    return self._DPS

  
  @property
  def time_since_last_input(self):
    return time() - self.__last_add_input_timestamp
  

  @property
  def received_first_data(self):
    return self.__has_received_data
  

  def start_timer(self, tmr_id):
    if not self._register_timers:
      return

    self.log.start_timer(sname=tmr_id, section=self._timers_section)
    return

  def get_timer(self, tmr_id):
    return self.log.get_timer(skey=tmr_id, section=self._timers_section)

  def end_timer(self, tmr_id, skip_first_timing=True, periodic=False):
    if not self._register_timers:
      return -1

    return self.log.end_timer(sname=tmr_id, section=self._timers_section, skip_first_timing=skip_first_timing, periodic=periodic)
  
  
  def reset_received_first_data(self):
    self.__has_received_data = False
    return
  
  def mark_receved_first_data(self):
    self.__has_received_data = True
    return
  

  def _new_input(self, img=None, struct_data=None, metadata=None, init_data=None):
    """
    Utilitary for creating a sub-stream input

    *** Very important observation: ***
      `img` and `struct_data` are mutual exclusive (i.e. a sub-stream can collect either a frame, or a chunk of structured data)

    Parameters:
    ----------
    img: np.ndarray, optional
      The image collected by the sub-stream
      The default is None.

    struct_data: object, optional
      The chunk of structured data collected by the sub-stream
      The default is None

    metadata: dict, optional
      Metadata of the sub-stream
      The default is None ({})

    init_data: object, optional
      Some initial data collected by the sub-stream (training data or anchor images for example)
      The default is None

    Returns:
    --------
    An input in the DecentrAI format - a dictionary as following:
      {
        "IMG"         : $(the received value in `img` parameter),
        "STRUCT_DATA" : $(the received value in `struct_data` parameter),
        "METADATA"    : $(the received value in `metadata` parameter),
        "INIT_DATA"   : $(the received value in `init_data` parameter),
        "TYPE"        : "IMG" or "STRUCT_DATA" ### specifies whether the field "IMG" or the field "STRUCT_DATA" has value
      }

    """

    if img is not None:
      assert struct_data is None
      _type = 'IMG'

    if struct_data is not None or init_data is not None:
      assert img is None
      _type = 'STRUCT_DATA'

    _metadata = metadata
    if _metadata is None:
      if hasattr(self, "_metadata") and isinstance(self._metadata, MetadataObject):
        _metadata = self._metadata.__dict__.copy()
      else:
        _metadata = {}
    _metadata = {
      **_metadata,
      # **self.cfg_stream_config_metadata,
    }
    return {
      "IMG" : img,
      "STRUCT_DATA" : struct_data,
      "METADATA" : _metadata,
      "INIT_DATA" : init_data,
      "TYPE" : _type
    }

  
  def __on_init(self):
    self._on_init()
    return
    
  def on_init(self):
    """
    Called before the normal connection is established. This method should be used to initialize
    variables.
    """
    return
  
  def _on_init(self):
    self.on_init()
    return  


  def _init(self):
    self._maybe_reconnect()
    return
      
  
  def _maybe_reconnect(self):
    if self.has_connection:
      return    
    if self.connect():
      self.has_connection = True  
    else:
      self.has_connection = False
    return self.has_connection
  
  @abc.abstractmethod
  def connect(self):
    raise NotImplementedError()
  
  @abc.abstractmethod
  def _release(self):
    raise NotImplementedError()

  @abc.abstractmethod
  def data_step(self):
    raise NotImplementedError()


  def run_data_aquisition_step(self):
    self.data_step()
    return

  def _run_data_aquisition_step(self):
    self.run_data_aquisition_step()
    return
  
  def __run_data_acquisition_step(self):
    self._run_data_aquisition_step()
    return

  def _handle_commands(self):
    # TODO: rethink command structure
    commands = self.config.get('COMMANDS', [])
    for cmd in commands:
      # check each command and execute specific setup
      known = False
      if cmd.get('FINISH_ACQUISITION', False):
        # basically a DCT loop exit
        self.has_finished_acquisition = True
        received_cmd = "FINISH_ACQUISITION"
        known = True
      else:
        received_cmd = cmd
        known = False
      # endif any known command check 
      msg = "Received {} '{}' command from INITIATOR_ID:'{}' with SESSION_ID:'{}'".format(
        'known' if known else 'unknown',
        received_cmd,
        self.config.get('INITIATOR_ID'),
        self.config.get('SESSION_ID'),
        )
      self.P(msg, color='r' if not known else None)
    #endfor each command
    return

  def _add_struct_data_input(self, obs):
    """
    This method is used to add a structured data input to the stream.

    Parameters
    ----------
    obs : any
        the structured data to be added to the stream.
    """
    self._add_inputs(
      [
       self._new_input(img=None, struct_data=obs, metadata=self._metadata.__dict__.copy(), init_data=None),
      ]
    )   
    
    
  def _add_struct_data_inputs(self, obs_list):
    """
    This method is used to add a list of structured data inputs to the stream.

    Parameters
    ----------
    obs_list : list
        list of any structured data to be added to the stream.
    """
    inputs = []
    for obs in obs_list:
      inputs.append(self._new_input(img=None, struct_data=obs, metadata=self._metadata.__dict__.copy(), init_data=None))
    self._add_inputs(inputs)
    return
  
    
  def _add_img_input(self, img):
    self._add_inputs(
      [
       self._new_input(img=img, struct_data=None, metadata=self._metadata.__dict__.copy(), init_data=None),
      ]
    )    
    

  def _add_inputs(self, inputs):
    """
    Threaded routine for adding stream data in the deque.

    Parameters
    ----------
    inputs : list[dict]
      A list with input for each sub-stream in the current stream.
      Each input (dictionary) is a structure returned by `self._new_input(...)`
    """

    assert isinstance(inputs, list)

    if not self.cfg_live_feed:
      #if in `buffer` mode and the buffer is full, wait the buffer to release a position and then add a new item
      tm_add_inputs_start = time()
      while True:
        # break loop if stop or config change
        if self._stop or self._loop_paused:
          self.P("Skipping {} frames! STOP:{}  LOOP_PAUSED:{}".format(len(inputs), self._stop, self._loop_paused), color='r')
          break
        # very important: update if maxlen==1 although this is already taken care by self.cfg_live_feed
        elif ((len(self._deque) == self._deque.maxlen) and (self._deque.maxlen > 1)): 
          self.sleep(0.05)
          # # now check if too much time wasted          
          # TODO(AID): add a flag that enables this
          # if (time() - tm_add_inputs_start) > 2:
          #   # maybe too much time wasted here so break it
          #   break
        else:
          break
        #endif
      #endwhile
    #endif
    self._deque.append(inputs)
    self.__last_add_input_timestamp = time()
    return
  
  # def _clear_inputs(self):
  #   """
  #   Use this method if you want to invalidate all data in the deque.
  #   This method should be used only in very specific situations, such as in a URL change,
  #   where the business plugin 
  #   """
  #   self._deque.clear()
    
  
  def _reset_state(self):
    self.has_connection = False
    self.has_finished_acquisition = False
    self.thread_stopped = False
    return

  def _can_run_data_aquisition_step(self):
    current_interval = self.acquisition_on_intervals_current_interval()
    if isinstance(current_interval, str) and len(current_interval) == 0:
      return False, current_interval
    return True, current_interval

  def _run_thread(self):
    try:
      self.__on_init()
      self._init() 
      lap_timestamp = None
      while True:

        while self._loop_paused:
          sleep(0.001)
          self._capture_thread_waiting = True
        
        # first trigger on-config and set LAST_UPDATE_TIME 
        self.maybe_trigger_on_config()
          
        self._capture_thread_waiting = False

        self._capture_loop_in_exec = True
        start_processing_time = time()
        self.start_timer('thread_loop')
        #  _maybe_reconnect must be defined by each individual DCT
        self._maybe_reconnect()

        # first run commands in any (this will also take care of LAST_UPDATE_TIME)
        self.__maybe_trigger_pipeline_command()
        
        # now run normal data acquisition                
        can_run, current_interval = self._can_run_data_aquisition_step()
        if can_run:
          self._metadata.current_interval = current_interval
          self.start_timer('run_data_acquisition_step')
          #  _run_data_aquisition_step must be defined by each individual DCT
          self.__run_data_acquisition_step()
          self.end_timer('run_data_acquisition_step')
        #endif
        self._capture_loop_in_exec = False

        if lap_timestamp is None:
          lap_timestamp = time()
        else:
          lap_time = time() - lap_timestamp
          self.__lap_times.append(lap_time)
          avg_time = np.mean(self.__lap_times)
          self._DPS = 1 / avg_time
          lap_timestamp = time()
        
        if self._stop:
          #stop command received from outside. stop imediatly
          self.P('`stop` command received. Exiting from `{}.run_thread`'.format(
            self.__class__.__name__))
          break
        elif self.has_finished_acquisition:
          if not self.is_reconnectable or self.is_keepalive:            
            # no more data to collect, no reconnection needed, then exit
            # at this point we also break from keep-alive streams that need to stay 
            # zombie (even with no data) until some cleanup happends, so while "NO"
            # streams will be collected for deletion the keep-alive ones will not be
            # collected
            self.P('`_run_data_aquisition_step` finished. Exiting from `run_thread`')
            break
          else:
            #reset states and allow main loop to restart the process
            self._reset_state()
        #endif
        ###
        self._handle_commands()
        ###
        processing_time = time() - start_processing_time
        self.__process_times.append(processing_time)
        # give other threads some time or predefined time
        self.start_timer('thread_sleep')
        # next we calc sleep based on cap_resolution property that choses
        # between proposed and heuristical resolution (if available)
        sleep_time = max(1 / self.cap_resolution - processing_time, 0.00001)
        sleep(sleep_time)
        elapsed_sleep = self.end_timer('thread_sleep')
        self.__sleep_times.append(elapsed_sleep)
        self.end_timer('thread_loop')
      #endwhile
      
      # wait for deque to be fully read (LIVE_FEED=false) if thread was not 
      # force-stopped, so this way we ensure the downstream tasks are aware 
      # and receive the last observation
      if len(self._deque) > 0 and not self._stop:
        self.P("thread waiting for manager to consume the queue...")
      while len(self._deque) > 0 and not self._stop:
        sleep(1)
      
      # THIS SHOULD BE DEFINED IN SUBCLASS
      self._release()
      # END
      
      self.P('DCT `run_thread` finished')
      self.thread_stopped = True
    except Exception as exc:
      exc_info = self.trace_info()
      msg = "EXCEPTION in capture's _run_thread: {}".format(exc)
      self.P(msg, color='error')
      self.P("  >> info:\n {}".format(exc_info), color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        info=exc_info,
        autocomplete_info=True
      )
    #end try-except whole DCT loop
    # now that loop exists we can safely perform additional cleanup
    return
  
  
  def get_runstats(self, last_n=1):
    np_laps = np.array(self.__lap_times).round(3)
    np_proc = np.array(self.__process_times).round(3)
    np_sleep = np.array(self.__sleep_times).round(3)    
    status = ''
    np.set_printoptions(precision=3, floatmode='fixed')
    if len(np_laps) > 1 and len(np_proc) > 1 and len(np_sleep) > 1:
      status = "{:.03f} {} = {:.03f} {} + {:.03f} {}".format(
        np_laps.mean(), np_laps[-last_n:],
        np_proc.mean(), np_proc[-last_n:],
        np_sleep.mean(), np_sleep[-last_n:],
      )
    return status

  def get_data_capture(self):
    if self.has_data and not self.received_first_data:
      self.P("Data received from source after connect.", color='g')
      self.mark_receved_first_data()

    if self.cfg_live_feed:
      all_inputs = self._deque.pop()
    else:
      all_inputs = []
      # TODO: review below section
      for _ in range(self.cfg_stream_window):
        if len(self._deque) == 0:
          break
        all_inputs += self._deque.popleft()
      #endfor
    #endif

    metadata = self._stream_metadata
    metadata.actual_dps = round(self.actual_dps, 2)
    
    metadata.cap_resolution = self.cap_resolution
    metadata.cap_max_queue_len = self._deque.maxlen
    metadata.cap_queue_len = len(self._deque)
    metadata.cap_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
    metadata.cap_elapsed_time = round(time() - self._start_time, 2)
    metadata.cap_signature = self.__class__.__name__
    
    metadata.live_feed = self.cfg_live_feed
    metadata.reconnectable = self.cfg_reconnectable
    metadata = metadata.__dict__

    data_capture = self.data_template(all_inputs, metadata)
    
    if self.__last_get_data_capture_timestamp > 0:
      elapsed = self.time() - self.__last_get_data_capture_timestamp
      self.__get_data_capture_laps.append(elapsed)
    #endif
    self.__last_get_data_capture_timestamp = self.time()
    return data_capture

  @property
  def has_data(self):
    has_data = self._deque and len(self._deque) > 0
    return has_data
  
  def start(self):
    if RUN_ON_THREAD:
      self._thread = Thread(
        target=self._run_thread, 
        args=(), 
        name=ct.THREADS_PREFIX + 'data_' + self.cfg_name,
        daemon=True,      
      )
      self._thread.daemon = True
      self._thread.start()
    else:
      self.P("Warning! Data Capture Thread does not run on thread", color='e')
      self._run_thread()
    return
  
  def stop(self, join_time=10):
    self._stop = True
    if join_time:
      self._thread.join(join_time)
    self._deque.clear()
    self._reset_state()
    return  
    
    
  def _download(self, url, file_name=None, progress=False, verbose=False, notify_download=None, unzip=False):
    if file_name is None:
      file_name = self.log.now_str()
      
    path = self.log.check_folder_data(ct.FOLDER_DOWNLOADS)
    fn = os.path.join(ct.FOLDER_DOWNLOADS, file_name)
    self.log.maybe_download(
      url=url,
      fn=fn,
      target=ct.FOLDER_DATA,
      force_download=True,
      print_progress=progress, #set False to avoid blocking threads
      verbose=verbose, #set False to avoid blocking threads
      publish_func=notify_download,
      unzip=unzip
      )
    fn_out = os.path.join(path, file_name)
    return fn_out
  
  
  def _on_pipeline_command(self, cmd_data, payload_context=None, **kwargs):
    """This method must be implemented in derived classes if they need to handle pipeline commands

    Parameters
    ----------
    cmd_data : dict / str
        the actual data of the command
        
    payload_context : dict, optional
        the payload context of the command. The default is None.
        
    **kwargs : optional arguments derived from the command
    
    """
    return
  
  
  def __on_pipeline_command(self, cmd_data):
    self.P("Running `_on_pipeline_command` ({}:{}) with command: {}...".format(
      ct.CONFIG_STREAM.LAST_UPDATE_TIME, self.config_data.get(ct.CONFIG_STREAM.LAST_UPDATE_TIME, None),
      str(cmd_data)[:30])
    )
    payload_context = None
    kwargs = {} # should be populated with data from the payload if any
    if isinstance(cmd_data, dict):
      payload_context = cmd_data.get('PAYLOAD_CONTEXT', None)
    self._metadata.payload_context = payload_context # set the payload context

    # this part handles pipeline commands that should be answered only on the local comms 
    use_local_comms_only = False
    if isinstance(cmd_data, dict):
      use_local_comms_only = cmd_data.get('USE_LOCAL_COMMS_ONLY', False)
    self._metadata.use_local_comms_only = use_local_comms_only
    self._on_pipeline_command(
      cmd_data=cmd_data,
      payload_context=payload_context,
      **kwargs,
    )
    self._metadata.payload_context = None # reset the payload context
    self._metadata.use_local_comms_only = False # reset the use_local_comms_only
    return
  
  
  def __maybe_trigger_pipeline_command(self):
    triggered = False
    if self.cfg_pipeline_command is not None and len(self.cfg_pipeline_command)>0:
      # new command at pipeline level handled by the DCT!
      command = self.cfg_pipeline_command
      # make sure we save config before code
      self.archive_config_keys(keys=['PIPELINE_COMMAND'], defaults=[{}])
      # now run the code (on-config should have already been triggered)
      self.__on_pipeline_command(cmd_data=command)
      # copy, erase and save
      triggered = True
    #end has pipeline command
    return triggered
  
  
  def __save_config(self, keys):
    # writes the config for a set of particular keys on the local cache!
    save_config_fn = self.shmem[ct.CALLBACKS.PIPELINE_CONFIG_SAVER_CALLBACK]
    try:
      config = {k : self.config_data[k] for k in keys}
      save_config_fn(pipeline_name=self.cfg_name, config_data=config, skip_update_time=True)
      # the save will trigger a update config when the CaptureManager will get the new config from
      # the ConfigManager and will pass it as upstream_config to the plugin - basically will see that
      # we have the "last key" and the "current" keys modified
      # so we already update the upstream_config to avoid this
      # nevertheless we must `skip_update_time=True` in order to avoid the update time to be set by ConfigManager
      keys.append(ct.CONFIG_STREAM.LAST_UPDATE_TIME) # this triggers re-update of the config if not properly set
      for k in keys:
        self._upstream_config[k] = self.config_data[k]
    except Exception as exc:
      self.P("Error '{}' while saving keys {}".format(exc, keys))
      raise exc
    return
  
  
  def archive_config_keys(self, keys : list, defaults=None):
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
    # TODO: maybe ignore if LAST_PIPELINE_COMMAND contained 'IMG' key or if too large

    for i, key in enumerate(keys):
      archive_key = self.ct.CONFIG_STREAM.LAST_PIPELINE_COMMAND
      self.config_data[archive_key] = self.config_data[key]
      self.config_data[key] = defaults[i]
      all_keys.append(archive_key)
    #endfor
    self.save_config_keys(all_keys)
    return


  def save_config_keys(self, keys : list):
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
    EXCEPTIONS = ['NAME', 'TYPE']
    result = False
    all_exist = all([k in self.config_data for k in keys])
    all_accepted = all([k not in EXCEPTIONS for k in keys])
    if all_exist and all_accepted:
      self.__save_config(keys)
      result = True
    return result
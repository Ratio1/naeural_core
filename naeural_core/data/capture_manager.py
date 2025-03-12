import traceback
import pandas as pd

from copy import deepcopy
from naeural_core import constants as ct
from time import time
from collections import OrderedDict

from naeural_core.manager import Manager
from naeural_core import Logger
from naeural_core.local_libraries import _ConfigHandlerMixin

DEFAULT_DISALLOWED_URL_DUPLICATES = ['VIDEOSTREAM']
WARNING_REDISPLAY_TIME = 30

class CaptureManager(Manager, _ConfigHandlerMixin):
  def __init__(self, log : Logger, shmem, owner, environment_variables=None, **kwargs):
    self.__name__ = ct.CAPTURE_MANAGER
    shmem[ct.CAPTURE_MANAGER] = {
      ct.NR_CAPTURES : 0, 
      }
    self.shmem = shmem

    self.owner = owner
    self._environment_variables = environment_variables or {}
    self._dct_config_streams = None
    self._dct_captures = None
    self._dct_killed_captures = None
    self._last_gather_summary = None
    self.is_data_available = False
    
    self.data_flow_stopped = False    
    self._data_flow_skip_last_log = 0
    self._nr_data_flow_skips = 0
    self._data_flow_stop_last_log = 0
    self._nr_data_flow_stops = 0


    super(CaptureManager, self).__init__(log=log, prefix_log='[{}]'.format(ct.CAPTURE_MANAGER), **kwargs)
    return
  
  def startup(self):
    super().startup()
    self._dct_captures = self._dct_subalterns
    self._dct_killed_captures = {}
    return

  def update_streams(self, dct_config_streams):
    if self._dct_config_streams != dct_config_streams:
      self.owner.set_loop_stage('4.collect.update_streams.start')
      self._dct_config_streams = dct_config_streams
      self.config_data = self._dct_config_streams
      self.owner.set_loop_stage('4.collect.update_streams._check_captures')
      current_captures = self._check_captures()
      self.owner.set_loop_stage('4.collect.update_streams._deallocate_unused_captures')
      self._deallocate_unused_captures(current_captures)
    #endif
    return
  
  def _maybe_log_dataflow_errors(self, msg=''):
    msg1, msg2 = None, None
    plugins = self.owner.get_overloaded_business_plugins()
    show1 = True #((time() - self._data_flow_skip_last_log) > (WARNING_REDISPLAY_TIME * 1.3)) and len(plugins) > 0
    show2 = True #(time() - self._data_flow_stop_last_log) > WARNING_REDISPLAY_TIME
    if msg == '':
      msg = 'DataFlow errors:'
    if (show1 or show2) and msg:
      self.P(msg, color='r')
    
    if show1:
      self._data_flow_skip_last_log = time()
      msg1 = "  {} skippings for data capture due to the following delayed plugins: {}".format(
        self._nr_data_flow_skips,
        plugins
      )
      self.P(msg1, color='r')
      
    if show2:
      self._data_flow_stop_last_log = time()
      msg2 = "  {} data acquisition stop/restarts so far due to business plugins issues".format(
        self._nr_data_flow_stops,
      )
      self.P(msg2, color='r')
      
    if msg1 is not None or msg2 is not None:
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING,
        msg="DCT Issues: delays in business plugins may generate data loss. Please check configs",
        info="M1:{} M2:{}".format(msg1, msg2),
        displayed=False, # force display
      )
    return
      
    
  
  def _needs_skip_acquisition(self):
    result = False
    if self.owner.any_overloaded_business_plugins:
      result = True
      self._nr_data_flow_skips += 1
    return result
        
    

  def _check_captures(self):
    current_captures = []
    self.shmem[ct.CAPTURE_MANAGER][ct.NR_CAPTURES] = len(self._dct_config_streams)
    streams = list(self._dct_config_streams.keys())
    for s in streams:
      config_stream = self._dct_config_streams[s]
      if config_stream[ct.TYPE].lower() == 'void':
        # void streams are not controlled by capture manager, as they do not provide data.
        continue

      key = config_stream[ct.NAME]
      current_captures.append(key)

      if key not in self._dct_captures:        
        # new capture
        self.owner.set_loop_stage('4.collect.update_streams._check_captures.start_capture.' + key)
        ok_capture = self.start_capture(config_stream)
        if ok_capture:
          self.log.P("Start capture '{}' succedeed.".format(key), color='g')
        else:
          self.P("Start capture '{}' failed.".format(key), color='r')
      else:
        # maybe config it
        capture = self._dct_captures[key]
        self.owner.set_loop_stage('4.collect.update_streams._check_captures.maybe_update_config.' + key)
        capture.maybe_update_config(config_stream)
      #endif
    #endfor    
    return current_captures

  def _deallocate_unused_captures(self, current_captures):
    keys = list(self._dct_captures.keys())
    for k in keys:
      if k not in current_captures:        
        self.stop_capture(k)
    return
  
  def _check_url_duplicate(self, cfg_url, cfg_type, cfg_name):
    if cfg_url is None:
      return 
    disallowed_types = self._environment_variables.get('DISALLOWED_URL_DUPLICATES', [])
    disallowed_types = [x.upper() for x in disallowed_types]
    for default_disallowed_type in DEFAULT_DISALLOWED_URL_DUPLICATES:
      if default_disallowed_type not in disallowed_types:
        disallowed_types.append(default_disallowed_type.upper())
    caps = [x for n,x in self._dct_captures.items() if n != cfg_name and x is not None]
    target_caps = [(cap.cfg_name, cap.cfg_type) for cap in caps if cap.cfg_type.upper() in disallowed_types]
    if cfg_type.upper() in disallowed_types:
      self.P("Checking URL duplicate for '{}' ({}) against: {}".format(cfg_name, cfg_type, target_caps))
    else:
      return
    for cap in caps:
      found_cap = cap.cfg_name
      found_url = cap.cfg_url
      found_type = cap.cfg_type
      
      if (cfg_url == found_url) and (cfg_type == found_type) and cfg_type.upper() in disallowed_types:
        msg = "Data capture '{}' ({}) has duplicate URL '{}' of existing capture '{}' ({}).".format(
          cfg_name, cfg_type, cfg_url, 
          found_cap, found_type,
        )
        self.P(msg, color='r')
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
          notif_code=ct.NOTIFICATION_CODES.PIPELINE_FAILED,
          msg=msg,
          stream_name=cfg_name,
          info=msg + ' Recommendation is to use "TYPE" : "Metastream" and "COLLECTED_STREAMS" : ["{}"] within {}'.format(
          found_cap, cfg_name),
          duplicate_url=found_url,
          duplicate_stream_name=found_cap,
          proposed_solution={"COLLECTED_STREAMS" : [found_cap]},
          displayed=True,
        )
        raise ValueError(msg)
      #endif
    #endfor
    return


  def start_capture(self, config):
    stream_name = config[ct.NAME]
    plugin_signature = config[ct.TYPE]
    _cls, _cls_config = self._get_plugin_class(plugin_signature)

    if _cls is None:
      capture = None
      msg = "Error loading capture plugin '{}'".format(plugin_signature)
      self.P(msg + " on stream {}".format(stream_name), color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        notif_code=ct.NOTIFICATION_CODES.PIPELINE_FAILED,
        msg=msg,
        session_id=config.get(ct.PAYLOAD_DATA.SESSION_ID),
        initiator_id=config.get(ct.PAYLOAD_DATA.INITIATOR_ID),
        stream_name=stream_name,
        info="No code/script defined for capture plugin '{}' in {}".format(plugin_signature, ct.PLUGIN_SEARCH.LOC_DATA_ACQUISITION_PLUGINS),
        ct=ct,
      )
    else:
      try:
        self.P("Attempting to start data capture <{}:{}> ...".format(
          plugin_signature, stream_name), color='m'
        )
        ## pre-instance checks
        ### check if this capture uses a alrady in use URL and raise exception
        cfg_url = config.get(ct.URL, None)
        self._check_url_duplicate(cfg_url=cfg_url, cfg_type=plugin_signature, cfg_name=stream_name)
        ### end check duplicate URL
        ## end pre-instance checks
        capture = _cls(
          log=self.log,
          default_config=_cls_config,
          upstream_config=config,
          environment_variables=self._environment_variables,
          shmem=self.shmem,
          fn_loop_stage_callback=self.owner.set_loop_stage,
          signature=plugin_signature
        )
        if capture.cfg_is_thread:
          capture.start()
          msg = "Started capture thread for pipeline {} (url:{} live:{} recon:{})".format(stream_name, capture.cfg_url, capture.cfg_live_feed, capture.is_reconnectable)
        else:
          msg = "Started META capture: {} (collected: {})".format(stream_name, capture.cfg_collected_streams)
        #endif
        self.P(msg)
        ### IMPORTANT: the following message signals that the pipeline can be used
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_NORMAL,
          notif_code=ct.NOTIFICATION_CODES.PIPELINE_OK,
          msg=msg,
          stream_name=stream_name,
          session_id=config.get(ct.PAYLOAD_DATA.SESSION_ID),
          initiator_id=config.get(ct.PAYLOAD_DATA.INITIATOR_ID),
          autocomplete_info=True,
          displayed=True,
        )        
      except Exception as exc:
        # capture is set to None and will trigger archiving from `get_finished_streams` via orchestrator 
        capture = None 
        msg = "Exception '{}' in capture plugin `{}` init for '{}': {}\n{}".format(
          exc, 
          plugin_signature, 
          stream_name,
          self.log.get_error_info(),
          traceback.format_exc(),
        )
        self.P(msg, color='r')
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
          notif_code=ct.NOTIFICATION_CODES.PIPELINE_FAILED,
          msg=msg,
          stream_name=stream_name,          
          session_id=config.get(ct.PAYLOAD_DATA.SESSION_ID),
          initiator_id=config.get(ct.PAYLOAD_DATA.INITIATOR_ID),
          autocomplete_info=True,
          displayed=True,
        )
      #end try-except
    #endif

    self._dct_captures[stream_name] = capture
    ok_capture = capture is not None
    return ok_capture

  def _get_plugin_class(self, signature):
    _module_name, _class_name, _class_def, _class_config = self._get_module_name_and_class(
      locations=ct.PLUGIN_SEARCH.LOC_DATA_ACQUISITION_PLUGINS,
      name=signature,
      suffix=ct.PLUGIN_SEARCH.SUFFIX_DATA_ACQUISITION_PLUGINS,
      safe_locations=ct.PLUGIN_SEARCH.SAFE_LOC_DATA_ACQUISITION_PLUGINS,
      safe_imports=ct.PLUGIN_SEARCH.SAFE_LOC_DATA_ACQUISITION_IMPORTS,
      safety_check=True, 
    )
    return _class_def, _class_config


  def stop_capture(self, key, shutdown=False):
    capture = self._dct_captures.pop(key)
    if capture is None:
      self.P("  Capture '{}' already stopped!".format(key), color='r')      
    else:
      cap_type = capture.cfg_type
      self.P("  Stopping capture/pipeline '{}'  ({})...".format(key, cap_type), color='y')
      
      if capture is None:
        return
      self.owner.set_loop_stage('4.collect.capture_stop_{}'.format(key))
      jointime = 0.1 if shutdown else 10 
      capture.stop(join_time=jointime)
      self._dct_killed_captures[key] = None
      self.P("  Stopped capture '{}'".format(key), color='y')
    #endif capture already stopped or not
    return


  def close(self):
    self.P("Stopping all DCTs...", color='y')
    self.stop_captures(shutdown=True)
    self.P("Dope stopping all DCTs...", color='y')
    return


  def stop_captures(self, shutdown=False):
    keys = list(self._dct_captures.keys())
    for k in keys:
      self.stop_capture(k, shutdown=shutdown)
    return


  def get_captured_data(self, key):
    """
    This handles only a DCT based on `key` that is not a metastream. Metastreams
    are handled in `get_all_captured_data` post the call of this function

    Parameters
    ----------
    key : str
      name of the DCT.

    Returns
    -------
    has_data : bool
      Data or no data bool.
      
    captured_data : dict
      the captured data.

    """

    has_data = False
    captured_data = None
    if key in self._dct_captures:
      capture = self._dct_captures[key]
      if capture is not None and capture.cfg_is_thread and capture.has_data:
        self.owner.set_loop_stage('4.collect.get_all_cap.get_captured_data.' + key)
        captured_data = capture.get_data_capture()
        has_data = True
      #endif
    #endif
    return has_data, captured_data

  def _aggregate_captured_data_in_metastreams(self, dct_captured_data):
    """
    This function populates meta-streams with data from normal DCTs
    """
    self.log.start_timer('aggregate_meta')
    for key in self._dct_captures:
      capture = self._dct_captures[key]
      if capture is not None and not capture.cfg_is_thread:
        self.log.start_timer('prepare_meta_capture')
        # is_thread will return True for normal DCT threads while form meta 
        # streams False will return (as it will be simple DataCapture instanaces)
        inputs = []
        all_collected_streams = capture.cfg_collected_streams
        lst_datas = []
        call_metastream_post_process = True
        for collected_stream in all_collected_streams:
          ### if any of the collected streams is not captured, then the meta-stream can't feed data because errors may occur (unless it is not configured to be loose: "IS_LOOSE")
          if collected_stream not in dct_captured_data:
            if capture.cfg_is_loose:
              # default behaviour of meta-streams is to accept partial data
              continue
            else:
              call_metastream_post_process = False
              break
          #endif

          crt_inputs = dct_captured_data[collected_stream]['INPUTS']
          self.log.start_timer('prepare_meta_capture_data')
          for dct_inp in crt_inputs:
            # we take each input and deep-copy it
            dct_res = deepcopy(dct_inp)
            # now we add original metadata & other info
            dct_res['METADATA'] = {
              **dct_res['METADATA'],
              **dct_captured_data[collected_stream]['STREAM_METADATA'],
              **{'SOURCE_STREAM_NAME' : dct_captured_data[collected_stream]['STREAM_NAME']}
            }
            lst_datas.append(dct_res)
          self.log.stop_timer('prepare_meta_capture_data')
        # endfor each collected stream (if any)
        if call_metastream_post_process:
          """
          If we do not hit the break at line 359, we execute the post_process_inputs.
          This means that we found all our captures with data.
          """
          if hasattr(capture, 'post_process_inputs'):
            # we post-process original inputs if required
            self.log.start_timer('prepare_meta_capture_post_proc')
            lst_metastream_inputs = capture.post_process_inputs(lst_datas)
            self.log.stop_timer('prepare_meta_capture_post_proc')
            # finally we add the inputs to the list of inputs
            inputs += lst_metastream_inputs
        # endif call_metastream_post_process
        if len(inputs) > 0:
          # finally we add the date in the captured data dict for downstream consumption
          dct_captured_data[key] = capture.data_template(inputs=inputs, metadata={
            "cap_time":inputs[-1]
              .get('METADATA', {})
              .get('cap_time', 0)
          })
        self.log.stop_timer('prepare_meta_capture')
      #endif is meta-stream
    #endfor
    self.log.stop_timer('aggregate_meta')
    return dct_captured_data

  def get_all_captured_data(self):
    self.log.start_timer('get_all_captures_full')
    
    self.log.start_timer('get_all_captures_stage1')
    self.owner.set_loop_stage('4.collect.get_all_cap.start')
    dct_captured_data = {}
    needs_skip = self._needs_skip_acquisition()
    self.log.stop_timer('get_all_captures_stage1')
    
    if needs_skip:
      self.log.start_timer('get_all_captures_skip')
      self.owner.set_loop_stage('4.collect.get_all_cap.data_flow_stopped')
      if not self.data_flow_stopped:
        self._nr_data_flow_stops += 1
        self._maybe_log_dataflow_errors(msg="Data flow STOPPED.")
      self.data_flow_stopped = True
      self.log.stop_timer('get_all_captures_skip')
    else:
      self.log.start_timer('get_all_captures_stage2')
      self.owner.set_loop_stage('4.collect.get_all_cap.data_flow_working')
      if self.data_flow_stopped:
        self._maybe_log_dataflow_errors(msg="Data flow RESTARTED.")        
      self.data_flow_stopped = False      
      self.owner.set_loop_stage('4.collect.get_all_cap.get_captured_data')
      self.log.stop_timer('get_all_captures_stage2')

      self.log.start_timer('get_all_captures_stage3')      
      lst_avail = [k for k in self._dct_captures if self._dct_captures[k] is not None]
      n = len(lst_avail)
      str_stage = 'get_captured_n{}'.format(n)
      self.log.start_timer(str_stage)
      self.owner.set_loop_stage('4.collect.get_all_cap.get_captured_data.' + str_stage)
      dct_captured_data_raw = {k: self.get_captured_data(k) for k in lst_avail}
      self.log.stop_timer(str_stage)
      
      self.owner.set_loop_stage('4.collect.get_all_cap.dct_captured_data_raw')
      dct_captured_data = {
        name: captured_data for name, (has_data, captured_data) in dct_captured_data_raw.items() if has_data
      }      
      self.log.stop_timer('get_all_captures_stage3')   
         
      self.log.start_timer('get_all_captures_stage4')
      # aggredate date where we have streams
      self.owner.set_loop_stage('4.collect.get_all_cap.aggregate_metastreams')
      dct_captured_data = self._aggregate_captured_data_in_metastreams(dct_captured_data)
      self.log.stop_timer('get_all_captures_stage4')

      # TODO(S+AID): check all `dct_captured_data` images and raise warning if images are not `uint8`!

      self.log.start_timer('get_all_captures_stage_status')
      display_status_each = self.log.config_data.get(ct.CAPTURE_STATS_DISPLAY,  ct.CAPTURE_STATS_DISPLAY_DEFAULT)
      if (self._last_gather_summary is None) or (time() - self._last_gather_summary > display_status_each):
        self._last_gather_summary = time()
        self.owner.set_loop_stage('4.collect.get_all_cap.get_captures_status')
        _ = self.get_captures_status(display=True)
      #endif display stats
      
      if len(dct_captured_data) > 0: 
        # this was "unrolled" for debug purposes, so please do not `self.is_data_available = len(dct_captured_data) > 0`
        self.is_data_available  = True
      else:
        self.is_data_available = False
      self.log.stop_timer('get_all_captures_stage_status')
    #endif needs skip
    self.log.stop_timer('get_all_captures_full')
    return dct_captured_data
  
  
  def get_captures_status(self, display=False, as_dict=True):
    name_maxlen = 15
    n_issues = 0
    info_text = ""
    alerts = {}
    issues = ""
    dct_cap_status = OrderedDict()
    issue_caps = []
    config_startup = self.shmem['config_startup'] 
    eeid = config_startup.get(ct.CONFIG_STARTUP_v2.K_EE_ID, '')[:ct.EE_ALIAS_MAX_SIZE]
    title = "Current active captures on '{}' (only DCT captures shown with no metastreams):".format(eeid)
    dct_msg = OrderedDict()
    # TODO: generalize the stat_cols keys so that they can be adapted to any type of capture
    #       and not only to the current implementation of ffmpeg
    stat_cols = ["Dup", "Drop", "Dec", "Cor", "Miss", "Con", "Delay", ] # this should be a `STAT_COLS` generalized constant with all needed keys 
    all_cols = ["Status" , "Name", "IdlCap", "IdlSnd", "LPS", "CFG/TGT", "DDPS", "Fails"]
    all_cols += stat_cols
    # all_cols += ["Running stats"]
    for col in all_cols:
      dct_msg[col] = []
    now_str = self.log.time_to_str()
    # process all DCTs
    cap_names = list(self._dct_captures.keys())
    maxlen = max([0] + [len(x) for x in cap_names])
    for key in cap_names:
      capture = self._dct_captures[key]
      if capture is None:
        continue
      alerts[key] = capture.is_idle_alert
      if capture.cfg_is_thread:
        dct_cap_status[key] = {
          'TYPE'        : capture.cfg_type,
          'FLOW'        : 'live' if capture.cfg_live_feed else 'buff',
          'IDLE'        : round(capture.time_since_last_input,1),
          'IDLE_ALERT'  : capture.is_idle_alert,
          'DPS'         : round(capture.actual_dps,3),
          'CFG_DPS'     : capture.cfg_cap_resolution,
          'TGT_DPS'     : capture.cap_resolution,
          'RUNSTATS'    : capture.get_runstats(),
          'PLUGINSTATS' : capture.get_plugin_specific_stats(),
          'COLLECTING'  : None,
          'FAILS'       : capture.nr_connection_issues,
          'NOW'         : now_str,
          'GET_DPS'     : capture.generate_resolution,
        }
        dct_msg['Status'].append(dct_cap_status[key]['FLOW'])
        dct_msg['Name'].append(key[:name_maxlen])
        dct_msg['IdlCap'].append(dct_cap_status[key]['IDLE'])
        dct_msg['IdlSnd'].append(round(capture.time_since_last_data,1))
        dct_msg['LPS'].append(dct_cap_status[key]['DPS'])
        dct_msg['CFG/TGT'].append("{}/{}".format(dct_cap_status[key]['CFG_DPS'], dct_cap_status[key]['TGT_DPS']))
        dct_msg['DDPS'].append(dct_cap_status[key]['GET_DPS'])
        dct_msg['Fails'].append(dct_cap_status[key]['FAILS'])
        # dct_msg['Running stats'].append(dct_cap_status[key]['RUNSTATS'])      
        for col in stat_cols:
          dct_msg[col].append(dct_cap_status[key]['PLUGINSTATS'].get(col.lower(), -1))

        if display and alerts[key]: # limit the number of alerts by counting them only at display time
          n_issues += 1
          issues += '\n {} idle: {}'.format(key, dct_cap_status[key]["IDLE"])
          issue_caps.append(key)
        #endif count alerts
      #endif is working thread 
    #endfor
    # process all meta-streams
    for key in cap_names:
      capture = self._dct_captures[key]
      if capture is None:
        continue
      alerts[key] = capture.is_idle_alert
      if not capture.cfg_is_thread:
        dct_cap_status[key] = {
          'TYPE'        : capture.cfg_type,
          'FLOW'        : 'meta',
          'IDLE'        : round(capture.time_since_last_data,1),
          'DPS'         : -1,
          'CFG_DPS'     : -1,
          'TGT_DPS'     : -1,
          'RUNSTATS'    : -1,
          'PLUGINSTATS' : capture.get_plugin_specific_stats(),
          'COLLECTING'  : capture.cfg_collected_streams,
          'FAILS'       : -1,
          'NOW'         : now_str,
        }

        dct_msg['Status'].append("[meta]")
        dct_msg['Name'].append(key[:name_maxlen])
        dct_msg['IdlCap'].append(None)
        dct_msg['IdlSnd'].append(round(capture.time_since_last_data,1))
        dct_msg['LPS'].append(None)
        dct_msg['CFG/TGT'].append(None)
        dct_msg['DDPS'].append(None)
        dct_msg['Fails'].append(None)
        # dct_msg['Running stats'].append(capture.cfg_collected_streams)      
        for col in stat_cols:
          dct_msg[col].append(dct_cap_status[key]['PLUGINSTATS'].get(col.lower(), -1))
        
        if display and alerts[key]: # limit the number of alerts by counting them only at display time
          n_issues += 1
          issues += '\n {} idle: {}'.format(key, dct_cap_status[key]["IDLE"])
          issue_caps.append(key)
        #endif count alerts
      #endif is working thread 
    #endfor
    
    is_alert = any(alerts.values())
    
    

    if display:
      if len(cap_names) == 0:
        info_text = title + "\n  -------  No captures are running at this moment -------"
      else:
        all_cap_timer = self.log._format_timer(
          key='get_all_captures_full',
          section=self.log.default_timers_section,
          was_recently_seen=True,
        )
        prec = pd.get_option('display.float_format')
        _format = '{:.1f}'
        pd.set_option('display.float_format', lambda x: _format.format(x))        
        info_text = "{}\n{}\n----------------------------------------\nTimings: {})".format(
          title, pd.DataFrame(dct_msg),
          all_cap_timer.lstrip()
        )
        pd.set_option("display.float_format", prec)
      #endif
      self.P(info_text, color='r' if is_alert else 'g')
    # endif display
    
    if n_issues > 0:
      msg = "Data acquisition failure. One or more captures idle time over accepted thresholds: {}".format(issue_caps)
      info = msg + '\n' + issues
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING,
        msg=msg,
        info=info,
        errors=issue_caps,
      )
    if as_dict:
      return dct_cap_status
    else:
      return info_text


  def get_finished_streams(self):
    """
    This function returns stream that can be deleted due to:
      - object is None in capture dict (maybe it was `None` since its creation)
      or
      - can_delete (stream is NOT reconnectable and NOT keep-alive)
      or
      - stream has a limited life that has elapsed
    """
    lst = [
      key for key,obj in self._dct_captures.items() 
      if (obj is None or (obj.can_delete or obj.alive_until_passed))
    ]
    return lst

  def get_plugin_default_config(self, signature):
    _module_name, _class_name, _cls_def, _config_dict = self._get_module_name_and_class(
      locations=ct.PLUGIN_SEARCH.LOC_DATA_ACQUISITION_PLUGINS,
      name=signature,
      verbose=0,
      suffix=ct.PLUGIN_SEARCH.SUFFIX_DATA_ACQUISITION_PLUGINS,
      safe_locations=ct.PLUGIN_SEARCH.SAFE_LOC_DATA_ACQUISITION_PLUGINS,
      safe_imports=ct.PLUGIN_SEARCH.SAFE_LOC_DATA_ACQUISITION_IMPORTS,
      safety_check=True, # TODO: should we do this?
    )

    return _config_dict

"""
TODO: pipelines examples

"""
from naeural_core.business.base import BasePluginExecutor
from naeural_core.business.mixins_admin.network_monitor_mixin import _NetworkMonitorMixin, NMonConst

__VER__ = '1.0.1'

_CONFIG = {
  **BasePluginExecutor.CONFIG,

  'ALLOW_EMPTY_INPUTS'            : True,
  
  "PROCESS_DELAY"                 : 10,
  
  "SUPERVISOR"                    : False, 
  "SUPERVISOR_ALERT_TIME"         : 30,
  "SUPERVISOR_LOG_TIME"           : 60,
  "SEND_IF_NOT_SUPERVISOR"        : False,
  
  "ALERT_RAISE_CONFIRMATION_TIME" : 1,
  "ALERT_LOWER_CONFIRMATION_TIME" : 2,
  "ALERT_DATA_COUNT"              : 2,
  "ALERT_RAISE_VALUE"             : 0.75,
  "ALERT_LOWER_VALUE"             : 0.4,
  "ALERT_MODE"                    : 'mean',
  "ALERT_MODE_LOWER"              : 'max',
  
  "DEBUG_EPOCHS"                  : False,
  
  
  # debug stuff
  "LOG_INFO"            : False,
  "LOG_FULL_INFO"       : False,
  "EXCLUDE_SELF"        : False,
  "SAVE_NMON_EACH"      : 5, # this saves each SAVE_NMON_EACH * PROCESS_DELAY seconds

  'VALIDATION_RULES': {
    **BasePluginExecutor.CONFIG['VALIDATION_RULES'],
  },
}


class NetMon01Plugin(
  BasePluginExecutor,
  _NetworkMonitorMixin,
  ):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(NetMon01Plugin, self).__init__(**kwargs)
    self._nmon_counter = 0
    self.__supervisor_log_time = 0
    self.__state_loaded = False
    self.__last_epoch_debug_time = 0
    return

  def startup(self):
    super().startup()
    return
  
  def on_init(self):
    # convert supervisor to bool if needed
    is_supervisor = self.cfg_supervisor
    if isinstance(is_supervisor, str):
      self.P("Found string value for SUPERVISOR: {}. Converting to bool".format(is_supervisor))
      self.config_data['SUPERVISOR'] = is_supervisor.lower() == 'true'
    #endif is string
    return
  
  def _maybe_load_state(self):
    if self.__state_loaded:
      return
    self.__state_loaded = True
    self.netmon.network_load_status()
    return
  
  def on_command(self, data, **kwargs):

    request_type = 'history' #default to history
    target_node = None
    target_addr = None
    request_type = 'history'
    request_options = {}
    if isinstance(data, dict):
      dct_cmd = {k.lower() : v for k,v in data.items()} # lower case instance command keys
      target_node = dct_cmd.get('node', None)
      target_addr = dct_cmd.get('addr', None)
      request_type = dct_cmd.get('request', 'history')
      request_options = dct_cmd.get('options', {})

    if target_node is not None:
      target_addr = target_addr or self.netmon.network_node_addr(target_node)

    if target_addr is not None:
      self.P("Network monitor on {} ({}) received request for {} ({}): {}".format(
        self.e2_addr, self.eeid, target_addr, target_node, data))
      self._exec_netmon_request(
        target_addr=target_addr,
        request_type=request_type,
        request_options=request_options,
        data=data,
      )
    else:
      self.P("Network monitor on {} ({}) received invalid request for {} ({}): {}".format(
        self.e2_addr, self.eeid, target_addr, target_node, data), color='r')
    return
  
  def _maybe_save_debug_epoch(self):
    if self.cfg_debug_epochs and self.time() - self.__last_epoch_debug_time > 3600: # 1 hour
      self.__last_epoch_debug_time = self.time()
      epoch_manager = self.netmon.epoch_manager
      epoch_node_list = epoch_manager.get_node_list()
      epoch_node_states = [epoch_manager.get_node_state(node) for node in epoch_node_list]
      epoch_node_states = self.deepcopy(epoch_node_states)
      for entry in epoch_node_states:
        entry['current_epoch']['hb_dates'] = sorted(entry['current_epoch']['hb_dates'])
      epoch_node_epochs = [epoch_manager.get_node_epochs(node) for node in epoch_node_list]
      epoch_node_previous_epoch = [epoch_manager.get_node_previous_epoch(node) for node in epoch_node_list]
      epoch_node_last_epoch = [epoch_manager.get_node_last_epoch(node) for node in epoch_node_list]
      epoch_node_first_epoch = [epoch_manager.get_node_first_epoch(node) for node in epoch_node_list]
      epoch_stats = epoch_manager.get_stats()
      debug_epoch={
        "node_list": epoch_node_list,
        "node_states": epoch_node_states,
        "node_epochs": epoch_node_epochs,
        "node_previous_epoch": epoch_node_previous_epoch,
        "node_last_epoch": epoch_node_last_epoch,
        "node_first_epoch": epoch_node_first_epoch,
        "stats": epoch_stats,
      }
      self.log.save_output_json(
        data_json=debug_epoch,
        fname="{}.json".format(self.now_str(short=True)),
        subfolder_path="debug_epoch",
        indent=True,
      )
    return

  def _process(self):
    payload = None
    self._nmon_counter += 1      
    self._maybe_load_state()
    
    # TODO: change to addresses later
    current_nodes, new_nodes = self._add_to_history()       
    ranking = self._get_rankings()    
    
    str_ranking = ", ".join(["{}:{:.0f}:{:.1f}s".format(a,b,c) for a,b,c in ranking])
    
    
    is_anomaly, alerted_nodes = False, None
    
    current_network = None
    current_alerted = None
    is_supervisor = False
    current_ranking = ranking
    current_new = new_nodes

    if self.cfg_supervisor:
      # save status
      save_nmon_each = int(min(self.cfg_save_nmon_each, 300))
      if (self._nmon_counter % save_nmon_each) == 0: 
        self.P("Saving netmon status for {} nodes".format(len(current_nodes)))
        self.netmon.network_save_status()
      #endif save status

      is_anomaly, alerted_nodes = self._supervisor_check()
      self.alerter_add_observation(int(is_anomaly))        
      current_network = current_nodes
      current_alerted = alerted_nodes
      is_supervisor = True  
      
      if (self.time() - self.__supervisor_log_time) > self.cfg_supervisor_log_time:
        str_log = "***** Supervisor node sending status for network of {} nodes *****".format(len(current_network))
        known_nodes = self.netmon.network_known_nodes()
        for addr in known_nodes:
          eeid = self.netmon.network_node_eeid(addr)
          str_eeid = "'{}'".format(eeid[:10])
          str_eeid = "{:<13}".format(str_eeid)
          node_info = known_nodes[addr]
          working_status = current_network.get(eeid, {}).get('working', False)
          pipelines = node_info['pipelines']
          last_received = node_info['timestamp']
          ago = "{:5.1f}".format(round(self.time() - last_received, 2))
          ago = ago.strip()[:5]
          str_log += "\n - Node: <{}> {} ago {}s had {} pipelines, status: {}".format(
            addr, str_eeid, ago, len(pipelines), working_status
          )
        nr_dir, nr_hb = self.netmon.get_hb_vs_direct_pipeline_sources()
        str_log +=   "\n   Sources: {} direct, {} hb".format(nr_dir, nr_hb)
        self.P(str_log)
        self.__supervisor_log_time = self.time()
      #endif supervisor log time       
    #endif supervisor or not
    
    self._maybe_save_debug_epoch()

    if self.cfg_supervisor or self.cfg_send_if_not_supervisor:
      message="" if len(current_alerted) == 0 else "Missing/lost processing nodes: {}".format(list(current_alerted.keys()))
      # for this plugin only ALERTS should be used in UI/BE
      payload = self._create_payload(
        current_server=self.e2_addr,
        current_network=current_network,
        current_alerted=current_alerted,
        message=message,
        status=message,
        current_ranking=current_ranking,
        current_new=current_new,
        is_supervisor=is_supervisor,
      )        

    if self.cfg_log_full_info:
      self.P("Full info:\n{}".format(self.json.dumps(current_nodes, indent=4)))

    if self.cfg_log_info:
      self.P("Anomaly: {}, IsAlert: {}, IsNewAlert: {}, Alerted: {}, Ranking: {}".format(
        is_anomaly, self.alerter_is_alert(), self.alerter_is_new_alert(),
        list(alerted_nodes.keys()), str_ranking
      ))
      self.P("Alerter status: {}".format(self.get_alerter_status()))
    if self.alerter_is_new_alert():
      self.P("NetMon anomaly:\n********************\nAnomaly reported for {} nodes:\n{}\n ********************".format(
        len(alerted_nodes), self.json_dumps(alerted_nodes, indent=2)
      ))
    #endif show alerts
    return payload
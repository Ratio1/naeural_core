
# TODO: move all string to naeural_client
class NMonConst:
  NMON_CMD_HISTORY = 'history'
  NMON_CMD_LAST_CONFIG = 'last_config'
  NMON_CMD_E2 = 'e2'
  NMON_CMD_REQUEST = 'request'
  NMON_RES_CURRENT_SERVER = 'current_server'
  NMON_RES_E2_TARGET_ID = 'e2_target_id'
  NMON_RES_E2_TARGET_ADDR = 'e2_target_addr'
  NMON_RES_NODE_HISTORY = 'node_history'
  NMON_RES_PIPELINE_INFO = 'e2_pipelines'

class _NetworkMonitorMixin:
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.__history = self.deque(maxlen=180)
    self.__active_nodes = set()
    self.__lost_nodes = self.defaultdict(int)
    return
  
  
  @property
  def active_nodes(self):
    return self.__active_nodes
  
  @property
  def all_nodes(self):
    return self.netmon.all_nodes

  def _convert_dct_from_addr_to_eeid_index(self, dct_addr_nodes):
    dct_eeid_lst_addrs = {}
    
    for addr in dct_addr_nodes:
      eeid = self.netmon.network_node_eeid(addr=addr)
      if eeid not in dct_eeid_lst_addrs:
        dct_eeid_lst_addrs[eeid] = []
      dct_eeid_lst_addrs[eeid].append(dct_addr_nodes[addr])
    
    dct_eeid_nodes = {}
    for eeid in dct_eeid_lst_addrs:
      latest_info = sorted(dct_eeid_lst_addrs[eeid], key=lambda x:x['last_seen_sec'])[0]
      dct_eeid_nodes[eeid] = latest_info

    return dct_eeid_nodes
  
  def _add_to_history(self):
    """
    TODO: WARNING: The dict should NOT be indexed via the eeid but rather using the address
      a bad actor could use a existing eeid to overwrite the data of another node
        
    """    
    nodes = self.netmon.network_nodes_status()
    new_nodes = []
    new_nodes_addr = []
    self.__history.append(nodes) 

    eeid_nodes = self._convert_dct_from_addr_to_eeid_index(nodes)
    current_nodes = list(eeid_nodes.keys())

    for eeid in current_nodes:
      addr = eeid_nodes[eeid]['address']
      if addr not in self.__active_nodes:
        if eeid_nodes[eeid]['last_seen_sec'] < self.cfg_supervisor_alert_time:
          if self.cfg_log_info:
            eeid = self.netmon.network_node_eeid(addr=eeid)
            self.P("New node {} ({}):\n{}".format(eeid, eeid, self.json.dumps(nodes[eeid], indent=4)))
          new_nodes.append(eeid)
          new_nodes_addr.append(addr)
    self.__active_nodes.update(new_nodes_addr)

    return eeid_nodes, new_nodes
  
  
  def _supervisor_check(self):
    is_alert, nodes = False, {}
    for addr in list(self.__active_nodes):
      last_seen_ago = self.netmon.network_node_last_seen(addr=addr, as_sec=True)
      if last_seen_ago > self.cfg_supervisor_alert_time:
        if self.cfg_log_info:
          hb = self.netmon.network_node_last_heartbeat(addr=addr)              
          hb_t = hb.get(self.const.HB.CURRENT_TIME)
          ts = hb[self.const.PAYLOAD_DATA.EE_TIMESTAMP]
          tz = hb.get(self.const.PAYLOAD_DATA.EE_TIMEZONE)
          dt_local = self.log.utc_to_local(ts, tz, fmt=self.const.HB.TIMESTAMP_FORMAT)
          dt_now = self.datetime.now()
          
          elapsed = dt_now.timestamp() - dt_local.timestamp()
          
          eeid = self.netmon.network_node_eeid(addr=addr)
          self.P("Found issue with {} ({}):\n\nLast seen: {}\nStatus: {}\nHB: {}\n\n".format(
            addr, eeid, last_seen_ago, self.json.dumps(self.__history[-1][addr], indent=4),
            self.json.dumps(dict(hb_t=hb_t, ts=ts, tz=tz, elapsed=elapsed),indent=4)
          ))
        #endif debug
        uptime_sec = self.netmon.network_node_uptime(addr=addr, as_str=True)
        is_alert = True
        eeid = self.netmon.network_node_eeid(addr=addr)
        nodes[eeid] = {
            'last_seen_sec' : round(last_seen_ago, 1),
            'uptime_sec' : uptime_sec,
        }
        self.__lost_nodes[addr] += 1
      else:
        if addr in self.__lost_nodes:
          del self.__lost_nodes[addr]
      #endif is active or not
    #endfor check if any "active" is not active anymore

    for lost_addr in self.__lost_nodes:
      if self.__lost_nodes[lost_addr] > 10 and lost_addr in self.__active_nodes:
        self.__active_nodes.remove(lost_addr)
        self.P("Removed '{}' from active nodes after {} fails. Ongoing issues: {}".format(
          lost_addr, self.__lost_nodes[lost_addr], {k:v for k,v in self.__lost_nodes.items()},
        ))
      #endif
    #endfor clean lost nodes after a while
    return is_alert, nodes    
    
  
  def _get_rankings(self):
    nodes = self.__history[-1]
    if self.cfg_exclude_self:
      nodes = {k:v for k,v in nodes.items() if k != self.e2_addr}
    # TODO: change to addresses later
    eeid_nodes = self._convert_dct_from_addr_to_eeid_index(nodes)
    ranking = [(eeid, eeid_nodes[eeid]['SCORE'], eeid_nodes[eeid]['last_seen_sec']) for eeid in eeid_nodes]
    ranking = sorted(ranking, key=lambda x:x[1], reverse=True)
    return ranking  
  
  
  def _exec_netmon_request(self, target_addr, request_type, request_options={}, data={}):
    if not isinstance(request_options, dict):
      request_options = {}
    payload_params = {}
    elapsed_t = 0
    if target_addr not in self.all_nodes:
      self.P("Received `{}` request for missing node '{}'".format(
        request_type, target_addr), color='r'
      )
      return
    
    eeid = self.netmon.network_node_eeid(addr=target_addr)
    
    if request_type == NMonConst.NMON_CMD_HISTORY:
      step = request_options.get('step', 20)
      time_window_hours = request_options.get('time_window_hours', 24)
      if not isinstance(time_window_hours, int):
        time_window_hours = 24
      if not isinstance(step, int):
        step = 4
      minutes = time_window_hours * 60
      self.P("Received edge node history request for {}, step={}, hours={}".format(
        target_addr, step, minutes // 60
      ))
      start_t = self.time()
      info = self.netmon.network_node_history(
        addr=target_addr, hb_step=step, minutes=minutes,
        reverse_order=False
      )
      elapsed_t = round(self.time() - start_t, 5)
      payload_params[NMonConst.NMON_RES_NODE_HISTORY] = info
      
    elif request_type == NMonConst.NMON_CMD_LAST_CONFIG:
      self.P("Received edge node status request for '{}'".format(target_addr))
      info = self.netmon.network_node_pipelines(addr=target_addr)    
      payload_params[NMonConst.NMON_RES_PIPELINE_INFO] = info
      
    else:
      self.P("Network monitor on `{}` received invalid request type `{}` for target:address <{}:{}>".format(
        self.e2_addr, request_type, target_addr, eeid
        ), color='r'
      )
      return
    # construct the payload
    payload_params[NMonConst.NMON_CMD_REQUEST] = request_type
    payload_params[NMonConst.NMON_RES_CURRENT_SERVER] = self.e2_addr
    payload_params[NMonConst.NMON_RES_E2_TARGET_ADDR] = target_addr 
    payload_params[NMonConst.NMON_RES_E2_TARGET_ID] = eeid
    self.P("  Network monitor sending <{}> response to <{}>".format(request_type, target_addr))
    self.add_payload_by_fields(
      call_history_time=elapsed_t,
      command_params=data,
      **payload_params,
    )
    return

"""
{
  "NAME" : "peer_config_pipeline",
  "TYPE" : "NetworkListener",
  
  "PATH_FILTER" : [
      null, null, 
      ["UPDATE_MONITOR_01", "NET_MON_01"],
      null
    ],
  "MESSAGE_FILTER" : {},
  
  "PLUGINS" : [
    {
      "SIGNATURE" : "NET_CONFIG_MONITOR",
      "INSTANCES" : [
        {
          "INSTANCE_ID" : "DEFAULT"
        }
      ]
    }
  ]
}


"""
from naeural_core.business.base.network_processor import NetworkProcessorPlugin as BasePlugin


__VER__ = '1.1.0'

_CONFIG = {
  
  **BasePlugin.CONFIG,
  'ALLOW_EMPTY_INPUTS' : True,
  'PLUGIN_LOOP_RESOLUTION' : 50, # we force this to be 50 Hz from the standard 20 Hz  
  'MAX_INPUTS_QUEUE_SIZE' : 128, # increase the queue size to 128 from std 1
  
  'VERBOSE_NETCONFIG_LOGS' : True,
  
  'PROCESS_DELAY' : 0,
  
  'SEND_EACH' : 10, # runs the send logc every 10 seconds
  
  'REQUEST_CONFIGS_EACH' : 30, # minimum time between requests to the same node
  
  'SHOW_EACH' : 60,
  
  'DEBUG_NETMON_COUNT' : 10,
  
  'VALIDATION_RULES' : {
    **BasePlugin.CONFIG['VALIDATION_RULES'],
  },
}

class NetConfigMonitorPlugin(BasePlugin):
  
  
  def on_init(self):
    self.P("Network peer config watch demo initializing...")
    self.__last_data_time = 0
    self.__new_nodes_this_iter = 0
    self.__last_shown = 0
    self.__recvs = self.defaultdict(int)
    self.__allowed_nodes = {} # contains addresses with no prefixes
    self.__debug_netmon_count = self.cfg_debug_netmon_count
    return
  
  
  def __check_dct_metadata(self):
    stream_metadata = self.dataapi_stream_metadata()
    if stream_metadata is not None:
      if self.cfg_verbose_netconfig_logs:
        self.P(f"Stream metadata:\n {self.json_dumps(stream_metadata, indent=2)}")
    return
  
  
  def __get_active_nodes(self, netmon_current_network : dict) -> dict:
    """
    Returns a dictionary with the active nodes in the network.
    """
    active_network = {
      v['address']: v 
      for k, v in netmon_current_network.items() 
      if v.get("working", False) == self.const.DEVICE_STATUS_ONLINE
    }    
    return active_network


  def __get_active_nodes_summary_with_peers(self, netmon_current_network: dict):
    """
    Looks in all whitelists and finds the nodes that is allowed by most other nodes.
    
    """
    node_coverage = {}
    
    active_network = self.__get_active_nodes(netmon_current_network)
    
    for addr in active_network:
      node_coverage[addr] = 0
    #endfor initialize node_coverage 
    
    whitelists = [x.get("whitelist", []) for x in active_network.values()]
    for whitelist in whitelists:
      for ee_addr in whitelist:
        if ee_addr not in active_network:
          continue # this address is not active in the network so we skip it
        if ee_addr not in node_coverage:
          node_coverage[ee_addr] = 0
        node_coverage[ee_addr] += 1
    coverage_list = [(k, v) for k, v in node_coverage.items()]
    coverage_list = sorted(coverage_list, key=lambda x: x[1], reverse=True)

    result = self.OrderedDict()
    my_addr = self.bc.maybe_remove_prefix(self.ee_addr)
    
    for i, (ee_addr, coverage) in enumerate(coverage_list):
      is_online = active_network.get(ee_addr, {}).get("working", False) == self.const.DEVICE_STATUS_ONLINE
      result[ee_addr] = {
        "peers" : coverage,
        "eeid" : active_network.get(ee_addr, {}).get("eeid", "UNKNOWN"),
        'ver'  : active_network.get(ee_addr, {}).get("version", "UNKNOWN"),
        'is_supervisor' : active_network.get(ee_addr, {}).get("is_supervisor", False),
        'allows_me' : my_addr in active_network.get(ee_addr, {}).get("whitelist", []),
        'online' : is_online,
        'whitelist' : active_network.get(ee_addr, {}).get("whitelist", []),
      }
    return result


  def __maybe_review_known(self):
    if ((self.time() - self.__last_shown) < self.cfg_show_each):
      return
    self.__last_shown = self.time()
    msg = "Known nodes: "
    if len(self.__allowed_nodes) == 0:
      msg += "\n=== No allowed nodes to show ==="
    else:
      for addr in self.__allowed_nodes:
        eeid = self.netmon.network_node_eeid(addr)
        pipelines = self.__allowed_nodes[addr].get("pipelines", [])
        names = [p.get("NAME", "NONAME") for p in pipelines]
        msg += f"\n  - '{eeid}' <{addr}> has {len(pipelines)} pipelines: {names}"
      #endfor __allowed_nodes    
    self.P(msg)
    return
  
  
  def __send_get_cfg(self, node_addr):
    node_ee_id = self.netmon.network_node_eeid(node_addr)
    if self.cfg_verbose_netconfig_logs:
      self.P(f"Sending GET_PIPELINES to <{node_addr}> '{node_ee_id}'...")    
    data = {
      "OP" : "GET_CONFIG",
      "DEST" : node_addr,
    }
    self.add_payload_by_fields(
      net_config_data=data,
      ee_is_encrypted=True,    
      ee_destination=node_addr,  
    )
    return
  
  
  def __send_set_cfg(self, node_addr):
    my_pipelines = self.node_pipelines
    self.P(f"Sending {len(my_pipelines)} pipelines req by '{self.modified_by_id}' <{self.modified_by_addr}>...")
    data = {
      "OP" : "SET_CONFIG",
      "DEST" : node_addr,
      "DATA" : my_pipelines,
    }    
    self.add_payload_by_fields(
      net_config_data=data,
      ee_is_encrypted=True,  
      ee_destination=node_addr,    
    )
    return    


  def __maybe_send(self):
    if self.time() - self.__last_data_time > self.cfg_send_each:
      self.__last_data_time = self.time()
      if len(self.__allowed_nodes) == 0:
        if self.cfg_verbose_netconfig_logs:
          self.P("No allowed nodes to send requests to. Waiting for network data...")
      else:
        if self.cfg_verbose_netconfig_logs:
          self.P("Initiating pipeline requests to allowed nodes...")
        to_send = []
        for node_addr in self.__allowed_nodes:
          last_request = self.__allowed_nodes[node_addr].get("last_config_get", 0)
          if (self.time() - last_request) > self.cfg_request_configs_each and self.__allowed_nodes[node_addr]["is_online"]:
            to_send.append(node_addr)
          #endif enough time since last request of this node
        #endfor __allowed_nodes
        if len(to_send) == 0:
          if self.cfg_verbose_netconfig_logs:
            self.P("No nodes need update.")
        else:
          if self.cfg_verbose_netconfig_logs:
            self.P(f"Local {len(self.local_pipelines)} pipelines. Sending requests to {len(to_send)} nodes...")        
          # now send some requests
          for node_addr in to_send:
            self.__send_get_cfg(node_addr)
            self.__allowed_nodes[node_addr]["last_config_get"] = self.time()
          #endfor to_send
        #endif len(to_send) == 0
      #endif have allowed nodes
    #endif time to send
    return
  

  
  def on_payload_net_config_monitor(self, payload: dict):
    receiver = payload.get(self.const.PAYLOAD_DATA.EE_DESTINATION, None)
    
    if receiver != self.ee_addr:
      if self.cfg_verbose_netconfig_logs:
        self.P(f"Received payload for '{receiver}' but I am '{self.ee_addr}'. Ignoring.", color='r')
      return
    
    sender = payload.get(self.const.PAYLOAD_DATA.EE_SENDER, None)
    sender_no_prefix = self.bc.maybe_remove_prefix(sender)
    sender_id = self.netmon.network_node_eeid(sender_no_prefix)
    is_encrypted = payload.get(self.const.PAYLOAD_DATA.EE_IS_ENCRYPTED, False)

    if is_encrypted:
      decrypted_data = self.check_payload_data(payload)
      if decrypted_data is not None:
        net_config_data = decrypted_data.get("NET_CONFIG_DATA", {})
        op = net_config_data.get("OP", "UNKNOWN")
        # now we can process the data based on the operation
        if op == "SET_CONFIG":
          if self.cfg_verbose_netconfig_logs:            
            self.P(f"Received SET_CONFIG data from '{sender_id}' <{sender}'.")
          received_pipelines = net_config_data.get("DATA", [])    
          # process in local cache
          self.__allowed_nodes[sender_no_prefix]["pipelines"] = received_pipelines
          # now we can add the pipelines to the netmon cache
          self.netmon.register_node_pipelines(addr=sender_no_prefix, pipelines=received_pipelines)
        #finished SET_CONFIG
        elif op == "GET_CONFIG":
          if self.cfg_verbose_netconfig_logs:
            self.P(f"Received GET_CONFIG data from '{sender_id}' <{sender}'.")
          self.__send_set_cfg(sender)
        #finished GET_CONFIG
        #endif ops
    else:
      self.P("Received unencrypted data. Dropping.", color='r')
    return  
  
  
  
  def on_payload_net_mon_01(self, data : dict):
    current_network = data.get("CURRENT_NETWORK", {})
    if len(current_network) == 0:
      self.P("Received NET_MON_01 data without CURRENT_NETWORK data.", color='r ')
    else:
      self.__new_nodes_this_iter = 0
      peers_status = self.__get_active_nodes_summary_with_peers(current_network)
      
      # mark all nodes that are not online
      non_online = {
        x.get("address"):x.get("eeid") for x in current_network.values() 
        if x.get("working", False) != self.const.DEVICE_STATUS_ONLINE
      }
      
      # mark all nodes that are not online
      for cached_addr in self.__allowed_nodes:
        if cached_addr in non_online and self.__allowed_nodes[cached_addr]["is_online"]:
          self.__allowed_nodes[cached_addr]["is_online"] = False
          self.P(f"Marking node '{non_online[cached_addr]}' <{cached_addr}> as offline.", color='r')
      # endfor marking non online nodes
      
      if self.__debug_netmon_count > 0:
        # self.P(f"NetMon debug:\n{self.json_dumps(self.__get_active_nodes(current_network), indent=2)}")
        self.P(f"Peers status:\n{self.json_dumps(peers_status, indent=2)}")
        self.__check_dct_metadata()
        self.__debug_netmon_count -= 1
      #endif debug initial iterations
      
      for addr in peers_status:
        if addr == self.ee_addr:
          # its us, no need to check whitelist
          continue
        if peers_status[addr]["allows_me"]:
          # we have found a whitelist that contains our address
          if addr not in self.__allowed_nodes:
            self.__allowed_nodes[addr] = {
              "whitelist" : peers_status[addr]["whitelist"],
              "last_config_get" : 0,
            } 
            self.__new_nodes_this_iter += 1
          #endif addr not in __allowed_nodes
          if not self.__allowed_nodes[addr].get("is_online", True):
            self.P("Node '{}' <{}> is back online.".format(peers_status[addr]["eeid"], addr))
          self.__allowed_nodes[addr]["is_online"] = True # by default we assume the node is online due to `__get_active_nodes_summary_with_peers`
        #endif addr allows me
      #endfor each addr in peers_status
      if self.__new_nodes_this_iter > 0:
        self.P(f"Found {self.__new_nodes_this_iter} new peered nodes.")
    #endif len(current_network) == 0
    return    
  

  def process(self):
    payload = None
    self.__maybe_send()
    self.__maybe_review_known()  
    return payload
  

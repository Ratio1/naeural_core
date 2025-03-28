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
from naeural_core.business.base.network_processor import NetworkProcessorPlugin 


__VER__ = '1.1.0'

_CONFIG = {
  
  **NetworkProcessorPlugin.CONFIG,
  
  'ALLOW_EMPTY_INPUTS' : True,
  
  'PLUGIN_LOOP_RESOLUTION' : 50, # we force this to be 50 Hz from the standard 20 Hz  
  'MAX_INPUTS_QUEUE_SIZE' : 128, # increase the queue size to 128 from std 1
  

  # DEBUGGING
  "FULL_DEBUG_PAYLOADS" : False,     
  "VERBOSE_NETCONFIG_LOGS" : False, 
  # END DEBUGGING
  
  'PROCESS_DELAY' : 1,
  
  # each cfg_send_get_config_each seconds we will send requests to the nodes that allow 
  # us to get their pipelines and that have not been requested in the last 
  # cfg_node_request_configs_each seconds.
  'SEND_GET_CONFIG_EACH' : 600, # runs the send request logic every 10 minutes
  'NODE_REQUEST_CONFIGS_EACH' : 1200, # minimum time between requests to the same node
  
  # sent each cfg_send_to_allowed_each seconds the pipeline configuration to the allowed nodes
  'SEND_TO_ALLOWED_EACH' : 600, # runs the send to allowed nodes logic every 10 minutes
  
  
  'SHOW_EACH' : 60,
  
  'DEBUG_NETMON_COUNT' : 1,
  
  'VALIDATION_RULES' : {
    **NetworkProcessorPlugin.CONFIG['VALIDATION_RULES'],
  },
}

class NetConfigMonitorPlugin(NetworkProcessorPlugin):
  CONFIG = _CONFIG
  
  CT_PIPELINE = "PIPELINES"
  CT_PLG_STATUSES = "PLUGIN_STATUSES"
  
  
  def Pd(self, *args, **kwargs):
    """
    This function will print the debug messages.
    """
    if self.cfg_verbose_netconfig_logs:
      self.P(*args, **kwargs)
    return
  
  
  def on_init(self):   
    self.P("Network fleet peer configuration monitor initializing...")
    self.__last_data_time = 0
    self.__new_nodes_this_iter = 0
    self.__last_shown = 0
    self.__recvs = self.defaultdict(int)
    self.__initial_send = False
    self.__last_pipelines = None
    self.__allowed_nodes = {} # contains addresses with no prefixes
    self.__last_sent_to_allowed = 0
    self.__debug_netmon_count = self.cfg_debug_netmon_count
    self._get_active_plugins_instances = self.global_shmem.get("get_active_plugins_instances")
    if not callable(self._get_active_plugins_instances):
      self.P(" ERROR: `get_active_plugins_instances` not found!", color='r', boxed=True)
    return
  
  
  def __check_dct_metadata(self):
    """ This is a debug function that checks the metadata of the dataapi stream. """
    stream_metadata = self.dataapi_stream_metadata()
    if stream_metadata is not None:
      if self.cfg_verbose_netconfig_logs:
        self.P(f"Stream metadata:\n {self.json_dumps(stream_metadata, indent=2)}")
    return
  
  
  def __update_allowed_nodes(self, addr, pipelines):
    sender_no_prefix = self.bc.maybe_remove_prefix(addr)
    if sender_no_prefix not in self.__allowed_nodes:
      self.__allowed_nodes[sender_no_prefix] = {}
    #endif sender_no_prefix not in __allowed_nodes
    self.__allowed_nodes[sender_no_prefix]["pipelines"] = pipelines
    self.__allowed_nodes[sender_no_prefix]["last_config_get"] = self.time()
    self.__allowed_nodes[sender_no_prefix]["is_online"] = True
    return


  def __preprocess_current_network_data(self, current_network):
    for _, v in current_network.items():
      addr = v.get(self.const.PAYLOAD_DATA.NETMON_ADDRESS)
      if addr is None:
        continue
      v[self.const.PAYLOAD_DATA.NETMON_ADDRESS] = self.bc.maybe_remove_addr_prefix(addr)
    return current_network


  def __get_active_nodes(self, netmon_current_network : dict) -> dict:
    """
    Returns a dictionary with the active nodes in the network.
    """
    active_network = {
      v[self.const.PAYLOAD_DATA.NETMON_ADDRESS] : v 
      for k, v in netmon_current_network.items() 
      if v.get(self.const.PAYLOAD_DATA.NETMON_STATUS_KEY, False) == self.const.DEVICE_STATUS_ONLINE
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
    """
    This function will show the known nodes every `cfg_show_each` seconds.
    """
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
        me_msg = ""
        prefixed_addr = self.bc.maybe_add_prefix(addr)
        if prefixed_addr == self.ee_addr:
          pipelines = self.node_pipelines
          names = [p.get("NAME", "NONAME") for p in pipelines]
          me_msg = " (ME)"
        msg += f"\n  - '{eeid}' <{addr}>{me_msg} has {len(pipelines)} pipelines: {names}"
      #endfor __allowed_nodes    
    self.P(msg)
    return
  
  
  def __send_get_cfg(self, node_addr):
    """
    Sends a request to a node or a list of nodes to get their configuration.
    """
    if isinstance(node_addr, list):
      node_addr = [self.bc.maybe_add_prefix(x) for x in node_addr if self.netmon.network_node_is_online(x)]
      node_ee_id = [self.netmon.network_node_eeid(x) for x in node_addr]
    else:
      node_addr = self.bc.maybe_add_prefix(node_addr) # add prefix if not present otherwise the protocol will fail
      node_ee_id = self.netmon.network_node_eeid(node_addr)

    if self.cfg_verbose_netconfig_logs:
      self.P(f"Sending {self.const.NET_CONFIG.REQUEST_COMMAND} to '{node_ee_id}' <{node_addr}>...")    
    payload = {
      self.const.NET_CONFIG.NET_CONFIG_DATA : {
        self.const.NET_CONFIG.OPERATION   : self.const.NET_CONFIG.REQUEST_COMMAND,
        self.const.NET_CONFIG.DESTINATION : node_addr,
      },
    }
    self.send_encrypted_payload(node_addr=node_addr, **payload)
    return
  
  
  def __send_set_cfg(self, node_addr):
    if isinstance(node_addr, list):
      node_addr = [self.bc.maybe_add_prefix(x) for x in node_addr if self.netmon.network_node_is_online(x)]
      node_ee_id = [self.netmon.network_node_eeid(x) for x in node_addr]
    else:
      node_addr = self.bc.maybe_add_prefix(node_addr) # add prefix if not present otherwise the protocol will fail
      node_ee_id = self.netmon.network_node_eeid(node_addr)

    my_pipelines = self.node_pipelines
    
    if self.cfg_verbose_netconfig_logs:
      self.P(f"Sending {self.const.NET_CONFIG.STORE_COMMAND}:{len(my_pipelines)} to requester '{node_ee_id}' <{node_addr}>...")
      
    statuses = []
    if self._get_active_plugins_instances is not None and callable(self._get_active_plugins_instances):
      statuses = self._get_active_plugins_instances()
    payload = {
      self.const.NET_CONFIG.NET_CONFIG_DATA : {
        self.const.NET_CONFIG.OPERATION : self.const.NET_CONFIG.STORE_COMMAND,
        self.const.NET_CONFIG.DESTINATION : node_addr,
        self.CT_PIPELINE : my_pipelines,
        self.CT_PLG_STATUSES : statuses,
      }
    }
    self.send_encrypted_payload(
      node_addr=node_addr,
      **payload
    )
    return    


  def __maybe_send_requests(self):
    """
    This function will send requests to the nodes that allow us to get their pipelines.
    
    This function will run every `cfg_send_get_config_each` seconds and check if there is any node that
    allow current node to request their pipelines and that node has not been requested 
    in the last `cfg_node_request_configs_each` seconds.
    
    """
    if self.time() - self.__last_data_time > self.cfg_send_get_config_each:
      if len(self.__allowed_nodes) == 0:
        self.Pd("No allowed nodes to send requests to. Waiting for network data...")
        self.__last_data_time = self.time() - self.cfg_send_get_config_each + 10 # we force after 10 seconds to trigger
      else:
        self.__last_data_time = self.time()
        self.Pd(f"Initiating pipeline requests to {len(self.__allowed_nodes)} allowed nodes...")
        to_send = []
        for node_addr in self.__allowed_nodes:
          last_request = self.__allowed_nodes[node_addr].get("last_config_get", 0)
          if (self.time() - last_request) > self.cfg_node_request_configs_each and self.__allowed_nodes[node_addr]["is_online"]:
            to_send.append(node_addr)
          #endif enough time since last request of this node
        #endfor __allowed_nodes
        if len(to_send) == 0:
          self.Pd("No nodes need update.")
        else:
          self.Pd(f"Sending requests to {len(to_send)} nodes...")        
          # now send some requests
          self.__send_get_cfg(node_addr=to_send)
          for node_addr in to_send:
            self.__allowed_nodes[node_addr]["last_config_get"] = self.time()
          #endfor to_send
        #endif len(to_send) == 0
      #endif have allowed nodes
    #endif time to send
    return
  
  
  def __check_allowed_request(self, node_addr):
    allowed_list = self.bc.get_whitelist()
    node_addr = self.bc.maybe_remove_addr_prefix(node_addr)
    result = True
    if node_addr not in allowed_list:
      result = False
    return result
  
  
  def __maybe_send_configuration_to_allowed(self):
    """
    This function will send the configuration to all the allowed nodes when the configuration is updated or when the node starts.
    """
    allowed_list = self.bc.get_whitelist(with_prefix=True)
    must_distribute = False    
    if not self.__initial_send:
      self.P(f"Sending initial configuration to {len(allowed_list)} allowed nodes.")
      must_distribute = True
      self.__initial_send = True
      
    if not must_distribute and self.__last_pipelines != self.node_pipelines:
      self.P("Sending updated configuration to all allowed nodes.")
      must_distribute = True
      self.__last_pipelines = self.deepcopy(self.node_pipelines)
    
    if not must_distribute and (self.time() - self.__last_sent_to_allowed > self.cfg_send_to_allowed_each):
      self.P(f"Sending configuration to all allowed nodes at timeout={self.cfg_send_to_allowed_each}.")
      must_distribute = True      
      
    if must_distribute:
      if len(allowed_list) > 0:
        self.__send_set_cfg(node_addr=allowed_list)
      else:
        self.P("No allowed nodes to send configuration to although we have updated configuration.")
      self.__last_sent_to_allowed = self.time()      
    #endif must_distribute
    return
    
  

  @NetworkProcessorPlugin.payload_handler()
  def default_handler(self, payload: dict):
    sender = payload.get(self.const.PAYLOAD_DATA.EE_SENDER, None)
    receiver = payload.get(self.const.PAYLOAD_DATA.EE_DESTINATION, None)
    if not isinstance(receiver, list):
      receiver = [receiver]
    
    if self.ee_addr not in receiver:
      if self.cfg_verbose_netconfig_logs:
        payload_path = payload.get(self.const.PAYLOAD_DATA.EE_PAYLOAD_PATH, [None, None, None, None])
        is_encrypted = payload.get(self.const.PAYLOAD_DATA.EE_IS_ENCRYPTED, False)
        self.P("Received {}payload for <{}> but I am <{}>: {}'".format(
        "ENCRYPTED " if is_encrypted else "", receiver, self.ee_addr, payload_path), color='r'
        )
      return
    else:
      if self.cfg_verbose_netconfig_logs:
        self.P(f"Received request from <{sender}>")
    
    sender_no_prefix = self.bc.maybe_remove_prefix(sender)
    sender_id = self.netmon.network_node_eeid(sender_no_prefix)
    is_encrypted = payload.get(self.const.PAYLOAD_DATA.EE_IS_ENCRYPTED, False)

    if is_encrypted:
      decrypted_data = self.receive_and_decrypt_payload(data=payload)
      if decrypted_data is not None:
        net_config_data = decrypted_data.get(self.const.NET_CONFIG.NET_CONFIG_DATA, {})
        op = net_config_data.get(self.const.NET_CONFIG.OPERATION, "UNKNOWN")
        # now we can process the data based on the operation
        if op == self.const.NET_CONFIG.STORE_COMMAND:
          received_pipelines = net_config_data.get(self.CT_PIPELINE, [])    
          received_plugins_statuses = net_config_data.get(self.CT_PLG_STATUSES, [])
          if self.cfg_verbose_netconfig_logs:            
            self.P("Received {} data from '{}' <{}>'.\n  - Pipelines: {}\n  - Plugins: {}".format(
              self.const.NET_CONFIG.STORE_COMMAND, sender_id, sender, 
              len(received_pipelines), len(received_plugins_statuses)
            ))
          # process in local cache
          self.__update_allowed_nodes(sender_no_prefix, received_pipelines)
          # now we can add the pipelines to the netmon cache
          self.netmon.register_node_pipelines(
            addr=sender_no_prefix, pipelines=received_pipelines,
            plugins_statuses=received_plugins_statuses,
            verbose=self.cfg_verbose_netconfig_logs
          )
        #finished SET_CONFIG
        
        elif op == self.const.NET_CONFIG.REQUEST_COMMAND:
          self.P(f"Received {self.const.NET_CONFIG.REQUEST_COMMAND} data from '{sender_id}' <{sender}'.")
          ###
          ### At this point we can check if the sender is allowed to request our pipelines
          ### While this is handled naturally by the comms for the normal commands in this
          ### case we need to check the whitelist directly.
          ###
          if self.__check_allowed_request(sender_no_prefix):
            self.__send_set_cfg(sender)
          else:
            self.P(f"Node '{sender_id}' <{sender}> is not allowed to request my pipelines. This behavior should be recorded!", color='r')
        #finished GET_CONFIG
        #endif ops
    else:
      self.P("Received unencrypted data. Dropping.", color='r')
    return  
  
  
  @NetworkProcessorPlugin.payload_handler("NET_MON_01")
  def netmon_handler(self, data : dict):
    """
    This function will process the NET_MON_01 data and update the allowed nodes list.
    The objective is to keep track of the nodes that we are allowed to send requests to.
    """
    current_network = data.get(self.const.PAYLOAD_DATA.NETMON_CURRENT_NETWORK, {})
    if len(current_network) == 0:
      self.P(f"[netmon_handler] Received NET_MON_01 data without {self.const.PAYLOAD_DATA.NETMON_CURRENT_NETWORK}.", color='r ')
    else:
      # here we will remove the prefix from each "address" within the nodes info
      current_network = self.__preprocess_current_network_data(current_network)
      # from this point on we will work with the no-prefix addresses      
      self.__new_nodes_this_iter = 0
      peers_status = self.__get_active_nodes_summary_with_peers(current_network)
      
      # mark all nodes that are not online
      non_online = {
        x.get(self.const.PAYLOAD_DATA.NETMON_ADDRESS) : x.get(self.const.PAYLOAD_DATA.NETMON_EEID) for x in current_network.values() 
        if x.get(self.const.PAYLOAD_DATA.NETMON_STATUS_KEY, False) != self.const.DEVICE_STATUS_ONLINE
      }
      
      # mark all nodes that are not online
      for cached_addr in self.__allowed_nodes:
        if cached_addr in non_online and self.__allowed_nodes[cached_addr]["is_online"]:
          self.__allowed_nodes[cached_addr]["is_online"] = False
          self.P(f"[netmon_handler] Marking node '{non_online[cached_addr]}' <{cached_addr}> as offline.", color='r')
      # endfor marking non online nodes
      
      if self.__debug_netmon_count > 0:
        # self.P(f"NetMon debug:\n{self.json_dumps(self.__get_active_nodes(current_network), indent=2)}")
        self.P(f"[netmon_handler] peers status:\n{self.json_dumps(peers_status, indent=2)}")
        self.__check_dct_metadata()
        self.__debug_netmon_count -= 1
      #endif debug initial iterations
      
      for addr in peers_status:
        prefixed_addr = self.bc.maybe_add_prefix(addr)
        if prefixed_addr == self.ee_addr:
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
            self.P("[netmon_handler] Node '{}' <{}> is back online.".format(peers_status[addr]["eeid"], addr))
          self.__allowed_nodes[addr]["is_online"] = True # by default we assume the node is online due to `__get_active_nodes_summary_with_peers`
        #endif addr allows me
      #endfor each addr in peers_status
      if self.__new_nodes_this_iter > 0:
        self.P(f"[netmon_handler] Found {self.__new_nodes_this_iter} new peered nodes.")
    #endif len(current_network) == 0
    return    
  

  def process(self):
    payload = None
    self.__maybe_send_requests()
    self.__maybe_send_configuration_to_allowed()
    self.__maybe_review_known()  
    return payload
  

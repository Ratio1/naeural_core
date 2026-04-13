"""
TODO:


  - Solve the issue for set-contention in the chain storage when two nodes try to set the same key at the same time
    - (better) implement a lock mechanism for the chain storage when setting a key value that 
      will allow multiple nodes to compete for the set-operation and thus load-balance it
      OR
    - implement set-value moving TOKEN via the network
  

  - peer-to-peer managed:
    - node 1 registers a app_id X via a list of peers that includes node 1, node 2 and node 3
    - node 2 sets value by:
      - key
      - value 
      - app_id X
    - node 2 broadcasts the set opration to all app_id X peers (not all peers)
    - node 2 waits for confirmations from at least half of the app_id peers
    - if node 4 tries to set key in app_id X, it will be rejected
    
  


"""


from naeural_core.business.base.network_processor import NetworkProcessorPlugin

_CONFIG = {
  **NetworkProcessorPlugin.CONFIG,

  'PROCESS_DELAY' : 0,

  'MAX_INPUTS_QUEUE_SIZE' : 1024,

  'ALLOW_EMPTY_INPUTS' : True,
  "ACCEPT_SELF" : False,
  
  "FULL_DEBUG_PAYLOADS" : False,
  "CHAIN_STORE_DEBUG" : False, # main debug flag
  
  
  "MIN_CONFIRMATIONS" : 1,
  
  "CHAIN_PEERS_REFRESH_INTERVAL" : 60,

  'VALIDATION_RULES' : { 
    **NetworkProcessorPlugin.CONFIG['VALIDATION_RULES'],
  },  
}

__VER__ = '0.8.1'

class ChainStoreBasePlugin(NetworkProcessorPlugin):
  CONFIG = _CONFIG
  
  CS_STORE = "SSTORE"
  CS_CONFIRM = "SCONFIRM"
  CS_DATA = "CHAIN_STORE_DATA"
  CS_PEERS = "PEERS"
  CS_INCLUDE_DEFAULT_PEERS = "INCLUDE_DEFAULT_PEERS"
  CS_TIMEOUT = "TIMEOUT"
  CS_MAX_RETRIES = "MAX_RETRIES"

  CS_CONFIRM_BY = "confirm_by"
  CS_CONFIRM_BY_ADDR = "confirm_by_addr"
  CS_CONFIRMATIONS = "confirms"
  CS_MIN_CONFIRMATIONS = "min_confirms"
  CS_OP = "op"
  CS_KEY = "key"
  CS_VALUE = "value"
  CS_OWNER = "owner"
  CS_READONLY = "readonly"
  CS_TOKEN = "token"
  CS_STORAGE_MEM = "__chain_storage" # shared memory key
  CS_GETTER = "__chain_storage_get"
  CS_SETTER = "__chain_storage_set"
  CS_HSYNC = "__chain_storage_hsync"
  CS_HSYNC_REQ = "SHSYNC_REQ"
  CS_HSYNC_RESP = "SHSYNC_RESP"
  CS_REQUEST_ID = "request_id"
  CS_HKEY = "hkey"
  CS_SNAPSHOT = "snapshot"
  CS_RESPONSE = "response"
  CS_SENDER = "sender"
  
  
  
  
  def on_init(self):
    super().on_init() # not mandatory anymore?
    
    self.P(" === ChainStoreBasicPlugin INIT")
    
    self.__chainstore_identity = "CS_MSG_{}".format(self.uuid(7))
    
    self.__ops = self.deque()
    
    try:
      self.__chain_storage = self.cacheapi_load_pickle(default={}, verbose=True)
    except Exception as e:
      self.P(f" === Chain storage could not be loaded: {e}")
      self.__chain_storage = {}
      
    memory = self.plugins_shmem
      

    ## DEBUG ONLY:
    if self.CS_STORAGE_MEM in memory:
      self.P(" === Chain storage already exists", color="r")
      self.__chain_storage = memory[self.CS_STORAGE_MEM]
    ## END DEBUG ONLY
    
    memory[self.CS_STORAGE_MEM] = self.__chain_storage
    memory[self.CS_GETTER] = self._get_value
    memory[self.CS_SETTER] = self._set_value
    memory[self.CS_HSYNC] = self._hsync
    
    self.__last_chain_peers_refresh = 0
    self.__chain_peers = []
    self.__pending_hsync = {}
    self.__maybe_refresh_chain_peers()
    return
  
  
  def __debug_dump_chain_storage(self):
    if self.cfg_chain_store_debug:
      self.P(" === Chain storage dump:\n{}".format(self.json_dumps(self.__chain_storage, indent=2)))
    return
   
  
  
  
  def __maybe_refresh_chain_peers(self):
    """
    This method refreshes the chain peers list from the network using the whitelist generated
    by the blockchain engine. This means it will allow broadcasting the local keys to all the
    oracle nodes in the network as well as the manually added nodes. This pretty much covers 
    the "private" part of the ChainStore.
    
    However the chain storage should be also accessible to all the nodes in the network so that 
    they can ALL the values stored in the chain storage publicly
        
    """
    if (self.time() - self.__last_chain_peers_refresh) > self.cfg_chain_peers_refresh_interval:
      _chain_peers = self.bc.get_whitelist(with_prefix=True)
      # now check and preserve only online peers
      self.__chain_peers = [
        peer for peer in _chain_peers if self.netmon.network_node_is_online(peer)
      ]
      self.__last_chain_peers_refresh = self.time()
    return
  
  
  def __normalize_peer_list(self, peers):
    """
    Normalize peer selectors into a de-duplicated address list.

    Parameters
    ----------
    peers : str or list or any
      Peer selector passed by higher-level chain-store APIs. Strings are
      promoted to one-element lists. Non-list, non-string values are treated
      as empty input.

    Returns
    -------
    list of str
      De-duplicated peer addresses with empty values and the local node
      address removed.
    """
    if isinstance(peers, str):
      peers = [peers]
    elif not isinstance(peers, list):
      peers = []
    result = []
    for peer in peers:
      if not isinstance(peer, str) or len(peer) == 0:
        continue
      if peer == self.ee_addr or peer in result:
        continue
      result.append(peer)
    return result


  def __get_target_peers(self, peers=None, include_default_peers=True):
    """
    Compute the effective peer list for a chain-store write.

    Parameters
    ----------
    peers : str or list, optional
      Explicit peer addresses requested for the write.
    include_default_peers : bool, optional
      If True, start from the refreshed default chain peer set and append any
      explicit peers not already present. If False, only explicit peers are
      returned. Default is True.

    Returns
    -------
    list of str
      Effective peer list used for confirmation math and outbound sends.
    """
    send_to = self.deepcopy(self.__chain_peers) if include_default_peers else []
    for peer in self.__normalize_peer_list(peers):
      if peer not in send_to:
        send_to.append(peer)
    return send_to


  def __send_data_to_chain_peers(self, data, peers=None, include_default_peers=True):
    """Send chainstore payloads to the default peer set and optional extras.

    Parameters
    ----------
    data : dict
      Payload body forwarded to ``send_encrypted_payload``.
    peers : str or list, optional
      Extra explicit targets. When ``include_default_peers`` is ``False``,
      these become the only recipients.
    include_default_peers : bool, optional
      If ``True``, start from ``self.__chain_peers`` and append any extra
      targets that are not already present. If ``False``, send only to the
      explicit ``peers`` argument.

    Returns
    -------
    None
    """
    send_to = self.__get_target_peers(
      peers=peers,
      include_default_peers=include_default_peers,
    )
    self.send_encrypted_payload(node_addr=send_to, **data)
    return
  
  
  def __get_min_peer_confirmations(self, peers=None, include_default_peers=True):
    """
    Return the minimum confirmations required for the effective target set.

    Parameters
    ----------
    peers : str or list, optional
      Explicit peers requested for the write.
    include_default_peers : bool, optional
      If True, include the default chain peer set when deriving the effective
      target count. Default is True.

    Returns
    -------
    int
      Required confirmation count for the current write.

    Notes
    -----
    When ``cfg_min_confirmations`` is configured, that value takes precedence
    even if it exceeds the effective peer count. A warning is emitted in that
    case because the write may only fail later through timeout/retry
    exhaustion.
    """
    target_peers = self.__get_target_peers(
      peers=peers,
      include_default_peers=include_default_peers,
    )
    if self.cfg_min_confirmations is not None and self.cfg_min_confirmations > 0:
      if len(target_peers) > 0 and self.cfg_min_confirmations > len(target_peers):
        self.P(
          f" === Configured MIN_CONFIRMATIONS={self.cfg_min_confirmations} exceeds targeted peers={len(target_peers)}",
          color='y'
        )
      return self.cfg_min_confirmations    
    return len(target_peers) // 2 + 1
  
  
  def __save_chain_storage(self):
    self.cacheapi_save_pickle(self.__chain_storage, verbose=True)
    self.__last_chain_storage_save = self.time()
    return


  def __hset_index(self, hkey):
    """
    Compute the composed-key prefix for one hash namespace.

    Parameters
    ----------
    hkey : str
      Logical hash namespace requested by higher-level ``h*`` APIs.

    Returns
    -------
    str
      Prefix shared by every field stored under ``hkey``.
    """
    hkey_hash = self.get_hash(hkey, algorithm="sha256", length=10)
    return f"hs:{hkey_hash}:"


  def __export_hset_snapshot(self, hkey):
    """
    Export a stable snapshot for one hash namespace.

    Parameters
    ----------
    hkey : str
      Logical hash namespace to export from the local replica.

    Returns
    -------
    dict
      Deep-copied chain-store records keyed by their composed storage key.

    Notes
    -----
    The export freezes ``self.__chain_storage.items()`` into a list before
    filtering. That keeps snapshot generation stable even if another local
    write mutates the dictionary while the snapshot is being assembled.
    """
    index = self.__hset_index(hkey)
    snapshot = {}
    # Freeze the current dictionary view before filtering so live writes do not
    # mutate the iterator while this response is being serialized.
    for key, record in list(self.__chain_storage.items()):
      if not isinstance(key, str) or not key.startswith(index):
        continue
      if not isinstance(record, dict):
        continue
      snapshot[key] = self.deepcopy(record)
    return snapshot


  def __merge_hset_snapshot(self, hkey, snapshot):
    """
    Merge one peer-exported snapshot into the local replica.

    Parameters
    ----------
    hkey : str
      Logical hash namespace being refreshed.
    snapshot : dict
      Snapshot payload exported by a peer for ``hkey``.

    Returns
    -------
    int
      Number of local fields that were inserted or overwritten from the peer
      snapshot.

    Notes
    -----
    The merge is additive only. Remote fields that overlap the local replica
    overwrite stale values, while fields absent from the remote snapshot are
    preserved locally and are never pruned by this method.
    """
    if not isinstance(snapshot, dict):
      return 0

    index = self.__hset_index(hkey)
    merged_fields = 0
    changed = False
    for key, record in snapshot.items():
      if not isinstance(key, str) or not key.startswith(index):
        continue
      if not isinstance(record, dict):
        continue
      if self.CS_OWNER not in record or self.CS_VALUE not in record:
        continue

      value = self.deepcopy(record.get(self.CS_VALUE))
      if value is None:
        continue

      readonly = record.get(self.CS_READONLY, False)
      token = record.get(self.CS_TOKEN, None)
      local_record = self.__chain_storage.get(key)
      if isinstance(local_record, dict):
        same_owner = local_record.get(self.CS_OWNER) == record.get(self.CS_OWNER)
        same_value = local_record.get(self.CS_VALUE) == value
        same_readonly = local_record.get(self.CS_READONLY, False) == readonly
        same_token = local_record.get(self.CS_TOKEN, None) == token
        if same_owner and same_value and same_readonly and same_token:
          continue

      # Apply the peer record only to the local replica. HSync intentionally
      # does not enqueue a broadcast because this node is catching up to peer
      # state rather than originating a new write.
      self.__chain_storage[key] = {
        self.CS_KEY: key,
        self.CS_VALUE: value,
        self.CS_OWNER: record.get(self.CS_OWNER),
        self.CS_READONLY: readonly,
        self.CS_TOKEN: token,
        self.CS_CONFIRMATIONS: -1,
        self.CS_MIN_CONFIRMATIONS: 0,
      }
      merged_fields += 1
      changed = True

    if changed:
      self.__save_chain_storage()
    return merged_fields
  
  ## START setter-getter methods

  def __get_key_value(self, key):
    return self.__chain_storage.get(key, {}).get(self.CS_VALUE, None)


  def __get_key_owner(self, key):
    return self.__chain_storage.get(key, {}).get(self.CS_OWNER, None)
  
  
  def __get_key_readonly(self, key):
    return self.__chain_storage.get(key, {}).get(self.CS_READONLY, False)


  def __get_key_token(self, key):
    return self.__chain_storage.get(key, {}).get(self.CS_TOKEN, None)


  def __get_key_confirmations(self, key):
    return self.__chain_storage.get(key, {}).get(self.CS_CONFIRMATIONS, 0)

  
  def __get_key_min_confirmations(self, key):
    return self.__chain_storage.get(key, {}).get(self.CS_MIN_CONFIRMATIONS, 0)


  def __reset_confirmations(self, key, peers=None, include_default_peers=True):
    """
    Reset confirmation counters for a stored key.

    Parameters
    ----------
    key : str
      Chain-store key whose counters are reset.
    peers : str or list, optional
      Explicit peers requested for the write associated with ``key``.
    include_default_peers : bool, optional
      If True, include the default chain peer set when deriving the minimum
      confirmation count. Default is True.

    Returns
    -------
    None
    """
    self.__chain_storage[key][self.CS_CONFIRMATIONS] = 0
    self.__chain_storage[key][self.CS_MIN_CONFIRMATIONS] = self.__get_min_peer_confirmations(
      peers=peers,
      include_default_peers=include_default_peers,
    )
    return


  def __increment_confirmations(self, key):
    self.__chain_storage[key][self.CS_CONFIRMATIONS] += 1
    return
  
  def __set_confirmations(self, key, confirmations):
    self.__chain_storage[key][self.CS_CONFIRMATIONS] = confirmations
    return


  def __set_key_value(
    self,
    key,
    value,
    owner,
    readonly=False,
    token=None,
    local_sync_storage_op=False,
    peers=None,
    include_default_peers=True,
  ):
    """
    Set a key-value pair in the local chain-store replica.

    This method is called to set a key-value pair in the chain storage.

    Parameters
    ----------
    key : str
      The key to set the value for.
    value : any
      The value to set. ``None`` deletes the key locally.
    owner : str
      The owner of the key-value pair.
    readonly : bool
      If True the key-value pair will be read-only and cannot be overwritten
      by other owners.
    token : any, optional
      Token associated with the key. If the token is not None, any read/write
      operations must provide the same token.
    local_sync_storage_op : bool
      If True, only set the local key-value pair without broadcasting to the
      network. This operation is used for remote sync when a node receives a
      set operation from the network and needs to set the value in the local
      chain storage replica.
    peers : str or list, optional
      Explicit peer targets associated with the write. These are used only for
      confirmation bookkeeping on local-originated writes.
    include_default_peers : bool, optional
      If True, include the default chain peer set when deriving confirmation
      thresholds. Default is True.

    Returns
    -------
    None

    Notes
    -----
    When ``value`` is ``None``, the key is deleted locally. The existing
    delete semantics and related trade-offs described below are preserved.
    """
    # key should be composed of the chainstore app identity and the actual key
    # so if two chainstore apps are running on the same node, they will not overwrite each other
    # also this way we can implement chaistore app allow-listing
    chain_key = key
    if value is None:
      # Delete the key from chain storage when value is None.
      # TRADE-OFF: This piggybacks on the existing CS_STORE operation (broadcast with value=None)
      # rather than using a dedicated CS_DELETE operation. As a result, the originator does NOT
      # wait for peer confirmations on deletes — the confirmation wait loop in _set_value exits
      # immediately (0 >= 0) because the key is already gone from local storage. The broadcast
      # still happens asynchronously via __maybe_broadcast(), so peers DO receive and process the
      # delete. If confirmed-deletes are needed in the future, introduce a dedicated CS_DELETE
      # operation with its own confirmation tracking (e.g. a __pending_deletes dict).
      self.__chain_storage.pop(chain_key, None)
      self.__save_chain_storage()
      return
    self.__chain_storage[chain_key] = {
      self.CS_KEY       : key,
      self.CS_VALUE     : value,
      self.CS_OWNER     : owner,
      self.CS_READONLY  : readonly,
      self.CS_TOKEN     : token,
    }
    self.__reset_confirmations(
      key,
      peers=peers,
      include_default_peers=include_default_peers,
    )
    if local_sync_storage_op:
      # set the confirmations to -1 to indicate that the key is remote synced on this node
      self.__set_confirmations(key, -1) # set to -1 to indicate that the key is remote synced on this node
    self.__save_chain_storage()
    return


  def _set_value(
    self, 
    key, 
    value, 
    owner=None, 
    readonly=False,
    token=None,
    local_sync_storage_op=False, 
    peers=None,
    include_default_peers=True,
    timeout=None,
    max_retries=None,
    debug=False, 
  ):
    """
    Set a value in chain storage and optionally wait for confirmations.

    This method is called to set a value in the chain storage. If called
    locally it pushes a broadcast request to the network, while if called from
    the network it updates only the local chain-store replica.

    Parameters
    ----------
    key : str
      The key to set the value for.
    value : any
      The value to set.
    owner : str, optional
      The owner of the key-value pair. If None, the current instance path is
      used.
    readonly : bool
      If True the key-value pair will be read-only and cannot be overwritten
      by other owners.
    token : any, optional
      Token associated with the set operation. If the token is not None, any
      read/write operations must use the same token.
    local_sync_storage_op : bool
      If True, update only the local key-value pair without broadcasting to
      the network.
    peers : str or list, optional
      Explicit peers to target for this write.
    include_default_peers : bool
      If True, also target the chain-store default peer set.
    timeout : float, optional
      Per-attempt confirmation wait timeout in seconds. If None, use the
      built-in default.
    max_retries : int, optional
      Maximum number of timeout-triggered rebroadcast retries. If None, use
      the built-in default.
    debug : bool, optional
      If True, print debug messages. Default is False.

    Returns
    -------
    bool
      True if the desired state was stored successfully or was already present.
      False if the write was rejected or timed out after the allowed retries.

    Raises
    ------
    ValueError
      If ``key`` is invalid, if ``timeout`` or ``max_retries`` fail
      validation, or if the underlying storage/update flow raises an error.

    Notes
    -----
    This method blocks the caller thread while waiting for confirmations on
    local-originated writes.
    """
    if not isinstance(key, str) or len(key) == 0:
      raise ValueError("Key must be a non-empty string.")
    try:
      where = "FROM_LOCAL: " if not local_sync_storage_op else "FROM_REMOTE: "
      debug = debug or self.cfg_chain_store_debug
      debug_val = str(value)[:20] + "..." if len(str(value)) > 20 else str(value)
      if owner is None:
        owner = self.get_instance_path()
      if timeout is not None and (
        isinstance(timeout, bool) or
        not isinstance(timeout, (int, float)) or
        timeout <= 0
      ):
        raise ValueError("timeout must be a positive number or None.")
      if max_retries is not None and (
        isinstance(max_retries, bool) or
        not isinstance(max_retries, int) or
        max_retries < 0
      ):
        raise ValueError("max_retries must be a non-negative integer or None.")
      if timeout is None:
        timeout = 10
      if max_retries is None:
        max_retries = 2
      target_peers = self.__get_target_peers(
        peers=peers,
        include_default_peers=include_default_peers,
      )
      if not local_sync_storage_op and len(target_peers) == 0:
        self.P(
          f" === No target peers resolved for chain-store write on key {key}",
          color='y'
        )
        return False
      need_store = True
      existing_owner = None
      existing_value = None
      if key in self.__chain_storage:
        existing_value = self.__get_key_value(key)
        existing_owner = self.__get_key_owner(key)
        is_readonly = self.__get_key_readonly(key)
        existing_token = self.__get_key_token(key)
        if token != existing_token:
          if debug:
            self.P(f" === Key {key} has a different token {existing_token} from {existing_owner} than the one provided {token} from {owner}", color='r')
          need_store = False
        elif value is None:
          # Always allow delete (set to None) regardless of current value
          if debug:
            self.P(f" === Key {key} will be deleted (value=None)")
          need_store = True
        elif existing_value == value:
          # Value already matches — no need to re-store or broadcast, but this is
          # a success (the desired state is already achieved), not a failure.
          if debug:
            self.P(f" === Key {key} stored by {existing_owner} has the same value")
          return True
        elif is_readonly and existing_owner != owner:
          if debug:
            self.P(f" === Key {key} readonly by {existing_owner} (requester: {owner})", color='r')
          need_store = False
      # end if key in chain storage
      if need_store:
        if debug:
          if value is None:
            action_str = "deleting"
          elif existing_owner not in [None, owner]:
            action_str = "overwriting"
          else:
            action_str = "setting"
          self.P(f" === {where}{action_str} <{key}> = <{debug_val}> by {owner} (orig: {existing_owner}), is_remote={local_sync_storage_op}")
        self.__set_key_value(
          key=key, value=value, owner=owner,
          local_sync_storage_op=local_sync_storage_op,
          readonly=readonly, token=token,
          peers=peers,
          include_default_peers=include_default_peers,
        )
        if not local_sync_storage_op:
          # now send set-value (including confirmation request) to all
          op = {
              self.CS_OP        : self.CS_STORE,
              self.CS_KEY       : key,
              self.CS_VALUE     : value,
              self.CS_OWNER     : owner,
              self.CS_TOKEN     : token,
              self.CS_READONLY  : readonly, # if the key is readonly, it will not be overwritten by other owners
              self.CS_PEERS     : peers,
              self.CS_INCLUDE_DEFAULT_PEERS : include_default_peers,
              self.CS_TIMEOUT   : timeout,
              self.CS_MAX_RETRIES : max_retries,
          }
          self.__ops.append(op)
          if debug:
            self.P(f" === {where} key {key} locally stored for {owner}. Now waiting for confirmations...")
          # at this point we can wait until we have enough confirmations
          _timeout = self.time() + timeout
          _done = False
          _prev_confirm = 0
          _retries = 0
          while not _done: # this LOCKS the calling thread set_value
            recv_confirm = self.__get_key_confirmations(key)
            if recv_confirm > _prev_confirm:
              _prev_confirm = recv_confirm
              if debug:
                self.P(f" === {where}Key received '{key}' has {recv_confirm} confirmations")
            if recv_confirm >= self.__get_key_min_confirmations(key):
              if debug:
                self.P(f" === {where}KEY CONFIRMED '{key}': has enough ({recv_confirm}) confirmations")
              _done = True
              need_store = True
              continue
            elif self.time() > _timeout:
              if debug:
                self.P(f" === {where}Key '{key}' has not enough confirmations after timeout. [Q:{self.input_queue_size}/{self.cfg_max_inputs_queue_size}]", color='r')
              _retries += 1
              if _retries > max_retries:
                if debug:
                  self.P(f" === {where}Key '{key}' has not enough confirmations after {max_retries} retries", color='r')
                _done = True
                need_store = False
              else:
                if debug:
                  self.P(f" === {where}Retrying key '{key}' with timeout...", color='r')
                self.__ops.append(op)
                _timeout = self.time() + timeout
              # end if retries
            # end if timeout
            self.sleep(0.100)  # sleep for 100ms to give protocol sync time
          # end while not done
        else:
          if debug:
            self.P(f" === {where}{key} locally sync-stored for remote {owner}")
        # end if not sync_storage
      # end if need_store
    except Exception as e:
      err_msg = f" === Error in _set_value for key {key}[Q:{self.input_queue_size}/{self.cfg_max_inputs_queue_size}]: {e}\n{self.trace_info()}"
      raise ValueError(err_msg)
    return need_store


  def _hsync(
    self,
    hkey,
    peers=None,
    include_default_peers=True,
    timeout=None,
    debug=False,
  ):
    """
    Request one live snapshot for a hash namespace and merge it locally.

    Parameters
    ----------
    hkey : str
      Logical hash namespace to refresh.
    peers : str or list, optional
      Explicit peer addresses to target for this refresh. Default is None.
    include_default_peers : bool, optional
      If True, also target the runtime default chain-peer set. If False, only
      the explicit ``peers`` list is used. Default is True.
    timeout : float, optional
      Maximum wait time in seconds for one valid peer snapshot. If None, use
      the built-in default. Default is None.
    debug : bool, optional
      If True, print detailed routing and timing logs. Default is False.

    Returns
    -------
    dict
      Result envelope with ``hkey``, the accepted ``source_peer``, and the
      number of ``merged_fields`` applied locally.

    Raises
    ------
    ValueError
      If ``hkey`` or ``timeout`` is invalid, if no target peer can be
      resolved, or if no valid peer snapshot is accepted before timeout.

    Notes
    -----
    A valid peer response with an empty snapshot is a successful cold-state
    sync. Timeout means that no valid peer response was accepted at all.
    """
    debug = debug or self.cfg_chain_store_debug
    if not isinstance(hkey, str) or len(hkey) == 0:
      raise ValueError("hsync hkey must be a non-empty string.")
    if timeout is not None and (
      isinstance(timeout, bool) or
      not isinstance(timeout, (int, float)) or
      timeout <= 0
    ):
      raise ValueError("timeout must be a positive number or None.")
    if timeout is None:
      timeout = 10

    explicit_peers = self.__normalize_peer_list(peers)
    allowed_peers = self.__get_target_peers(
      peers=explicit_peers,
      include_default_peers=include_default_peers,
    )
    if len(allowed_peers) == 0:
      raise ValueError(f"No target peers resolved for hsync '{hkey}'.")

    request_id = self.uuid()
    self.__pending_hsync[request_id] = {
      self.CS_HKEY: hkey,
      self.CS_PEERS: allowed_peers,
      self.CS_RESPONSE: None,
    }

    try:
      if debug:
        self.P(
          f" === HSYNC request for {hkey} to peers {allowed_peers}",
          color="green",
        )

      self.__send_data_to_chain_peers(
        {
          self.CS_DATA: {
            self.CS_OP: self.CS_HSYNC_REQ,
            self.CS_REQUEST_ID: request_id,
            self.CS_HKEY: hkey,
          }
        },
        peers=explicit_peers or None,
        include_default_peers=include_default_peers,
      )

      deadline = self.time() + timeout
      while self.time() <= deadline:
        pending = self.__pending_hsync.get(request_id, {})
        response = pending.get(self.CS_RESPONSE, None)
        if response is not None:
          sender = response.get(self.CS_SENDER, None)
          snapshot = response.get(self.CS_SNAPSHOT, {})
          # Empty snapshots still prove that a valid peer answered, so they are
          # treated as successful cold-state syncs with zero merged fields.
          merged_fields = self.__merge_hset_snapshot(hkey, snapshot)
          return {
            self.CS_HKEY: hkey,
            "source_peer": sender,
            "merged_fields": merged_fields,
          }
        self.sleep(0.100)

      raise ValueError(f"hsync for '{hkey}' timed out after {timeout}s.")
    finally:
      self.__pending_hsync.pop(request_id, None)


  def _get_value(self, key, token=None, get_owner=False, debug=False):
    """ This method is called to get a value from the chain storage """
    # TODO: Check if this constraint could break anything.
    # if not isinstance(key, str) or len(key) == 0:
    #   raise ValueError("Key must be a non-empty string.")

    debug = debug or self.cfg_chain_store_debug
    if debug:
      self.P(f" === Getting value for key {key}")

    existing_token = self.__get_key_token(key)
    result_value, result_owner = None, None
    if token != existing_token:
      if debug:
        self.P(f" === Key {key} has a different token {existing_token} than the one provided {token}", color='r')
    else:
      result_value = self.__get_key_value(key)
      if get_owner:
        result_owner = self.__get_key_owner(key)
    # end if token
    if result_value is not None:
      result_value = self.deepcopy(result_value)  # make sure we return a copy of the value, in case it is mutable
    if get_owner:
      return result_value, result_owner
    return result_value
  
  ### END setter-getter methods


  def __maybe_broadcast(self):
    """
    Broadcast queued chain-store operations to the network.

    This method is called to broadcast the chain store operations to the
    network. For each operation in the queue, a broadcast is sent to the
    network.

    Returns
    -------
    None
    """
    if self.cfg_chain_store_debug and len(self.__ops) > 0:
      self.P(
        f" === Broadcasting {len(self.__ops)} chain store {self.CS_STORE} ops using default chain peers {self.__chain_peers}"
      )
    while len(self.__ops) > 0:
      data = self.__ops.popleft()
      peers = data.get(self.CS_PEERS, None)
      include_default_peers = data.get(self.CS_INCLUDE_DEFAULT_PEERS, True)
      payload_data = {
        self.CS_DATA : data
      }
      self.__send_data_to_chain_peers(
        payload_data,
        peers=peers,
        include_default_peers=include_default_peers,
      )
    return


  def __exec_store(self, data, peers=None):
    """
    Apply a remote store operation to the local replica and confirm it.

    This method is called when a store operation is received from the network.
    The method will:
      - set the value in the chain storage
      - send an encrypted confirmation of the storage operation to the sender

    Parameters
    ----------
    data : dict
      Decrypted chain-store operation payload.
    peers : str or list, optional
      Sender address or addresses that should receive the confirmation.

    Returns
    -------
    None
    """
    key = data.get(self.CS_KEY, None)
    value = data.get(self.CS_VALUE , None)
    owner = data.get(self.CS_OWNER, None)
    readonly = data.get(self.CS_READONLY, False) # if the key is readonly local node consumers cannot overwrite it
    token = data.get(self.CS_TOKEN, None)
    if self.cfg_chain_store_debug:
      self.P(f" === REMOTE: Exec remote-to-local-sync store for {key}={value} by {owner}")
    result = self._set_value(
      key, value, owner=owner, 
      token=token, readonly=readonly,
      local_sync_storage_op=True,
    )
    if result:
      # now send confirmation of the storage execution
      if self.cfg_chain_store_debug:
        self.P(f" === REMOTE: {self.CS_CONFIRM} for {key} of {owner} back to sender {peers}")
      data = {
        self.CS_DATA : {
          self.CS_OP : self.CS_CONFIRM,
          self.CS_KEY: key,
          self.CS_VALUE : value,
          self.CS_OWNER : owner,
          self.CS_CONFIRM_BY : self.get_instance_path(),
          self.CS_CONFIRM_BY_ADDR : self.ee_addr,
        }
      }
      self.__send_data_to_chain_peers(data, peers=peers, include_default_peers=False)
    else:
      if self.cfg_chain_store_debug:
        self.P(f" === REMOTE: Store for {key}={value} of {owner} failed", color='r')
    return


  def __exec_received_confirm(self, data):
    """
    Process a confirmation for a previously broadcast store operation.

    Parameters
    ----------
    data : dict
      Decrypted confirmation payload received from the network.

    Returns
    -------
    None
    """
    key = data.get(self.CS_KEY, None)
    value = data.get(self.CS_VALUE, None)
    owner = data.get(self.CS_OWNER, None)
    confirm_by = data.get(self.CS_CONFIRM_BY, None)
    op = data.get(self.CS_OP, None)
    
    local_owner = self.__get_key_owner(key)
    local_value = self.__get_key_value(key)
    if self.cfg_chain_store_debug:
      self.P(f" === LOCAL: Received {op} from {confirm_by} for  {key}={value}, owner{owner}")
    if value is None and key not in self.__chain_storage:
      # Deletion confirmed — key already removed locally, nothing to increment
      if self.cfg_chain_store_debug:
        self.P(f" === LOCAL: Key {key} deletion confirmed by {confirm_by}")
    elif owner == local_owner and value == local_value:
      self.__increment_confirmations(key)
      if self.cfg_chain_store_debug:
        self.P(f" === LOCAL: Key {key} confirmed by {confirm_by}")
    return


  def __exec_hsync_request(self, data, peers=None):
    """
    Reply to a peer snapshot request for one hash namespace.

    Parameters
    ----------
    data : dict
      Decrypted hsync request payload.
    peers : str or list, optional
      Sender address or addresses that should receive the snapshot response.

    Returns
    -------
    None

    Notes
    -----
    The response carries only the requested hash namespace snapshot. This
    keeps the wire payload small and avoids broad chain-store exports.
    """
    request_id = data.get(self.CS_REQUEST_ID, None)
    hkey = data.get(self.CS_HKEY, None)
    if not isinstance(request_id, str) or len(request_id) == 0:
      return
    if not isinstance(hkey, str) or len(hkey) == 0:
      return

    snapshot = self.__export_hset_snapshot(hkey)
    if self.cfg_chain_store_debug:
      self.P(
        f" === REMOTE: HSYNC request {request_id} for {hkey} back to {peers}",
        color="green",
      )
    self.__send_data_to_chain_peers(
      {
        self.CS_DATA: {
          self.CS_OP: self.CS_HSYNC_RESP,
          self.CS_REQUEST_ID: request_id,
          self.CS_HKEY: hkey,
          self.CS_SNAPSHOT: snapshot,
        }
      },
      peers=peers,
      include_default_peers=False,
    )
    return


  def __exec_hsync_response(self, data, sender=None):
    """
    Record the first valid peer snapshot response for one pending request.

    Parameters
    ----------
    data : dict
      Decrypted hsync response payload.
    sender : str, optional
      Transport-level sender address for the current payload.

    Returns
    -------
    None

    Notes
    -----
    The first valid response wins. This keeps each ``_hsync`` call
    deterministic and avoids merging multiple peer views into a single refresh
    operation. Freshness arbitration remains a documented follow-up concern.
    """
    request_id = data.get(self.CS_REQUEST_ID, None)
    pending = self.__pending_hsync.get(request_id, None)
    if pending is None or pending.get(self.CS_RESPONSE, None) is not None:
      return
    if not isinstance(sender, str) or len(sender) == 0:
      return

    allowed_peers = pending.get(self.CS_PEERS, [])
    if sender not in allowed_peers:
      return

    hkey = data.get(self.CS_HKEY, None)
    if hkey != pending.get(self.CS_HKEY, None):
      return

    snapshot = data.get(self.CS_SNAPSHOT, {})
    if not isinstance(snapshot, dict):
      return

    # Accept only one response from the precomputed peer set so an unrelated
    # or late peer cannot replace the snapshot chosen for this sync request.
    pending[self.CS_RESPONSE] = {
      self.CS_SENDER: sender,
      self.CS_SNAPSHOT: self.deepcopy(snapshot),
    }
    return

  @NetworkProcessorPlugin.payload_handler()
  def default_handler(self, payload):
    sender = payload.get(self.const.PAYLOAD_DATA.EE_SENDER, None)
    alias = payload.get(self.const.PAYLOAD_DATA.EE_ID, None)
    destination = payload.get(self.const.PAYLOAD_DATA.EE_DESTINATION, None)
    is_encrypted = payload.get(self.const.PAYLOAD_DATA.EE_IS_ENCRYPTED, False)
    destination = destination if isinstance(destination, list) else [destination]
    decrypted_data = self.receive_and_decrypt_payload(data=payload)    
    # DEBUG AREA
    if self.cfg_chain_store_debug:
      from_myself = sender == self.ee_addr
      str_sender = sender if not from_myself else f"{sender} (myself)"
      self.P(f" === PAYLOAD_CSTORE: from {str_sender} (enc={is_encrypted})")
      if self.ee_addr in destination:
        self.P(f" === PAYLOAD_CSTORE: received for me")
      else:
        if not from_myself:
          self.P(f" === PAYLOAD_CSTORE: to {destination} (not for me {self.ee_addr})", color='r')
        return
    # try to decrypt the payload
    if self.cfg_chain_store_debug:
      if decrypted_data is None or len(decrypted_data) == 0:
        self.P(f" === PAYLOAD_CSTORE: FAILED decrypting payload", color='r')
      else:
        self.P(f" === PAYLOAD_CSTORE: decrypted payload OK")
    # END DEBUG AREA    
    # get the data and call the appropriate operation method
    data = decrypted_data.get(self.CS_DATA, {})
    operation = data.get(self.CS_OP, None)
    owner = data.get(self.CS_OWNER, None)
    if self.cfg_chain_store_debug:
      if operation is None:
        self.P(f" === PAYLOAD_CSTORE: NO OPERATION from data: {data}", color='r')
      else:
        self.P(f" === PAYLOAD_CSTORE: {operation=} from {alias=} {owner=}")
    if operation == self.CS_STORE:
      self.__exec_store(data, peers=sender) # make sure you send also to the sender
    elif operation == self.CS_CONFIRM:
      self.__exec_received_confirm(data)
    elif operation == self.CS_HSYNC_REQ:
      self.__exec_hsync_request(data, peers=sender)
    elif operation == self.CS_HSYNC_RESP:
      self.__exec_hsync_response(data, sender=sender)
    return
  

  
  def process(self):
    self.__maybe_refresh_chain_peers()
    self.__maybe_broadcast()
    return 

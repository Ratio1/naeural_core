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


# Used at start-up to keep a replica file that gave no records under another name.
import os
from threading import Lock, RLock
from ratio1.const.base import BCct, dAuth

from naeural_core.business.base.network_processor import NetworkProcessorPlugin
# Runs this plugin's periodic replica save on its own thread instead of on the plugin thread.
from naeural_core.main.serialization_manager import SerializationManager

# Node key rotations: written only by dAuth servers, each value a signed rotation proof.
NODE_KEY_ROTATIONS_HKEY = "NODE_KEY_ROTATIONS"
# Tunnels Manager namespaces (tunnel credentials, and TCP routes under the plugin's default TCP_ROUTES_HKEY): the
# plugin runs only on oracles, so a remote write, a sync request or a sync answer for them is taken only from an
# oracle registry member.
TUNNELS_MANAGER_HKEYS = ("tunnels_manager_secrets", "tunnels_manager_tcp_routes")

_CONFIG = {
  **NetworkProcessorPlugin.CONFIG,

  'PROCESS_DELAY' : 0,

  'MAX_INPUTS_QUEUE_SIZE' : 1024,
  # Take up to 32 queued inputs per loop lap (other network processors keep the base default of 1): one input
  # per lap paid process(), a save check and a >= 20 ms sleep each, so confirmations queued and hsync timed out.
  'MAX_INPUTS_PER_ITERATION' : 32,

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
  # Seconds between two saves of a changed chain storage. The SerializationManager runs them at this cadence
  # on a thread of their own; one full-store save covers every write accepted since the previous one. That
  # thread checks once a second, so an accepted write reaches the disk after up to this cadence, plus up to
  # one second until the thread's next check, plus this store's own save. A save of another owner runs on
  # its own thread and does not delay this one, but the saves compete for the interpreter: on an oracle the
  # network-monitor save (20 MB, measured at 1.5-1.9 s idle and 6.1-6.3 s busy under a 0.7 CPU limit, longer
  # on a slower host) slows this save down while both run.
  CS_SAVE_INTERVAL = 5
  # Seconds between two CSTORE_STATS lines in the node log (written from process()); new, there was no such line.
  CS_STATS_INTERVAL = 300
  # One lock for every save of the replica file in this process. Before, each plugin instance created its own:
  # the last save of an instance that is closing and the first save of the instance that replaces it run on
  # two threads, and without a common lock the older snapshot could be the one left in the file. With one
  # lock the snapshots reach the file in the order they were taken.
  __save_lock = Lock()


  
  
  def on_init(self):
    """
    Load the stored replica, publish the chain-store API to the other plugins and start the periodic save.

    The replica is read from this plugin's cache file (an empty store when there is none or it cannot be
    read). Then the counters of the ``CSTORE_STATS`` line start, and the flush of a changed replica is
    registered with the SerializationManager, which runs it on a thread kept for this save every
    ``CS_SAVE_INTERVAL`` seconds, so this plugin's thread no longer runs the save. When the store this
    instance will use is empty although the replica file exists, the file is renamed to
    ``object.pickle.unreadable-<UTC YYYYmmddHHMMSS>`` and a red line says so, so the first save does not
    overwrite it. Then the getter, setter and hsync functions are placed in the shared plugin memory, where
    the plugins' ``chainstore_*`` calls find them.

    Returns
    -------
    None

    Notes
    -----
    The registration comes after the load, so a save can never run before the stored replica is in memory,
    and before the steps that can fail, so ``on_close`` always finds the registration it removes.

    The rename is one of those steps: when it fails (the cache folder cannot be written) this method raises
    and the plugin does not start, where it used to start empty and fail at its first save. A file that
    held a legitimately empty store is renamed too; that loses nothing. A store that is empty because there
    is no file renames nothing and logs nothing new. A store taken over from a previous instance of this
    plugin in the same process is used as it is; the file is renamed only when that store is empty too.
    """
    super().on_init() # not mandatory anymore?
    
    self.P(" === ChainStoreBasicPlugin INIT")
    
    self.__chainstore_identity = "CS_MSG_{}".format(self.uuid(7))
    
    self.__ops = self.deque()
    self.__persistence_lock = RLock()
    self.__chain_storage_dirty = False
    
    try:
      self.__chain_storage = self.cacheapi_load_pickle(default={}, verbose=True)
    except Exception as e:
      self.P(f" === Chain storage could not be loaded: {e}")
      self.__chain_storage = {}
    # Counters of the CSTORE_STATS line (label -> count), taken and reset by process() at each line.
    self.__cs_stats = {}
    # Where the next CSTORE_STATS line is measured from: the time of the last line and the largest input queue
    # seen since then.
    self.__cs_stats_last, self.__cs_stats_queue_peak = self.time(), 0
    # The plugin's own count of lost inputs at start, so the first line reports only what was lost after it.
    self.__cs_stats_lost = self.lost_inputs_count
    # The replica is loaded: from here the SerializationManager saves it every CS_SAVE_INTERVAL seconds when
    # it changed. A full save took this plugin's thread 0.26 s on an idle node and 1.3 s on a busy one
    # (5 MB, 0.7 CPU), during which it confirmed no write; the thread no longer runs the save. Registered
    # before anything below can fail, so on_close always finds the registration it removes.
    SerializationManager.instance.register(self.__maybe_flush_chain_storage, every=self.CS_SAVE_INTERVAL)
      
    memory = self.plugins_shmem
      

    ## DEBUG ONLY:
    if self.CS_STORAGE_MEM in memory:
      self.P(" === Chain storage already exists", color="r")
      self.__chain_storage = memory[self.CS_STORAGE_MEM]
    ## END DEBUG ONLY

    # The file the strict save writes. Before, a replica file that gave no records (it could not be read, or
    # it held none) was silently overwritten by the first save; now it is kept under another name. Checked on
    # the store this instance will use, after the branch above: a store taken over from a previous instance
    # that holds records is saved over the file as before. A file that held a legitimately empty store is
    # renamed too, which loses nothing.
    replica_file = os.path.join(self.log.get_data_folder(), self._persistence_fld.lstrip("/"), "object.pickle")
    if not self.__chain_storage and os.path.exists(replica_file):
      # Next to the replica, named after the UTC time of this start, where no save writes.
      kept_file = "{}.unreadable-{}".format(replica_file, self.datetime.now(self.timezone.utc).strftime("%Y%m%d%H%M%S"))
      # Move it aside before the first save can replace it: no save writes before a write is accepted, and no
      # write is accepted before the setter is published below.
      os.rename(replica_file, kept_file)
      self.P(f" === Chain storage replica file gave no records; kept it as {kept_file}, the store starts empty", color='r')

    memory[self.CS_STORAGE_MEM] = self.__chain_storage
    memory[self.CS_GETTER] = self._get_value
    memory[self.CS_SETTER] = self._set_value
    memory[self.CS_HSYNC] = self._hsync
    
    self.__last_chain_peers_refresh = 0
    self.__chain_peers = []
    self.__pending_hsync = {}
    self.__maybe_refresh_chain_peers()
    return
  

  def __netmon_node_is_online_for_control(self, peer):
    """
    Tell whether ``peer`` is running and should receive records.

    NetMon reports a node that left through an error as PAST-EXCEPTION for
    the next hour, also when it is back and heartbeating. That rule guards
    deployments; applied to replication it cuts a restarted oracle off for an
    hour, and nothing sends it the records written meanwhile. So the hour of
    memory is not used here: a peer counts when its latest status is ONLINE.
    A status that comes from an oracle's network summary keeps the reporter's
    hour of memory, so PAST-EXCEPTION is accepted too; a peer that stops after
    an exception is then dropped when its summary goes stale (LOST STATUS
    after 60 s), the same bound as a peer that dies silently.

    Parameters
    ----------
    peer : str
      Address of the node to check, as listed by
      ``self.bc.get_whitelist(with_prefix=True)`` (for example ``"0xai_..."``).

    Returns
    -------
    bool
      True when NetMon's latest status for ``peer`` is ONLINE or
      PAST-EXCEPTION; False for any other status (LOST STATUS, EXCEPTION,
      SHUTDOWN, STOP and so on).

    Raises
    ------
    AttributeError
      When NetMon has no ``network_node_simple_status``; no older predicate is
      tried instead.

    Notes
    -----
    NetMon is called directly, with no fallback to the older predicates
    (``network_node_is_online_for_control`` or ``network_node_is_online``).
    NetMon ships in the same package as the chain store, so a NetMon without
    ``network_node_simple_status`` makes the peer refresh fail instead of
    silently bringing back the hour of exception memory.

    Examples
    --------
    >>> self.__netmon_node_is_online_for_control("0xai_peer_back_after_error")
    True
    >>> self.__netmon_node_is_online_for_control("0xai_peer_not_seen_for_2_min")  # LOST STATUS
    False
    """
    # Latest status only (0 minutes of exception memory): the default 60-minute window keeps a restarted peer
    # at PAST-EXCEPTION for an hour and cuts it off from the records written meanwhile.
    status = self.netmon.network_node_simple_status(peer, last_exception_check_time_minutes=0)
    # PAST-EXCEPTION can still come from an oracle's network summary, which a normal node relies on; such a
    # peer is dropped once that summary goes stale (LOST STATUS after 60 s), like a peer that dies silently.
    return status in ("ONLINE", "PAST-EXCEPTION")

  
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
      # ChainStore routing is not consensus evidence; on normal nodes the
      # control liveness check may use fresh oracle summaries when peer
      # heartbeats are intentionally not received.
      self.__chain_peers = [
        peer for peer in _chain_peers if self.__netmon_node_is_online_for_control(peer)
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
      Effective peer list used for confirmation math and outbound sends. Always a new list, so the caller
      may extend it without changing the plugin's peers.

    Notes
    -----
    The default peers are copied with ``list()``: they are address strings in a list that the refresh
    replaces and nobody changes in place, so a deep copy bought nothing, and this runs several times per
    write.
    """
    # A plain copy: before, a deep copy of this list of address strings, which is rebuilt on refresh and never
    # changed in place, on a path that runs several times per write.
    send_to = list(self.__chain_peers) if include_default_peers else []
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
    """
    Write a snapshot of the whole chain storage to the replica file.

    The records are copied under the persistence lock and the dirty flag is cleared there; the copy is then
    written outside that lock. The whole save runs under the save lock, which is one for the process, so
    snapshots reach the file in the order they were taken, also across a restart of this plugin. Each save
    is counted as ``saves`` for the ``CSTORE_STATS`` line.

    Returns
    -------
    None

    Notes
    -----
    A failed write marks the store dirty again, so the next flush retries it. A failed write is counted too:
    the count is taken where the dirty flag is cleared, before the write.
    """
    # Callers persist after releasing the persistence lock. Records are copied
    # shallowly under that lock so concurrent writers and confirmations cannot
    # change the store while it is pickled; the disk write runs outside it.
    # The save lock keeps snapshots landing on disk in the order they were
    # taken. A failed write marks the store dirty so the next flush retries it.
    with self.__save_lock:
      with self.__persistence_lock:
        snapshot = {
          key: {
            field: list(value) if isinstance(value, list) else value
            for field, value in record.items()
          } if isinstance(record, dict) else record
          for key, record in self.__chain_storage.items()
        }
        self.__chain_storage_dirty = False
        # Count this save for the CSTORE_STATS line, under the lock already held here; before, saves were not counted.
        self.__cs_stats["saves"] = self.__cs_stats.get("saves", 0) + 1
      try:
        self.cacheapi_save_pickle(snapshot, verbose=False, strict=True)
      except Exception as exc:
        self.__chain_storage_dirty = True
        self.P(f" === Chain storage save failed, will retry: {exc}", color='r')
    return


  def __maybe_flush_chain_storage(self):
    """
    Save the chain storage if it changed since the last save.

    The SerializationManager calls this on its own thread every
    `CS_SAVE_INTERVAL` seconds (registered in `on_init`), so the plugin thread
    no longer runs the save. Accepted writes only mark the store dirty, so one
    full-store save covers every write made since the previous run. The same
    path retries a failed save, because a failed save marks the store dirty
    again.

    Returns
    -------
    None

    Notes
    -----
    Safe to call from another thread: `__save_chain_storage` copies the records
    under the persistence lock and orders overlapping saves with the save lock.

    Examples
    --------
    >>> SerializationManager.instance.register(self.__maybe_flush_chain_storage, every=self.CS_SAVE_INTERVAL)
    """
    if not self.__chain_storage_dirty:
      return
    # The cadence is the manager's: no interval check here, the manager calls this every CS_SAVE_INTERVAL seconds.
    self.__save_chain_storage()
    return


  def __namespace_for_key(self, key, namespaces):
    """Recognize reserved hash namespaces without exposing composed fields."""
    if not isinstance(key, str):
      return None
    return next((name for name in namespaces if key.startswith(self.__hset_index(name))), None)

  def __registry_member(self, address, dauth_only=False):
    """Authorize a transport identity against registry ETH addresses; fail closed."""
    try:
      eth_address = self.bc.node_address_to_eth_address(address).lower()
      allowed = list(self.bc.get_eth_dauth_oracles() or [])
      if not dauth_only:
        allowed.extend(self.bc.get_eth_oracles() or [])
      return eth_address in {item.lower() for item in allowed}
    except Exception:
      return False

  def __secret_namespace(self, key):
    """Identify the protected mutable and revision-specific dAuth namespaces."""
    return self.__namespace_for_key(key, ("DAUTH_JOB_SECRETS", "DAUTH_JOB_SECRET_REVISIONS"))

  def __oracle_only_write(self, key, value):
    """
    Tell whether a remote write is applied only when its sender is an oracle registry member.

    Two kinds of record are written only by plugins that run on oracles: every field of the Tunnels Manager
    namespaces (``TUNNELS_MANAGER_HKEYS``), and a deployed-job pointer whose value carries no revision, the
    unversioned form that ``__versioned_job_record`` accepts (a plain CID string, ``None`` for a delete, or a
    dict without ``revision``). A pointer with a revision keeps the rule of ``__versioned_job_record``: the
    signature of its coordinator is checked, whoever relays it.

    Parameters
    ----------
    key : any
      Composed chain-store key of the write, as received; a key that is not a str is in no namespace.
    value : any
      Value of the write; ``None`` for a delete.

    Returns
    -------
    bool
      True for a write that only an oracle registry member may make; False for every other write.

    Examples
    --------
    >>> self.__oracle_only_write(self.__hset_index("tunnels_manager_secrets") + "MHhhYmM=", None)
    True
    >>> self.__oracle_only_write(self.__hset_index("DEEPLOY_DEPLOYED_JOBS") + "Nw==", "cid")
    True
    >>> self.__oracle_only_write(self.__hset_index("DEEPLOY_DEPLOYED_JOBS") + "Nw==", {"revision": 2})
    False
    >>> self.__oracle_only_write(self.__hset_index("players") + "YWxwaGE=", "v")
    False
    """
    # Every write and delete of a Tunnels Manager namespace, and a deployed-job pointer whose value has no revision
    # (read as __versioned_job_record reads it).
    return self.__namespace_for_key(key, TUNNELS_MANAGER_HKEYS) is not None or (
      self.__namespace_for_key(key, ("DEEPLOY_DEPLOYED_JOBS",)) is not None
      and (value.get("revision") if isinstance(value, dict) else None) is None
    )

  def __revoked_secret(self, key, snapshot=None):
    """Check whether a secret revision has a local or incoming revocation.

    Parameters
    ----------
    key : str
      Composed secret payload or revocation-marker key.
    snapshot : dict, optional
      Incoming namespace records considered alongside local storage.

    Returns
    -------
    bool
      Whether a populated marker exists under the exact HSET key encoding.
    """
    if self.__secret_namespace(key) != "DAUTH_JOB_SECRET_REVISIONS":
      return False
    prefix = self.__hset_index("DAUTH_JOB_SECRET_REVISIONS")
    field = self.base64_to_str(key[len(prefix):], url_safe=True)
    # Preserve Base64 padding, matching the key written by chainstore_hset.
    marker = key if field.endswith("|revoked") else prefix + self.str_to_base64(field + "|revoked", url_safe=True)
    return bool(self.__chain_storage.get(marker, {}).get(self.CS_VALUE)) or bool((snapshot or {}).get(marker, {}).get(self.CS_VALUE))

  def __erased_secret(self, key, value, snapshot=None):
    """Validate a redacted tombstone against its exact revocation marker.

    Parameters
    ----------
    key : str
      Composed secret payload key.
    value : object
      Candidate redacted tombstone; plaintext fields are not permitted.
    snapshot : dict, optional
      Incoming namespace records considered alongside local storage.

    Returns
    -------
    bool
      Whether the tombstone identity matches a local or incoming marker.
    """
    if not isinstance(value, dict) or set(value) != {"job_id", "revision", "revoked"} or value.get("revoked") is not True:
      return False
    prefix = self.__hset_index("DAUTH_JOB_SECRET_REVISIONS")
    field = self.base64_to_str(key[len(prefix):], url_safe=True)
    marker_key = prefix + self.str_to_base64(field + "|revoked", url_safe=True)
    marker = self.__chain_storage.get(marker_key, {}).get(self.CS_VALUE) or (snapshot or {}).get(marker_key, {}).get(self.CS_VALUE)
    return isinstance(marker, dict) and marker.get("job_id") == value["job_id"] and marker.get("revision") == value["revision"]

  def __versioned_job_record(self, key, value, namespace):
    """Protect canonical pointers and current bundles with signed generations.

    The generation increases across deployment, restore and delete commits; it
    is independent of the runtime configuration revision restored after failure.
    A higher generation permits explicit reuse after deletion. Once a versioned
    record exists, unversioned writes and stale snapshot repair cannot replace it.
    """
    version_key = "revision" if namespace == "DEEPLOY_DEPLOYED_JOBS" else "deployment_revision"
    local = self.__chain_storage.get(key, {}).get(self.CS_VALUE)
    local_version = local.get(version_key) if isinstance(local, dict) else None
    version = value.get(version_key) if isinstance(value, dict) else None
    if version is None:
      return local_version is None
    if type(version) is not int or version < 1:
      return False
    try:
      field = self.base64_to_str(key[len(self.__hset_index(namespace)):], url_safe=True)
      principal = value.get("coordinator")
      author = value.get("EE_SENDER")
      # dAuth attests the coordinator's verified operation when publishing its
      # custodied current bundle. Public pointers remain coordinator-signed.
      authorized_author = author == principal or (
        namespace == "DAUTH_JOB_SECRETS" and self.__registry_member(author, dauth_only=True)
      )
      if (value.get("job_id") != field or not isinstance(value.get("operation_id"), str)
          or not value["operation_id"] or not authorized_author
          or not self.__registry_member(principal)
          or not self.bc.verify(value, return_full_info=True, log_hash_sign_fails=False).valid):
        return False
      if namespace == "DEEPLOY_DEPLOYED_JOBS":
        if type(value.get("deleted")) is not bool:
          return False
        if value["deleted"]:
          if value.get("pipeline_cid") is not None:
            return False
        elif not isinstance(value.get("pipeline_cid"), str) or not value["pipeline_cid"]:
          return False
      if type(local_version) is int:
        if version < local_version:
          return False
        if version == local_version:
          signature_keys = {"EE_SIGN", "EE_SENDER", "EE_HASH", BCct.SIGN_CANON_V, "EE_ETH_SENDER", "EE_ETH_SIGN"}
          return ({key: item for key, item in value.items() if key not in signature_keys}
                  == {key: item for key, item in local.items() if key not in signature_keys})
    except Exception:
      return False
    return True

  def __node_key_rotation_record(self, key, value):
    """Accept only a valid rotation proof for the field's key; the first new key is final.

    The field is the replaced key's lowercase ETH address and the value is the signed
    proof checked by ``bc.verify_node_key_rotation``. A stored mapping cannot be deleted
    or pointed at another new key; another proof of the same mapping is accepted.
    """
    try:
      field = self.base64_to_str(key[len(self.__hset_index(NODE_KEY_ROTATIONS_HKEY)):], url_safe=True)
      old_node, new_node = self.bc.verify_node_key_rotation(value)
      if self.bc.node_address_to_eth_address(old_node).lower() != field:
        return False
      local = self.__chain_storage.get(key, {}).get(self.CS_VALUE)
      if local is None:
        return True
      return local[dAuth.DAUTH_KEY_ROTATION]["new_node"] == new_node
    except Exception:
      return False

  def __versioned_record(self, key, value):
    """Validate signed operation/node records before accepting sequence order."""
    if self.__namespace_for_key(key, (NODE_KEY_ROTATIONS_HKEY,)):
      return self.__node_key_rotation_record(key, value)
    job_namespace = self.__namespace_for_key(key, ("DEEPLOY_DEPLOYED_JOBS", "DAUTH_JOB_SECRETS"))
    if job_namespace:
      return self.__versioned_job_record(key, value, job_namespace)
    namespace = self.__namespace_for_key(key, ("DEEPLOY_OPERATIONS", "DEEPLOY_NODE_OPERATIONS"))
    if namespace is None:
      return True
    if not isinstance(value, dict) or not isinstance(value.get("sequence"), int) or isinstance(value["sequence"], bool):
      return False
    principal = value.get("coordinator") if namespace == "DEEPLOY_OPERATIONS" else value.get("node_addr")
    operation_id = value.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id or not isinstance(principal, str):
      return False
    try:
      field = self.base64_to_str(key[len(self.__hset_index(namespace)):], url_safe=True)
    except Exception:
      return False
    expected = operation_id
    if namespace == "DEEPLOY_NODE_OPERATIONS":
      if value.get("attempt") not in ("deploy", "restore", "retire"):
        return False
      expected += "|" + value["attempt"] + "|" + principal
    if field not in (expected, expected + "|" + str(value["sequence"] % 8)):
      return False
    if value.get("EE_SENDER") != principal:
      return False
    try:
      if not self.bc.verify(value, return_full_info=True).valid:
        return False
      if namespace == "DEEPLOY_OPERATIONS" and not self.__registry_member(principal):
        return False
      local = self.__chain_storage.get(key, {}).get(self.CS_VALUE)
      if isinstance(local, dict) and local.get("sequence", -1) > value["sequence"]:
        return False
    except Exception:
      return False
    return True

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

    A dAuth namespace is exported only by a dAuth registry member. The
    namespace is the one whose index (``__hset_index``) ``hkey`` has, the
    prefix the records are selected by.

    Examples
    --------
    >>> list(self.__export_hset_snapshot("players"))  # composed keys of the fields that carry no token
    ['hs:...:YWxwaGE=']
    """
    # The dAuth rule applies to the namespace of the index hkey has, the prefix the records are selected by below.
    if self.__secret_namespace(self.__hset_index(hkey)) and not self.__registry_member(self.ee_addr, dauth_only=True):
      return {}
    index = self.__hset_index(hkey)
    snapshot = {}
    # Freeze the current dictionary view before filtering so live writes do not
    # mutate the iterator while this response is being serialized.
    for key, record in list(self.__chain_storage.items()):
      if not isinstance(key, str) or not key.startswith(index):
        continue
      if not isinstance(record, dict):
        continue
      # HSync carries no token capability. Never disclose token values or
      # protected records merely because the caller knows a namespace name.
      if record.get(self.CS_TOKEN) is not None:
        continue
      snapshot[key] = self.deepcopy(record)
      snapshot[key].pop(self.CS_TOKEN, None)
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

    A dAuth namespace is merged only by a dAuth registry member. The namespace
    is the one whose index (``__hset_index``) ``hkey`` has, the prefix the
    records are selected by.

    Examples
    --------
    >>> self.__merge_hset_snapshot("players", {key: record})
    1
    """
    if not isinstance(snapshot, dict):
      return 0
    # The dAuth rule applies to the namespace of the index hkey has, the prefix the records are selected by below.
    if self.__secret_namespace(self.__hset_index(hkey)) and not self.__registry_member(self.ee_addr, dauth_only=True):
      return 0

    index = self.__hset_index(hkey)
    merged_fields = 0
    changed = False
    with self.__persistence_lock:
      for key, record in snapshot.items():
        if not isinstance(key, str) or not key.startswith(index):
          continue
        if not isinstance(record, dict):
          continue
        if self.CS_OWNER not in record or self.CS_VALUE not in record:
          continue

        value = self.deepcopy(record.get(self.CS_VALUE))
        if value is None or not self.__versioned_record(key, value):
          continue
        if self.__secret_namespace(key) == "DAUTH_JOB_SECRET_REVISIONS":
          prefix = self.__hset_index("DAUTH_JOB_SECRET_REVISIONS")
          if self.base64_to_str(key[len(prefix):], url_safe=True).endswith("|revoked") and not value:
            continue
        if self.__revoked_secret(key, snapshot):
          prefix = self.__hset_index("DAUTH_JOB_SECRET_REVISIONS")
          field = self.base64_to_str(key[len(prefix):], url_safe=True)
          if not field.endswith("|revoked") and not self.__erased_secret(key, value, snapshot):
            self.__chain_storage.pop(key, None)
            changed = True
            continue
          existing_marker = self.__chain_storage.get(key, {}).get(self.CS_VALUE)
          if field.endswith("|revoked") and existing_marker:
            continue

        readonly = record.get(self.CS_READONLY, False)
        token = record.get(self.CS_TOKEN, None)
        versioned_namespace = self.__namespace_for_key(key, ("DEEPLOY_OPERATIONS", "DEEPLOY_NODE_OPERATIONS"))
        if versioned_namespace:
          record = dict(record)
          record[self.CS_OWNER] = value.get("coordinator") if versioned_namespace == "DEEPLOY_OPERATIONS" else value.get("node_addr")
          readonly = True
        job_namespace = self.__namespace_for_key(key, ("DEEPLOY_DEPLOYED_JOBS", "DAUTH_JOB_SECRETS"))
        if job_namespace and isinstance(value, dict) and "coordinator" in value:
          record = dict(record)
          record[self.CS_OWNER] = value["coordinator"]
          readonly = False
          token = None
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
      # Saved by the SerializationManager's next flush (every CS_SAVE_INTERVAL s).
      self.__chain_storage_dirty = True
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
    self.__chain_storage[key]["confirmed_peers"] = []
    self.__chain_storage[key]["target_peers"] = self.__get_target_peers(
      peers=peers, include_default_peers=include_default_peers,
    )
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
    The caller must hold the persistence lock and, after releasing it, mark
    the chain storage dirty when the call changed the replica (``_set_value``
    does not mark it for a delete of a key not held or for a write of what
    was already stored).
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

    A write that leaves the replica as it was does not mark it for a save: a
    delete of a key this replica does not hold, and a local write whose value
    equals the stored one (``==``) with the stored owner and readonly. The
    identical local write is still stored (its confirmations are reset),
    broadcast and confirmed as before. A delete of a key not held stores
    nothing; when it is local it is broadcast and its wait ends at once, as
    for any delete. After an identical write, what differs between memory and
    the file until the next save are the confirmation fields, which only the
    wait loop and the confirmation handler of a write in progress use (a
    hash-sync answer carries them and its receiver ignores them), and the
    value when the new one is equal to the stored one but not in the same
    form (``1`` and ``1.0``, or a dict with its keys in another order).

    Every call with a valid key is counted once for the ``CSTORE_STATS``
    line, by the label ``<bucket>/<origin>/<outcome>``: the bucket is the
    first 14 characters of an ``hs:`` key (the prefix of its hash namespace)
    or ``plain``, the origin is ``local`` or ``remote``, and the outcome is
    ``changed``, ``identical``, ``absent_delete`` or ``refused`` (any other
    way out). ``changed`` says what happened to this replica: a local write
    that changed it and then was not confirmed, or raised while waiting, is
    still counted as ``changed``.

    A local write or delete of a Tunnels Manager namespace is sent only to
    the oracle registry members (either registry) among its target peers;
    the others are left out of its send, its retries and its confirmation
    count. With no member among them, which includes a registry that cannot
    be read on this node, it says so in red and returns False, as a write
    with no target peers.

    Examples
    --------
    >>> self._set_value("key", "value", owner="node", timeout=5, max_retries=1)  # confirmed by enough peers
    True
    >>> self._set_value("key", "value", owner="peer1", local_sync_storage_op=True)  # a remote write, stored here
    True
    """
    if not isinstance(key, str) or len(key) == 0:
      raise ValueError("Key must be a non-empty string.")
    # Outcome counted once by the finally below; every way out that does not set it is a refusal.
    outcome = "refused"
    try:
      where = "FROM_LOCAL: " if not local_sync_storage_op else "FROM_REMOTE: "
      debug = debug or self.cfg_chain_store_debug
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
      # A local write of a Tunnels Manager namespace goes only to oracle registry members, the senders a write or a
      # sync request of it is taken from: the default peers (the allowed list) and the configured or extra peers may
      # name other nodes, which are left out of this write's targets.
      if not local_sync_storage_op and self.__namespace_for_key(key, TUNNELS_MANAGER_HKEYS):
        # The members, as the only explicit targets of the send, its retries and its confirmation count.
        peers, include_default_peers = [peer for peer in target_peers if self.__registry_member(peer)], False
        # The targets checked below: with no member among them, the write ends as one with no target peers.
        target_peers = peers
        if len(target_peers) == 0:
          self.P(
            f" === No oracle registry member among the targets of the Tunnels Manager write on key {key}, or the "
            "registry could not be read: nothing is sent", color='r'
          )
      if not local_sync_storage_op and len(target_peers) == 0:
        self.P(
          f" === No target peers resolved for chain-store write on key {key}",
          color='y'
        )
        return False
      secret_namespace = self.__secret_namespace(key)
      rotation_namespace = self.__namespace_for_key(key, (NODE_KEY_ROTATIONS_HKEY,))
      if rotation_namespace and not local_sync_storage_op and not self.__registry_member(self.ee_addr, dauth_only=True):
        return False
      if secret_namespace and not local_sync_storage_op:
        if not self.__registry_member(self.ee_addr):
          return False
        if any(not self.__registry_member(peer, dauth_only=True) for peer in target_peers):
          return False
      with self.__persistence_lock:
        if not self.__versioned_record(key, value):
          return False
        versioned_namespace = self.__namespace_for_key(key, ("DEEPLOY_OPERATIONS", "DEEPLOY_NODE_OPERATIONS"))
        if versioned_namespace:
          owner = value.get("coordinator") if versioned_namespace == "DEEPLOY_OPERATIONS" else value.get("node_addr")
          readonly = True
        job_namespace = self.__namespace_for_key(key, ("DEEPLOY_DEPLOYED_JOBS", "DAUTH_JOB_SECRETS"))
        if job_namespace and isinstance(value, dict) and "coordinator" in value:
          owner, readonly, token = value["coordinator"], False, None
        if secret_namespace == "DAUTH_JOB_SECRET_REVISIONS":
          prefix = self.__hset_index(secret_namespace)
          field = self.base64_to_str(key[len(prefix):], url_safe=True)
          if field.endswith("|revoked"):
            existing_marker = self.__chain_storage.get(key, {}).get(self.CS_VALUE)
            if not value or (existing_marker and value != existing_marker):
              return False
          elif value is not None and self.__revoked_secret(key) and not self.__erased_secret(key, value):
            return False
        need_store = True
        existing_owner = None
        existing_value = None
        # True when this write leaves the replica as it was, so it does not mark it for a save; before, every
        # stored write marked it, and a marked replica is saved whole at the next flush. A delete of a key this
        # replica does not hold (local or remote) changes nothing here; a later version that records deletions
        # would record its marker at this point. The identical local write is decided below.
        unchanged = value is None and key not in self.__chain_storage
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
            # Matching remote writes are already applied locally.
            # Matching local writes must re-establish remote confirmation.
            if debug:
              self.P(f" === Key {key} stored by {existing_owner} has the same value")
            if local_sync_storage_op:
              # Counted as identical: the value was already stored. Before, this way out was not counted.
              outcome = "identical"
              return True
            # A previous timeout may have left only the local copy. Re-publish
            # and collect distinct peer confirmations instead of claiming success.
            # The replica stays as it was when the final owner and readonly (after the namespace rules above)
            # match the stored ones too; the token is already known to be equal here. Before, it was marked.
            unchanged = owner == existing_owner and readonly == is_readonly
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
            # The value shortened for this log line only; before, it was built at the top of every write, also
            # with debug off.
            debug_val = str(value)[:20] + "..." if len(str(value)) > 20 else str(value)
            self.P(f" === {where}{action_str} <{key}> = <{debug_val}> by {owner} (orig: {existing_owner}), is_remote={local_sync_storage_op}")
          self.__set_key_value(
            key=key, value=value, owner=owner,
            local_sync_storage_op=local_sync_storage_op,
            readonly=readonly, token=token,
            peers=peers,
            include_default_peers=include_default_peers,
          )
          # The outcome counted for this call: a delete of a key not held, a write of what was already stored,
          # or a change of the replica. Before, nothing was counted.
          outcome = ("absent_delete" if value is None else "identical") if unchanged else "changed"
      if need_store:
        # A write that left the replica as it was does not mark it for a save; before, every stored write did.
        if not unchanged:
          # Saved by the SerializationManager's next flush (every CS_SAVE_INTERVAL s). The flag is set after
          # the change is in the store, so a concurrent snapshot that clears it
          # either contains this write or is followed by another flush.
          self.__chain_storage_dirty = True
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
            with self.__persistence_lock:
              # A concurrent write can replace this key while we wait. Its
              # acknowledgments never establish replication of our value.
              if value is not None and (
                self.__get_key_owner(key) != owner or
                self.__get_key_value(key) != value
              ):
                return False
              recv_confirm = self.__get_key_confirmations(key)
              min_confirm = self.__get_key_min_confirmations(key)
            if recv_confirm > _prev_confirm:
              _prev_confirm = recv_confirm
              if debug:
                self.P(f" === {where}Key received '{key}' has {recv_confirm} confirmations")
            if recv_confirm >= min_confirm:
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
    finally:
      # Count this call once for the CSTORE_STATS line, by namespace bucket (the "hs:" prefix of one hash
      # namespace, or "plain"), origin and outcome. Nothing here can raise for the valid key checked above.
      label = "{}/{}/{}".format(key[:14] if key.startswith("hs:") else "plain", "remote" if local_sync_storage_op else "local", outcome)
      # Under the persistence lock, as every count of the line.
      with self.__persistence_lock:
        # A plain increment of one counter of a small dict; it holds one label per bucket, origin and outcome.
        self.__cs_stats[label] = self.__cs_stats.get(label, 0) + 1
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

    A dAuth namespace is synced only by a dAuth registry member from dAuth
    registry members. The namespace is the one whose index (``__hset_index``)
    ``hkey`` has, the prefix the merge selects records by.

    Examples
    --------
    >>> self._hsync("players", peers=["0xai_peer"], include_default_peers=False)
    {'hkey': 'players', 'source_peer': '0xai_peer', 'merged_fields': 2}
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

    # The dAuth rule applies to the namespace of the index hkey has, the prefix the merge selects records by.
    if self.__secret_namespace(self.__hset_index(hkey)):
      if not self.__registry_member(self.ee_addr, dauth_only=True):
        raise ValueError("Protected namespace requires a dAuth registry participant")
      if any(not self.__registry_member(peer, dauth_only=True) for peer in allowed_peers):
        raise ValueError("Protected namespace repair requires dAuth registry peers")

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
    peers : str, optional
      Address of the node that sent this store: the payload's ``EE_SENDER``, one address, passed by
      ``default_handler``. The confirmation goes back to it. It is not the ``PEERS`` list of the message, which
      this method does not read.

    Returns
    -------
    None

    Notes
    -----
    A store from a sender that is not in this node's replication peers is counted as
    ``<bucket>/remote_non_peer`` for the ``CSTORE_STATS`` line, before any check of the write.

    A write or delete of a Tunnels Manager namespace, or of a deployed-job pointer without a revision
    (``__oracle_only_write``), is applied only when the sender is an oracle registry member (either registry,
    as ``__registry_member`` reads them). A refused one gets no confirmation and is counted as
    ``<bucket>/remote/refused``, the label ``_set_value`` gives the remote writes it refuses; ``_set_value``
    is not called for it, so it is counted once. When the registries cannot be read, ``__registry_member``
    answers False and the store is refused, as for the dAuth namespaces.

    A refused delete is not reported back to its sender: the sender does not wait for the confirmations of a
    delete, so it treats it as done. On that node ``__set_key_value`` removes the key at once, so the wait loop of its
    ``_set_value`` reads 0 confirmations of 0 required for a key it no longer holds and returns True, and
    ``__exec_received_confirm`` counts nothing for a deleted key.

    Examples
    --------
    >>> key = self.__hset_index("tunnels_manager_secrets") + "MHhhYmM="
    >>> self.__exec_store({"key": key, "value": {"token": "t"}, "owner": "x"}, peers="0xai_oracle")  # stored
    >>> self.__exec_store({"key": key, "value": None, "owner": "x"}, peers="0xai_other")  # refused, not confirmed
    """
    key = data.get(self.CS_KEY, None)
    value = data.get(self.CS_VALUE , None)
    owner = data.get(self.CS_OWNER, None)
    readonly = data.get(self.CS_READONLY, False) # if the key is readonly local node consumers cannot overwrite it
    token = data.get(self.CS_TOKEN, None)
    if peers not in self.__chain_peers:
      # Count stores whose sender (``peers`` is that one address) is outside this node's replication peers, by
      # namespace bucket, without asking the blockchain engine; before, they were not counted. One store, one
      # count. The key comes from the network and may not be a str.
      label = (key[:14] if isinstance(key, str) and key.startswith("hs:") else "plain") + "/remote_non_peer"
      # Under the persistence lock, as every count of the CSTORE_STATS line.
      with self.__persistence_lock:
        # A plain increment of one counter; one label per bucket.
        self.__cs_stats[label] = self.__cs_stats.get(label, 0) + 1
    if self.__secret_namespace(key):
      if not self.__registry_member(peers) or not self.__registry_member(self.ee_addr, dauth_only=True):
        return
    if self.__namespace_for_key(key, (NODE_KEY_ROTATIONS_HKEY,)) and not self.__registry_member(peers, dauth_only=True):
      return
    # Tunnel namespaces and unversioned deployed-job pointers are written only by plugins that run on oracles: such
    # a write or delete is applied only when its sender is an oracle registry member. A refused one gets no
    # confirmation, like a write that _set_value refuses.
    if self.__oracle_only_write(key, value) and not self.__registry_member(peers):
      # Counted under the label _set_value gives a refused remote write; the bucket is the key's "hs:" prefix.
      label = key[:14] + "/remote/refused"
      # Under the persistence lock, as every count of the CSTORE_STATS line.
      with self.__persistence_lock:
        # A plain increment of one counter; one label per bucket.
        self.__cs_stats[label] = self.__cs_stats.get(label, 0) + 1
      return
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


  def __exec_received_confirm(self, data, sender=None):
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
    
    if self.cfg_chain_store_debug:
      self.P(f" === LOCAL: Received {op} from {confirm_by} for  {key}={value}, owner{owner}")
    with self.__persistence_lock:
      local_owner = self.__get_key_owner(key)
      local_value = self.__get_key_value(key)
      if value is None and key not in self.__chain_storage:
        # Deletion confirmed — key already removed locally, nothing to increment
        if self.cfg_chain_store_debug:
          self.P(f" === LOCAL: Key {key} deletion confirmed by {confirm_by}")
      elif owner == local_owner and value == local_value:
        record = self.__chain_storage.get(key, {})
        confirmed = record.setdefault("confirmed_peers", [])
        if sender not in record.get("target_peers", []) or sender in confirmed:
          return
        confirmed.append(sender)
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

    A request for a dAuth namespace is answered only between dAuth registry
    members. A request for a Tunnels Manager namespace is answered only when
    the sender is an oracle registry member (either registry); another
    sender gets no answer. The namespace of a request is the one whose index
    (``__hset_index``) its ``hkey`` has, the prefix the export selects
    records by.

    Examples
    --------
    >>> self.__exec_hsync_request({"request_id": "r1", "hkey": "players"}, peers="0xai_any")  # answered
    >>> self.__exec_hsync_request({"request_id": "r2", "hkey": "tunnels_manager_secrets"}, peers="0xai_other")  # not
    """
    request_id = data.get(self.CS_REQUEST_ID, None)
    hkey = data.get(self.CS_HKEY, None)
    if not isinstance(request_id, str) or len(request_id) == 0:
      return
    if not isinstance(hkey, str) or len(hkey) == 0:
      return

    # The index hkey has: the export selects the records by it, so the namespace rules below are applied to it.
    index = self.__hset_index(hkey)
    if self.__secret_namespace(index):
      if not self.__registry_member(peers, dauth_only=True) or not self.__registry_member(self.ee_addr, dauth_only=True):
        return
    # The Tunnels Manager namespaces are read only by plugins that run on oracles: their snapshot is sent only to an
    # oracle registry member.
    if self.__namespace_for_key(index, TUNNELS_MANAGER_HKEYS) and not self.__registry_member(peers):
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

    An answer for a Tunnels Manager namespace is taken only from an oracle
    registry member (either registry); another sender's answer is dropped and
    the sync keeps waiting. From the answer of a sender outside the registries
    for ``DEEPLOY_DEPLOYED_JOBS``, only the records that are not unversioned
    pointers (``__oracle_only_write``) are kept for the merge, and that answer
    still ends the wait; a signed pointer in it keeps its own check there.
    The namespace of an answer is the one whose index (``__hset_index``) its
    ``hkey`` has, the prefix the merge selects records by.

    Examples
    --------
    >>> self.__exec_hsync_response({"request_id": rid, "hkey": "players", "snapshot": {}}, sender="0xai_peer")
    >>> self.__pending_hsync[rid]["response"]["sender"]
    '0xai_peer'
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

    # The index hkey has (a str: it equals the pending sync's): the merge selects the records by it, so the namespace
    # rules below are applied to it.
    index = self.__hset_index(hkey)
    if self.__secret_namespace(index):
      if not self.__registry_member(sender, dauth_only=True) or not self.__registry_member(self.ee_addr, dauth_only=True):
        return
    # The key rotations are written only by dAuth servers: an answer for the namespace of this index is taken only
    # from a dAuth registry member.
    if self.__namespace_for_key(index, (NODE_KEY_ROTATIONS_HKEY,)):
      if not self.__registry_member(sender, dauth_only=True):
        return
    # The Tunnels Manager namespaces are written only by plugins that run on oracles: an answer for them is taken
    # only from an oracle registry member, so another sender's answer does not end the wait of the sync.
    if self.__namespace_for_key(index, TUNNELS_MANAGER_HKEYS) and not self.__registry_member(sender):
      return
    snapshot = data.get(self.CS_SNAPSHOT, {})
    if not isinstance(snapshot, dict):
      return
    # Unversioned deployed-job pointers are written only by plugins that run on oracles: from the answer of a sender
    # outside the registries the merge gets only the other records (a signed pointer keeps its own check there).
    if self.__namespace_for_key(index, ("DEEPLOY_DEPLOYED_JOBS",)) and not self.__registry_member(sender):
      # Drops the unversioned pointers, and the records that are not dicts, which the merge skips anyway.
      snapshot = {
        key: record for key, record in snapshot.items()
        if isinstance(record, dict) and not self.__oracle_only_write(key, record.get(self.CS_VALUE))
      }

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
      self.__exec_received_confirm(data, sender=sender)
    elif operation == self.CS_HSYNC_REQ:
      self.__exec_hsync_request(data, peers=sender)
    elif operation == self.CS_HSYNC_RESP:
      self.__exec_hsync_response(data, sender=sender)
    return
  

  
  def process(self):
    """
    Run one plugin lap: refresh the replication peers when due and send the queued writes.

    Saving is not part of the lap any more: the SerializationManager calls
    ``__maybe_flush_chain_storage`` on its own thread every ``CS_SAVE_INTERVAL`` seconds. This thread used
    to run the full-store save itself (0.26 s on an idle node, 1.3 s on a busy one for 5 MB at 0.7 CPU)
    and handled no write meanwhile. It can still wait briefly on the persistence lock while the save
    copies the records, and on the interpreter while the save serializes them.

    Every ``CS_STATS_INTERVAL`` seconds the lap also writes one ``CSTORE_STATS`` line: the counters taken
    (and reset) under the persistence lock, then logged outside it as one JSON object with sorted keys::

      CSTORE_STATS {"counts":{...},"lost_inputs":0,"period_s":300,"queue_peak":0}

    Returns
    -------
    None

    Notes
    -----
    The line is written also when nothing was counted, so a missing line means this loop is not running
    (or the wall clock, which times the interval, was set back). ``queue_peak`` is the largest input queue
    a lap found when it reached this method, after the lap took its inputs, so a full queue shows as
    ``lost_inputs``: the increase of the plugin's own count of inputs dropped at a full queue.
    """
    self.__maybe_refresh_chain_peers()
    self.__maybe_broadcast()
    # Keep the largest input queue seen since the last CSTORE_STATS line; before, it was not kept.
    self.__cs_stats_queue_peak = max(self.__cs_stats_queue_peak, self.input_queue_size)
    if self.time() - self.__cs_stats_last >= self.CS_STATS_INTERVAL:
      # Take the counters and start new ones, under the lock the counts are made under.
      with self.__persistence_lock:
        # The counts go to the new dict from here; the old one is only read below.
        counts, self.__cs_stats = self.__cs_stats, {}
      # The clock and the plugin's count of lost inputs, read once: for this line and as the start of the next one.
      now, lost = self.time(), self.lost_inputs_count
      # One JSON object with sorted keys on one line, built and logged outside the lock.
      self.P("CSTORE_STATS " + self.json_dumps({
        "period_s": round(now - self.__cs_stats_last), "counts": counts,
        "queue_peak": self.__cs_stats_queue_peak, "lost_inputs": lost - self.__cs_stats_lost,
      }, sort_keys=True))
      # The next line is measured from here.
      self.__cs_stats_last, self.__cs_stats_queue_peak, self.__cs_stats_lost = now, 0, lost
    return


  def on_close(self):
    """
    End the periodic save of this instance, then save pending chain storage changes before the plugin stops.

    The registration made in ``on_init`` is removed first, so the thread the SerializationManager keeps
    for this instance's save ends at its next check and a restarted instance registers its own. A save that
    thread had already picked can still run once after the removal, even after this method returned: the
    manager does not wait for it, and the flush checks the dirty flag before it takes the save lock. That
    late save writes the same shared store (the dict a restarted instance takes over from the shared plugin
    memory) through an atomic file replace. Every save of the replica file in this process takes the one
    class-level save lock, so a save of the restarted instance cannot be overtaken by that late save: the
    file always ends with the newest snapshot. A save that is being written at the moment of the removal
    is waited for; if it failed it left the store marked as changed, and the final save below writes it
    again.

    Returns
    -------
    None

    Raises
    ------
    KeyError
      When ``on_init`` did not reach the registration (it failed before it, or never ran).

    Notes
    -----
    The final save runs on the closing plugin's own thread, as before.
    """
    # Stop the periodic save of this instance first: its thread ends at its next check, and a restarted
    # instance registers its own save. A save that thread already picked can still run once after this
    # method returned; it writes the same shared store under the one save lock of the process, so it cannot
    # land after a newer save of the restarted instance.
    SerializationManager.instance.unregister(self.__maybe_flush_chain_storage)
    # Wait for a save the manager may be writing right now: if that write fails it marks the store dirty
    # again, and the check below must see it, or the changes since the last good save would stay unsaved.
    with self.__save_lock:
      pass
    if self.__chain_storage_dirty:
      self.__save_chain_storage()
    super().on_close()
    return

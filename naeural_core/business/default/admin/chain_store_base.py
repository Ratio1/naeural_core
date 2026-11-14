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


# The census line's digest of each record.
import hashlib
# The canonical JSON of each value the census line hashes.
import json
# Used at start-up to keep a replica file that gave no records under another name.
import os
# The hand-off's count of re-sends in flight per peer.
from collections import Counter
# Keeps one copy of each writer address that stamps carry, so the stamps of one writer share it in memory and in the
# replica file.
from sys import intern
# The resync of the signed namespaces: the random stretch of each wait between two requests, and the pick of the peers
# one request asks.
from random import random, sample
from threading import Lock, RLock
# Times each periodic save for the save back-off: its wall time on a clock no wall-clock step moves, and the CPU
# time of the save thread.
from time import monotonic, thread_time
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

  # The wave 03 switches of the store. Each is 'off' (wave 02 behaviour), 'report' (the rule runs and is counted in
  # the CSTORE_STATS line, nothing acts on it) or 'on' (the rule acts). on_init reads them once and stops the start
  # on any other value; wave 03 ships all four in 'report', and a later release changes only these defaults.
  # Ordering of writes (version stamps and deletes that stick).
  "CSTORE_ORDER" : "report",
  # How far, in seconds, a stamp may run ahead of this node's wall clock: a stamp further ahead is a future one, and
  # the node's own stamp clock is held within it.
  "CSTORE_VER_MAX_AHEAD" : 60,
  # How many days the tombstone a delete leaves is kept (0: no tombstone is kept), and how many tombstones are held at
  # most (the oldest go first): about 10 % of a production replica's records. Both apply with CSTORE_ORDER not 'off'.
  "CSTORE_TOMBSTONE_DAYS" : 7,
  # The cap, a count a later release can set from the measured rate of new tombstones.
  "CSTORE_TOMBSTONE_CAP" : 570,
  # Confirmations by key and stamp instead of echoing the value.
  "CSTORE_LEAN_CONFIRM" : "report",
  # The writer re-sends its own unconfirmed writes.
  "CSTORE_HANDOFF" : "report",
  # Seconds from a write to its first re-send (also the first gap of the doubling back-off), and how many re-sends
  # may wait for an answer per peer: numbers a later release can retune from the report counts.
  "CSTORE_HANDOFF_FIRST_S" : 300,
  # The re-sends in flight per peer.
  "CSTORE_HANDOFF_WINDOW" : 8,
  # Saves spaced out after a slow save.
  "CSTORE_SAVE_BACKOFF" : "report",
  # The longest pause, in seconds, the save back-off puts between two save starts (0 makes it pause nothing).
  "CSTORE_SAVE_MAX_GAP" : 30,
  # How many keys one sender that is neither a replication peer nor in an oracle registry may create, outside the
  # protected namespaces, between two CSTORE_STATS lines; a store that would create one more is refused until the next
  # line. 0, the default, refuses none: the keys are only counted (new_keys/<sender>), and a later release can set the
  # number from those counts.
  "CSTORE_NEW_KEYS_MAX" : 0,

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
  # A record's and a store's version stamp: [time in ms, address of the node that wrote it].
  CS_VER = "ver"
  # The prefix of the key a delete's tombstone is held under: "dl:" + the deleted key. No write may name such a key.
  CS_DEL = "dl:"
  # A refusal answer: the confirmation message with the refusal word and the receiver's stamp for the key.
  CS_REFUSED, CS_HELD_VER = "refused", "held_ver"
  # On this node's own records: the stamp of its last own store, which confirmations and refusal answers echo.
  CS_SENT_VER = "sent_ver"
  # On this node's own records: True when the write was meant for every default peer (not a secret record), so a
  # peer that joins later is added to its targets.
  CS_DEFAULT_PEERS = "default_peers"
  # The field that marks a store as a re-send of the hand-off: its receiver applies the order rule as in 'on'.
  CS_RESEND = "resend"
  # The fields of a record a namespace-sync answer carries (the merge reads no other; the confirmation fields stay).
  CS_EXPORT_FIELDS = (CS_KEY, CS_VALUE, CS_OWNER, CS_READONLY, CS_VER)
  # The most bytes one record may take on the wire, counted as a namespace-sync answer carries it (its key twice, its
  # value, owner, readonly mark and stamp) plus the token a store carries: 256 KiB, about twice the largest record
  # found on the production network when the cap was set (125 KB). A larger record is refused on every path that
  # stores one, so one record always fits one message, well below the broker's packet limit.
  CS_MAX_RECORD_BYTES = 262_144
  # The hand-off: seconds between two sweeps, re-sends per sweep, re-sends per record and peer before giving up,
  # the longest gap of the back-off, and how long before a lost peer was dropped its settlements are re-opened.
  CS_HANDOFF_INTERVAL, CS_HANDOFF_BUDGET, CS_HANDOFF_MAX_SENDS, CS_HANDOFF_MAX_GAP = 15, 32, 6, 3600
  # The look-back of a re-confirm, in seconds.
  CS_HANDOFF_LOOKBACK = 300
  # The namespaces with write rules of their own (the signed ones and the dAuth secret revisions): the write-permission
  # count leaves them out.
  CS_PROTECTED_NAMESPACES = (
    "DEEPLOY_OPERATIONS", "DEEPLOY_NODE_OPERATIONS", "DEEPLOY_DEPLOYED_JOBS", "DAUTH_JOB_SECRETS",
    "DAUTH_JOB_SECRET_REVISIONS", NODE_KEY_ROTATIONS_HKEY,
  )
  # The signed sequence or revision each signed namespace carries in its values: the first step of the order rule.
  CS_SIGNED_FIELDS = {
    "DEEPLOY_OPERATIONS": "sequence", "DEEPLOY_NODE_OPERATIONS": "sequence", "DEEPLOY_DEPLOYED_JOBS": "revision",
    "DAUTH_JOB_SECRETS": "deployment_revision",
  }
  # The namespaces the store resyncs by itself, one per request: the four with a signed field and the key rotations,
  # whose merge takes only a valid rotation proof and keeps the first one.
  CS_RESYNC_NAMESPACES = (*CS_SIGNED_FIELDS, NODE_KEY_ROTATIONS_HKEY)
  # The resync: seconds from one request to the next within a round (also how long the answers to a request are taken),
  # seconds from a round's last request to the next round's first, and how many peers one request asks.
  CS_RESYNC_GAP, CS_RESYNC_PAUSE, CS_RESYNC_PEERS = 60, 1800, 2
  # In the entry of a pending sync request the store made itself (no caller waits for it): the time until which its
  # answers are taken. A blocking sync's entry has no such field.
  CS_UNTIL = "until"
  # Seconds between two saves of a changed chain storage. The SerializationManager runs them at this cadence
  # on a thread of their own; one full-store save covers every write accepted since the previous one. That
  # thread checks once a second, so an accepted write reaches the disk after up to this cadence, plus up to
  # one second until the thread's next check, plus this store's own save (with the save back-off on, a slow
  # save stretches the cadence to up to CSTORE_SAVE_MAX_GAP: see __maybe_flush_chain_storage). A save of
  # another owner runs on its own thread and does not delay this one, but the saves compete for the
  # interpreter: on an oracle the network-monitor save (20 MB, measured at 1.5-1.9 s idle and 6.1-6.3 s busy
  # under a 0.7 CPU limit, longer on a slower host) slows this save down while both run.
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
    read). Then the counters of the ``CSTORE_STATS`` line start, the wave 03 switches (``CSTORE_ORDER``,
    ``CSTORE_LEAN_CONFIRM``, ``CSTORE_HANDOFF``, ``CSTORE_SAVE_BACKOFF``) are read once into the mode table
    every later step reads, the save back-off's ceiling ``CSTORE_SAVE_MAX_GAP``, the stamp bound
    ``CSTORE_VER_MAX_AHEAD``, the hand-off's numbers ``CSTORE_HANDOFF_FIRST_S`` and ``CSTORE_HANDOFF_WINDOW``
    and the limit of new keys per outside sender ``CSTORE_NEW_KEYS_MAX``
    are read once too, and the flush of a changed replica is registered with the
    SerializationManager, which runs it on a thread kept for this save every ``CS_SAVE_INTERVAL`` seconds, so
    this plugin's thread no longer runs the save. A switch set
    to anything but ``'off'``, ``'report'`` or ``'on'`` stops the start. When the store this instance will
    use is empty although the replica file exists, the file is renamed to
    ``object.pickle.unreadable-<UTC YYYYmmddHHMMSS>`` and a red line says so, so the first save does not
    overwrite it. Then the getter, setter and hsync functions are placed in the shared plugin memory, where
    the plugins' ``chainstore_*`` calls find them.

    Returns
    -------
    None

    Raises
    ------
    ValueError
      When a switch is not exactly ``'off'``, ``'report'`` or ``'on'`` (``'ON'``, ``True`` and ``None`` are
      refused too); the message names each such config key and its value. Also when ``CSTORE_HANDOFF`` is
      'on' while ``CSTORE_ORDER`` is not 'on', ``CSTORE_TOMBSTONE_DAYS`` or ``CSTORE_TOMBSTONE_CAP`` is 0, or
      ``CSTORE_LEAN_CONFIRM`` is 'off'. The plugin then does not start:
      its setter is never published, so a blocking ``chainstore_set``, ``chainstore_get`` or
      ``chainstore_hlist`` on this node waits for it with no timeout.
    TypeError
      When ``CSTORE_SAVE_MAX_GAP`` is not a number nor a str (``None``, a list); the start stops the same way.
      A str that is not a number raises ValueError instead; a numeric str such as ``"30"`` is taken. The same
      holds for the hand-off's numbers (``float`` and ``int``), for ``CSTORE_NEW_KEYS_MAX`` (``int``) and for
      ``CSTORE_VER_MAX_AHEAD`` and
      ``CSTORE_TOMBSTONE_DAYS`` (``float``, then milliseconds as an int): ``"60"`` and ``"7"`` are taken as 60 and
      7, ``"soon"`` raises ValueError and ``None`` TypeError.

    Notes
    -----
    The registration comes after the load, so a save can never run before the stored replica is in memory,
    and before the steps that can fail, so ``on_close`` always finds the registration it removes. The switch
    checks are among those steps. The one check of a switch against others: hand-off 'on' needs the order rule
    'on' (receivers refuse an older re-send), tombstones kept past a sweep (days and cap above 0: a re-sent older
    value cannot bring back a key deleted meanwhile) and the lean switch not 'off' (confirmations that carry a
    stamp, which prove their senders: with the same defaults on every node, lean 'off' would withdraw each peer's
    proof at its next confirmation).

    The table of the writes made with ``wait=False`` still owed a re-send starts empty, in memory only, before
    the setter that fills it is published.

    The hand-off's state starts empty, in memory only: its side table, its first sweep
    ``CSTORE_HANDOFF_FIRST_S`` after this start, the peers dropped as lost or back, and the peers proven to
    stamp their stores (none until each sends a stamped store or a stamped confirmation).

    The resync of the signed namespaces (``__maybe_resync``) starts due, at its first namespace: the first lap
    that finds a replication peer sends its first request.

    The rename is one of those steps: when it fails (the cache folder cannot be written) this method raises
    and the plugin does not start, where it used to start empty and fail at its first save. A file that
    held a legitimately empty store is renamed too; that loses nothing. A store that is empty because there
    is no file renames nothing and logs nothing new. A store taken over from a previous instance of this
    plugin in the same process is used as it is; the file is renamed only when that store is empty too.

    With the order switch not 'off', the stamp clock starts at the newest stamp held that is at most
    ``CSTORE_VER_MAX_AHEAD`` ahead of the wall clock (one walk of the replica at start), so a write after a
    restart is stamped above this node's writes from before it; with the switch 'off' it is not read.

    Examples
    --------
    >>> plugin.on_init()   # every switch 'report', the wave 03 default
    >>> plugin.on_init()   # with CSTORE_SAVE_BACKOFF set to 'ON'
    Traceback (most recent call last):
    ValueError: Chain-store switches must be 'off', 'report' or 'on', got: {'CSTORE_SAVE_BACKOFF': 'ON'}
    """
    super().on_init() # not mandatory anymore?
    
    self.P(" === ChainStoreBasicPlugin INIT")
    
    self.__chainstore_identity = "CS_MSG_{}".format(self.uuid(7))
    
    self.__ops = self.deque()
    # The local writes made with wait=False still owed a re-send (key -> [store, next re-send time, re-sends left]),
    # in memory only; created before the setter is published, as the setter fills it.
    self.__unwaited = {}
    self.__persistence_lock = RLock()
    self.__chain_storage_dirty = False
    # Changes held in memory without a dirty mark (a newer stamp on an unchanged value): no periodic save is made for
    # them alone, the save at an orderly stop is. Set before the registration, so on_close can read it.
    self.__chain_storage_unmarked = False
    
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
    # The UTC hour of the last CSTORE_CENSUS line, the start's hour at first: the census runs at the first lap after
    # each full hour, not right after start.
    self.__census_hour = int(self.time() // 3600)
    # The wave 03 switches, read once here so the code never reads them from the config again: a config change
    # acts at the next start, where it is checked again. Each CSTORE_STATS line carries this table as "modes".
    self.__cs_modes = {
      "order": self.cfg_cstore_order, "lean_confirm": self.cfg_cstore_lean_confirm,
      "handoff": self.cfg_cstore_handoff, "save_backoff": self.cfg_cstore_save_backoff,
    }
    # Monotonic time before which the save back-off defers a save: none before the first save, which is immediate.
    self.__save_resume = 0.0
    # The replica is loaded: from here the SerializationManager saves it every CS_SAVE_INTERVAL seconds when
    # it changed. A full save took this plugin's thread 0.26 s on an idle node and 1.3 s on a busy one
    # (5 MB, 0.7 CPU), during which it confirmed no write; the thread no longer runs the save. Registered
    # before anything below can fail, so on_close always finds the registration it removes.
    SerializationManager.instance.register(self.__maybe_flush_chain_storage, every=self.CS_SAVE_INTERVAL)
    # The save back-off's ceiling, read once like the switches; a value that is not a number stops the start here,
    # after the registration, so on_close still finds it. No save reads it before: the store is not changed
    # before the setter is published below.
    self.__save_max_gap = float(self.cfg_cstore_save_max_gap)
    # How far a stamp may run ahead of the wall clock, in ms, read once like the switches (not a number: the start
    # stops here). Converted with float() first, as the other numbers are: a config value that is a str was repeated
    # 1000 times by the multiplication ("60" gave a bound of 2,000 digits) instead of converted or refused. The
    # largest lead of a stamp seen since the last CSTORE_STATS line, in ms.
    self.__ver_ahead, self.__ver_lead = int(1000 * float(self.cfg_cstore_ver_max_ahead)), 0
    # The life of a tombstone in ms, read once as well, with the same conversion ("7" built a string of 86 million
    # characters before the start failed on it).
    self.__tomb_ms = int(86_400_000 * float(self.cfg_cstore_tombstone_days))
    # The cap, a count a later release can set.
    self.__tomb_cap = int(self.cfg_cstore_tombstone_cap)
    # The hand-off's first re-send delay in seconds and its window (re-sends in flight per peer), read once as well.
    self.__handoff_first = float(self.cfg_cstore_handoff_first_s)
    # The window.
    self.__handoff_window = int(self.cfg_cstore_handoff_window)
    # The limit of new keys per outside sender and CSTORE_STATS period, read once as well (0: they are only counted).
    self.__new_keys_max = int(self.cfg_cstore_new_keys_max)
    # The newest stamp of a tombstone this node removed (expired or past the cap), at most CSTORE_VER_MAX_AHEAD ahead
    # of the wall clock: no record stamped below it may come back by a later catch-up. 0 until the first removal.
    self.__tomb_horizon = 0
    # The switches set to anything but the three modes, by config key; checked after the registration, so the
    # on_close that follows a failed start still finds the registration it removes.
    bad_modes = {
      "CSTORE_" + name.upper(): mode for name, mode in self.__cs_modes.items() if mode not in ("off", "report", "on")
    }
    if bad_modes:
      # Fail loudly: the store does not start (one START FAILURE line) rather than run a rule in a mode nobody chose.
      raise ValueError(f"Chain-store switches must be 'off', 'report' or 'on', got: {bad_modes}")
    if self.__cs_modes["handoff"] == "on" and (self.__cs_modes["order"] != "on" or not self.__tomb_ms
                                               or not self.__tomb_cap or self.__cs_modes["lean_confirm"] == "off"):
      # Fail loudly as well: re-sends are safe only while receivers refuse an older one (order 'on') and deletes leave
      # tombstones that outlive a sweep (a re-sent older value cannot bring a deleted key back; a cap of 0 drops them
      # all at each sweep), and they reach only peers whose confirmations carry a stamp (lean not 'off': with the
      # fleet's lean switch 'off' every confirmation withdraws its sender's proof, and the hand-off re-sends almost
      # nothing).
      raise ValueError("Chain-store switch CSTORE_HANDOFF 'on' needs CSTORE_ORDER 'on', CSTORE_TOMBSTONE_DAYS and "
                       "CSTORE_TOMBSTONE_CAP above 0, and CSTORE_LEAN_CONFIRM not 'off'")
      
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

    # The newest stored stamp that is not a future one (more than CSTORE_VER_MAX_AHEAD ahead of the wall clock).
    limit = int(1000 * self.time()) + self.__ver_ahead
    # The node's stamp clock, in ms, changed only under the persistence lock. With the order switch 'off' no stamp is
    # read or made; else it starts at that stamp, so a write after a restart is stamped above the node's own writes
    # made before it, also when its clock had been pushed ahead by a peer's stamp.
    self.__ver_clock = 0 if self.__cs_modes["order"] == "off" else max([
      record[self.CS_VER][0] for record in self.__chain_storage.values()
      if isinstance(record, dict) and record.get(self.CS_VER) is not None and record[self.CS_VER][0] <= limit
    ] or [0])
    if self.__cs_modes["order"] != "off":
      # A tombstone whose key is held again (rewritten while the node ran the previous release, which does not see
      # tombstones) is dropped, so a key and its tombstone are never both held; it does not raise the horizon.
      for key in [key for key, record in self.__chain_storage.items() if key.startswith(self.CS_DEL)
                  and record.get(self.CS_VALUE) is None and key[len(self.CS_DEL):] in self.__chain_storage]:
        # Removed from memory; the file keeps it until the next save, and the next start drops it again.
        del self.__chain_storage[key]

    memory[self.CS_STORAGE_MEM] = self.__chain_storage
    memory[self.CS_GETTER] = self._get_value
    memory[self.CS_SETTER] = self._set_value
    memory[self.CS_HSYNC] = self._hsync
    
    # The hand-off's side table (storage key -> (record, {peer: [re-sends, next due time or None once given up]}),
    # memory only), the time of its first sweep (CSTORE_HANDOFF_FIRST_S after start: the sweep after the first refresh,
    # which makes every peer join, scans every own record's lists, and is kept out of the boot; no peer is proven
    # before it sends a stamped message anyway) and the pairs left pending by the last sweep.
    self.__handoff, self.__handoff_next, self.__handoff_pending = {}, self.time() + self.__handoff_first, 0
    # Peers dropped from the replication peers as lost (address -> when), and peers that joined or came back since the
    # last sweep (address -> when they were dropped as lost, or None); the first refresh below makes every peer join.
    self.__peer_left, self.__peer_back = {}, {}
    # The peers whose last store or confirmation carried a stamp, since they last joined or left the replication peers
    # (nodes of this release with the order switch not 'off'): the only ones a re-send goes to. A target never in the
    # replication peers is never seen joining or leaving: only an unstamped message withdraws its proof.
    self.__stamped_peers = set()
    self.__last_chain_peers_refresh = 0
    self.__chain_peers = []
    self.__pending_hsync = {}
    # The resync of the signed namespaces: the time of its next request (0: the first lap that finds a replication
    # peer) and the turn of the namespace it asks then, in memory only.
    self.__resync_next, self.__resync_turn = 0, 0
    self.__maybe_refresh_chain_peers()
    return
  

  def __cs_count(self, event, key=None, origin=None, n=1):
    """
    Count one event for the next ``CSTORE_STATS`` line.

    The one counting helper of the line: a count of the whole store goes under the event's own name (for
    example ``saves``), a count of one write under ``<bucket>/<origin>/<event>``, where the bucket is the
    first 14 characters of an ``hs:`` key (the prefix shared by every field of one hash namespace) or
    ``plain``. The count is made under the persistence lock, like every count of the line, and ``process``
    takes and resets the counters at each line.

    Parameters
    ----------
    event : str
      What happened, for example ``"saves"`` or ``"changed"``.
    key : str, optional
      The composed chain-store key the event is about; None (the default) for a count of the whole store.
      It must be a str when given (a key that came from the network is checked before it reaches here).
    origin : str, optional
      Where the write came from, ``"local"`` or ``"remote"``; used only with ``key``.
    n : int, optional
      How much to add, 1 by default (a sum such as milliseconds can add more).

    Returns
    -------
    None

    Notes
    -----
    The lock is the re-entrant persistence lock, so a caller that already holds it can count. The cost is a
    slice, a format, a lock and one dict update per call.

    Examples
    --------
    >>> self.__cs_count("saves")                                           # counts["saves"] += 1
    >>> self.__cs_count("changed", "hs:0123456789:YWxwaGE=", "remote")     # counts["hs:0123456789:/remote/changed"]
    >>> self.__cs_count("changed", "plain-key", "local")                   # counts["plain/local/changed"]
    """
    if key is not None:
      # The label of one write: its namespace bucket, its origin and the event.
      event = "{}/{}/{}".format(key[:14] if key.startswith("hs:") else "plain", origin, event)
    # Under the persistence lock, as every count of the line; process() swaps the dict under the same lock.
    with self.__persistence_lock:
      # A plain increment of one counter of a small dict (one label per bucket, origin and event).
      self.__cs_stats[event] = self.__cs_stats.get(event, 0) + n


  def __sweep_tombstones(self):
    """
    Remove the tombstones that expired or are past the cap, and count those held, once per ``CSTORE_STATS`` line.

    A tombstone is a record under ``"dl:" + key`` with value None and a stamp (any other ``"dl:"`` record is
    left alone). With the order switch 'off', or ``CSTORE_TOMBSTONE_DAYS`` 0, every tombstone is removed; else
    those stamped more than ``CSTORE_TOMBSTONE_DAYS`` ago, and the oldest beyond ``CSTORE_TOMBSTONE_CAP``. Each
    removal raises the horizon (the newest removed stamp that is at most ``CSTORE_VER_MAX_AHEAD`` ahead of the
    wall clock) in the same hold of the persistence lock. The removed ones are counted as
    ``tombstones_dropped`` and mark the replica for a save; those left as ``tombstones_held``.

    Returns
    -------
    None

    Notes
    -----
    Runs on the plugin thread from ``process``, every 300 s, in every mode, so a switch back to 'off' leaves no
    tombstone after the next line. One walk of the replica and one sort of its tombstones under the lock (1.2 to
    1.4 ms of CPU for 6,000 records with 570 tombstones, measured on the node image's interpreter). A removed
    tombstone no longer stops an older copy of its key: a peer away longer than the tombstone's life, or past the
    cap, can bring the key back.

    Examples
    --------
    >>> self.__sweep_tombstones()   # 600 tombstones, cap 570, none expired: the 30 oldest removed
    """
    # The wall clock in ms.
    now = int(1000 * self.time())
    # The removals and the horizon under one hold of the lock, so no save holds one without the other.
    with self.__persistence_lock:
      # The tombstones held, by stamp, oldest first.
      tombs = sorted((record[self.CS_VER], key) for key, record in self.__chain_storage.items()
                     if key.startswith(self.CS_DEL) and record.get(self.CS_VALUE) is None
                     and isinstance(record.get(self.CS_VER), list))
      # How many of the oldest go: all with the order switch 'off' or no tombstone life; else the expired ones and
      # those beyond the cap, whichever is more.
      cut = len(tombs) if self.__cs_modes["order"] == "off" or not self.__tomb_ms else max(
        len(tombs) - self.__tomb_cap, sum(ver[0] < now - self.__tomb_ms for ver, _ in tombs))
      # The oldest ones, up to the cut.
      for ver, key in tombs[:cut]:
        # Removed from the replica.
        del self.__chain_storage[key]
        if ver[0] <= now + self.__ver_ahead:
          # The horizon rises with it, in the same lock hold; a future stamp is left out of it.
          self.__tomb_horizon = max(self.__tomb_horizon, ver[0])
    if cut:
      # Saved by the next flush; counted for the line.
      self.__chain_storage_dirty = True
      # How many went.
      self.__cs_count("tombstones_dropped", n=cut)
    if len(tombs) > cut:
      # How many are held, for the line this sweep precedes.
      self.__cs_count("tombstones_held", n=len(tombs) - cut)


  def __maybe_handoff(self):
    """
    The hand-off: re-send this node's own writes to the target peers that have not settled them, when due.

    Every ``CS_HANDOFF_INTERVAL`` (15) seconds, the first time ``CSTORE_HANDOFF_FIRST_S`` after start, the sweep
    walks the replica for this node's own records whose stamp is still the one of their last own store
    (``ver`` equal to ``sent_ver``; the tombstones of local deletes included). A pair is one such record and
    one of its targets; it is settled when the peer confirmed it or refused it with an answer (the peer is in
    ``confirmed_peers``) or when the sweep gave up on it. For each pair not settled:

    - it is first due ``CSTORE_HANDOFF_FIRST_S`` after the write, then after a gap that doubles from
      ``CSTORE_HANDOFF_FIRST_S`` up to ``CS_HANDOFF_MAX_GAP`` (3600 s): with the defaults the re-sends go at the
      record's age of 300, 600, 1200, 2400, 4800 and 8400 s;
    - a due pair whose peer is offline, or is not proven to stamp its stores, is skipped and uses nothing: an
      oracle away for any length gets it once back. A peer is proven by its last store or confirmation to this
      node carrying a well-formed stamp, and loses the proof when it leaves or joins the replication peers (it
      may come back on the previous release);
    - a due pair of a dAuth secret record is skipped unless this node is a registry member and the peer a
      dAuth registry member, as the write itself requires; so is a due pair of a secret revision's payload once
      this node holds the revision's revocation marker (the write rule takes only the redacted erasure then),
      while the marker and the erasure are still re-sent;
    - a due pair that had ``CS_HANDOFF_MAX_SENDS`` (6) re-sends is given up (``handoff/gave_up/<peer>``);
    - in 'on' only, at most ``CSTORE_HANDOFF_WINDOW`` (8) re-sends wait for their next due time per peer (1 when
      this node is in neither registry, a worker); a due pair beyond it waits (``handoff/window_full/<peer>``,
      counted for each pair held back, at each sweep). The registry is read for it once per sweep, only when a due
      pair of a reachable peer reaches this check, so a sweep with nothing to re-send reads no registry;
    - at most ``CS_HANDOFF_BUDGET`` (32) re-sends per sweep.

    A re-send counts ``handoff/send/<peer>`` (the first of its pair) or ``handoff/resend/<peer>``. A peer that
    joined since the last sweep becomes a target of every own record meant for all default peers that does
    not target it (widening); a peer that comes back after the peer refresh dropped it as lost is unsettled
    on the records it settled that were written at most ``CS_HANDOFF_LOOKBACK`` (300 s) before it was dropped
    (re-confirm), as their copy may have died with it.

    - ``'off'``: nothing runs and nothing is kept.
    - ``'report'``: everything above but the window runs on an in-memory table only: no store is sent, no
      stored record changes (a give-up, a widening and a re-confirm live in the table). No re-send is answered
      here, and in 'on' a healthy peer frees its window within seconds, so each due pair is counted at its due
      time, limited only by the budget per sweep, and a late real confirmation of a counted pair is
      ``answered``.
    - ``'on'``: each record goes as one store to its due peers, with the record's own stamp (never a new one)
      and ``resend`` set; widening and re-confirm change the record's ``target_peers`` and
      ``confirmed_peers``, and a give-up adds the peer to ``confirmed_peers``.

    Returns
    -------
    None

    Notes
    -----
    Runs on the plugin thread from ``process``, before the queued stores are sent, so a re-send leaves on the
    same lap. The walk reads a list of the replica's items without the persistence lock (a record is stored
    whole, and its lists change only on this thread); the changes of stored lists in 'on' take the lock. The
    table (storage key -> (record, {peer: [re-sends, next due time, or None once given up in 'report']})) is
    used only on this thread, by this sweep and by ``__exec_received_confirm``, which ends a pair on its
    answer; an entry whose record was replaced is dropped at the next sweep, so the table holds only pairs
    of own records still held. The sweep builds the new table aside and puts it in place when its walk ends:
    a raise in the walk leaves the last table (with the changes made to its pairs before the raise), so no
    pair starts its back-off again. Nothing here marks the replica for a save. The pairs left pending are kept
    for the ``handoff_pending`` field of the next ``CSTORE_STATS`` line.

    A record's age is read from its own stamp, which this node's clock keeps within ``CSTORE_VER_MAX_AHEAD`` of
    its wall clock, so a re-send is never early and at most that much late. Cost: one dict read and one
    comparison per record that is not an own record, two dict reads, a table lookup and two lengths more per
    settled own record, and a few scans of its targets per own record with a pair pending; a sweep after a peer
    joined or came back scans every own record's lists once.

    Examples
    --------
    >>> self.__maybe_handoff()   # 'on', a record written 300 s ago and confirmed by peer1 only: re-sent to peer2
    """
    # The hand-off's mode, read once at start.
    mode = self.__cs_modes["handoff"]
    # The wall clock in seconds: the due times are on it.
    now = self.time()
    if mode == "off" or now < self.__handoff_next:
      return
    # The next sweep.
    self.__handoff_next = now + self.CS_HANDOFF_INTERVAL
    # The peers that joined or came back since the last sweep (with when they were dropped as lost, or None), for this
    # sweep alone.
    back, self.__peer_back = self.__peer_back, {}
    # The last sweep's table, and the one this sweep builds from the entries of the own records still held; the last
    # one stays in place until the walk ends, so a raise in the walk does not restart every pair's back-off.
    table, new = self.__handoff, {}
    # The re-sends in flight per peer: pairs re-sent whose next due time has not come.
    flight = Counter(peer for _, pairs in table.values() for peer, (sends, due) in pairs.items()
                     if sends and due is not None and due > now)
    # The re-send window per peer, read from the registry only when a due pair reaches its check in 'on' below, so a
    # sweep with nothing to re-send reads no registry (with the registry cache expired, a hung RPC holds each read).
    window = None
    # Peers checked this sweep (address -> may be sent to), the re-sends left to it, and the pairs left pending.
    reachable, budget, pending = {}, self.CS_HANDOFF_BUDGET, 0
    # Each record of a copy of the replica's items, read without the lock.
    for key, record in list(self.__chain_storage.items()):
      # Only an own record whose stamp is its last own store's: a remote record or copy has no sent_ver, a legacy one
      # no stamp, and a peer's identical write that raised the stamp is delivered by that peer.
      if record.get(self.CS_SENT_VER) is None or record.get(self.CS_VER) != record[self.CS_SENT_VER]:
        continue
      # Its targets and the peers that settled it (stored lists, changed here only in 'on').
      targets, settled = record["target_peers"], record["confirmed_peers"]
      # Its entry from the last sweep, kept only for the same record (a rewrite starts afresh).
      owner_record, pairs = table.get(key, (None, {}))
      # Its pairs.
      pairs = pairs if owner_record is record else {}
      # The common case, at the cost of two lengths: a record every target settled (the settled peers are always
      # among the targets), with no pair in the table and no peer that joined or came back.
      if not (pairs or back) and len(settled) >= len(targets):
        continue
      # When it was written, in seconds, from its stamp.
      sent_at = record[self.CS_SENT_VER][0] / 1000
      # Widening and re-confirm: a joined peer the record meant for every default peer does not target, and a peer
      # back after being lost that settled the record written at most CS_HANDOFF_LOOKBACK before it was dropped.
      opened = [peer for peer, left in back.items() if (record[self.CS_DEFAULT_PEERS] and peer not in targets) or (
        left is not None and peer in settled and sent_at >= left - self.CS_HANDOFF_LOOKBACK)]
      if opened and mode == "on":
        # The stored lists change only in 'on', under the lock the saves copy them under.
        with self.__persistence_lock:
          # A joined peer becomes a target (its confirmation then counts).
          targets.extend(peer for peer in opened if peer not in targets)
          # A returned peer is unsettled.
          settled[:] = [peer for peer in settled if peer not in opened]
      # Each peer of a pair not settled: a target not settled, or a peer just opened.
      for peer in [peer for peer in targets if peer not in settled] + opened:
        # Each pair not settled, first due CSTORE_HANDOFF_FIRST_S after the write (in 'report' the opened ones live
        # only here).
        pairs.setdefault(peer, [0, sent_at + self.__handoff_first])
      # The peers this sweep re-sends the record to.
      due_peers = []
      # Each pair, over a copy, so a give-up in 'on' can drop it.
      for peer, pair in list(pairs.items()):
        if pair[1] is None or pair[1] > now:
          continue
        if peer not in reachable:
          # Proven to stamp its stores, and online: in the replication peers, or as NetMon reports another target.
          reachable[peer] = peer in self.__stamped_peers and (
            peer in self.__chain_peers or self.__netmon_node_is_online_for_control(peer))
        # A dAuth secret goes only from a registry member to a dAuth registry member, checked again before each re-send.
        if not reachable[peer] or (self.__secret_namespace(record[self.CS_KEY]) and not (
            self.__registry_member(self.ee_addr) and self.__registry_member(peer, dauth_only=True))):
          continue
        # As the write rule: a secret revision's payload (not its marker, not its redacted erasure) is not re-sent once
        # this node holds the revision's revocation marker; the pair stays pending, and the marker and erasure still go.
        if record[self.CS_VALUE] is not None and self.__revoked_secret(record[self.CS_KEY]) and not (
            self.__erased_secret(record[self.CS_KEY], record[self.CS_VALUE]) or self.base64_to_str(
              record[self.CS_KEY][len(self.__hset_index("DAUTH_JOB_SECRET_REVISIONS")):], url_safe=True
            ).endswith("|revoked")):
          continue
        if pair[0] >= self.CS_HANDOFF_MAX_SENDS:
          # Given up once its last re-send's wait is over and the peer is reachable, counted per peer.
          self.__cs_count("handoff/gave_up/" + peer)
          if mode == "on":
            # Settled in the record, under the lock.
            with self.__persistence_lock:
              # The peer settled by the give-up.
              settled.append(peer)
            # The table forgets it.
            del pairs[peer]
          else:
            # In 'report' settled in the table only.
            pair[1] = None
          continue
        if mode == "on":
          if window is None:
            # A writer in neither registry (a worker) keeps one re-send in flight per peer: some 50 workers with 8
            # each would land on an oracle as it restarts. Read once per sweep, at the first pair that needs it.
            window = self.__handoff_window if self.__registry_member(self.ee_addr) else 1
          if flight[peer] >= window:
            # The window of this peer is full, counted per peer: it names a slow or deaf peer. Only in 'on', where a
            # healthy peer answers a re-send within seconds; in 'report' nothing is answered, so a window would hold
            # a healthy peer's first sends back for hours.
            self.__cs_count("handoff/window_full/" + peer)
            continue
        if not budget:
          continue
        # One more re-send; the next is due after CSTORE_HANDOFF_FIRST_S doubled per earlier re-send, at most
        # CS_HANDOFF_MAX_GAP.
        pair[0], pair[1] = pair[0] + 1, now + min(self.__handoff_first * 2 ** pair[0], self.CS_HANDOFF_MAX_GAP)
        # In flight now, and one re-send less for this sweep.
        flight[peer], budget = flight[peer] + 1, budget - 1
        # Sent with the record's store.
        due_peers.append(peer)
        # Counted per peer: the first re-send of a pair, or a later one.
        self.__cs_count(("handoff/send/" if pair[0] == 1 else "handoff/resend/") + peer)
      if due_peers and mode == "on":
        # One store to the due peers alone, as the record holds it, with its own stamp and the re-send mark.
        self.__ops.append({
          self.CS_OP: self.CS_STORE, self.CS_KEY: record[self.CS_KEY], self.CS_VALUE: record[self.CS_VALUE],
          self.CS_OWNER: record[self.CS_OWNER], self.CS_TOKEN: record[self.CS_TOKEN],
          self.CS_READONLY: record[self.CS_READONLY], self.CS_PEERS: due_peers, self.CS_INCLUDE_DEFAULT_PEERS: False,
          self.CS_VER: record[self.CS_SENT_VER], self.CS_RESEND: True,
        })
      if pairs:
        # Kept for the next sweep.
        new[key] = (record, pairs)
        # Its pairs not given up.
        pending += sum(pair[1] is not None for pair in pairs.values())
    # The new table, and its pending pairs for the next CSTORE_STATS line, once the walk ended.
    self.__handoff, self.__handoff_pending = new, pending


  def __maybe_resync(self):
    """
    The resync: ask two peers for the next signed namespace when due, and forget the store's own requests whose
    wait is over.

    An oracle that was away gets the records of the signed namespaces it missed from the store itself, not only
    from its callers' periodic syncs. A round asks the namespaces of ``CS_RESYNC_NAMESPACES`` in turn
    (``DEEPLOY_OPERATIONS``, ``DEEPLOY_NODE_OPERATIONS``, ``DEEPLOY_DEPLOYED_JOBS``, ``DAUTH_JOB_SECRETS``,
    ``NODE_KEY_ROTATIONS``), one request every ``CS_RESYNC_GAP`` (60) seconds; the next round starts
    ``CS_RESYNC_PAUSE`` (1800) seconds after a round's last request. Each wait is stretched or shortened by up to a
    quarter at random, so the oracles of a network do not ask together. The first request goes at the first lap
    that finds a replication peer.

    One request is the namespace sync's own message (``SHSYNC_REQ`` with a request id and the namespace), sent to
    ``CS_RESYNC_PEERS`` (2) peers picked at random among the replication peers that are registry members, so a
    node of any release answers it as it answers a caller's sync:

    - only a registry member asks (an oracle or a dAuth server); ``DAUTH_JOB_SECRETS`` only a dAuth registry
      member;
    - ``DAUTH_JOB_SECRETS`` and ``NODE_KEY_ROTATIONS``, which dAuth servers write, are asked of dAuth registry
      members only (another node's answer for them is not taken);
    - a turn with nobody to ask, or on a node that may not ask, passes without a request.

    No caller waits for the answers: the request is registered with ``until``, the time its answers are taken
    until (``CS_RESYNC_GAP`` after it), and ``__exec_hsync_response`` merges the answer of each asked peer when
    it arrives, not only the first. Each request counts ``<bucket>/resync/asked`` under its namespace's bucket.

    Returns
    -------
    None

    Notes
    -----
    Runs on the plugin thread from ``process``, every lap, after the peer refresh. Every lap it first drops each
    pending request whose ``until`` has passed, whoever registered it, so a later answer is not taken; the entry of
    a blocking sync (``_hsync``) has no ``until`` and is left to its caller. A lap with nothing pending and no
    request due costs one copy of the (empty) pending table and two comparisons (under 1 microsecond, measured
    on the node image's interpreter under a 0.35 CPU limit). The registries are read once for this node and
    once per replication peer at each request, never between requests. Merging one answer of 1,000 signed
    operations (2.1 MB) that all equal the held ones took 82 ms of CPU and 214 ms of wall time under the same
    limit, with no signature check.

    It has no switch and acts in every mode of the order switch: the merge it feeds applies the rules a caller's
    sync gets, and for these namespaces a lower signed sequence or revision is refused in every mode. A node of
    the previous release never asks, and answers these requests as today.

    Examples
    --------
    >>> self.__maybe_resync()   # a round's first lap on an oracle: DEEPLOY_OPERATIONS asked of two registry members
    """
    # The wall clock the requests and their waits are timed on.
    now = self.time()
    # Each pending request the store made itself whose wait is over (a blocking sync's entry has no such time).
    for request_id in [name for name, entry in list(self.__pending_hsync.items())
                       if entry.get(self.CS_UNTIL, now) < now]:
      # Forgotten: an answer that comes later is not taken.
      del self.__pending_hsync[request_id]
    if now < self.__resync_next or not self.__chain_peers:
      return
    # The namespace whose turn it is.
    hkey = self.CS_RESYNC_NAMESPACES[self.__resync_turn]
    # The next namespace's turn.
    self.__resync_turn = (self.__resync_turn + 1) % len(self.CS_RESYNC_NAMESPACES)
    # When it is asked: one gap later within a round, one pause later once the round ended (the turn is back at the
    # first namespace), stretched or shortened by up to a quarter at random.
    self.__resync_next = now + (self.CS_RESYNC_GAP if self.__resync_turn else self.CS_RESYNC_PAUSE) * (
      0.75 + random() / 2)
    if not self.__registry_member(self.ee_addr, dauth_only=hkey == "DAUTH_JOB_SECRETS"):
      return
    # The two namespaces dAuth servers write are answered only by dAuth registry members.
    dauth = hkey in ("DAUTH_JOB_SECRETS", NODE_KEY_ROTATIONS_HKEY)
    # Who may be asked: the registry members among the replication peers (dAuth members for those two namespaces).
    peers = [peer for peer in self.__chain_peers if self.__registry_member(peer, dauth_only=dauth)]
    if not peers:
      return
    # Up to CS_RESYNC_PEERS of them, picked at random, so the requests spread over the peers.
    peers = sample(peers, min(len(peers), self.CS_RESYNC_PEERS))
    # The request's id, which each answer echoes.
    request_id = self.uuid()
    # Registered as the store's own request: the peers whose answers are taken, and until when; no caller reads
    # the response slot a blocking sync fills.
    self.__pending_hsync[request_id] = {
      self.CS_HKEY: hkey, self.CS_PEERS: peers, self.CS_RESPONSE: None, self.CS_UNTIL: now + self.CS_RESYNC_GAP,
    }
    # The request a caller's sync sends, to those peers alone.
    self.__send_data_to_chain_peers({self.CS_DATA: {
      self.CS_OP: self.CS_HSYNC_REQ, self.CS_REQUEST_ID: request_id, self.CS_HKEY: hkey,
    }}, peers=peers, include_default_peers=False)
    # Counted under the namespace's bucket, to read against the answers that came.
    self.__cs_count("asked", self.__hset_index(hkey), "resync")


  def __log_census(self):
    """
    Write the hourly ``CSTORE_CENSUS`` line: per namespace bucket, what the replica holds and a digest of it.

    One line, one JSON object with sorted keys, a row per bucket (the first 14 characters of an ``hs:`` key, the
    prefix of one hash namespace, or ``plain``)::

      CSTORE_CENSUS {"hs:<hash>:":[records,unstamped,tombstones,readonly,size,"digest",unencodable],"plain":[...]}

    - ``records``: live records (a tombstone is not one); ``unstamped``: those without a stamp (written before
      stamps, or by a node of the previous release); ``readonly``: those marked readonly;
    - ``tombstones``: the tombstones of deleted keys, counted in the bucket of the key they deleted;
    - ``size``: the characters of the live values' canonical JSON (sorted keys), made from each value as the
      wire delivers it: a copy from a peer is already in that form, and this node's own record goes through the
      wire's encoder and back first (numpy and datetime values, int dict keys), so a writer and its receivers
      give equal records the same text;
    - ``digest``: 8 hex digits, the XOR of a 4-byte BLAKE2b hash of each live record: of its key and its
      value's canonical JSON, or, for a record with a token or in ``DAUTH_JOB_SECRETS`` or
      ``DAUTH_JOB_SECRET_REVISIONS``, of its key and its stamp, so no value of those records is hashed into a log
      line (other values are, the tunnel secrets included). For those records the digest compares keys and
      stamps only: two unstamped records, or two with the same stamp, give the same digest whatever their
      values (only ``size`` still reflects the values), and an equal value whose stamp was raised on one
      replica gives a different digest;
    - ``unencodable``: live records whose value cannot be put in canonical form (a type or a dict key no encoder
      takes, which the wire could not send either, as an old own record can hold): counted here and left out of
      ``size`` and ``digest``, so the line is still written.

    Two replicas that hold the same live records give the same row whatever order the records arrived in and
    whatever their bookkeeping fields (confirmations, peer lists, owner); tombstones, which each node sweeps on
    its own schedule, are left out of the digest. A key under ``hs:`` whose 14th character is not ``:`` (a
    namespace's keys start with ``hs:``, 10 hex digits and ``:``) belongs to no namespace that syncs and is left out.

    Returns
    -------
    None

    Notes
    -----
    Runs on the plugin thread from ``process``, at the first lap after each full UTC hour, so every node takes it
    within a lap of the same moment and the digests compare one moment's state, except for writes still in
    transit then. It walks a list copy of the replica's items without the persistence lock: records are replaced,
    not changed in value, so each one read is a whole record, and every field of its row is read from that record,
    never again by key, so a record another thread deletes or replaces during the walk is still read as one record
    (before, its token was read from the live replica, and the value of a token record deleted meanwhile was
    hashed). The line holds counts, sizes and digests only: no key and no value. Cost: one JSON encoding and one
    hash per live record, and the wire encoder's round trip for each own record, once an hour.

    It does not raise on what a replica can hold: a record of an older shape (no ``readonly`` or ``token``
    field) is read as not readonly and without a token, a key decoded from a JSON escape that holds a lone
    surrogate is hashed as it is, and a value that cannot be encoded (a type or dict key no encoder takes, a
    circular or too deeply nested value) is counted as ``unencodable``. Before, any of these raised out of
    ``process`` at every census, and the plugin loop's exception back-off grew with each raise.

    Examples
    --------
    >>> self.__log_census()
    CSTORE_CENSUS {"hs:afc0d772a2:":[2,0,1,0,6,"5c1d02a9",0],"plain":[1,1,0,0,3,"0e7f3b10",0]}
    """
    # The bucket prefixes of the two dAuth secret namespaces, whose values are not hashed.
    secrets = {self.__hset_index(name) for name in ("DAUTH_JOB_SECRETS", "DAUTH_JOB_SECRET_REVISIONS")}
    # Per bucket: records, unstamped, tombstones, readonly, size, digest (an int), and the records left out of the size
    # and the digest because their value cannot be put in canonical form.
    rows = {}
    # Each record of a copy of the replica's items, read without the lock.
    for key, record in list(self.__chain_storage.items()):
      # A tombstone, counted under the key it deleted.
      tomb = key.startswith(self.CS_DEL) and record[self.CS_VALUE] is None
      # The key the record is counted under.
      name = key[len(self.CS_DEL):] if tomb else key
      if name.startswith("hs:") and name[13:14] != ":":
        continue
      # Its bucket's row.
      row = rows.setdefault(name[:14] if name.startswith("hs:") else "plain", [0, 0, 0, 0, 0, 0, 0])
      if tomb:
        # One more tombstone; not in the digest.
        row[2] += 1
        continue
      # One more record, unstamped or not, readonly or not, read from the record walked, never again by key from the
      # live replica, where another thread may have replaced or deleted it since the copy (an older record shape has
      # no readonly field: not readonly).
      row[0], row[1], row[3] = (row[0] + 1, row[1] + (record.get(self.CS_VER) is None),
                                row[3] + bool(record.get(self.CS_READONLY)))
      try:
        # The value's canonical JSON, made from the value as its receivers hold it: a copy from a peer (confirms -1,
        # read from the record walked) is already in the wire's form, and this node's own record goes through the
        # wire's encoder (unsorted: the sort is done once, here) and back first (numpy and datetime values, int dict
        # keys), so a writer and its receivers give equal records one text.
        text = json.dumps(record[self.CS_VALUE] if record.get(self.CS_CONFIRMATIONS) == -1 else
                          self.json_loads(self.json_dumps(record[self.CS_VALUE], sort_keys=False)), sort_keys=True)
      except (TypeError, ValueError, RecursionError):
        # A value of a type or with a dict key no encoder takes (TypeError), or circular (ValueError) or nested too
        # deep (RecursionError): the wire could not send it either, but an old own record can hold it. Counted in the
        # last column and left out of the size and the digest; the line is written.
        row[6] += 1
        continue
      # Its size.
      row[4] += len(text)
      # The hashed text: the key and the value, or the key and the stamp for a record with a token or in a dAuth
      # secret namespace.
      hashed = name + "\0" + (
        # The token read from the record walked (an older record shape has none): a token record deleted or replaced
        # since the copy still has its stamp hashed, never its value.
        str(record.get(self.CS_VER)) if record.get(self.CS_TOKEN) is not None or name[:14] in secrets else text)
      # XOR-ed into the digest, which makes it independent of the records' order; a key decoded from a JSON escape
      # can hold a lone surrogate, which strict UTF-8 refuses, so it is encoded as is (other keys give the same bytes).
      row[5] ^= int.from_bytes(hashlib.blake2b(hashed.encode("utf-8", "surrogatepass"), digest_size=4).digest(), "big")
    # One line, sorted keys, the digest as 8 hex digits, then the count of records left out of it.
    self.P("CSTORE_CENSUS " + self.json_dumps(
      {bucket: row[:5] + ["{:08x}".format(row[5]), row[6]] for bucket, row in rows.items()}, sort_keys=True))


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
    Refresh the default replication peers from the blockchain engine's whitelist, when due.

    This method refreshes the chain peers list from the network using the whitelist generated
    by the blockchain engine. This means it will allow broadcasting the local keys to all the
    oracle nodes in the network as well as the manually added nodes. This pretty much covers 
    the "private" part of the ChainStore.
    
    However the chain storage should be also accessible to all the nodes in the network so that 
    they can ALL the values stored in the chain storage publicly
        
    A whitelisted node is kept when NetMon reports it running (``__netmon_node_is_online_for_control``),
    except the node itself, which is never a peer: its own copy of a store was dropped on arrival and never
    confirmed. A seed whose whitelist holds only itself therefore has no default peer, and its writes fail at
    once (``refused_no_peers``) instead of waiting 3 x 10 s for a confirmation that never comes.

    For the hand-off (``__maybe_handoff``), each refresh also notes which peers left and which joined: a peer
    that left while NetMon reports it ``LOST STATUS`` is recorded with the time it was dropped, and a peer that
    joins (every peer at the first refresh after start) or comes back is queued for the next sweep with that
    time, or None when it was not dropped as lost.

    Returns
    -------
    None

    Notes
    -----
    Runs at most once per ``CHAIN_PEERS_REFRESH_INTERVAL`` seconds (from ``process`` and once from
    ``on_init``). The own address is compared before NetMon is asked, so NetMon is never asked about this
    node; both sides carry the address prefix (``get_whitelist(with_prefix=True)`` and ``ee_addr``). On an
    oracle, whose whitelist holds itself, a write is no longer sent to itself either.

    NetMon is asked once more only for a peer that left. An orderly stop (status ``SHUTDOWN``) or a peer taken
    off the whitelist is not recorded as lost: it lost nothing, so its settlements are not re-opened. A peer
    that comes back within one refresh interval is seen neither leaving nor joining.

    Every peer that left, lost or not, and every peer that joined also loses its proof that it stamps its stores:
    it may come back on the previous release, which applies a re-sent delete in arrival order, so the hand-off
    re-sends nothing to it until its next stamped store or stamped confirmation processed after it joined (a
    stamped message it sent before it stopped, processed after the refresh that dropped it, proves it only until
    it joins again). A peer that comes back within one refresh interval keeps its proof until it sends a store or
    confirmation without a stamp.

    Examples
    --------
    >>> self.__maybe_refresh_chain_peers()   # whitelist [self, peer-1, peer-2], both peers running
    >>> self.__get_target_peers()
    ['peer-1', 'peer-2']
    """
    if (self.time() - self.__last_chain_peers_refresh) > self.cfg_chain_peers_refresh_interval:
      _chain_peers = self.bc.get_whitelist(with_prefix=True)
      # ChainStore routing is not consensus evidence; on normal nodes the
      # control liveness check may use fresh oracle summaries when peer
      # heartbeats are intentionally not received.
      # The list before this refresh, to see which peers left and which joined.
      old, self.__chain_peers = self.__chain_peers, [
        # The node itself is left out first, before NetMon is asked: it never confirms its own writes, so with it
        # in the list a seed holding only itself waited 3 x 10 s per write.
        peer for peer in _chain_peers if peer != self.ee_addr and self.__netmon_node_is_online_for_control(peer)
      ]
      # A peer that left or joined loses its proof that it stamps its stores, whatever the reason: it may come back on
      # the previous release (a rollback), so it proves it again before the hand-off re-sends anything to it. Dropped
      # on a join too, as a stamped message it sent before it stopped can be processed after the refresh that dropped
      # it (an input queue that lags) and would prove it again while it was away.
      self.__stamped_peers.difference_update(set(old) ^ set(self.__chain_peers))
      # Each peer of the list before the refresh.
      for peer in old:
        # A peer dropped because NetMon reports it lost (a crash or a cut, not an orderly stop or a whitelist change),
        # asked only for the peers that left: when it comes back, the hand-off re-opens its recent settlements.
        if peer not in self.__chain_peers and self.netmon.network_node_simple_status(
            peer, last_exception_check_time_minutes=0) == "LOST STATUS":
          # When it was dropped.
          self.__peer_left[peer] = self.time()
      # Each peer of the new list.
      for peer in self.__chain_peers:
        if peer not in old:
          # A peer that joined or came back, for the next hand-off sweep, with the time it was dropped as lost (None
          # when it was not).
          self.__peer_back[peer] = self.__peer_left.pop(peer, None)
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
    is counted as ``saves`` for the ``CSTORE_STATS`` line, through ``__cs_count``.

    Returns
    -------
    None

    Notes
    -----
    A failed write marks the store dirty again, so the next flush retries it. A failed write is counted too:
    the count is taken where the dirty flag is cleared, before the write.

    Examples
    --------
    >>> self.__save_chain_storage()   # from the periodic flush, or from on_close at an orderly stop
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
        # The copy holds every change made so far, those held without a dirty mark (newer stamps) included.
        self.__chain_storage_dirty = self.__chain_storage_unmarked = False
        # Count this save for the CSTORE_STATS line, through the one counting helper (the lock it takes is the
        # one already held here, and it is re-entrant).
        self.__cs_count("saves")
      try:
        self.cacheapi_save_pickle(snapshot, verbose=False, strict=True)
      except Exception as exc:
        self.__chain_storage_dirty = True
        self.P(f" === Chain storage save failed, will retry: {exc}", color='r')
    return


  def __maybe_flush_chain_storage(self):
    """
    Save the chain storage if it changed since the last save, spacing saves out after a slow one.

    The SerializationManager calls this on its own thread every
    `CS_SAVE_INTERVAL` seconds (registered in `on_init`), so the plugin thread
    no longer runs the save. Accepted writes only mark the store dirty, so one
    full-store save covers every write made since the previous run. The same
    path retries a failed save, because a failed save marks the store dirty
    again.

    Save back-off (switch ``CSTORE_SAVE_BACKOFF``, read once at start): after a save that took d seconds of
    wall time, the next save waits until ``min(CSTORE_SAVE_MAX_GAP, 10 * d)`` seconds after that save started.
    The first save after a start is immediate, and so is the save of an orderly stop (``on_close`` saves
    directly, not through this method).

    - ``'off'``: wave 02, every call that finds the store changed saves; no clock is read, nothing new counted.
    - ``'report'``: every such call saves, as in wave 02. A call inside the pause counts ``saves_deferred`` (the
      save ``'on'`` would have skipped) and its save does not restart the pause, so the counts follow the
      ``'on'`` schedule: ``saves - saves_deferred`` is what ``'on'`` would have saved.
    - ``'on'``: a call inside the pause counts ``saves_deferred`` and returns without saving; the store stays
      changed, so the first call after the pause saves it.

    In ``'report'`` and ``'on'`` every save also adds its CPU time on the save thread, in milliseconds, to
    ``save_cpu_ms``.

    Returns
    -------
    None

    Notes
    -----
    Safe to call from another thread: `__save_chain_storage` copies the records
    under the persistence lock and orders overlapping saves with the save lock. The pause is read and set
    only here, on the one thread of this registration, so it takes no lock.

    The pause is kept on the monotonic clock, so a wall clock set back cannot hold saves back. With the
    defaults, a save of 0.26 s (an idle healthy oracle) leaves the 5 s cadence as it was; 1.3 s (a busy
    0.7-core oracle) and 1.93 s (a 0.35-core oracle under load) space the save starts about 15 s and 21 s
    apart; any save of 3 s up to 30 s, 30 to 35 s (the pause is rounded up to the manager's 5 s cadence).
    Saves then take at most 10 % of the wall time while d is 3 s or less. In ``'on'``, writes accepted after a
    save starts reach the disk at the next save, so a crash can lose those of up to ``CSTORE_SAVE_MAX_GAP`` +
    5 s (the cadence) + 1 s (the manager's check) + d, against 5 s + 1 s + d in wave 02. A failed save also
    sets the pause from its own duration and is retried after it.

    The cost in ``'report'`` and ``'on'``: one clock read and one comparison per call that finds the store
    changed, and three clock reads, two counts and one ``min`` per save; nothing per write.

    Examples
    --------
    >>> SerializationManager.instance.register(self.__maybe_flush_chain_storage, every=self.CS_SAVE_INTERVAL)
    >>> self.__maybe_flush_chain_storage()   # 'on', 1.93 s save: the calls of the next 19.3 s are deferred
    """
    if not self.__chain_storage_dirty:
      return
    # The back-off's mode, read once at start.
    mode = self.__cs_modes["save_backoff"]
    if mode == "off":
      # Wave 02: every call that finds the store changed saves, and no clock is read. The cadence is the manager's:
      # it calls this every CS_SAVE_INTERVAL seconds.
      self.__save_chain_storage()
      return
    # When this call starts, on the monotonic clock: a wall clock set back would otherwise stretch the pause.
    started = monotonic()
    # Inside the pause the last save outside a pause set; a save made inside it does not restart it, so 'report'
    # follows the schedule of 'on'.
    deferred = started < self.__save_resume
    if deferred:
      # The save 'on' skips here; in 'report', the count of what 'on' would have skipped.
      self.__cs_count("saves_deferred")
      if mode == "on":
        return
    # The save thread's CPU time before the save.
    cpu = thread_time()
    self.__save_chain_storage()
    # The CPU milliseconds this save took on the save thread, summed per CSTORE_STATS line.
    self.__cs_count("save_cpu_ms", n=round(1000 * (thread_time() - cpu)))
    if not deferred:
      # The next save waits until min(CSTORE_SAVE_MAX_GAP, 10 x d) after this one started, d being its wall time:
      # saves then take at most 10 % of the wall time while d is 3 s or less (at the 30 s default), and two save
      # starts are at most the ceiling plus one cadence (5 s) and the manager's check (1 s) apart for any d up
      # to the ceiling.
      self.__save_resume = started + min(self.__save_max_gap, 10 * (monotonic() - started))
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

  def __stale_revision(self, key, value, snapshot=False):
    """
    Tell whether `value` must not replace the stored record because the stored one is newer.

    Ordering is opt-in: a writer that has a source of truth outside the store (for example a
    signed client timestamp) puts it in the value as an integer "cs_rev". Such a record is not
    replaced by a value with a lower "cs_rev", whatever order writes reach this replica in, and a
    peer snapshot (hsync after a restart) never replaces it with an older or unversioned copy.
    A live write without "cs_rev" comes from a writer that does not version yet: for it, and
    for records that carry no "cs_rev", the last arrival wins as before.

    Parameters
    ----------
    key : str
      Composed chain-store key of the write or of the snapshot record.
    value : any
      The incoming value. It is versioned only when it is a dict whose ``cs_rev`` is an int (a bool is not
      taken as one).
    snapshot : bool, optional
      True for a record of a peer's hsync answer, which must not replace a versioned record with an
      unversioned copy either; False (the default) for a live write.

    Returns
    -------
    bool
      True when the stored value carries an int ``cs_rev`` and the incoming one carries a lower one (or,
      for a snapshot, none); False otherwise. An equal revision is not stale, so a retry of the same update
      is applied as before.

    Notes
    -----
    Acts from the wave 03 release with no switch: today only the Tunnels Manager credentials carry
    ``cs_rev`` (the signed client time of the update). A live write refused here is counted as
    ``refused_cs_rev`` and not confirmed, as every refused remote write. A delete (``value`` None) is a live
    write without ``cs_rev`` and is applied; a snapshot carries a delete only as a tombstone copy, which the
    merge reads with the order switch 'on', and then it is stale over a record that carries ``cs_rev``. The
    cost per write is two dict reads and a few type checks.

    The comparison is ``__rev_order``'s, the one the order rule (``__ver_admit``) reads ``cs_rev`` with too.
    With the order switch 'on' that rule goes one step further for a live write: a value without ``cs_rev``
    over one that carries it is refused there as ``older``.

    Examples
    --------
    >>> self.__stale_revision(key, {"token": "old", "cs_rev": 100})   # the stored value has cs_rev 200
    True
    >>> self.__stale_revision(key, {"token": "legacy"})               # a writer that does not version yet
    False
    >>> self.__stale_revision(key, {"token": "legacy"}, snapshot=True)
    True
    """
    # How the incoming value's cs_rev compares with the stored value's, read as the order rule reads it.
    order = self.__rev_order(self.__chain_storage.get(key, {}).get(self.CS_VALUE), value, "cs_rev")
    # A lower revision loses; an unversioned copy over a versioned record loses only as a snapshot (a live write still
    # wins: its writer does not version yet). An equal one (a retry of the same update) is applied as before.
    return order == "older" or (snapshot and order == "lacks")

  def __rev_order(self, held, value, field):
    """
    Compare an incoming value with the held one by one integer field: a signed sequence or revision, or ``cs_rev``.

    A value is versioned by ``field`` when it is a dict whose ``field`` is an int (a bool is not taken as one).
    The incoming value is newer when it carries a higher one, or carries one the held value lacks; it is older
    when both carry one and its own is lower; it lacks it when only the held value carries one. Equal fields,
    or neither side carrying one, do not decide.

    Parameters
    ----------
    held : any
      The value this replica holds; None for an absent key or a deleted one.
    value : any
      The incoming value; None for a delete.
    field : str or None
      The field read, for example ``"cs_rev"``; None (a namespace without a signed field) never decides.

    Returns
    -------
    str or None
      ``"newer"``, ``"older"``, ``"lacks"`` or None when the field does not decide.

    Notes
    -----
    The one reading of a revision: ``__stale_revision`` (the ``cs_rev`` rule that acts in every mode) and step
    5 of the order rule (``__ver_admit``) both call it. The cost is two dict reads and two type checks per side.

    Examples
    --------
    >>> self.__rev_order({"cs_rev": 200}, {"cs_rev": 100}, "cs_rev")
    'older'
    >>> self.__rev_order("cid", {"revision": 1}, "revision")      # a versioned pointer over an unversioned one
    'newer'
    >>> self.__rev_order({"cs_rev": 200}, {"token": "t"}, "cs_rev")
    'lacks'
    """
    # The int field of each side, else None: a side without it is not versioned by this field.
    new, old = [side.get(field) if isinstance(side, dict) and type(side.get(field)) is int else None
                for side in (value, held)]
    if new == old:
      # Equal, or neither side versioned: the field does not decide.
      return None
    # Only the held value carries it; else the incoming one wins when it carries a higher one or one the held lacks.
    return "lacks" if new is None else ("newer" if old is None or new > old else "older")

  def __ver_valid(self, ver, key=None, origin=None):
    """
    Check the form of a version stamp from the network: a list ``[int ms, str address]``.

    A stamp of any other form (a tuple, a bool or a float for the time, a third item) is malformed; every rule
    then treats it as no stamp, and it is never stored or echoed.

    Parameters
    ----------
    ver : any
      The stamp as received in a store or an hsync record; None for an unstamped one.
    key : str, optional
      The composed key it came with. When given, a malformed stamp is counted as ``ver_invalid`` under its
      bucket; None (the default) counts nothing, for a second look at a stamp already counted.
    origin : str, optional
      ``"remote"`` or ``"hsync"``: where the stamp came from, for that count.

    Returns
    -------
    list or None
      The same list, its address interned; None for an unstamped or malformed stamp.

    Notes
    -----
    The address is interned in place, so the stamps of one writer share one string in memory and in the
    replica file (the pickle stores a shared string once): about 16 B per stamped record instead of about 70.

    Examples
    --------
    >>> self.__ver_valid([1700000000000, "0xai_peer"], key, "remote")
    [1700000000000, '0xai_peer']
    >>> self.__ver_valid([1.7e12, "0xai_peer"], key, "remote")   # counted as ver_invalid
    """
    if type(ver) is list and len(ver) == 2 and type(ver[0]) is int and type(ver[1]) is str:
      # One string per writer address, in place in the received list.
      ver[1] = intern(ver[1])
      # The stamp, well formed.
      return ver
    if ver is not None and key is not None:
      # A malformed stamp, counted once under its key's bucket and treated as none.
      self.__cs_count("ver_invalid", key, origin)
    # No stamp, or a malformed one: unstamped for every rule.
    return None

  def __ver_tick(self):
    """
    Make the stamp of one local write: ``[time in ms, this node's address]``, above every stamp made or taken.

    The clock is a hybrid one: the wall clock in ms, or one ms above the last stamp this node made or took from a
    peer when that is later. It is held within ``CSTORE_VER_MAX_AHEAD`` of the wall clock: at or past that bound
    (the wall clock was set back, or stamps taken near the bound) it starts again from the wall clock.

    Returns
    -------
    list
      A new list ``[ms, self.ee_addr]``.

    Notes
    -----
    Called under the persistence lock, as every change of the clock. Each call takes a new ms: two writes in one
    ms get ms and ms + 1, and an identical rewrite gets a newer stamp than the write it repeats, so a value
    written, replaced and written again is ordered by its last write. After a restart from the bound this node's
    stamps can be lower than its earlier ones, so its next writes of those keys are older than its own earlier
    writes for up to that bound. The cost is one clock read, one comparison and one ``max``.

    Examples
    --------
    >>> self.__ver_tick()
    [1700000000000, '0xai_self']
    >>> self.__ver_tick()   # in the same ms
    [1700000000001, '0xai_self']
    """
    # The wall clock in ms.
    now = int(1000 * self.time())
    # One ms above the last stamp made or taken, or the wall clock when later; at the bound, the wall clock again.
    self.__ver_clock = now if self.__ver_clock >= now + self.__ver_ahead else max(now, self.__ver_clock + 1)
    # A new list each time, so a record and a message never share one.
    return [self.__ver_clock, self.ee_addr]

  def __ver_admit(self, key, value, ver, sender, origin):
    """
    The order rule: tell whether a write or an hsync copy is older than what this replica holds for its key.

    The steps, in order; the first that decides gives the answer:

    1. a stamp more than ``CSTORE_VER_MAX_AHEAD`` ahead of the wall clock: ``future``;
    2. a live remote write stamped with another address than its verified sender: ``origin``;
    3. nothing held under the key, neither a record nor the tombstone of its delete: admitted;
    4. a live write (local or remote) of the value held: admitted (an identical write);
    5. the namespace's signed sequence or revision (``CS_SIGNED_FIELDS``), then ``cs_rev``, read by
       ``__rev_order``: a side that carries the field beats a side that lacks it, else the higher wins; the
       incoming side admitted when it wins, ``older`` when it loses;
    6. a held record without a stamp, or with one more than ``CSTORE_VER_MAX_AHEAD`` ahead (not trusted):
       admitted, the last arrival wins as in the previous release;
    7. an unstamped arrival over a trusted stamp: ``unstamped``;
    8. an older stamp: ``older``, or ``older_than_delete`` over a tombstone;
    9. an equal stamp with another value: ``collision`` for the side that loses one fixed comparison (a delete
       beats a value, else the larger canonical JSON), so every replica keeps the same one; the winner is
       admitted.

    A stamp past steps 1 and 2 moves this node's clock up to it (so its next write is stamped above it) and
    raises the period's largest stamp lead. Each word is counted as ``<bucket>/<origin>/ver_<word>``.

    Parameters
    ----------
    key : str
      Composed key of the write or the copy.
    value : any
      The incoming value; None for a delete.
    ver : list or None
      Its stamp, checked by ``__ver_valid`` (or made by ``__ver_tick``); None for an unstamped one.
    sender : str or None
      The verified address of the node that sent a live remote write; not read for other origins.
    origin : str
      ``"local"``, ``"remote"`` (a live store from a peer) or ``"hsync"`` (a record of a peer's sync answer).

    Returns
    -------
    str or None
      The word: ``future``, ``origin``, ``older``, ``older_than_delete``, ``unstamped`` or ``collision``; None
      when admitted. With the order switch 'on' the caller refuses the write (or skips the copy) on a word; in
      'report' it only counts.

    Notes
    -----
    Called under the persistence lock, after the signed-record rule (``__versioned_record``) and the ``cs_rev``
    rule (``__stale_revision``), which act in every mode. It changes nothing but the clock, the lead and the
    counters, so its answer is the 'on' answer in every mode. "The value held" in step 4 is equality of the
    values only, as an identical write is decided in the previous release; an hsync copy of the same value
    skips step 5 (its fields are the held ones) and goes on to the stamp steps, so it is admitted only with a
    stamp at least the held one: an equal copy never lowers a held stamp. The cost per call: a clock read and a
    dict read, and when the held value differs, the namespace lookup (four key hashes) and a few comparisons;
    the canonical JSON only for equal stamps with different values.

    Examples
    --------
    >>> self.__ver_admit(key, "v2", [1700000000000, "0xai_peer"], "0xai_peer", "remote")   # held at a later stamp
    'older'
    >>> self.__ver_admit(key, "v2", [1700000000000, "0xai_other"], "0xai_peer", "remote")
    'origin'
    """
    # The wall clock in ms, against which a stamp is a future one.
    now = int(1000 * self.time())
    # The record held under the key, else the tombstone of its delete, if any.
    held = self.__chain_storage.get(key) or self.__chain_storage.get(self.CS_DEL + key)
    if ver is not None and ver[0] > now + self.__ver_ahead:
      # Step 1: further ahead than any trusted clock; not taken, and the clock does not move.
      reason = "future"
    elif ver is not None and origin == "remote" and ver[1] != sender:
      # Step 2: a live write stamped as another node's write; not taken.
      reason = "origin"
    else:
      if ver is not None:
        # A stamp taken: the clock moves up to it, and the lead of the stamp over the wall clock is kept for the line.
        self.__ver_clock, self.__ver_lead = max(self.__ver_clock, ver[0]), max(self.__ver_lead, ver[0] - now)
      if held is None or (origin != "hsync" and held[self.CS_VALUE] == value):
        # Steps 3 and 4: nothing held, or an identical write.
        return None
      # Step 5: the namespace's signed field first (none outside the signed namespaces), then cs_rev; a copy of the
      # held value carries the same fields, so it is not read for one (False) and goes on to the stamp.
      order = held[self.CS_VALUE] != value and (self.__rev_order(
        held[self.CS_VALUE], value, self.CS_SIGNED_FIELDS.get(self.__namespace_for_key(key, self.CS_SIGNED_FIELDS)),
      ) or self.__rev_order(held[self.CS_VALUE], value, "cs_rev"))
      # The held stamp, if any.
      held_ver = held.get(self.CS_VER)
      if order:
        # Decided by a field: admitted when the incoming side wins, older when it loses or lacks it.
        reason = None if order == "newer" else "older"
      elif held_ver is None or held_ver[0] > now + self.__ver_ahead:
        # Step 6: no trusted stamp held; the last arrival wins.
        reason = None
      elif ver is None:
        # Step 7: an unstamped write over a stamped record.
        reason = "unstamped"
      elif ver < held_ver:
        # Step 8: an older stamp; over a tombstone, a write the delete already replaced.
        reason = "older" if held[self.CS_VALUE] is not None else "older_than_delete"
      else:
        # Step 9: the loser of an equal stamp with another value (order None: the values differ) is the side lower by
        # (is a delete, canonical JSON); an equal stamp of the same value, a sync's steady state, builds no JSON.
        reason = "collision" if ver == held_ver and order is None and (
          (value is None, self.json_dumps(value, sort_keys=True))
          < (held[self.CS_VALUE] is None, self.json_dumps(held[self.CS_VALUE], sort_keys=True))
        ) else None
    if reason is not None:
      # Counted in 'report' and 'on' alike, under the key's bucket and the write's origin.
      self.__cs_count("ver_" + reason, key, origin)
    # The word the caller acts on in 'on', or None.
    return reason

  def __perm_count(self, key, value, owner, token, writer, origin):
    """
    Count a write that changes a readonly record for another owner or node, or deletes another node's record.

    Counts only, in every mode of the order switch but 'off'; it never refuses. Outside the protected namespaces
    (``CS_PROTECTED_NAMESPACES``), for a held record (not a tombstone) whose token the write carries:

    - ``perm_readonly``: the record is readonly and the write names another owner, or the record is stamped by
      another node than the writer, and the readonly check does not already refuse it: a delete, a write of
      the same value (a takeover), or another value under the same owner from another node. A sync copy, which
      no readonly check refuses, counts also with another value and owner;
    - ``delete_foreign``: a delete of a record that is not readonly, stamped by another node than the writer.

    Parameters
    ----------
    key : str
      Composed key of the write or the copy.
    value : any
      The incoming value; None for a delete.
    owner : str
      The owner the write names, after the namespace rules.
    token : any
      The write's token.
    writer : str or None
      The node making the write: this node for a local write, the verified sender for a remote one, the stamp's
      address (not verified) for a sync copy; None for an unstamped copy.
    origin : str
      ``"local"``, ``"remote"`` or ``"hsync"``, for the count's label.

    Returns
    -------
    None

    Notes
    -----
    A readonly record outside the protected namespaces is written by no caller, so any ``perm_readonly`` is a
    defect or a forged write to look into; ``delete_foreign`` measures how often unsigned namespaces are cleaned
    by another node (tunnel routes, Deeploy answers deleted by the requester). A record written before stamps
    existed is judged by its owner only. The namespace lookup (six key hashes) runs only for a write that
    counts.

    Examples
    --------
    >>> self.__perm_count(key, None, "other-owner", None, "0xai_peer", "remote")   # a readonly record deleted
    """
    # The record the write replaces; a tombstone is not one (a write over it is a write to a key not held).
    held = self.__chain_storage.get(key)
    if held is None or held.get(self.CS_TOKEN) != token:
      return
    # Stamped by another node than the one writing now.
    other_node = held.get(self.CS_VER) is not None and held[self.CS_VER][1] != writer
    if held.get(self.CS_READONLY) and (owner != held.get(self.CS_OWNER) or other_node) and (
      origin == "hsync" or value is None or value == held[self.CS_VALUE] or owner == held.get(self.CS_OWNER)
    ):
      # A readonly record changed for another owner or node, past the readonly check.
      event = "perm_readonly"
    elif value is None and other_node:
      # Another node's record deleted.
      event = "delete_foreign"
    else:
      return
    if not self.__namespace_for_key(key, self.CS_PROTECTED_NAMESPACES):
      # Counted under the key's bucket and the write's origin.
      self.__cs_count(event, key, origin)

  def __wire_refusal(self, key, value, owner, readonly, token, ver):
    """
    Tell why a record must not be stored for what it is on the wire: too large, or not encodable at all.

    The record is encoded once as a namespace-sync answer would carry it, ``{key: {key, value, owner, readonly,
    token, ver}}``, with the encoder every message leaves with (``json_dumps``: compact, a non-ASCII character as
    itself) and then as UTF-8, the bytes that travel. Every path that stores a record asks this first: a local
    write and a store from a peer (``_set_value``, a hand-off re-send included) and each copy of a namespace-sync
    answer (``__merge_hset_snapshot``).

    Parameters
    ----------
    key : str
      Composed key of the record.
    value : any
      Its value; None for a delete.
    owner : any
      Its owner, as it would be stored.
    readonly : any
      Its readonly mark, as it would be stored.
    token : any
      Its token, as it would be stored.
    ver : list or None
      Its stamp, as it would be stored; None for an unstamped record.

    Returns
    -------
    str or None
      ``"unencodable"`` when the encoder or UTF-8 cannot take the record (a string that holds a lone surrogate,
      a value of a type or with a dict key no encoder takes, a circular or too deeply nested value);
      ``"too_large"`` when it takes more than ``CS_MAX_RECORD_BYTES`` (262,144) bytes; None when it may be stored.

    Notes
    -----
    Every field a store or a sync answer can fill counts, not only the value: a peer chooses the owner, the
    readonly mark, the token and the address in the stamp as well. The size is the same on the writer and on
    every receiver, as each measures the record with the owner, mark, token and stamp it would store. A record
    that passes fits one message: a sync answer's entry for it is at most the size measured here.

    One record UTF-8 cannot encode made every sync answer of its namespace fail to be prepared for sending, as
    the answer carries every record of the namespace; a JSON escape in a peer's message, or a local caller, can
    deliver such a string. A value the encoder turns into JSON (a numpy number or array, a datetime) is
    encodable, and is measured as the JSON it becomes.

    The cost is one JSON encoding of the record. Measured on the node image's interpreter under a 0.35 CPU
    limit, in wall time with the limit's throttling: 0.05 to 0.11 ms for a 0.4 KB value, 3.9 to 5.0 ms for a
    122 KB record of nested configuration (the kind the largest production value is; about what the receiver
    spends parsing the same value out of its message), about 8 ms for one at the cap or for 125 KB of small
    nested dicts, 20 ms for 125 KB of floats. At full speed: 0.01 ms, 0.85 ms and 1.7 ms.

    Examples
    --------
    >>> self.__wire_refusal(key, {"a": 1}, "owner", False, None, [1700000000000, "0xai_self"])
    >>> self.__wire_refusal(key, "x" * 300_000, "owner", False, None, None)
    'too_large'
    >>> self.__wire_refusal(key, {"name": "\\ud800"}, "owner", False, None, None)
    'unencodable'
    """
    try:
      # The bytes the record takes in a sync answer: one encoding with the wire's encoder (unsorted: the order of
      # the keys does not change the length; without the encoder's own search for a circular value, which costs
      # 13 to 31 % of the encoding and ends in the RecursionError below anyway), then as UTF-8, which is what the
      # transport encrypts and sends.
      size = len(self.json_dumps({key: {
        self.CS_KEY: key, self.CS_VALUE: value, self.CS_OWNER: owner, self.CS_READONLY: readonly,
        self.CS_TOKEN: token, self.CS_VER: ver,
      }}, sort_keys=False, ensure_ascii=False, check_circular=False).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
      # No encoder takes it (TypeError: a type or a dict key; ValueError: a string UTF-8 cannot encode, or an integer
      # too long to be written as text; RecursionError: a circular value, or one nested too deep): no message could
      # carry this record to a peer.
      return "unencodable"
    # Larger than one record may be, or fine.
    return "too_large" if size > self.CS_MAX_RECORD_BYTES else None

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
      Deep copies of the records' ``CS_EXPORT_FIELDS`` (key, value, owner, readonly and, when present, the
      stamp ``ver``), keyed by their composed storage key.

    Notes
    -----
    Only the fields a merge reads are sent (the previous release's merge reads owner, value, readonly and
    token, and a record with a token is never exported): the confirmation fields, the peer lists, ``sent_ver``
    and ``default_peers`` stay on this node.

    The export freezes ``self.__chain_storage.items()`` into a list before
    filtering. That keeps snapshot generation stable even if another local
    write mutates the dictionary while the snapshot is being assembled.

    A dAuth namespace is exported only by a dAuth registry member. The
    namespace is the one whose index (``__hset_index``) ``hkey`` has, the
    prefix the records are selected by.

    With the order switch ``CSTORE_ORDER`` not 'off', the tombstones of the
    namespace's deleted keys are exported too, each under the key it deleted
    (value None, with its stamp), so a peer that missed a delete can apply it
    ('on'). A node of the previous release skips a record with value None.

    Examples
    --------
    >>> list(self.__export_hset_snapshot("players"))  # composed keys of the fields that carry no token
    ['hs:...:YWxwaGE=']
    """
    # The dAuth rule applies to the namespace of the index hkey has, the prefix the records are selected by below.
    if self.__secret_namespace(self.__hset_index(hkey)) and not self.__registry_member(self.ee_addr, dauth_only=True):
      return {}
    index = self.__hset_index(hkey)
    # The prefix of the tombstones of the namespace's keys; none with the order switch 'off', which sends none.
    tombs = None if self.__cs_modes["order"] == "off" else self.CS_DEL + index
    snapshot = {}
    # Freeze the current dictionary view before filtering so live writes do not
    # mutate the iterator while this response is being serialized.
    for key, record in list(self.__chain_storage.items()):
      # A record of the namespace, or the tombstone of a deleted key of it.
      if not isinstance(key, str) or not (key.startswith(index) or (tombs is not None and key.startswith(tombs))):
        continue
      if not isinstance(record, dict):
        continue
      # HSync carries no token capability. Never disclose token values or
      # protected records merely because the caller knows a namespace name.
      if record.get(self.CS_TOKEN) is not None:
        continue
      if key.startswith(self.CS_DEL) and record.get(self.CS_VALUE) is not None:
        # Under the tombstones' prefix but holding a value (a plain key the previous release let a caller write):
        # not a tombstone, never sent as the key it names.
        continue
      # A tombstone travels under the key it deleted, with value None and its stamp.
      name = key[len(self.CS_DEL):] if key.startswith(self.CS_DEL) else key
      # A copy of the five fields a merge reads (the stamp only when there is one): before, the whole record went,
      # with its confirmation fields and peer lists and a token None, about 1,082 B a record against 461 B now.
      snapshot[name] = self.deepcopy({field: record[field] for field in self.CS_EXPORT_FIELDS if field in record})
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

    A record whose stored value carries an int ``cs_rev`` (``__stale_revision``)
    is replaced only by a copy with a higher or equal ``cs_rev``: an older or
    unversioned copy is skipped and not counted in the result.

    A copy that takes more than ``CS_MAX_RECORD_BYTES`` on the wire, or that
    no message could carry (``__wire_refusal``, asked with the copy's key,
    value, owner, readonly mark, token and stamp as the peer sent them), is
    skipped before any other rule reads it and counted as
    ``<bucket>/hsync/refused_too_large`` or ``refused_unencodable``; the
    other copies of the answer are merged. In every mode. A copy that is the
    record held under its key field for field (key, value, owner, readonly
    mark, stamp and token) is not measured, as nothing of it is stored: a
    periodic sync between replicas that agree pays one comparison per copy,
    and one JSON encoding only for each copy that differs, under the
    persistence lock.

    A copy whose value is the one held under its key is not put through the
    signed-record rule (``__versioned_record``) again: that rule passed the
    held value when it was stored, and its one signature check per record
    (about 3 ms at 0.35 core) made a sync of a namespace that already agrees
    cost as much as a first sync. Such a copy still goes through every rule
    below and changes no value: at most the held record's stamp, or its
    owner, readonly mark and token where the namespace does not derive them
    from the value, as before. A copy with any other value is checked as
    before.

    The order rule (switch ``CSTORE_ORDER``, read once at start): with the switch
    'off' the stamp of a copy is not read. Else a copy equal to the held record
    in value, owner, readonly and token only raises the held stamp to the copy's
    when that one is newer (held in memory without a dirty mark: see
    ``on_close``), and any other copy is skipped in 'on' when ``__ver_admit``
    gives it a word (older, future, unstamped over a trusted stamp, or the losing
    side of a collision) and not counted in the result; in 'report' it is applied
    as in the previous release. Every copy goes through ``__ver_admit``, which
    counts its word as ``<bucket>/hsync/ver_<word>`` and moves the stamp clock up
    to a stamp it takes; an equal copy raises the held stamp only when it gives
    no word. An applied copy keeps its stamp.

    A delete travels as a copy with value None and a stamp (a tombstone, see
    ``__export_hset_snapshot``). The previous release, and this one with the
    order switch 'off', skip it. In 'report' it runs the signed-record,
    ``cs_rev`` and order rules (counted) and is skipped; one 'on' would apply
    over a value held here is counted as ``<bucket>/hsync/ver_copy_delete``.
    In 'on' it goes through the same rules as a value and, admitted, removes
    the key's value (counted ``ver_copy_delete``) and stores the tombstone
    under ``"dl:" + key`` (``tombstone_new`` when there was none), so a
    replica that missed the delete does not keep the key. A value copy
    removes the key's tombstone (``__held_tombstone``: not another record
    under ``"dl:"``, and none with the order switch 'off'). A delete copy of
    a key whose ``"dl:"`` record holds a value (an older release's caller
    wrote it there) is skipped in every mode, as the previous release skips
    every delete copy, so that record stays. A copy that is
    applied, with the order
    switch not 'off', also goes through the write-permission count
    (``__perm_count``), which counts and never refuses.

    Examples
    --------
    >>> self.__merge_hset_snapshot("players", {key: record})
    1
    """
    # The order switch, read once at start.
    order = self.__cs_modes["order"]
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
        # The copy's stamp, checked for form (a malformed one counts ver_invalid and is unstamped); none is read
        # with the order switch 'off'.
        ver = None if order == "off" else self.__ver_valid(record.get(self.CS_VER), key, "hsync")
        # The record held under the key, to tell a copy that is this record field for field (most copies of a periodic
        # sync between replicas that agree): nothing of such a copy is stored, so it is not measured.
        held = self.__chain_storage.get(key, {})
        if any(held.get(field) != record.get(field) for field in self.CS_EXPORT_FIELDS + (self.CS_TOKEN,)):
          # Why the copy must not be stored for its wire form, if so (too large, or not encodable), from the fields
          # as the peer sent them and before any rule reads them.
          word = self.__wire_refusal(key, value, record.get(self.CS_OWNER), record.get(self.CS_READONLY, False),
                                     record.get(self.CS_TOKEN), ver)
          if word is not None:
            # Skipped and counted as a refused write is, under the key's bucket; the rest of the answer is merged.
            self.__cs_count("refused_" + word, key, "hsync")
            continue
        if value is None and self.__chain_storage.get(self.CS_DEL + key, {}).get(self.CS_VALUE) is not None:
          # A delete copy of a key whose "dl:" record holds a value (an older release's caller wrote it there; it is
          # no tombstone): skipped in every mode, as the previous release skips every delete copy, so the tombstone
          # never replaces that record (nor counts in 'report' what 'on' would remove).
          continue
        if value is None and order != "on":
          # A delete travels as a stamped copy with value None (a tombstone), applied only in 'on'; with 'off', or
          # unstamped, it is skipped as before. In 'report' the rules below run and count, and a delete 'on' would
          # apply over a value held here is counted as ver_copy_delete.
          if (ver is not None and self.__versioned_record(key, None) and not self.__stale_revision(key, None, True)
              and self.__ver_admit(key, None, ver, None, "hsync") is None and key in self.__chain_storage):
            # The value 'on' would remove.
            self.__cs_count("ver_copy_delete", key, "hsync")
          continue
        # A copy whose value is the one held under the key is not put through the signed-record rule again (one
        # signature check per record of a signed namespace): the held value passed that rule when it was stored, and
        # such a copy can change nothing below but the held record's stamp, owner, readonly mark and token, as before.
        same = value is not None and held.get(self.CS_VALUE) == value
        if (value is None and ver is None) or not (same or self.__versioned_record(key, value)):
          continue
        if self.__stale_revision(key, value, snapshot=True):
          # The peer's copy is older than ours, or unversioned over a versioned record: a restart sync must not
          # bring it back.
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
        # The order rule's word for the copy (counted as ver_<word>; it also moves the clock up to a stamp taken),
        # None when admitted or with the order switch 'off'.
        reason = None if order == "off" else self.__ver_admit(key, value, ver, None, "hsync")
        # The record the copy is compared with: the key's, or for a delete the tombstone held for it.
        local_record = self.__chain_storage.get(self.CS_DEL + key if value is None else key)
        if isinstance(local_record, dict):
          same_owner = local_record.get(self.CS_OWNER) == record.get(self.CS_OWNER)
          same_value = local_record.get(self.CS_VALUE) == value
          same_readonly = local_record.get(self.CS_READONLY, False) == readonly
          same_token = local_record.get(self.CS_TOKEN, None) == token
          if same_owner and same_value and same_readonly and same_token:
            if reason is None and ver is not None and ver > (local_record.get(self.CS_VER) or []):
              # The same record with a newer stamp the rule admits: the stamp is raised in place, with no dirty mark
              # (nothing is saved for an equal copy), so it reaches the file with the next save or the save of an
              # orderly stop.
              local_record[self.CS_VER], self.__chain_storage_unmarked = ver, True
            continue
        if reason is not None and order == "on":
          # Older than the held record by the order rule (or a future, unstamped or losing copy): skipped in 'on'; in
          # 'report' only counted, and applied below as in the previous release.
          continue
        if order != "off":
          # The write-permission count, only counted; a copy's writer is its stamp's address, which nothing verifies.
          self.__perm_count(key, value, record.get(self.CS_OWNER), token, ver and ver[1], "hsync")

        # The copy lands under its key, a delete under "dl:" and its key; the other of the two goes, so a key and its
        # tombstone are never both held.
        name, other = (self.CS_DEL + key, key) if value is None else (key, self.CS_DEL + key)
        if value is None and key in self.__chain_storage:
          # In 'on' only: a delete a peer holds removes a value held here.
          self.__cs_count("ver_copy_delete", key, "hsync")
        if value is None and name not in self.__chain_storage:
          # A new tombstone, counted per bucket as a live delete's is.
          self.__cs_count("tombstone_new", key, "hsync")
        if value is None or self.__held_tombstone(key):
          # Dropped: the value a delete removes, or the tombstone a value replaces (only a tombstone, and not with
          # the order switch 'off'; another record under "dl:" stays).
          self.__chain_storage.pop(other, None)
        # Apply the peer record only to the local replica. HSync intentionally
        # does not enqueue a broadcast because this node is catching up to peer
        # state rather than originating a new write.
        self.__chain_storage[name] = {
          self.CS_KEY: key,
          self.CS_VALUE: value,
          self.CS_OWNER: record.get(self.CS_OWNER),
          self.CS_READONLY: readonly,
          self.CS_TOKEN: token,
          self.CS_CONFIRMATIONS: -1,
          self.CS_MIN_CONFIRMATIONS: 0,
          # The copy's stamp, when it has one; with the order switch 'off' none, as in the previous release.
          **({self.CS_VER: ver} if ver is not None else {}),
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
    Give the confirmation fields of a new own record: no confirmation yet, its targets and how many must confirm.

    Parameters
    ----------
    key : str
      Chain-store key of the write (the deleted key for a delete's tombstone), whose namespace decides whether
      a peer that joins later is added to the record's targets.
    peers : str or list, optional
      Explicit peers requested for the write associated with ``key``.
    include_default_peers : bool, optional
      If True, include the default chain peer set when deriving the minimum
      confirmation count. Default is True.

    Returns
    -------
    dict
      ``confirms`` 0, ``confirmed_peers`` (an empty list: the peers that settled the record, by a confirmation,
      a refusal answer or the hand-off giving up), ``target_peers``, ``min_confirms``, and ``default_peers``:
      True when the write is meant for every default peer and is not a dAuth secret, so the hand-off adds a peer
      that joins later to its targets.

    Notes
    -----
    Before, this set the fields on the stored record one after the other; the caller now builds the record with
    them, so the hand-off's walk, which reads records without the persistence lock, never sees a record with
    ``confirms`` and without its lists. A remote record no longer gets these fields (``__set_key_value``).

    Examples
    --------
    >>> self.__reset_confirmations("key")   # default peers peer1 and peer2, MIN_CONFIRMATIONS 1 (the default)
    {'confirms': 0, 'confirmed_peers': [], 'target_peers': ['peer1', 'peer2'], 'min_confirms': 1, 'default_peers': True}
    """
    # The fields, built before the record is stored.
    return {
      self.CS_CONFIRMATIONS: 0, "confirmed_peers": [],
      # The targets as the peers are now, and the confirmations needed from them.
      "target_peers": self.__get_target_peers(peers=peers, include_default_peers=include_default_peers),
      # The confirmations needed.
      self.CS_MIN_CONFIRMATIONS: self.__get_min_peer_confirmations(
        peers=peers,
        include_default_peers=include_default_peers,
      ),
      # A peer that joins later is added to the targets of a write meant for every default peer, never of a secret.
      self.CS_DEFAULT_PEERS: include_default_peers and not self.__secret_namespace(key),
    }


  def __increment_confirmations(self, key):
    self.__chain_storage[key][self.CS_CONFIRMATIONS] += 1
    return


  def __held_tombstone(self, key):
    """
    Tell whether a value written to ``key`` replaces a tombstone held under ``"dl:" + key``.

    A tombstone is the record a stamped delete leaves under ``"dl:" + key``: value None and a well-formed stamp.
    Any other record there (one a caller of an older release could write under that prefix, holding a value) is
    not one and is never replaced by a write of ``key``, nor by a delete of it (``__set_key_value`` and
    ``__merge_hset_snapshot`` leave no tombstone over a record with a value). With the order switch 'off' nothing
    there is replaced, as in the previous release; the tombstone sweep removes every tombstone at the next
    ``CSTORE_STATS`` line then.

    Parameters
    ----------
    key : str
      The key a value is written to.

    Returns
    -------
    bool
      True when the order switch is not 'off' and the record held under ``"dl:" + key`` is a tombstone.

    Examples
    --------
    >>> self.__held_tombstone("key")   # order switch 'report', after a stamped delete of "key"
    True
    >>> self.__held_tombstone("job1")  # "dl:job1" holds {"progress": 5}, written by an older release's caller
    False
    """
    # The record held under the tombstones' prefix and the key, if any.
    held = self.__chain_storage.get(self.CS_DEL + key)
    # A tombstone (value None, a well-formed stamp), and the order switch not 'off'.
    return (self.__cs_modes["order"] != "off" and held is not None and held[self.CS_VALUE] is None
            and self.__ver_valid(held.get(self.CS_VER)) is not None)

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
    ver=None,
  ):
    """
    Set a key-value pair in the local chain-store replica.

    This method is called to set a key-value pair in the chain storage. The record keeps the write's version
    stamp when it has one.

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
    ver : list, optional
      The write's version stamp ``[ms, address]``, stored as the record's ``ver``; None (the default, and
      always with the order switch 'off') stores no ``ver`` field, as the previous release.

    Returns
    -------
    None

    Notes
    -----
    The record is built whole before it is stored. A local record gets the
    confirmation fields of ``__reset_confirmations`` (its targets,
    ``default_peers`` among them); a remote record gets only ``confirms`` -1
    and ``min_confirms`` 0, as a sync copy, and no peer lists (the previous
    release gave it this node's lists, which nothing read).

    When ``value`` is ``None``, the key is deleted locally. The existing
    delete semantics and related trade-offs described below are preserved.
    A stamped delete (``ver`` given, ``CSTORE_TOMBSTONE_DAYS`` above 0) also
    leaves a tombstone: a record under ``"dl:" + key`` with value None, the
    delete's owner, readonly and token, and its stamp (none when a record
    with a value is held there: the key is only removed, as the previous
    release removes it, and that record stays); a local one gets the
    confirmation fields of a local write, a remote one the -1 of a remote
    write; a local record or tombstone also keeps the stamp as ``sent_ver``,
    the one its confirmations and refusal answers echo. A tombstone already
    held with an equal or newer stamp is kept as
    it is; an older one is replaced (a stamp-only change: ``_set_value``
    does not mark it for a save). A new tombstone is counted as
    ``<bucket>/<origin>/tombstone_new``. Storing a value removes the key's
    tombstone (``__held_tombstone``), so a key and its tombstone are never
    both held; with the order switch 'off' the tombstone stays until the
    next sweep, and a record under ``"dl:" + key`` that is not a tombstone
    (a value an older release's caller wrote there) always stays, under a
    write or a delete of the key.
    The caller must hold the persistence lock and, after releasing it, mark
    the chain storage dirty when the call changed the replica (``_set_value``
    does not mark it for a delete of a key not held or for a write of what
    was already stored).

    Examples
    --------
    >>> self.__set_key_value("key", "v", "node", ver=[1700000000000, "0xai_self"])   # record with its stamp
    >>> self.__set_key_value("key", None, "node")                                    # deleted, no tombstone
    >>> self.__set_key_value("key", None, "node", ver=[1700000000001, "0xai_self"])  # tombstone under "dl:key"
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
      # The tombstone already held for the key, if any (or a record with a value an older release's caller wrote
      # under "dl:" and the key).
      held = self.__chain_storage.get(self.CS_DEL + key)
      if ver is None or not self.__tomb_ms or (held is not None and (
          held[self.CS_VALUE] is not None or (held.get(self.CS_VER) or []) >= ver)):
        # No tombstone: an unstamped delete (order switch 'off', or a writer of the previous release), tombstones
        # turned off (CSTORE_TOMBSTONE_DAYS 0), one at least as new already held, or a record with a value held under
        # "dl:" and the key, which stays: the key is only removed, as the previous release removes it.
        return
      if held is None:
        # A new tombstone, counted per bucket: the rate the tombstone cap is sized from.
        self.__cs_count("tombstone_new", key, "remote" if local_sync_storage_op else "local")
      # The delete leaves a tombstone under "dl:" and the key: the record below, with value None and the stamp.
      chain_key = self.CS_DEL + key
    elif self.__held_tombstone(key):
      # A value replaces the key's tombstone (only a tombstone, and not with the order switch 'off'): a key and its
      # tombstone are then never both held.
      self.__chain_storage.pop(self.CS_DEL + key)
    self.__chain_storage[chain_key] = {
      self.CS_KEY       : key,
      self.CS_VALUE     : value,
      self.CS_OWNER     : owner,
      self.CS_READONLY  : readonly,
      self.CS_TOKEN     : token,
      # The write's stamp, when it has one; a record of the previous release's form otherwise.
      **({self.CS_VER: ver} if ver is not None else {}),
      # A local write's stamp again, as the one its store carried: a peer's identical write can raise the record's
      # stamp later, and the confirmations and refusal answers of this store still echo this one.
      **({self.CS_SENT_VER: ver} if ver is not None and not local_sync_storage_op else {}),
      # A remote record: -1 (synced from a peer) and no peer lists, as a sync copy has; before, it also carried this
      # node's peer lists, which nothing read. A local record (or a local delete's tombstone): its confirmation fields.
      **({self.CS_CONFIRMATIONS: -1, self.CS_MIN_CONFIRMATIONS: 0} if local_sync_storage_op else
         # The local record's fields, for the write's own key (the deleted key for a tombstone).
         self.__reset_confirmations(key, peers=peers, include_default_peers=include_default_peers)),
    }
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
    result_info=None,
    ver=None,
    sender=None,
    resend=False,
    wait=True,
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
    result_info : dict, optional
      A dict the caller passes to learn how the call ended: its ``"outcome"`` is set to the word of the
      way out (below), also when the call raises. None (the default) sets nothing.
    ver : any, optional
      For a remote write only, the version stamp the store carried, as received (checked here); a local
      write takes a fresh one. Only ``__exec_store`` passes it. Default is None (unstamped).
    sender : str, optional
      For a remote write only, the verified address of the node that sent it, which the stamp must name.
      Only ``__exec_store`` passes it. Default is None.
    resend : bool, optional
      For a remote write only: True for a hand-off re-send (a store that carries ``resend``), which the order
      rule refuses on its word as in 'on' whatever this node's order switch (except 'off', which reads no
      stamp). Only ``__exec_store`` passes it. Default is False.
    wait : bool, optional
      True (the default) waits for the confirmations as before. False, for a
      local write: every check and the local apply run as for a blocking
      write, the same store is queued, and the call returns True without
      waiting for any confirmation (``result_info`` outcome: the write's own,
      ``changed``, ``identical`` or ``absent_delete``); the plugin thread
      re-sends a value while it is unconfirmed (``__maybe_resend_unwaited``).
      Not allowed in the protected namespaces (``CS_PROTECTED_NAMESPACES``).
      Once this instance's plugin loop has ended (``done_loop``: a stop, or a
      restart before the next instance publishes its setter) it applies
      nothing and returns False with the outcome ``not_ready``.

    Returns
    -------
    bool
      True if the desired state was stored successfully or was already present.
      False if the write was rejected or timed out after the allowed retries.
      The bool is the same, on every path, as before ``result_info`` existed.
      With ``wait=False``, True means stored here and queued, not confirmed.

    Raises
    ------
    ValueError
      If ``key`` is invalid (not a str, empty, or starting with ``"dl:"``,
      the prefix of the tombstones), if ``timeout`` or ``max_retries`` fail
      validation, if ``wait`` is False for a key of a protected namespace
      (counted ``error``), or if the underlying storage/update flow raises an
      error.

    Notes
    -----
    This method blocks the caller thread while waiting for confirmations on
    local-originated writes, unless ``wait`` is False. A non-blocking write
    is refused exactly as a blocking one (the same words) and leaves the same
    record; its store goes out on the plugin's next lap, and a value is queued again after
    each ``timeout`` while unconfirmed, up to ``max_retries`` times, by the
    plugin thread, whose later wait end (``unconfirmed``, ``overwritten``,
    ``refused_by_peers``) is counted as a blocking write's. A delete is
    queued once, as a blocking
    delete, which does not wait either. The caller's thread takes, and may
    wait for, the persistence lock for the checks and the apply, as for any
    write, and once more to register the re-sends, which it does only while
    the key still holds the record this write left (a newer write by another
    thread in between owns the key's entry). Other threads hold that lock for
    whole-store walks: a namespace sync's merge holds it for its whole answer,
    with one signature check per signed record whose value is not the one
    held (about 3 ms each at 0.35 core, so 3 s for 1,000 records a replica
    lacks), and the save holds it while it copies the replica;
    a ``wait=False`` write waits for them as any write does.

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
    ``changed``, ``identical``, ``absent_delete``, a refusal word,
    ``error``, or ``not_ready`` (a ``wait=False`` write after the plugin
    loop ended, whose count no later line carries). ``changed`` says what happened to this replica: a local write
    that changed it and then was not confirmed, or raised while waiting, is
    still counted as ``changed``.

    The refusal words, one per way out that refuses the write (wave 02
    counted them all, and ``error``, as ``refused``):

    - ``refused_no_peers``: a local write with no target peer (the node's own
      address is never one);
    - ``refused_oracle_only``: a local write of a Tunnels Manager namespace
      with no oracle registry member among its targets;
    - ``refused_not_member``: a local key-rotation write on a node outside
      the dAuth registry, or a local dAuth secret write on a node in neither
      registry or with a target outside the dAuth registry;
    - ``refused_versioned``: the signed-record rule (``__versioned_record``);
    - ``refused_cs_rev``: a value whose ``cs_rev`` is lower than the stored
      value's (``__stale_revision``);
    - ``refused_secret_rule``: a changed or empty revocation marker, or a
      payload for a revoked secret revision;
    - ``refused_token``: the stored record has another token;
    - ``refused_readonly``: the stored record is readonly for another owner;
    - ``refused_unencodable``: a record no message can carry: the wire's
      encoder, or UTF-8, cannot take its key, value, owner, readonly mark,
      token or stamp (``__wire_refusal``);
    - ``refused_too_large``: a record that takes more than
      ``CS_MAX_RECORD_BYTES`` (256 KiB) on the wire, all those fields
      counted (``__wire_refusal``);
    - ``refused_new_keys``: only with ``CSTORE_NEW_KEYS_MAX`` above 0, a
      peer's store that would create a key after its sender, outside the
      replication peers and the oracle registries, created that many in
      the current ``CSTORE_STATS`` period;
    - with the order switch ``CSTORE_ORDER`` 'on' (or 'report', for a
      hand-off re-send) only, the order rule's
      words (``__ver_admit``): ``refused_older``, ``refused_future``,
      ``refused_origin``, ``refused_unstamped``, ``refused_collision`` and
      ``refused_older_than_delete``.

    The size cap has no switch and acts in every mode. After the namespace
    rules and the stamp, and before the order rule, the record is measured
    as it would be stored (``__wire_refusal``) and refused on a word:
    nothing is stored or sent for a local write, and a peer's store is not
    confirmed. A delete is a record with value None, so the delete of a key
    that holds a larger record, written before the cap existed, is taken.
    The check costs one JSON encoding of the record per call (the numbers
    are in ``__wire_refusal``).

    A peer's store that creates a key this replica did not hold, outside
    the protected namespaces (``CS_PROTECTED_NAMESPACES``), is counted as
    ``new_keys/<sender>`` when its verified sender is neither among this
    node's replication peers nor in an oracle registry. With
    ``CSTORE_NEW_KEYS_MAX`` 0, the default, it only counts: the write is
    applied. With the number above 0, such a store is refused as
    ``refused_new_keys`` once that sender's count has reached the number
    in the current ``CSTORE_STATS`` period, which runs from one line to
    the next (``CS_STATS_INTERVAL``, 300 s; ``process`` restarts the
    counts at each line): it is not stored, not counted as a new key and
    not confirmed, and a stamped one is answered like any other refusal
    (order switch 'on', or a re-send). An overwrite, a delete, a key of a
    protected namespace, a replication peer's store and a registry
    member's are never refused by it. The registries are read only for
    such a store from a sender outside the replication peers.

    The order rule (``CSTORE_ORDER``, read once at start), after the
    namespace rules and before the token and readonly checks. 'off': no
    stamp is made or read, as in the previous release. 'report' and 'on': a
    local write takes a fresh stamp (``__ver_tick``) and a remote one's is
    checked for form; the record keeps the stamp, the store message carries
    it, and ``__ver_admit`` counts each rule as ``ver_<word>``. Only 'on'
    refuses on its word; 'report' writes as the previous release, except a
    hand-off re-send (``resend``), refused on its word in 'report' too, so a
    re-send never replaces a newer copy during a switch to 'on'. A remote
    write of the value held is identical in every mode; it raises the held
    stamp to its own when that is newer, in memory without a dirty mark, as
    an identical local write's fresh stamp is.

    With the order switch not 'off', an admitted write also goes through
    the write-permission count (``__perm_count``: ``perm_readonly``,
    ``delete_foreign``), which counts and never refuses.

    A stamped delete leaves a tombstone (``__set_key_value``), which the
    order rule takes as the held record: an older value arriving after it is
    ``older_than_delete``. A delete of a key not held, or a newer delete over
    a held tombstone, changes only the tombstone: held without a dirty mark
    and saved at an orderly stop.

    ``error`` is a call that raised before it changed the replica (a bad
    ``timeout`` or ``max_retries``, for example). A local write that changed
    the replica and then failed its wait gets a second label,
    ``<bucket>/local/<wait end>``: ``unconfirmed`` (too few confirmations
    after the retries), ``overwritten`` (another write replaced the key
    during the wait) or ``refused_by_peers`` (every target settled, one at
    least by a refusal answer, and too few confirmed it: the wait ends at
    once). ``result_info["outcome"]`` is the wait end when there
    is one, else the outcome. None of these words changes the bool returned.

    A local write or delete of a Tunnels Manager namespace is sent only to
    the oracle registry members (either registry) among its target peers;
    the others are left out of its send, its retries and its confirmation
    count. With no member among them, which includes a registry that cannot
    be read on this node, it says so in red and returns False at once.

    Examples
    --------
    >>> self._set_value("key", "value", owner="node", timeout=5, max_retries=1)  # confirmed by enough peers
    True
    >>> self._set_value("key", "value", owner="peer1", local_sync_storage_op=True)  # a remote write, stored here
    True
    >>> info = {}
    >>> self._set_value("key", "other", owner="node", token="t", result_info=info), info  # held with no token
    (False, {'outcome': 'refused_token'})
    """
    if not isinstance(key, str) or len(key) == 0:
      raise ValueError("Key must be a non-empty string.")
    if key.startswith(self.CS_DEL):
      # Reserved for the tombstones of deletes: no write may name such a key (a remote one is dropped by __exec_store).
      raise ValueError(f"Keys starting with {self.CS_DEL!r} are reserved for the chain store's deletes.")
    # Outcome counted once by the finally below: every way out sets its own word, so one left unset is an error
    # (wave 02 counted every other way out as "refused"); and how a local write's wait ended, when it failed.
    outcome, wait_end = "error", None
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
      if not wait and self.__namespace_for_key(key, self.CS_PROTECTED_NAMESPACES):
        # The callers of the protected namespaces rely on the confirmation: wait=False is a caller's mistake there.
        raise ValueError(f"wait=False is not allowed in the protected namespace of key {key}.")
      if not wait and self.done_loop:
        # This instance's plugin loop has ended (a stop, or an in-process restart before the next instance publishes
        # its setter), so no lap would send the store or its re-sends: nothing is applied, as before a start.
        outcome = "not_ready"
        # The caller retries later, as it does for a store that has not started.
        return False
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
          # Its own word: the namespace's oracle-only rule left no target, as the receivers' rule refuses senders.
          outcome = "refused_oracle_only"
          # Ends here, before the generic no-peer exit below, so it is not counted under that word.
          return False
      if not local_sync_storage_op and len(target_peers) == 0:
        self.P(
          f" === No target peers resolved for chain-store write on key {key}",
          color='y'
        )
        # No peer to confirm it; a seed whose list holds only itself ends here at once (its own address is not a
        # peer), where it waited 3 x 10 s for confirmations that never came.
        outcome = "refused_no_peers"
        return False
      secret_namespace = self.__secret_namespace(key)
      rotation_namespace = self.__namespace_for_key(key, (NODE_KEY_ROTATIONS_HKEY,))
      if rotation_namespace and not local_sync_storage_op and not self.__registry_member(self.ee_addr, dauth_only=True):
        # Only a dAuth registry member writes key rotations.
        outcome = "refused_not_member"
        return False
      if secret_namespace and not local_sync_storage_op:
        if not self.__registry_member(self.ee_addr):
          # This node is in neither registry.
          outcome = "refused_not_member"
          return False
        if any(not self.__registry_member(peer, dauth_only=True) for peer in target_peers):
          # A target outside the dAuth registry would receive a secret.
          outcome = "refused_not_member"
          return False
      with self.__persistence_lock:
        if not self.__versioned_record(key, value):
          # The signed-record rule of the namespace.
          outcome = "refused_versioned"
          # Not stored; from a peer, not confirmed.
          return False
        if self.__stale_revision(key, value):
          # A value with a lower "cs_rev" than the stored one: not stored, and from a peer not confirmed.
          outcome = "refused_cs_rev"
          # Before any other check of the value: the writer's own order says this copy is old.
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
              # A revocation marker is never emptied nor changed.
              outcome = "refused_secret_rule"
              return False
          elif value is not None and self.__revoked_secret(key) and not self.__erased_secret(key, value):
            # A revoked revision takes only its redacted tombstone.
            outcome = "refused_secret_rule"
            return False
        # The order switch, read once at start.
        order = self.__cs_modes["order"]
        if order == "off":
          # No stamp is made or read: a stamp a peer sent is dropped here, and the record and the message keep the
          # previous release's form.
          ver = None
        else:
          # A local write takes a fresh stamp; a remote one's is checked for form (malformed: ver_invalid, unstamped).
          ver = self.__ver_valid(ver, key, "remote") if local_sync_storage_op else self.__ver_tick()
        # Why the record must not be stored for its wire form, if so: larger than CS_MAX_RECORD_BYTES, or not
        # encodable. Asked with the owner, readonly mark, token and stamp the record would be stored with, so the
        # writer and every receiver measure the same record, in every mode of the order switch.
        word = self.__wire_refusal(key, value, owner, readonly, token, ver)
        if word is not None:
          # Refused: not stored, and from a peer not confirmed.
          outcome = "refused_" + word
          # Before the order rule, which then neither counts nor takes the stamp of a record refused here.
          return False
        if order != "off":
          # The order rule's word for this write (each counted as ver_<word>), None when admitted.
          reason = self.__ver_admit(key, value, ver, sender, "remote" if local_sync_storage_op else "local")
          # A hand-off re-send is refused on the rule's word in 'report' too, as the live-write steps of 'on'.
          if reason is not None and (order == "on" or resend):
            # Refused by the order rule: not stored, and from a peer not confirmed. In 'report' only counted.
            outcome = "refused_" + reason
            # Before the token and readonly checks, which an older or forged write does not reach.
            return False
          # The write-permission count, only counted: a remote write's writer is its verified sender.
          self.__perm_count(key, value, owner, token, sender if local_sync_storage_op else self.ee_addr,
                            "remote" if local_sync_storage_op else "local")
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
            # Refused, returned below: the stored record has another token.
            need_store, outcome = False, "refused_token"
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
              if ver is not None and ver > (self.__chain_storage[key].get(self.CS_VER) or []):
                # A newer stamp of the same value: kept in place, with no dirty mark (nothing a reader sees changed),
                # so it reaches the file with the next save or the save of an orderly stop.
                self.__chain_storage[key][self.CS_VER], self.__chain_storage_unmarked = ver, True
              return True
            # A previous timeout may have left only the local copy. Re-publish
            # and collect distinct peer confirmations instead of claiming success.
            # The replica stays as it was when the final owner and readonly (after the namespace rules above)
            # match the stored ones too; the token is already known to be equal here. Before, it was marked.
            unchanged = owner == existing_owner and readonly == is_readonly
          elif is_readonly and existing_owner != owner:
            if debug:
              self.P(f" === Key {key} readonly by {existing_owner} (requester: {owner})", color='r')
            # Refused, returned below: the stored record is readonly for another owner.
            need_store, outcome = False, "refused_readonly"
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
          if (local_sync_storage_op and value is not None and key not in self.__chain_storage
              and sender not in self.__chain_peers
              and not self.__namespace_for_key(key, self.CS_PROTECTED_NAMESPACES)
              and not self.__registry_member(sender)):
            # A key this replica did not hold, created outside the protected namespaces by a peer's store whose
            # verified sender is in neither oracle registry: counted per sender, and refused only past the limit when
            # one is set. A sender among the replication peers is not counted, so the registries are read only for the
            # other senders' new keys.
            if 0 < self.__new_keys_max <= self.__cs_stats.get(f"new_keys/{sender}", 0):
              # With a limit set (CSTORE_NEW_KEYS_MAX above 0) and this sender's count of the current CSTORE_STATS
              # period at it: refused under its own word, not stored, not counted as a new key, and not confirmed.
              outcome = "refused_new_keys"
              # As every refused store: answered to its sender when stamped and the order switch is 'on'.
              return False
            # A new key this sender creates, in the count the limit reads.
            self.__cs_count(f"new_keys/{sender}")
          self.__set_key_value(
            key=key, value=value, owner=owner,
            local_sync_storage_op=local_sync_storage_op,
            readonly=readonly, token=token,
            peers=peers,
            include_default_peers=include_default_peers,
            # The stamp the record keeps; None with the order switch 'off'.
            ver=ver,
          )
          # The record this write left: a wait=False write registers its re-sends below only while it is still held.
          mine = self.__chain_storage.get(key)
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
        elif ver is not None:
          # An identical write still stores its fresh stamp: held in memory without a dirty mark, saved with the next
          # save or the save of an orderly stop.
          self.__chain_storage_unmarked = True
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
              # The write's stamp, for the receivers' order rule; absent with the order switch 'off' (a receiver of
              # the previous release reads only the fields above and ignores it).
              **({self.CS_VER: ver} if ver is not None else {}),
          }
          self.__ops.append(op)
          if not wait:
            if value is not None:
              # A value is re-sent by the plugin thread (__maybe_resend_unwaited) on the schedule of the wait below,
              # while unconfirmed; a newer wait=False write of the key replaces the entry, and a later blocking write
              # of another value or a delete ends it at the next lap (counted overwritten). The lock is held for this
              # check and one dict operation only (the plugin thread walks the dict under it).
              with self.__persistence_lock:
                if self.__chain_storage.get(key) is mine:
                  # Registered only while the key holds this write's record: a newer write applied since this one's
                  # apply (another thread) registered its own entry, which an older write must not replace.
                  self.__unwaited[key] = [op, self.time() + timeout, max_retries]
            # At once, without waiting for any confirmation.
            return True
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
                # Counted in addition to "changed": this write's value is no longer the stored one.
                wait_end = "overwritten"
                return False
              recv_confirm = self.__get_key_confirmations(key)
              min_confirm = self.__get_key_min_confirmations(key)
              # The peers settled on the record: those that confirmed it and those that refused it with an answer.
              settled = len(self.__chain_storage.get(key, {}).get("confirmed_peers", []))
              # Only for this node's own record (a count from 0 up): a sync copy that took its place during the wait
              # counts -1 and has no target list, so it is left to the timeout below, as in the previous release.
              if 0 <= recv_confirm < min_confirm and recv_confirm < settled >= len(
                self.__chain_storage[key]["target_peers"]
              ):
                # Every target settled, one at least by a refusal answer, and too few confirmed: no confirmation can
                # come, so the wait ends now instead of after the retries. Counted in addition to "changed".
                wait_end = "refused_by_peers"
                # The same False a wait that ran out returns.
                return False
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
                # Counted in addition to "changed" or "identical": too few peers confirmed after the retries.
                need_store, wait_end = False, "unconfirmed"
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
      # namespace, or "plain"), origin and outcome, through the one counting helper. Nothing here can raise for
      # the valid key checked above.
      self.__cs_count(outcome, key, "remote" if local_sync_storage_op else "local")
      if wait_end is not None:
        # A local write whose wait failed: also counted by how it ended.
        self.__cs_count(wait_end, key, "local")
      if result_info is not None:
        # The caller's copy of the word: the wait end when there is one, else the outcome.
        result_info["outcome"] = wait_end or outcome
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


  def __maybe_resend_unwaited(self):
    """
    Re-send the local writes made with ``wait=False`` that are still unconfirmed, on the schedule of a blocking wait.

    For each entry of the table ``_set_value`` fills for a non-blocking write of a value, the checks of the blocking
    wait loop, once per lap instead of every 100 ms:

    - the key no longer holds the write's owner and value: dropped, counted ``<bucket>/local/overwritten``;
    - enough confirmations: dropped, nothing counted (a confirmed blocking write counts nothing either);
    - an own record whose every target settled, one at least by a refusal answer, with too few confirmations:
      dropped, counted ``<bucket>/local/refused_by_peers``;
    - before the entry's re-send time: kept;
    - re-sends left: the same store queued again, the next re-send one timeout later;
    - none left: dropped, counted ``<bucket>/local/unconfirmed``.

    With the defaults (timeout 10 s, 2 retries) the store goes at 0, 10 and 20 s and the entry ends unconfirmed at
    30 s, as a blocking write; after that, with ``CSTORE_HANDOFF`` 'on', the hand-off (``__maybe_handoff``) re-sends
    to targets that never settled, as for any own record (in 'report' it only counts what it would send).

    Returns
    -------
    None

    Notes
    -----
    Runs on the plugin thread from ``process``, before the queued stores are sent. The walk holds the persistence
    lock, under which ``_set_value`` adds or replaces an entry from the caller's thread. A ``wait=False`` write
    registers its entry only while the key still holds the record it left, so when two threads write one key, an
    older write's entry never replaces a newer one's. A blocking write or a delete registers nothing: the entry
    of an earlier ``wait=False`` write of the key then ends at the next lap, counted ``overwritten``, unless the
    owner and value are unchanged, when it goes on re-sending its own store (with that write's stamp) until the
    record is confirmed or its re-sends run out. The table is empty unless a caller uses ``wait=False`` (one entry
    per key in flight), so a lap pays one emptiness check.

    Examples
    --------
    >>> self.__maybe_resend_unwaited()   # 10 s after a wait=False write no peer confirmed: queued again
    """
    if not self.__unwaited:
      return
    # The wall clock the re-send times are on.
    now = self.time()
    # The walk under the lock the caller's thread adds entries under.
    with self.__persistence_lock:
      # Each entry, over a copy, so one can be dropped.
      for key, entry in list(self.__unwaited.items()):
        # The write's store, its next re-send time and the re-sends left.
        op, due, retries = entry
        # The record the write left, if it is still held.
        record = self.__chain_storage.get(key, {})
        # Its confirmations and how many peers settled it, read as the blocking wait reads them.
        confirms, settled = record.get(self.CS_CONFIRMATIONS, 0), len(record.get("confirmed_peers", []))
        if record.get(self.CS_OWNER) != op[self.CS_OWNER] or record.get(self.CS_VALUE) != op[self.CS_VALUE]:
          # Another write replaced it: its own confirmations never establish this one.
          end = "overwritten"
        elif confirms >= record.get(self.CS_MIN_CONFIRMATIONS, 0):
          # Confirmed by enough peers.
          end = None
        elif 0 <= confirms < settled >= len(record["target_peers"]):
          # An own record every target settled, one at least by a refusal answer: no confirmation can come.
          end = "refused_by_peers"
        elif now < due:
          continue
        elif retries:
          # Re-sent: the same store queued again, the next re-send one timeout later.
          self.__ops.append(op)
          # In place, so the entry stays the same object.
          entry[1:] = [now + op[self.CS_TIMEOUT], retries - 1]
          continue
        else:
          # Too few confirmations after the re-sends.
          end = "unconfirmed"
        # Done with this write.
        del self.__unwaited[key]
        if end is not None:
          # Counted as a blocking write's wait end is.
          self.__cs_count(end, key, "local")


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

    A write of a dAuth secret namespace from a sender in neither registry, or received by a node outside the
    dAuth registry, and a key rotation from a sender outside the dAuth registry, are dropped unconfirmed and
    counted as ``<bucket>/remote/refused_not_member`` (before, they were not counted).

    A write or delete of a Tunnels Manager namespace, or of a deployed-job pointer without a revision
    (``__oracle_only_write``), is applied only when the sender is an oracle registry member (either registry,
    as ``__registry_member`` reads them). A refused one gets no confirmation and is counted as
    ``<bucket>/remote/refused_oracle_only``; ``_set_value`` is not called for it, so it is counted once. When
    the registries cannot be read, ``__registry_member`` answers False and the store is refused, as for the
    dAuth namespaces. The writes ``_set_value`` refuses are counted there, under their own words.

    The store's version stamp and its verified sender go to ``_set_value``, whose order rule reads them
    (switch ``CSTORE_ORDER``; with it 'off' the stamp is dropped there). A store that carries ``resend`` (a
    hand-off re-send) goes there as one, and is refused on the order rule's word in 'report' as in 'on'.

    The sender of a stamped store (order switch not 'off' here) becomes a peer the hand-off may re-send to; the
    sender of an unstamped one stops being one: a node of the previous release applies a re-sent delete in
    arrival order and could remove a newer value.

    Confirmation form (switch ``CSTORE_LEAN_CONFIRM``, read once at start): a stamped store (order switch not
    'off') is confirmed with ``{op, key, ver}`` when the switch is 'on', and with the previous release's full
    form plus ``ver`` when it is 'report'; an unstamped store, and every store with the switch 'off', get the
    previous release's full form.

    Refusal answer (order switch ``CSTORE_ORDER`` 'on', or a re-send in 'report'): a stamped store that
    ``_set_value`` refuses is answered to its sender with one confirmation message
    ``{op, key, ver, refused, held_ver}``: the store's
    stamp echoed, the refusal word without its ``refused_`` prefix, and this node's stamp for the key (None
    when unstamped or not held), never the value. A refused ``future`` stamp, a record that is not encodable
    (``unencodable``: the answer would echo its key and stamp, which may hold the string no message can carry),
    an unstamped store, and the stores dropped before ``_set_value`` (membership, oracle-only, ``"dl:"`` keys) are
    not answered. A record over the size cap (``too_large``) is answered like any other refusal. In
    'report' nothing is sent for a live store and nothing more is counted. A writer of the previous release
    never gets one: it sends no stamp.

    A store whose key starts with ``"dl:"``, the prefix of the tombstones, is dropped unconfirmed and counted
    as ``<bucket>/remote/refused_invalid_key`` under the bucket of the key after the prefix; ``_set_value``
    would raise for it, and a raise here would stall the plugin loop. So is a store or a delete whose key is
    not a non-empty str (a number, a list, a dict, a missing or empty key), which no node of this release
    sends: counted as ``plain/remote/refused_invalid_key``.

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
    if not isinstance(key, str) or not key or key.startswith(self.CS_DEL):
      # A key no store may name: one that is not a non-empty str (a number, a list, a missing key), or one reserved for
      # tombstones. _set_value raises for each, and a raise here leaves the plugin loop, which then sleeps, so one such
      # store would stall every write here: it is dropped unconfirmed and counted, under the bucket of the key after
      # the tombstones' prefix, or under "plain" for a key that is not a str.
      self.__cs_count("refused_invalid_key", key.removeprefix(self.CS_DEL) if isinstance(key, str) else "", "remote")
      return
    if self.__secret_namespace(key):
      if not self.__registry_member(peers) or not self.__registry_member(self.ee_addr, dauth_only=True):
        # Counted as a refused remote write of the bucket; before, these drops were not counted at all.
        self.__cs_count("refused_not_member", key, "remote")
        return
    if self.__namespace_for_key(key, (NODE_KEY_ROTATIONS_HKEY,)) and not self.__registry_member(peers, dauth_only=True):
      # The same for a key rotation from a sender outside the dAuth registry.
      self.__cs_count("refused_not_member", key, "remote")
      return
    # Tunnel namespaces and unversioned deployed-job pointers are written only by plugins that run on oracles: such
    # a write or delete is applied only when its sender is an oracle registry member. A refused one gets no
    # confirmation, like a write that _set_value refuses.
    if self.__oracle_only_write(key, value) and not self.__registry_member(peers):
      # Counted under its own word, the one a local write gets when this rule leaves it no target, through the one
      # counting helper; the key is a str here (it is in a hash namespace), and its bucket is its "hs:" prefix.
      self.__cs_count("refused_oracle_only", key, "remote")
      return
    if self.cfg_chain_store_debug:
      self.P(f" === REMOTE: Exec remote-to-local-sync store for {key}={value} by {owner}")
    # The store's stamp when it is well formed (counted once, by _set_value, when it is not); none with the order
    # switch 'off'. Only a stamped store is answered when refused.
    ver = None if self.__cs_modes["order"] == "off" else self.__ver_valid(data.get(self.CS_VER))
    # The sender joins the peers a hand-off re-send may go to when its store is stamped, and leaves them when it is
    # not: a node of the previous release (or with the order switch 'off') would apply a re-sent delete in arrival
    # order over a newer value.
    (self.__stamped_peers.add if ver is not None else self.__stamped_peers.discard)(peers)
    # A hand-off re-send, which the order rule refuses on its word in every mode but 'off'.
    resend = bool(data.get(self.CS_RESEND))
    # The word _set_value ends with: the refusal word of a refused store.
    info = {}
    result = self._set_value(
      key, value, owner=owner, 
      token=token, readonly=readonly,
      local_sync_storage_op=True,
      # The store's stamp as received (checked there, and read only when the order switch is not 'off'), and the
      # verified sender its stamp must name.
      ver=data.get(self.CS_VER), sender=peers,
      # How it ended; and whether it is a re-send.
      result_info=info, resend=resend,
    )
    if result:
      # now send confirmation of the storage execution
      if self.cfg_chain_store_debug:
        self.P(f" === REMOTE: {self.CS_CONFIRM} for {key} of {owner} back to sender {peers}")
      # The lean switch, read once at start.
      lean = self.__cs_modes["lean_confirm"]
      data = {
        self.CS_DATA : {
          self.CS_OP : self.CS_CONFIRM,
          self.CS_KEY: key,
          self.CS_VALUE : value,
          self.CS_OWNER : owner,
          self.CS_CONFIRM_BY : self.get_instance_path(),
          self.CS_CONFIRM_BY_ADDR : self.ee_addr,
          # The previous release's full form; with the lean switch 'report' it also carries the store's stamp, so the
          # writer counts how the lean rule would have judged it.
          **({self.CS_VER: ver} if ver is not None and lean != "off" else {}),
        }
      }
      if ver is not None and lean == "on":
        # A stamped store with the lean switch 'on': confirmed by key and stamp, without echoing the value.
        data = {self.CS_DATA: {self.CS_OP: self.CS_CONFIRM, self.CS_KEY: key, self.CS_VER: ver}}
      self.__send_data_to_chain_peers(data, peers=peers, include_default_peers=False)
    elif ver is not None and (self.__cs_modes["order"] == "on" or resend) and info["outcome"] not in (
        "refused_future", "refused_unencodable"):
      # A refused stamped store, with the order switch 'on' (or a re-send, refused in 'report' too): answered to its
      # sender in a confirmation message that carries the refusal word and this node's stamp for the key instead of
      # the value, so the writer stops waiting for it (or re-sending it). A future stamp is not answered: refused
      # now, it can be taken once the clocks agree. Nor is a record that is not encodable: the answer would echo its
      # key and stamp, which may hold the string no message can carry, and a node of this release never sends such a
      # store (its own write refuses it first).
      held = self.__chain_storage.get(key) or self.__chain_storage.get(self.CS_DEL + key)
      # The answer, to the sender only.
      self.__send_data_to_chain_peers({self.CS_DATA: {
        self.CS_OP: self.CS_CONFIRM, self.CS_KEY: key, self.CS_VER: ver,
        self.CS_REFUSED: info["outcome"][len("refused_"):], self.CS_HELD_VER: held and held.get(self.CS_VER),
      }}, peers=peers, include_default_peers=False)
    else:
      if self.cfg_chain_store_debug:
        self.P(f" === REMOTE: Store for {key}={value} of {owner} failed", color='r')
    return


  def __exec_received_confirm(self, data, sender=None):
    """
    Process a confirmation for a previously broadcast store operation.

    A refusal answer (a message with ``refused``, from a receiver with the order switch 'on') settles the
    sender on this node's own record whose stamp it echoes, when the sender is one of its targets and not
    yet settled: the sender joins ``confirmed_peers`` (now "settled": confirmed or refused) without adding to
    ``confirms``, so the wait of ``_set_value`` can end at once once every target settled. Its ``held_ver``,
    when well formed and not a future stamp, moves this node's stamp clock up to it. Handled in every mode.

    A confirmation counts when the sender is one of the record's targets, has not confirmed it yet, and the
    confirmation matches the record: a message that carries a value (the full form, the previous release's)
    when it echoes the record's owner and value; a lean one (``{op, key, ver}``, sent by a receiver with
    ``CSTORE_LEAN_CONFIRM`` 'on') when its stamp is the record's ``sent_ver``, the stamp of this node's last
    own store, so it still counts after a peer's identical write raised the record's stamp. A full form that
    also carries a stamp ('report') is counted by value; when the two rules disagree it is counted as
    ``<bucket>/confirm/disagree_own`` (an echo of an earlier own stamp of the same value) or
    ``disagree_other``. The record is the key's, or, when the key is not held,
    the tombstone a stamped delete left under ``"dl:" + key`` (none with the order switch 'off'), so a delete's
    confirmation (value None) now lands on the tombstone of a local delete instead of being dropped.

    A counted confirmation or a settling refusal answer also ends the hand-off's pair of that record and sender
    (``__maybe_handoff``), when its table tracks it; a confirmation of a pair already re-sent (in 'report', one
    'on' would have re-sent) is counted as ``<bucket>/handoff/answered``. A remote record, which has no peer
    lists, is left as it is (before, a confirmation of it added an empty settled list).

    Every message here, whatever key it names, also proves or withdraws its sender as a peer the hand-off may
    re-send to: one that carries a well-formed stamp (a lean confirmation, a full one from a receiver with the
    lean switch 'report', a refusal answer) comes only from a node of this release whose order switch is not
    'off', which admits a re-send by the order rule; one without a stamp withdraws it. A peer whose last store or
    confirmation to this node carried no stamp (the previous release, or the order or lean switch 'off') gets no
    re-send; a peer with the lean switch 'off' loses its proof at each confirmation, which is why the hand-off
    'on' does not start with that switch 'off'.

    A confirmation or refusal answer whose key is not a str (a number, a list, a dict, none), which no node of
    this release sends, is dropped before any of the above and counted as ``plain/confirm/refused_invalid_key``;
    a key that cannot be hashed raised here before.

    Parameters
    ----------
    data : dict
      Decrypted confirmation payload received from the network.
    sender : str, optional
      The verified address of the confirming node.

    Returns
    -------
    None

    Examples
    --------
    >>> self.__exec_received_confirm({"op": "SCONFIRM", "key": "k", "value": "v", "owner": owner}, sender="peer1")
    >>> self.__exec_received_confirm({"op": "SCONFIRM", "key": "k", "value": None, "owner": owner}, sender="peer1")
    """
    key = data.get(self.CS_KEY, None)
    value = data.get(self.CS_VALUE, None)
    owner = data.get(self.CS_OWNER, None)
    confirm_by = data.get(self.CS_CONFIRM_BY, None)
    op = data.get(self.CS_OP, None)
    if not isinstance(key, str):
      # A key that is not a str names no record, and one that cannot be hashed (a list, a dict) raised at the lookups
      # below: the message is dropped whole and counted, and proves or withdraws nothing about its sender.
      self.__cs_count("refused_invalid_key", "", "confirm")
      return

    if self.cfg_chain_store_debug:
      self.P(f" === LOCAL: Received {op} from {confirm_by} for  {key}={value}, owner{owner}")
    # A confirmation or refusal answer that echoes a well-formed stamp proves its sender stamps its stores, as a
    # stamped store does (oracles send a worker few stores, so a worker's oracles are proven this way); one without a
    # stamp (the previous release, or the order or lean switch 'off') withdraws the proof.
    (self.__stamped_peers.add if self.__ver_valid(data.get(self.CS_VER)) is not None
     else self.__stamped_peers.discard)(sender)
    if self.CS_REFUSED in data:
      # A refusal answer: it carries no value, so the confirmation rule below never matches it.
      with self.__persistence_lock:
        # This node's own record the refused store was for: the key's, else the tombstone of its delete.
        record = self.__chain_storage.get(key) or self.__chain_storage.get(f"{self.CS_DEL}{key}")
        if (record is None or record.get(self.CS_CONFIRMATIONS, -1) < 0 or data.get(self.CS_VER) is None
            or record.get(self.CS_SENT_VER) != data.get(self.CS_VER)
            or sender not in record.get("target_peers", []) or sender in record["confirmed_peers"]):
          return
        # Settled by the refusal: no confirmation will come from it for this stamp.
        record["confirmed_peers"].append(sender)
        # The hand-off's entry of the record, when it tracks it (the record's storage key: the key's, else the
        # tombstone's), so the settled pair leaves it.
        owner_record, pairs = self.__handoff.get(key if key in self.__chain_storage else f"{self.CS_DEL}{key}", (None, {}))
        if owner_record is record:
          # The hand-off re-sends it no more to this peer.
          pairs.pop(sender, None)
        # The receiver's stamp for the key, checked for form.
        held_ver = self.__ver_valid(data.get(self.CS_HELD_VER))
        if held_ver is not None and held_ver[0] <= int(1000 * self.time()) + self.__ver_ahead:
          # Taken into the clock, so this node's next write of the key is stamped above what the receiver holds.
          self.__ver_clock = max(self.__ver_clock, held_ver[0])
      return
    with self.__persistence_lock:
      # The record confirmed: the key's, or the tombstone of a delete of it (a key from the network is formatted, not
      # concatenated, so one that is not a str finds nothing instead of raising).
      target = key if key in self.__chain_storage else f"{self.CS_DEL}{key}"
      # Its owner and value, which a confirmation must echo.
      local_owner = self.__get_key_owner(target)
      # The value.
      local_value = self.__get_key_value(target)
      # The previous release's rule: the echoed owner and value are the record's.
      by_value = owner == local_owner and value == local_value
      # The lean rule: the echoed stamp is the one of this node's last own store of the record.
      by_stamp = data.get(self.CS_VER) is not None and data[self.CS_VER] == self.__chain_storage.get(
        target, {}).get(self.CS_SENT_VER)
      if self.CS_VALUE in data and self.CS_VER in data and by_value != by_stamp and target in self.__chain_storage:
        # A full confirmation with a stamp ('report') the two rules judge apart. The stamp it echoes, checked for form.
        echoed = self.__ver_valid(data[self.CS_VER])
        # "own" when it echoes an earlier own stamp of the same value (an identical rewrite since), "other" otherwise
        # (the lean rule would decide differently from the previous release's), under the key's bucket.
        self.__cs_count("disagree_own" if by_value and echoed is not None and echoed[1] == self.ee_addr
                        else "disagree_other", target.removeprefix(self.CS_DEL), "confirm")
      if value is None and target not in self.__chain_storage:
        # Deletion confirmed — key already removed locally, nothing to increment
        if self.cfg_chain_store_debug:
          self.P(f" === LOCAL: Key {key} deletion confirmed by {confirm_by}")
      elif by_value if self.CS_VALUE in data else by_stamp:
        # Counted by the rule of its form: a message that carries a value (the full form) by the echoed owner and
        # value, as the previous release; a lean one, without a value, by its stamp.
        # The record confirmed.
        record = self.__chain_storage.get(target, {})
        # Only a target that has not settled yet counts. The target list is read first, so a remote record, which has
        # no peer lists, is not given an empty settled list (before, setdefault added one).
        if sender not in record.get("target_peers", []) or sender in record["confirmed_peers"]:
          return
        # Settled by its confirmation.
        record["confirmed_peers"].append(sender)
        # Counted on the record confirmed: the tombstone's own counter for a delete.
        self.__increment_confirmations(target)
        # The hand-off's entry of the record, when it tracks it.
        owner_record, pairs = self.__handoff.get(target, (None, {}))
        if owner_record is record and (pairs.pop(sender, None) or [0])[0]:
          # The pair leaves it; one already re-sent (in 'report': that 'on' would have re-sent, so the re-send was
          # wasted) counts as answered, the reading the first re-send delay is tuned by.
          self.__cs_count("answered", target.removeprefix(self.CS_DEL), "handoff")
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

    A request whose ``hkey`` holds a surrogate code point (U+D800 to
    U+DFFF, which UTF-8 does not encode and the hash of the name raised
    for) is dropped unanswered and counted as
    ``plain/hsync/refused_invalid_key``. A request without a str
    ``request_id`` or ``hkey`` is dropped uncounted, as before.

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
    if hkey.encode("utf-8", "ignore") != hkey.encode("utf-8", "surrogatepass"):
      # A namespace name that holds a surrogate code point (a lone one: a JSON escape in a peer's message decodes to
      # it, and a pair is decoded to one character) cannot be hashed into an index, as UTF-8 does not encode it, and
      # the hash raised here: no node can hold such a namespace, so the request is dropped unanswered and counted.
      # Surrogates are the only code points UTF-8 does not encode: the first encoding leaves them out and the second
      # writes them, so the two differ exactly when the name holds one. Both run at the speed of the hash they guard
      # (0.2 microseconds for a name of 23 characters, where a scan of the characters in Python took 2, and 59 ms for
      # a name of a million characters, which a peer can send).
      self.__cs_count("refused_invalid_key", "", "hsync")
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
    Take one peer's snapshot answer for a pending request: recorded for a waiting caller, or merged at once.

    For a blocking sync (``_hsync``) the first valid answer is recorded in the request's entry, where the waiting
    caller finds and merges it. For a request the store made itself (an entry with ``until``, registered by
    ``__maybe_resync``) nobody waits: the answer is merged here, on the plugin thread, and so is the answer of
    every other peer that was asked, each peer once, until the request's wait is over.

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
    For a blocking sync the first valid response wins. This keeps each ``_hsync`` call
    deterministic and avoids merging multiple peer views into a single refresh
    operation. Freshness arbitration remains a documented follow-up concern.

    An answer to the store's own request passes the same checks (the sender was asked, the namespace is the one
    asked, the namespace's sender rules below) and is then merged by ``__merge_hset_snapshot``, whose rules
    decide each record. Of such an answer only the records whose value carries the namespace's signed field
    (``CS_SIGNED_FIELDS``: a sequence or a revision; every record for the key rotations, which have none and
    whose merge checks each proof) are merged: a pointer of the old form, a plain CID without a revision, that
    was removed here is not brought back, and a delete copy is never applied. The answering peer is taken off
    the request's peers, so it is not merged twice. Each merged answer counts ``<bucket>/resync/answered`` and
    adds the records it inserted or replaced to ``<bucket>/resync/merged`` (the label is present with 0 when it
    changed nothing), under the namespace's bucket. An answer that comes after the wait finds no entry and is
    dropped like any answer to an unknown request.

    An answer whose ``request_id`` is not a str (a number, a list, a dict), which no node of this release sends,
    is dropped and counted as ``plain/hsync/refused_malformed``; an id that cannot be hashed raised here before.

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
    if not isinstance(request_id, str):
      # An id that is not a str is no request's id, and one that cannot be hashed (a list, a dict) raised at the
      # lookup below: the answer is dropped and counted.
      self.__cs_count("refused_malformed", "", "hsync")
      return
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

    if self.CS_UNTIL in pending:
      # An answer to a request the store made itself (the resync): this peer's answer is taken once.
      allowed_peers.remove(sender)
      # The signed sequence or revision the namespace's values carry (none for the key rotations).
      field = self.CS_SIGNED_FIELDS.get(hkey)
      # Merged now, on the plugin thread, as no caller waits: every asked peer's answer, not only the first. Only
      # the records whose value carries the signed field are taken, so a pointer of the old form (a plain CID, with
      # no revision) that was removed here is not brought back, and no delete is applied.
      merged = self.__merge_hset_snapshot(hkey, {
        key: record for key, record in snapshot.items()
        if isinstance(record, dict) and (field is None or self.__rev_order(None, record.get(self.CS_VALUE), field))
      })
      # Counted under the namespace's bucket: the answer, and the records it inserted or replaced.
      self.__cs_count("answered", index, "resync")
      # The records.
      self.__cs_count("merged", index, "resync", n=merged)
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
    """
    Handle one chain-store payload from the network: decrypt it and run the operation it names.

    The payload's ``CHAIN_STORE_DATA`` names one operation in ``op``: a store (``SSTORE``, a delete included), a
    confirmation or refusal answer (``SCONFIRM``), a namespace-sync request (``SHSYNC_REQ``) or its answer
    (``SHSYNC_RESP``). Each goes to its own method with the payload's verified sender. A payload that is not
    addressed to this node, cannot be decrypted, or names no known operation is ignored.

    Parameters
    ----------
    payload : dict
      The received payload, signature already verified by the network processor; its fields are those of
      ``const.PAYLOAD_DATA`` (sender, destination, the encrypted data).

    Returns
    -------
    None

    Notes
    -----
    Runs on the plugin thread, inside the lap that takes the queued inputs. An exception that leaves this method
    ends that lap: the inputs not yet taken wait for the next one and the plugin loop sleeps first, so every
    input a peer can send must end here without one. What a peer controls is checked where it is read: a
    ``CHAIN_STORE_DATA`` that is not a dict is dropped here and counted as ``plain/remote/refused_malformed``
    (reading its operation raised before); keys, namespace names and request ids are checked by the operations'
    methods.

    Examples
    --------
    >>> self.default_handler(payload)   # a store from a replication peer: applied and confirmed
    """
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
    if not isinstance(data, dict):
      # The operation's fields must be a dict: any other JSON value (a list, a str, a number) names no operation, and
      # reading one from it raised. Dropped and counted.
      self.__cs_count("refused_malformed", "", "remote")
      return
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
    Run one plugin lap: refresh the replication peers when due, send the resync's next request when due
    (``__maybe_resync``), run the hand-off's sweep when due
    (``__maybe_handoff``), queue the due re-sends of writes made with ``wait=False``
    (``__maybe_resend_unwaited``) and send the queued writes.

    Saving is not part of the lap any more: the SerializationManager calls
    ``__maybe_flush_chain_storage`` on its own thread every ``CS_SAVE_INTERVAL`` seconds. This thread used
    to run the full-store save itself (0.26 s on an idle node, 1.3 s on a busy one for 5 MB at 0.7 CPU)
    and handled no write meanwhile. It can still wait briefly on the persistence lock while the save
    copies the records, and on the interpreter while the save serializes them.

    Every ``CS_STATS_INTERVAL`` seconds the lap also writes one ``CSTORE_STATS`` line: the counters taken
    (and reset) under the persistence lock, then logged outside it as one JSON object with sorted keys::

      CSTORE_STATS {"counts":{...},"handoff_pending":0,"lost_inputs":0,"modes":{"handoff":"report",
                    "lean_confirm":"report","order":"report","save_backoff":"report"},"period_s":300,
                    "queue_peak":0,"stamp_lead_max_s":0.0}

    (one line in the log). ``modes`` is the table of the wave 03 switches read once by ``on_init``.
    ``stamp_lead_max_s`` is the largest lead over this node's wall clock, in seconds, of a version stamp the
    order rule took in the period (a peer's clock ahead of this one, plus the transit time); 0 when none led.
    ``handoff_pending`` is the number of the hand-off's pairs (an own record and a target) not settled at its
    last sweep.
    Before the counters are taken, the lap sweeps the tombstones (``__sweep_tombstones``). The first lap after
    each full UTC hour of the wall clock (none before the first full hour after start) writes one
    ``CSTORE_CENSUS`` line (``__log_census``), after the stats line when both fall on that lap, so every node
    takes its census within a lap of the same moment, whenever it started.

    Returns
    -------
    None

    Notes
    -----
    The line is written also when nothing was counted, so a missing line means this loop is not running
    (or the wall clock, which times the interval, was set back). ``queue_peak`` is the largest input queue
    a lap found when it reached this method, after the lap took its inputs, so a full queue shows as
    ``lost_inputs``: the increase of the plugin's own count of inputs dropped at a full queue. Adding
    ``modes`` costs one small dict in a line written every 300 s; a parser that reads the line by key is
    not affected by the new key.

    Examples
    --------
    >>> self.process()   # one lap of the plugin loop; every 300 s it also logs the CSTORE_STATS line
    """
    self.__maybe_refresh_chain_peers()
    # The resync of the signed namespaces: its next request when due, and its requests whose wait is over forgotten.
    self.__maybe_resync()
    # The hand-off's sweep, when due, before the queued stores go out, so its re-sends leave on this lap.
    self.__maybe_handoff()
    # The re-sends of the writes made with wait=False, when due, on this lap too.
    self.__maybe_resend_unwaited()
    self.__maybe_broadcast()
    # Keep the largest input queue seen since the last CSTORE_STATS line; before, it was not kept.
    self.__cs_stats_queue_peak = max(self.__cs_stats_queue_peak, self.input_queue_size)
    if self.time() - self.__cs_stats_last >= self.CS_STATS_INTERVAL:
      # The tombstones swept (expired, past the cap, or all with the order switch 'off'), counted into this line.
      self.__sweep_tombstones()
      # Take the counters and start new ones, under the lock the counts are made under.
      with self.__persistence_lock:
        # The counts go to the new dict from here; the old one is only read below. The same for the largest lead of
        # a stamp taken over the wall clock, kept by the order rule under this lock.
        counts, self.__cs_stats, lead, self.__ver_lead = self.__cs_stats, {}, self.__ver_lead, 0
      # The clock and the plugin's count of lost inputs, read once: for this line and as the start of the next one.
      now, lost = self.time(), self.lost_inputs_count
      # One JSON object with sorted keys on one line, built and logged outside the lock.
      # Also the mode of each wave 03 switch, read once at start, so a line says whether each rule counted in it
      # acted ('on') or was only evaluated ('report').
      self.P("CSTORE_STATS " + self.json_dumps({
        "period_s": round(now - self.__cs_stats_last), "counts": counts, "modes": self.__cs_modes,
        "queue_peak": self.__cs_stats_queue_peak, "lost_inputs": lost - self.__cs_stats_lost,
        # In seconds, 0 when no stamp taken led the wall clock (always with the order switch 'off').
        "stamp_lead_max_s": round(lead / 1000, 1),
        # The hand-off's pairs left pending by its last sweep (0 with it 'off'): the size of its in-memory table.
        "handoff_pending": self.__handoff_pending,
      }, sort_keys=True))
      # The next line is measured from here.
      self.__cs_stats_last, self.__cs_stats_queue_peak, self.__cs_stats_lost = now, 0, lost
    # The UTC hour of the wall clock.
    hour = int(self.time() // 3600)
    if hour != self.__census_hour:
      # The first lap after each full hour, on every node alike (before, every 12th stats line counted from each
      # node's own start, up to an hour apart between nodes), so the oracles' digests are taken together.
      self.__census_hour = hour
      # The census line.
      self.__log_census()
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
    The final save runs on the closing plugin's own thread, as before. It is made when the replica is marked
    changed, and also when it holds changes kept without a dirty mark (a newer stamp on an unchanged value),
    which no periodic save writes on their own: without it they were lost at every orderly stop, and the
    restarted replica could take an older write over them.

    Examples
    --------
    >>> self.on_close()   # at an orderly stop: the registration removed, pending changes saved
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
    if self.__chain_storage_dirty or self.__chain_storage_unmarked:
      # Also changes held without a dirty mark (a newer stamp on an unchanged value), which no periodic save writes
      # alone: lost at a stop otherwise, so a restarted replica would take an older write over them.
      self.__save_chain_storage()
    super().on_close()
    return

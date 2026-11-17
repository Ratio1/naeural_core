import base64
import copy
import hashlib
import json
import unittest
from threading import RLock
from pathlib import Path
from types import SimpleNamespace

from naeural_core import __file__ as _NAEURAL_CORE_INIT_FILE


def _load_class_from_source(source_path, class_name, extra_globals=None, strip_text=()):
  source = Path(source_path).read_text(encoding="utf-8")
  for needle in strip_text:
    source = source.replace(needle, "")
  namespace = {"__name__": f"loaded_{class_name}"}
  if extra_globals:
    namespace.update(extra_globals)
  exec(compile(source, str(source_path), "exec"), namespace)  # noqa: S102
  return namespace[class_name]


_BASE_DIR = Path(_NAEURAL_CORE_INIT_FILE).resolve().parent
_BasePluginAPIMixin = _load_class_from_source(
  _BASE_DIR / "business" / "base" / "base_plugin_biz_api.py",
  "_BasePluginAPIMixin",
)


class _FakeNetworkProcessorPlugin:
  CONFIG = {"VALIDATION_RULES": {}}

  @staticmethod
  def payload_handler(signature="DEFAULT"):
    def decorator(func):
      return func

    return decorator

  def on_close(self):
    return


ChainStoreBasePlugin = _load_class_from_source(
  _BASE_DIR / "business" / "default" / "admin" / "chain_store_base.py",
  "ChainStoreBasePlugin",
  extra_globals={"NetworkProcessorPlugin": _FakeNetworkProcessorPlugin},
  strip_text=("from naeural_core.business.base.network_processor import NetworkProcessorPlugin\n",),
)


class _PayloadData:
  EE_SENDER = "EE_SENDER"
  EE_ID = "EE_ID"
  EE_DESTINATION = "EE_DESTINATION"
  EE_IS_ENCRYPTED = "EE_IS_ENCRYPTED"


class _Const:
  PAYLOAD_DATA = _PayloadData


def _hset_index(hkey):
  return f"hs:{hashlib.sha256(hkey.encode('utf-8')).hexdigest()[:10]}:"


def _hset_key(hkey, field):
  encoded = base64.urlsafe_b64encode(field.encode("utf-8")).decode("utf-8")
  return _hset_index(hkey) + encoded


def _chain_store_record(key, value, owner, readonly=False, token=None):
  return {
    ChainStoreBasePlugin.CS_KEY: key,
    ChainStoreBasePlugin.CS_VALUE: value,
    ChainStoreBasePlugin.CS_OWNER: owner,
    ChainStoreBasePlugin.CS_READONLY: readonly,
    ChainStoreBasePlugin.CS_TOKEN: token,
    ChainStoreBasePlugin.CS_CONFIRMATIONS: 0,
    ChainStoreBasePlugin.CS_MIN_CONFIRMATIONS: 1,
  }


def _hset_record(hkey, field, value, owner, readonly=False, token=None):
  key = _hset_key(hkey, field)
  return key, _chain_store_record(
    key=key,
    value=value,
    owner=owner,
    readonly=readonly,
    token=token,
  )


def _build_hset_storage(hkey, fields, owner):
  storage = {}
  for field, value in fields.items():
    key, record = _hset_record(hkey, field, value, owner=owner)
    storage[key] = record
  return storage


class _PeerSelectionHarness(_BasePluginAPIMixin):
  def __init__(self):
    super().__init__()
    self.cfg_chainstore_peers = ["peer-config", "peer-config", "peer-self"]
    self.ee_addr = "peer-self"
    self.ee_id = "peer-self-id"
    self._stream_id = "stream"
    self._signature = "CHAIN_STORE"
    self.cfg_instance_id = "instance"
    self._now = 0.0
    self.messages = []
    self.calls = []
    self.plugins_shmem = {
      "__chain_storage_set": self._record_set,
      "__chain_storage_hsync": self._record_hsync,
    }

  def P(self, msg, color=None, **kwargs):  # pylint: disable=unused-argument
    self.messages.append(msg)

  def start_timer(self, _name):
    return None

  def end_timer(self, _name):
    return 0.0

  def time(self):
    return self._now

  def sleep(self, seconds):
    self._now += seconds

  @staticmethod
  def json_dumps(value):
    return json.dumps(value)

  @staticmethod
  def json_loads(value):
    return json.loads(value)

  @staticmethod
  def deepcopy(value):
    return copy.deepcopy(value)

  def _record_set(self, key, value, **kwargs):
    self.calls.append(("set", key, value, kwargs))
    return {"kind": "set", "key": key, "value": value, "kwargs": kwargs}

  def _record_hsync(self, hkey, **kwargs):
    self.calls.append(("hsync", hkey, kwargs))
    return {"kind": "hsync", "hkey": hkey, "kwargs": kwargs}


class _FakeNetwork:
  def __init__(self):
    self.nodes = {}

  def register(self, node):
    self.nodes[node.ee_addr] = node
    node.network = self

  def dispatch(self, sender, targets, decoded):
    if isinstance(targets, str):
      targets = [targets]
    elif not isinstance(targets, list):
      targets = []

    for target in targets:
      peer = self.nodes.get(target)
      if peer is None:
        continue
      payload = {
        _PayloadData.EE_SENDER: sender,
        _PayloadData.EE_ID: sender,
        _PayloadData.EE_DESTINATION: [target],
        _PayloadData.EE_IS_ENCRYPTED: True,
        "decoded": decoded,
      }
      peer.default_handler(payload)


class _WhitelistBlockEngine:
  def __init__(self, peers):
    self.peers = list(peers)

  def get_whitelist(self, with_prefix=False):  # pylint: disable=unused-argument
    return list(self.peers)


class _SummaryControlNetmon:
  def __init__(self, online_for_control):
    self.online_for_control = set(online_for_control)
    self.direct_calls = []
    self.control_calls = []

  def network_node_is_online(self, peer):
    self.direct_calls.append(peer)
    return False

  def network_node_simple_status(self, peer, dt_now=None, last_exception_check_time_minutes=60):  # pylint: disable=unused-argument
    self.control_calls.append(peer)
    return "ONLINE" if peer in self.online_for_control else "LOST STATUS"


class _StatusNetmon:
  """NetMon that remembers an error for an hour unless asked for the latest status only."""

  def __init__(self, with_memory, latest):
    self.with_memory, self.latest, self.asked = dict(with_memory), dict(latest), []

  def network_node_simple_status(self, peer, dt_now=None, last_exception_check_time_minutes=60):  # pylint: disable=unused-argument
    self.asked.append(last_exception_check_time_minutes)
    return (self.with_memory if last_exception_check_time_minutes > 0 else self.latest)[peer]

  def network_node_is_online_for_control(self, peer):
    return self.with_memory[peer] == "ONLINE"


class _OldNetmonStub:
  def __init__(self, online):
    self.online = set(online)
    self.calls = []

  def network_node_is_online(self, peer):
    self.calls.append(peer)
    return peer in self.online


class _ChainStoreRuntimeHarness(ChainStoreBasePlugin):
  # The mode of every switch of the store in a harness: 'off' runs these suites as the previous release behaves; the
  # report-mode run in test_chain_store_switches.py sets 'report' here for each of its tests.
  CSTORE_MODE = "off"

  def __init__(self, ee_addr, default_peers=None, storage=None):
    """
    Build a chain store with the state on_init would give it, without starting it.

    Parameters
    ----------
    ee_addr : str
      The node's address: its writes and confirmations are sent from it.
    default_peers : list of str, optional
      The replication peers the store starts with, as a peer refresh would leave them; none by default.
    storage : dict, optional
      Records (composed key -> record) the store holds, deep-copied; empty by default.

    Returns
    -------
    None

    Notes
    -----
    Every switch of the store is in the class's ``CSTORE_MODE``: both as the ``cfg_cstore_*`` value on_init reads
    and in the mode table on_init would build from them. The save back-off has its 30 s ceiling and no pause yet.

    Examples
    --------
    >>> store = _ChainStoreRuntimeHarness("peer-1", default_peers=["peer-2"])
    >>> store._ChainStoreBasePlugin__cs_modes["save_backoff"]
    'off'
    """
    self.ee_addr = ee_addr
    self.ee_id = f"{ee_addr}-id"
    self._stream_id = "stream"
    self._signature = "CHAIN_STORE"
    self.cfg_instance_id = "instance"
    self.const = _Const
    self.cfg_chain_store_debug = False
    self.cfg_chain_peers_refresh_interval = 60
    self.cfg_min_confirmations = 1
    self.cfg_max_inputs_queue_size = 1024
    self.input_queue_size = 0
    self.network = None
    self.sent_payloads = []
    self.messages = []
    self.saved_storage = []
    self._now = 0.0
    self._ChainStoreBasePlugin__chain_storage = copy.deepcopy(storage or {})
    self._ChainStoreBasePlugin__persistence_lock = RLock()
    self._ChainStoreBasePlugin__chain_storage_dirty = False
    self._ChainStoreBasePlugin__chain_peers = list(default_peers or [])
    self._ChainStoreBasePlugin__pending_hsync = {}
    self._ChainStoreBasePlugin__last_chain_peers_refresh = self._now
    # The state on_init creates for the CSTORE_STATS line, and the plugin's own queue counters it reads.
    self._ChainStoreBasePlugin__cs_stats = {}
    self._ChainStoreBasePlugin__cs_stats_last, self._ChainStoreBasePlugin__cs_stats_queue_peak = self._now, 0
    self._ChainStoreBasePlugin__cs_stats_lost = 0
    self.lost_inputs_count = 0
    # The four switches as on_init reads them from the plugin config, each in this harness's mode.
    self.cfg_cstore_order = self.cfg_cstore_lean_confirm = self.CSTORE_MODE
    # The other two.
    self.cfg_cstore_handoff = self.cfg_cstore_save_backoff = self.CSTORE_MODE
    # The mode table on_init builds from them.
    self._ChainStoreBasePlugin__cs_modes = dict.fromkeys(("order", "lean_confirm", "handoff", "save_backoff"),
                                                         self.CSTORE_MODE)
    # The save back-off's ceiling as the config gives it (the 30 s default) and as on_init reads it.
    self.cfg_cstore_save_max_gap, self._ChainStoreBasePlugin__save_max_gap = 30, 30.0
    # No save yet, so no pause: the first save is immediate, as after on_init.
    self._ChainStoreBasePlugin__save_resume = 0.0
    # The stamp bound as the config gives it (60 s) and as on_init reads it (ms); no stamp taken yet, a clock at 0.
    self.cfg_cstore_ver_max_ahead, self._ChainStoreBasePlugin__ver_ahead = 60, 60000
    # The largest stamp lead of the period and the stamp clock, as on_init starts them on an empty store.
    self._ChainStoreBasePlugin__ver_lead = self._ChainStoreBasePlugin__ver_clock = 0
    # Nothing held without a dirty mark.
    self._ChainStoreBasePlugin__chain_storage_unmarked = False
    # The tombstone life (7 days) and cap (570) as the config gives them and as on_init reads them; no horizon yet.
    self.cfg_cstore_tombstone_days, self.cfg_cstore_tombstone_cap = 7, 570
    # As on_init reads them: the life in ms, the cap, and the horizon of a store that removed no tombstone.
    self._ChainStoreBasePlugin__tomb_ms, self._ChainStoreBasePlugin__tomb_cap = 7 * 86_400_000, 570
    # No tombstone removed yet.
    self._ChainStoreBasePlugin__tomb_horizon = 0
    # The hand-off's numbers as the config gives them (first re-send after 300 s, 8 in flight per peer) and as on_init
    # reads them.
    self.cfg_cstore_handoff_first_s, self.cfg_cstore_handoff_window = 300, 8
    # As on_init reads them.
    self._ChainStoreBasePlugin__handoff_first, self._ChainStoreBasePlugin__handoff_window = 300.0, 8
    # The limit of new keys per outside sender as the config gives it and as on_init reads it: 0, they are only counted.
    self.cfg_cstore_new_keys_max = self._ChainStoreBasePlugin__new_keys_max = 0
    # The oracles a catch-up asks, as the config gives it and as on_init reads it.
    self.cfg_cstore_pull_peers = self._ChainStoreBasePlugin__pull_peers = 2
    # No marks owed and no catch-up running, as on_init leaves a store that keeps none (every switch 'off', or a
    # replica without the record).
    self._ChainStoreBasePlugin__pull_owed = self._ChainStoreBasePlugin__pull = None
    # No save made yet, so no duration of one for the catch-up's pace.
    self._ChainStoreBasePlugin__save_took = 0.0
    # How many inputs one lap takes, as the plugin config gives it: with more than that waiting, no page is sent.
    self.cfg_max_inputs_per_iteration = 32
    # No page sent yet, and the key prefixes no page carries, as on_init computes them: the tombstones' own, the
    # reserved key and the indexes of the six protected namespaces.
    self._ChainStoreBasePlugin__pull_sent, self._ChainStoreBasePlugin__pull_skip = 0, (
      "dl:", "hs:cstore_pull", *map(_hset_index, ChainStoreBasePlugin.CS_PROTECTED_NAMESPACES))
    # The hand-off's state as on_init leaves it at time 0: an empty table, the first sweep at 300 s, nothing pending.
    self._ChainStoreBasePlugin__handoff, self._ChainStoreBasePlugin__handoff_next = {}, 300.0
    # Nothing pending.
    self._ChainStoreBasePlugin__handoff_pending = 0
    # No peer dropped as lost or back yet, and no peer proven to stamp its stores.
    self._ChainStoreBasePlugin__peer_left, self._ChainStoreBasePlugin__peer_back = {}, {}
    # No stamped store received yet.
    self._ChainStoreBasePlugin__stamped_peers = set()
    # No write made with wait=False owed a re-send.
    self._ChainStoreBasePlugin__unwaited = {}
    # The plugin loop runs (the base plugin sets this when its loop is told to stop).
    self.done_loop = False
    # The UTC hour of the start (time 0), as on_init sets it: no CSTORE_CENSUS line before the next full hour.
    self._ChainStoreBasePlugin__census_hour = 0
    # The resync of the signed namespaces as on_init leaves it: due at the first lap that finds a peer, at its first
    # namespace.
    self._ChainStoreBasePlugin__resync_next, self._ChainStoreBasePlugin__resync_turn = 0, 0
    # No namespace owed a second request, as on_init leaves it.
    self._ChainStoreBasePlugin__resync_again = []

  def P(self, msg, color=None, **kwargs):  # pylint: disable=unused-argument
    self.messages.append(msg)

  def time(self):
    return self._now

  def sleep(self, seconds):
    self._now += seconds

  @staticmethod
  def deepcopy(value):
    return copy.deepcopy(value)

  @staticmethod
  def json_dumps(value, **kwargs):
    return json.dumps(value, **kwargs)

  @staticmethod
  def json_loads(value):
    return json.loads(value)

  @staticmethod
  def str_to_base64(value, url_safe=True):  # pylint: disable=unused-argument
    raw = value.encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw) if url_safe else base64.b64encode(raw)
    return encoded.decode("utf-8")

  @staticmethod
  def base64_to_str(value, url_safe=True):
    """
    Decode base64 text to a str exactly as the node's helper does (``plugin_base_utils.base64_to_str``).

    No padding is added and nothing is caught, so a text the node's helper raises for raises here too: one
    without its padding, one that is not UTF-8 once decoded, one that holds a lone surrogate. Before, this
    stand-in added the missing padding, and a key no node decodes passed in the tests.

    Parameters
    ----------
    value : str
      The base64 text, for example the field part of a composed hash key.
    url_safe : bool, optional
      True (the default) for the URL-safe alphabet the hash keys use; False for the standard one.

    Returns
    -------
    str
      The decoded text.

    Raises
    ------
    ValueError
      ``binascii.Error`` for wrong padding, ``UnicodeDecodeError`` for bytes that are not UTF-8,
      ``UnicodeEncodeError`` for a text UTF-8 cannot encode (all are ValueError).

    Examples
    --------
    >>> _ChainStoreRuntimeHarness.base64_to_str("YWxwaGE=")
    'alpha'
    >>> _ChainStoreRuntimeHarness.base64_to_str("YWxwaGE")
    Traceback (most recent call last):
    binascii.Error: Incorrect padding
    """
    # The text as bytes, as the node's helper encodes it.
    raw = value.encode("utf-8")
    # Decoded with the alphabet asked for.
    decoded = base64.urlsafe_b64decode(raw) if url_safe else base64.b64decode(raw)
    # And read as UTF-8.
    return decoded.decode("utf-8")

  @staticmethod
  def get_hash(value, algorithm="sha256", length=10):  # pylint: disable=unused-argument
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return digest[:length]

  @staticmethod
  def uuid(length=8):  # pylint: disable=unused-argument
    return "req-" + "1" * length

  def cacheapi_save_pickle(self, value, verbose=True, strict=False):  # pylint: disable=unused-argument
    self.saved_storage.append(copy.deepcopy(value))

  def cacheapi_load_pickle(self, default=None, verbose=True):  # pylint: disable=unused-argument
    return copy.deepcopy(default)

  def send_encrypted_payload(self, node_addr=None, **data):
    self.sent_payloads.append({
      "node_addr": copy.deepcopy(node_addr),
      "data": copy.deepcopy(data),
    })
    if self.network is not None:
      self.network.dispatch(self.ee_addr, node_addr, data)

  @staticmethod
  def receive_and_decrypt_payload(data):
    return copy.deepcopy(data.get("decoded", {}))

  def trace_info(self):
    return "trace"

  @property
  def storage(self):
    return self._ChainStoreBasePlugin__chain_storage

  @property
  def pending_hsync(self):
    return self._ChainStoreBasePlugin__pending_hsync

  def make_request_payload(self, request_id="req-1", sender="peer-requester", hkey="players"):
    return {
      _PayloadData.EE_SENDER: sender,
      _PayloadData.EE_ID: sender,
      _PayloadData.EE_DESTINATION: [self.ee_addr],
      _PayloadData.EE_IS_ENCRYPTED: True,
      "decoded": {
        self.CS_DATA: {
          self.CS_OP: self.CS_HSYNC_REQ,
          self.CS_REQUEST_ID: request_id,
          self.CS_HKEY: hkey,
        }
      },
    }

  def make_response_payload(self, request_id="req-1", sender="peer-1", hkey="players", snapshot=None):
    return {
      _PayloadData.EE_SENDER: sender,
      _PayloadData.EE_ID: sender,
      _PayloadData.EE_DESTINATION: [self.ee_addr],
      _PayloadData.EE_IS_ENCRYPTED: True,
      "decoded": {
        self.CS_DATA: {
          self.CS_OP: self.CS_HSYNC_RESP,
          self.CS_REQUEST_ID: request_id,
          self.CS_HKEY: hkey,
          self.CS_SNAPSHOT: copy.deepcopy(snapshot if snapshot is not None else {}),
        }
      },
    }


class TestChainStoreHsync(unittest.TestCase):
  def test_hash_key_helpers_match_production_writer_padding(self):
    writer = _PeerSelectionHarness()
    writer.str_to_base64 = lambda value, **kwargs: base64.urlsafe_b64encode(value.encode("utf-8")).decode("utf-8")
    writer.get_hash = _ChainStoreRuntimeHarness.get_hash
    for field in ("7|revision|revoked", "7|revisionx|revoked", "7|revisionxx|revoked"):
      with self.subTest(field=field):
        expected = writer._BasePluginAPIMixin__hset_key("DAUTH_JOB_SECRET_REVISIONS", field)
        self.assertEqual(_hset_key("DAUTH_JOB_SECRET_REVISIONS", field), expected)
        self.assertEqual(_hset_index("DAUTH_JOB_SECRET_REVISIONS") + _ChainStoreRuntimeHarness.str_to_base64(field), expected)

  def test_default_peer_refresh_uses_summary_control_liveness(self):
    requester = _ChainStoreRuntimeHarness("peer-requester")
    requester.bc = _WhitelistBlockEngine(["peer-1", "peer-2"])
    requester.netmon = _SummaryControlNetmon(online_for_control={"peer-1"})
    requester._now = 61

    requester._ChainStoreBasePlugin__maybe_refresh_chain_peers()

    self.assertEqual(requester._ChainStoreBasePlugin__get_target_peers(), ["peer-1"])
    self.assertEqual(requester.netmon.direct_calls, [])
    self.assertEqual(requester.netmon.control_calls, ["peer-1", "peer-2"])

  def test_default_peer_refresh_fails_on_a_netmon_without_the_status_call(self):
    # No silent fallback to the direct-heartbeat predicate: that would bring the hour of exception memory back.
    requester = _ChainStoreRuntimeHarness("peer-requester")
    requester.bc = _WhitelistBlockEngine(["peer-1", "peer-2"])
    requester.netmon = _OldNetmonStub(online={"peer-2"})
    requester._now = 61

    with self.assertRaises(AttributeError):
      requester._ChainStoreBasePlugin__maybe_refresh_chain_peers()
    self.assertEqual(requester.netmon.calls, [])

  def test_default_peer_refresh_uses_latest_status_not_the_hour_of_exception_memory(self):
    # A peer back after an error stays PAST-EXCEPTION for an hour; replication must still reach it.
    latest = {
      "peer-back": "ONLINE", "peer-summary": "PAST-EXCEPTION", "peer-lost": "LOST STATUS",
      "peer-exception": "EXCEPTION", "peer-shutdown": "SHUTDOWN", "peer-stop": "STOP",
    }
    with_memory = dict(latest, **{"peer-back": "PAST-EXCEPTION"})
    requester = _ChainStoreRuntimeHarness("peer-requester")
    requester.bc = _WhitelistBlockEngine(list(latest))
    requester.netmon = _StatusNetmon(with_memory=with_memory, latest=latest)
    requester._now = 61

    requester._ChainStoreBasePlugin__maybe_refresh_chain_peers()

    self.assertFalse(requester.netmon.network_node_is_online_for_control("peer-back"))  # what cut it off
    # An oracle's network summary still carries the hour of memory, so PAST-EXCEPTION from it counts.
    self.assertEqual(requester._ChainStoreBasePlugin__get_target_peers(), ["peer-back", "peer-summary"])
    self.assertEqual(set(requester.netmon.asked), {0})

  def test_chainstore_set_and_hsync_share_peer_selection(self):
    harness = _PeerSelectionHarness()

    set_result = harness.chainstore_set(
      "alpha",
      {"nested": {1: "one"}},
      extra_peers=["peer-extra", "peer-config", "peer-extra"],
      include_default_peers=False,
    )
    hsync_result = harness.chainstore_hsync(
      "players",
      extra_peers=["peer-extra", "peer-config", "peer-extra"],
      include_default_peers=False,
    )

    self.assertEqual(set_result["kwargs"]["peers"], ["peer-config", "peer-extra"])
    self.assertEqual(hsync_result["kwargs"]["peers"], ["peer-config", "peer-extra"])
    self.assertEqual(set_result["kwargs"]["include_default_peers"], False)
    self.assertEqual(hsync_result["kwargs"]["include_default_peers"], False)

  def test_hsync_uses_backend_default_peers_when_no_explicit_targets(self):
    requester = _ChainStoreRuntimeHarness("peer-requester", default_peers=["peer-1"])
    responder = _ChainStoreRuntimeHarness(
      "peer-1",
      storage=_build_hset_storage("players", {"alpha": "remote-alpha"}, owner="peer-1"),
    )
    network = _FakeNetwork()
    network.register(requester)
    network.register(responder)

    result = requester._hsync("players")

    self.assertEqual(
      result,
      {"hkey": "players", "source_peer": "peer-1", "merged_fields": 1},
    )
    self.assertEqual(requester.sent_payloads[0]["node_addr"], ["peer-1"])

  def test_hsync_overwrites_stale_overlaps_and_preserves_local_only_fields(self):
    requester = _ChainStoreRuntimeHarness(
      "peer-requester",
      storage={
        **_build_hset_storage("players", {"alpha": "local-alpha", "gamma": "local-gamma"}, owner="peer-requester"),
      },
    )
    responder = _ChainStoreRuntimeHarness(
      "peer-1",
      storage=_build_hset_storage("players", {"alpha": "remote-alpha", "beta": "remote-beta"}, owner="peer-1"),
    )
    network = _FakeNetwork()
    network.register(requester)
    network.register(responder)

    result = requester._hsync("players", peers=["peer-1"], include_default_peers=False)

    self.assertEqual(
      result,
      {"hkey": "players", "source_peer": "peer-1", "merged_fields": 2},
    )
    self.assertEqual(
      requester.storage[_hset_key("players", "alpha")][ChainStoreBasePlugin.CS_VALUE],
      "remote-alpha",
    )
    self.assertEqual(
      requester.storage[_hset_key("players", "beta")][ChainStoreBasePlugin.CS_VALUE],
      "remote-beta",
    )
    self.assertEqual(
      requester.storage[_hset_key("players", "gamma")][ChainStoreBasePlugin.CS_VALUE],
      "local-gamma",
    )
    self.assertEqual(len(requester.sent_payloads), 1)
    self.assertEqual(len(responder.sent_payloads), 1)

  def test_restart_sync_brings_back_an_old_value_unless_the_record_carries_a_revision(self):
    """A restarted replica that holds the newer value and syncs from a peer holding an older one keeps its own
    value when the record carries a cs_rev revision; without one the older copy wins, as before."""
    # The field both replicas hold.
    field = _hset_key("secrets", "wallet")
    # Without revisions the peer's old token replaces the new one; with revisions the new one is kept.
    for versioned, kept in ((False, "old-token"), (True, "new-token")):
      # One sub-test each.
      with self.subTest(versioned=versioned):
        # The revisions of the new and the old value, or none.
        new_rev, old_rev = ({"cs_rev": 200}, {"cs_rev": 100}) if versioned else ({}, {})
        # The replica that holds the new value and restarts.
        restarted = _ChainStoreRuntimeHarness(
          "peer-requester",
          storage=_build_hset_storage("secrets", {"wallet": {"token": "new-token", **new_rev}},
                                      owner="peer-requester"),
        )
        # The peer that still holds the old value.
        stale_peer = _ChainStoreRuntimeHarness(
          "peer-1",
          storage=_build_hset_storage("secrets", {"wallet": {"token": "old-token", **old_rev}}, owner="peer-1"),
        )
        # The network both answer on.
        network = _FakeNetwork()
        # The restarted replica, reachable for the answer.
        network.register(restarted)
        # The peer, which answers the sync.
        network.register(stale_peer)
        # The sync after the restart.
        result = restarted._hsync("secrets", peers=["peer-1"], include_default_peers=False)
        # Nothing merged when the record is versioned, the old copy merged when it is not.
        self.assertEqual(result["merged_fields"], 0 if versioned else 1)
        # The token the restarted replica holds.
        self.assertEqual(restarted.storage[field][ChainStoreBasePlugin.CS_VALUE]["token"], kept)
        # The other direction still works: a replica that is behind takes the newer copy.
        stale_peer._hsync("secrets", peers=["peer-requester"], include_default_peers=False)
        # Both replicas now hold the same token.
        self.assertEqual(stale_peer.storage[field][ChainStoreBasePlugin.CS_VALUE]["token"], kept)

  def test_a_live_write_is_ordered_by_its_revision_and_a_snapshot_never_downgrades(self):
    """A live write with a lower cs_rev is refused (refused_cs_rev, not confirmed), a higher or unversioned one is
    applied; a sync copy replaces a versioned record only with a higher revision."""
    # The field written.
    field = _hset_key("secrets", "wallet")
    # A replica of "peer-1" holding the field at cs_rev 200, with "peer-2" as its peer.
    node = self.make_versioned_replica(field)
    # A late or replayed copy at cs_rev 100 from "peer-2": refused and not confirmed.
    self.assertEqual(self.store_from_peer(node, field, {"token": "old-token", "cs_rev": 100}), 0)
    # The new token kept, and the refusal counted under its word.
    self.assertEqual((node.storage[field][node.CS_VALUE]["token"], node._ChainStoreBasePlugin__cs_stats),
                     ("new-token", {field[:14] + "/remote/refused_cs_rev": 1}))
    # The caller's dict of a local write.
    info = {}
    # The same old copy written locally: False with its word.
    self.assertEqual(
      (node._set_value(field, {"token": "old-token", "cs_rev": 100}, owner="local-writer", result_info=info), info),
      (False, {"outcome": "refused_cs_rev"}))
    # A newer copy from the peer: applied and confirmed.
    self.assertEqual(self.store_from_peer(node, field, {"token": "newer-token", "cs_rev": 300}), 1)
    # Its token stored.
    self.assertEqual(node.storage[field][node.CS_VALUE]["token"], "newer-token")
    # A writer that does not version yet (an oracle on older code): last arrival wins, as before.
    self.assertEqual(self.store_from_peer(node, field, {"token": "legacy-writer"}), 1)
    # Its token stored.
    self.assertEqual(node.storage[field][node.CS_VALUE]["token"], "legacy-writer")
    # A snapshot is old state by nature: it replaces a versioned record only with a newer version.
    for snapshot_value, kept in (({"token": "unversioned"}, "new-token"),
                                 ({"token": "old", "cs_rev": 100}, "new-token"),
                                 ({"token": "newest", "cs_rev": 300}, "newest")):
      # One sub-test each.
      with self.subTest(snapshot=snapshot_value):
        # A fresh replica holding cs_rev 200.
        node = self.make_versioned_replica(field)
        # The peer's record as a sync answer carries it.
        key, record = _hset_record("secrets", "wallet", snapshot_value, owner="peer-2")
        # The merge of that answer.
        node._ChainStoreBasePlugin__merge_hset_snapshot("secrets", {key: record})
        # The token kept or taken.
        self.assertEqual(node.storage[field][node.CS_VALUE]["token"], kept)

  @staticmethod
  def make_versioned_replica(field):
    """
    Build the replica of "peer-1" holding ``field`` = {"token": "new-token", "cs_rev": 200}, written by "peer-2".

    Parameters
    ----------
    field : str
      The composed key of the "wallet" field of the "secrets" namespace.

    Returns
    -------
    _ChainStoreRuntimeHarness
      The replica, with "peer-2" as its only peer and an instance path for its confirmations.

    Examples
    --------
    >>> node = TestChainStoreHsync.make_versioned_replica(_hset_key("secrets", "wallet"))
    """
    # The replica, with its peer and the versioned record.
    node = _ChainStoreRuntimeHarness(
      "peer-1", default_peers=["peer-2"],
      storage=_build_hset_storage("secrets", {"wallet": {"token": "new-token", "cs_rev": 200}}, owner="peer-2"),
    )
    # The instance path its confirmations name: a list's copy method returns the path when called.
    node.get_instance_path = ["peer-1", "stream", "CHAIN_STORE", "instance"].copy
    # Ready.
    return node

  @staticmethod
  def store_from_peer(node, field, value):
    """
    Deliver a live store of ``field`` = ``value`` from "peer-2" to ``node`` through its payload handler.

    Parameters
    ----------
    node : _ChainStoreRuntimeHarness
      The receiving replica.
    field : str
      The composed key written.
    value : dict
      The value written.

    Returns
    -------
    int
      How many payloads ``node`` sent back: 1 when it stored and confirmed the write, 0 when it refused it.

    Examples
    --------
    >>> TestChainStoreHsync.store_from_peer(node, field, {"token": "t", "cs_rev": 300})
    1
    """
    # Only what this store makes the replica send.
    node.sent_payloads.clear()
    # The payload as the network delivers it.
    node.default_handler({
      _PayloadData.EE_SENDER: "peer-2", _PayloadData.EE_ID: "peer-2", _PayloadData.EE_DESTINATION: ["peer-1"],
      _PayloadData.EE_IS_ENCRYPTED: True,
      "decoded": {node.CS_DATA: {
        node.CS_OP: node.CS_STORE, node.CS_KEY: field, node.CS_VALUE: value, node.CS_OWNER: "peer-2",
      }},
    })
    # The confirmations sent back.
    return len(node.sent_payloads)

  def test_hsync_treats_empty_snapshot_as_successful_cold_state(self):
    requester = _ChainStoreRuntimeHarness(
      "peer-requester",
      storage=_build_hset_storage("players", {"alpha": "local-alpha"}, owner="peer-requester"),
    )
    responder = _ChainStoreRuntimeHarness("peer-1", storage={})
    network = _FakeNetwork()
    network.register(requester)
    network.register(responder)

    result = requester._hsync("players", peers=["peer-1"], include_default_peers=False)

    self.assertEqual(
      result,
      {"hkey": "players", "source_peer": "peer-1", "merged_fields": 0},
    )
    self.assertEqual(
      requester.storage[_hset_key("players", "alpha")][ChainStoreBasePlugin.CS_VALUE],
      "local-alpha",
    )

  def test_hsync_times_out_only_when_no_valid_peer_responds(self):
    requester = _ChainStoreRuntimeHarness("peer-requester", default_peers=["peer-missing"])

    with self.assertRaisesRegex(ValueError, "timed out"):
      requester._hsync("players", timeout=0.01)

  def test_default_handler_rejects_unrequested_sender_response(self):
    requester = _ChainStoreRuntimeHarness(
      "peer-requester",
      storage=_build_hset_storage("players", {"alpha": "local-alpha"}, owner="peer-requester"),
    )
    requester.pending_hsync["req-1"] = {
      requester.CS_HKEY: "players",
      requester.CS_PEERS: ["peer-1"],
      requester.CS_RESPONSE: None,
    }

    requester.default_handler(
      requester.make_response_payload(
        request_id="req-1",
        sender="rogue-peer",
        snapshot=_build_hset_storage("players", {"beta": "rogue-beta"}, owner="rogue-peer"),
      )
    )

    self.assertIsNone(requester.pending_hsync["req-1"][requester.CS_RESPONSE])
    self.assertNotIn(_hset_key("players", "beta"), requester.storage)
    self.assertEqual(len(requester.sent_payloads), 0)

  def test_default_handler_replies_to_request_with_snapshot(self):
    responder = _ChainStoreRuntimeHarness(
      "peer-1",
      storage=_build_hset_storage("players", {"alpha": "remote-alpha"}, owner="peer-1"),
    )

    responder.default_handler(
      responder.make_request_payload(request_id="req-1", sender="peer-requester", hkey="players")
    )

    self.assertEqual(len(responder.sent_payloads), 1)
    response = responder.sent_payloads[0]
    self.assertEqual(response["node_addr"], ["peer-requester"])
    self.assertEqual(
      response["data"][responder.CS_DATA][responder.CS_OP],
      responder.CS_HSYNC_RESP,
    )
    self.assertEqual(
      response["data"][responder.CS_DATA][responder.CS_HKEY],
      "players",
    )
    self.assertEqual(
      response["data"][responder.CS_DATA][responder.CS_SNAPSHOT][_hset_key("players", "alpha")][responder.CS_VALUE],
      "remote-alpha",
    )


if __name__ == "__main__":
  unittest.main()

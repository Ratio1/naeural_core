import base64
import copy
import hashlib
import json
import unittest
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
  encoded = base64.urlsafe_b64encode(field.encode("utf-8")).decode("utf-8").rstrip("=")
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


class _ChainStoreRuntimeHarness(ChainStoreBasePlugin):
  def __init__(self, ee_addr, default_peers=None, storage=None):
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
    self._ChainStoreBasePlugin__chain_peers = list(default_peers or [])
    self._ChainStoreBasePlugin__pending_hsync = {}
    self._ChainStoreBasePlugin__last_chain_peers_refresh = self._now

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
    return encoded.decode("utf-8").rstrip("=")

  @staticmethod
  def base64_to_str(value, url_safe=True):  # pylint: disable=unused-argument
    padding = "=" * ((4 - len(value) % 4) % 4)
    raw = f"{value}{padding}".encode("utf-8")
    decoded = base64.urlsafe_b64decode(raw) if url_safe else base64.b64decode(raw)
    return decoded.decode("utf-8")

  @staticmethod
  def get_hash(value, algorithm="sha256", length=10):  # pylint: disable=unused-argument
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return digest[:length]

  @staticmethod
  def uuid(length=8):  # pylint: disable=unused-argument
    return "req-" + "1" * length

  def cacheapi_save_pickle(self, value, verbose=True):  # pylint: disable=unused-argument
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

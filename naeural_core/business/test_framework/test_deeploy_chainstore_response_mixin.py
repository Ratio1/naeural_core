import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest


def _load_deeploy_chainstore_response_mixin_module():
  """
  Load the mixin module without importing the full `naeural_core` package.

  The package import path pulls optional runtime dependencies such as torch,
  which are not needed for these focused mixin tests.
  """
  evm_net_constants = types.SimpleNamespace(
    SEED_NODES_ADDRESSES_KEY="SEED_NODES_ADDRESSES",
  )
  base_ct = types.SimpleNamespace(
    EvmNetConstants=evm_net_constants,
  )
  constants = types.SimpleNamespace(
    BASE_CT=base_ct,
    CURRENT_EVM_NET_CONSTANTS={
      evm_net_constants.SEED_NODES_ADDRESSES_KEY: ["seed-1", "seed-2"],
    },
  )
  naeural_core = types.ModuleType("naeural_core")
  naeural_core.constants = constants

  previous_naeural_core = sys.modules.get("naeural_core")
  previous_constants = sys.modules.get("naeural_core.constants")
  sys.modules["naeural_core"] = naeural_core
  sys.modules["naeural_core.constants"] = constants
  try:
    module_path = (
      Path(__file__).resolve().parents[1]
      / "mixins_base"
      / "deeploy_chainstore_response_mixin.py"
    )
    spec = importlib.util.spec_from_file_location(
      "deeploy_chainstore_response_mixin_under_test",
      module_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
  finally:
    if previous_naeural_core is None:
      sys.modules.pop("naeural_core", None)
    else:
      sys.modules["naeural_core"] = previous_naeural_core
    if previous_constants is None:
      sys.modules.pop("naeural_core.constants", None)
    else:
      sys.modules["naeural_core.constants"] = previous_constants


_DEEPLOY_CHAINSTORE_RESPONSE_MIXIN_MODULE = _load_deeploy_chainstore_response_mixin_module()
_DeeployChainstoreResponseMixin = (
  _DEEPLOY_CHAINSTORE_RESPONSE_MIXIN_MODULE._DeeployChainstoreResponseMixin
)


class _DummyBase:
  def __init__(self):
    self.base_initialized = True


class _DeeployChainstoreResponseHarness(_DeeployChainstoreResponseMixin, _DummyBase):
  def __init__(self):
    self.calls = []
    self.logs = []
    self.selection_inputs = []
    self.seed_nodes = ["seed-1", "seed-2"]
    self.selected_seed = "seed-1"
    self.cfg_chainstore_response_key = "response-key"
    self.modified_by_addr = "initiator-addr"
    self.ee_id = "node-id"
    self.ee_addr = "node-addr"
    self.__version__ = "test-version"
    super().__init__()

  def P(self, message, color=None):
    self.logs.append((message, color))

  def chainstore_set(self, *args, **kwargs):
    self.calls.append((args, kwargs))
    return True

  def _get_chainstore_response_seed_nodes(self):
    return self.seed_nodes

  def _select_chainstore_response_seed_peer(self, seed_peers):
    self.selection_inputs.append(list(seed_peers))
    if self.selected_seed in seed_peers:
      return self.selected_seed
    return seed_peers[0] if len(seed_peers) > 0 else None

  def json_dumps(self, value):
    return json.dumps(value)

  def time(self):
    return 1

  def time_to_str(self, _value):
    return "2026-05-29T17:00:00"

  def get_signature(self):
    return "TEST_PLUGIN"

  def get_instance_id(self):
    return "TEST_INSTANCE"

  def get_stream_id(self):
    return "TEST_STREAM"


class DeeployChainstoreResponseMixinTests(unittest.TestCase):
  def test_response_peers_include_initiator_and_one_seed(self):
    harness = _DeeployChainstoreResponseHarness()

    peers = harness._get_chainstore_response_peers()

    self.assertEqual(peers, ["initiator-addr", "seed-1"])
    self.assertEqual(harness.selection_inputs, [["seed-1", "seed-2"]])

  def test_response_peers_prefer_seed_distinct_from_initiator(self):
    harness = _DeeployChainstoreResponseHarness()
    harness.seed_nodes = ["initiator-addr", "seed-2"]
    harness.selected_seed = "seed-2"

    peers = harness._get_chainstore_response_peers()

    self.assertEqual(peers, ["initiator-addr", "seed-2"])
    self.assertEqual(harness.selection_inputs, [["seed-2"]])

  def test_response_peers_do_not_duplicate_initiator_when_only_seed_matches(self):
    harness = _DeeployChainstoreResponseHarness()
    harness.seed_nodes = ["initiator-addr"]
    harness.selected_seed = "initiator-addr"

    peers = harness._get_chainstore_response_peers()

    self.assertEqual(peers, ["initiator-addr"])
    self.assertEqual(harness.selection_inputs, [["initiator-addr"]])

  def test_response_peers_fall_back_to_initiator_when_seed_list_empty(self):
    harness = _DeeployChainstoreResponseHarness()
    harness.seed_nodes = []

    peers = harness._get_chainstore_response_peers()

    self.assertEqual(peers, ["initiator-addr"])
    self.assertEqual(harness.selection_inputs, [])
    self.assertIn(
      ("No seed oracle addresses configured for chainstore response peer routing", "y"),
      harness.logs,
    )

  def test_send_chainstore_response_uses_restricted_peers(self):
    harness = _DeeployChainstoreResponseHarness()

    self.assertTrue(harness._send_chainstore_response())

    args, kwargs = harness.calls[-1]
    self.assertEqual(args[0], "response-key")
    self.assertEqual(args[1]["status"], "ready")
    self.assertEqual(kwargs["extra_peers"], ["initiator-addr", "seed-1"])
    self.assertEqual(kwargs["include_default_peers"], False)
    self.assertEqual(kwargs["include_configured_peers"], False)
    self.assertEqual(kwargs["debug"], True)

  def test_reset_chainstore_response_uses_restricted_peers(self):
    harness = _DeeployChainstoreResponseHarness()

    self.assertTrue(harness._reset_chainstore_response())

    args, kwargs = harness.calls[-1]
    self.assertEqual(args, ("response-key", None))
    self.assertEqual(kwargs["extra_peers"], ["initiator-addr", "seed-1"])
    self.assertEqual(kwargs["include_default_peers"], False)
    self.assertEqual(kwargs["include_configured_peers"], False)
    self.assertEqual(kwargs["debug"], True)

  def test_local_reset_peers_use_one_seed_oracle_only(self):
    harness = _DeeployChainstoreResponseHarness()
    harness.seed_nodes = ["node-addr", "seed-2"]
    harness.selected_seed = "seed-2"

    peers = harness._get_chainstore_response_local_reset_peers()

    self.assertEqual(peers, ["seed-2"])
    self.assertEqual(harness.selection_inputs, [["seed-2"]])

  def test_reset_explicit_chainstore_response_key_uses_provided_kwargs(self):
    harness = _DeeployChainstoreResponseHarness()
    reset_kwargs = harness._get_chainstore_response_local_reset_write_kwargs()

    self.assertTrue(
      harness._reset_chainstore_response_key(
        "explicit-response-key",
        write_kwargs=reset_kwargs,
      )
    )

    args, kwargs = harness.calls[-1]
    self.assertEqual(args, ("explicit-response-key", None))
    self.assertEqual(kwargs["extra_peers"], ["seed-1"])
    self.assertEqual(kwargs["include_default_peers"], False)
    self.assertEqual(kwargs["include_configured_peers"], False)
    self.assertEqual(kwargs["debug"], True)


if __name__ == "__main__":
  unittest.main()

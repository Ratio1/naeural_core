import importlib.util
import sys
import types
import unittest
from pathlib import Path


def _load_bc_wrapper():
  wrapper_path = Path(__file__).resolve().parents[2] / "utils" / "plugins_base" / "bc_wrapper.py"
  module_name = "_test_bc_wrapper_dauth_oracles"
  spec = importlib.util.spec_from_file_location(module_name, wrapper_path)
  module = importlib.util.module_from_spec(spec)

  fake_naeural_core = types.ModuleType("naeural_core")
  fake_bc = types.ModuleType("naeural_core.bc")
  fake_bc.DefaultBlockEngine = object
  fake_ratio1 = types.ModuleType("ratio1")
  fake_ratio1_const = types.ModuleType("ratio1.const")
  fake_evm_net = types.ModuleType("ratio1.const.evm_net")
  fake_evm_net.EVM_NET_DATA = {}

  fake_modules = {
    "naeural_core": fake_naeural_core,
    "naeural_core.bc": fake_bc,
    "ratio1": fake_ratio1,
    "ratio1.const": fake_ratio1_const,
    "ratio1.const.evm_net": fake_evm_net,
  }
  originals = {name: sys.modules.get(name) for name in fake_modules}
  sys.modules.update(fake_modules)
  try:
    spec.loader.exec_module(module)
  finally:
    for name, original in originals.items():
      if original is None:
        sys.modules.pop(name, None)
      else:
        sys.modules[name] = original
  return module.BCWrapper


BCWrapper = _load_bc_wrapper()


class _FakeEpochManager:
  def __init__(self, conversions):
    self.conversions = conversions

  def eth_to_internal(self, eth_address):
    return self.conversions.get(eth_address)


class _FakeNetmon:
  def __init__(self, conversions, aliases):
    self.epoch_manager = _FakeEpochManager(conversions)
    self.aliases = aliases

  def network_node_eeid(self, internal_address):
    return self.aliases.get(internal_address)


class _FakeOwner:
  def __init__(self, conversions=None, aliases=None):
    self.netmon = _FakeNetmon(conversions or {}, aliases or {})
    self.messages = []

  def P(self, msg, **kwargs):
    self.messages.append((msg, kwargs))


class _FakeBlockEngine:
  def __init__(self, dauth_oracles=None, dauth_members=None):
    self.eth_address = "0xSELF"
    self.dauth_oracles = dauth_oracles or []
    self.dauth_members = set(dauth_members or [])
    self.dauth_oracle_checks = []

  def web3_get_dauth_oracles(self):
    return list(self.dauth_oracles)

  def web3_is_dauth_oracle(self, address):
    self.dauth_oracle_checks.append(address)
    return address in self.dauth_members

  def node_address_to_eth_address(self, node_address):
    return "0xETH_" + node_address


class TestBCWrapperDAuthOracles(unittest.TestCase):
  def test_get_eth_dauth_oracles_delegates_to_blockchain_engine(self):
    bc = _FakeBlockEngine(dauth_oracles=["0xA", "0xB"])
    wrapper = BCWrapper(bc, _FakeOwner())

    self.assertEqual(wrapper.get_eth_dauth_oracles(), ["0xA", "0xB"])

  def test_get_dauth_oracles_converts_registry_addresses(self):
    owner = _FakeOwner(
      conversions={"0xA": "node_a", "0xB": "node_b"},
      aliases={"node_a": "Oracle A", "node_b": "Oracle B"},
    )
    bc = _FakeBlockEngine(dauth_oracles=["0xA", "0xB"])
    wrapper = BCWrapper(bc, owner)

    internal, names, eth_addresses = wrapper.get_dauth_oracles(
      include_eth_addrs=True,
      wait_interval=0,
    )

    self.assertEqual(internal, ["node_a", "node_b"])
    self.assertEqual(names, ["Oracle A", "Oracle B"])
    self.assertEqual(eth_addresses, ["0xA", "0xB"])

  def test_get_dauth_oracles_logs_dauth_context_on_registry_error(self):
    owner = _FakeOwner()
    bc = _FakeBlockEngine(dauth_oracles=[])
    wrapper = BCWrapper(bc, owner)

    self.assertEqual(wrapper.get_dauth_oracles(wait_interval=0), ([], []))
    self.assertIn("dAuth oracle data", owner.messages[-1][0])

  def test_is_dauth_oracle_uses_eth_address_variants(self):
    bc = _FakeBlockEngine(dauth_members={"0xSELF", "0xETH_node_a"})
    wrapper = BCWrapper(bc, _FakeOwner())

    self.assertTrue(wrapper.is_dauth_oracle())
    self.assertTrue(wrapper.is_dauth_oracle(node_address="node_a"))
    self.assertFalse(wrapper.is_dauth_oracle(node_address_eth="0xOTHER"))
    self.assertEqual(bc.dauth_oracle_checks, ["0xSELF", "0xETH_node_a", "0xOTHER"])


if __name__ == "__main__":
  unittest.main()

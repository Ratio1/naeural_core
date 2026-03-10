import unittest
from threading import Lock

from naeural_core.main.orchestrator import Orchestrator


class _FakeBCWrapper:
  def __init__(self, oracles):
    self._oracles = oracles

  def get_oracles(self, **kwargs):
    return list(self._oracles), []


class _FakeBlockchainManager:
  def __init__(self, whitelist=None):
    self.whitelist = list(whitelist or [])
    self.added_batches = []

  def _add_prefix(self, addr):
    return addr if addr.startswith("0xai_") else "0xai_" + addr

  def maybe_add_prefix(self, addr):
    return self._add_prefix(addr)

  def add_address_to_allowed(self, addresses):
    if isinstance(addresses, str):
      addresses = [addresses]
    self.added_batches.append(list(addresses))
    for addr in addresses:
      if addr not in self.whitelist:
        self.whitelist.append(addr)
    return True


class _FakeThread:
  def __init__(self, alive=True):
    self._alive = alive
    self.joined = False

  def is_alive(self):
    return self._alive

  def join(self):
    self.joined = True
    self._alive = False
    return


class TestOrchestratorUpdateNodeOracles(unittest.TestCase):
  CACHED_ATTR = "_Orchestrator__cached_node_oracles"
  REFRESH_VERSION_ATTR = "_Orchestrator__node_oracle_refresh_version"
  APPLY_VERSION_ATTR = "_Orchestrator__node_oracle_apply_version"
  LOCK_ATTR = "_Orchestrator__node_oracle_refresh_lock"

  def _make_orchestrator(self, whitelist=None, oracles=None):
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator._blockchain_manager = _FakeBlockchainManager(whitelist=whitelist)
    orchestrator._bc = _FakeBCWrapper(oracles or [])
    orchestrator._last_oracle_update = 0
    setattr(orchestrator, self.LOCK_ATTR, Lock())
    setattr(orchestrator, self.CACHED_ATTR, [])
    setattr(orchestrator, self.REFRESH_VERSION_ATTR, 0)
    setattr(orchestrator, self.APPLY_VERSION_ATTR, 0)
    orchestrator._messages = []
    orchestrator.P = lambda msg, color=None, **kwargs: orchestrator._messages.append((msg, color, kwargs))
    return orchestrator

  def test_refresh_node_oracles_caches_latest_results(self):
    orchestrator = self._make_orchestrator(
      whitelist=[],
      oracles=["oracle_1", "oracle_2"],
    )

    refreshed_count = orchestrator.refresh_node_oracles_cache()

    self.assertEqual(refreshed_count, 2)
    self.assertEqual(getattr(orchestrator, self.CACHED_ATTR), ["oracle_1", "oracle_2"])
    self.assertEqual(getattr(orchestrator, self.REFRESH_VERSION_ATTR), 1)

  def test_appends_only_missing_oracles(self):
    orchestrator = self._make_orchestrator(whitelist=["existing_node"])
    setattr(orchestrator, self.CACHED_ATTR, ["existing_node", "oracle_1", "oracle_1", "oracle_2"])
    setattr(orchestrator, self.REFRESH_VERSION_ATTR, 1)

    added_count = orchestrator.apply_cached_node_oracles_to_whitelist()

    self.assertEqual(added_count, 2)
    self.assertEqual(orchestrator.blockchain_manager.added_batches, [["oracle_1", "oracle_2"]])
    self.assertEqual(orchestrator.blockchain_manager.whitelist, ["existing_node", "oracle_1", "oracle_2"])
    self.assertEqual(getattr(orchestrator, self.APPLY_VERSION_ATTR), 1)

  def test_noop_when_all_oracles_already_whitelisted(self):
    orchestrator = self._make_orchestrator(whitelist=["oracle_1", "oracle_2"])
    setattr(orchestrator, self.CACHED_ATTR, ["oracle_1", "oracle_2"])
    setattr(orchestrator, self.REFRESH_VERSION_ATTR, 1)

    added_count = orchestrator.apply_cached_node_oracles_to_whitelist()

    self.assertEqual(added_count, 0)
    self.assertEqual(orchestrator.blockchain_manager.added_batches, [])
    self.assertIn("already whitelisted", orchestrator._messages[-1][0].lower())

  def test_noop_when_oracle_list_empty(self):
    orchestrator = self._make_orchestrator(whitelist=["existing_node"])
    refreshed_count = orchestrator.refresh_node_oracles_cache()

    self.assertEqual(refreshed_count, 0)
    self.assertEqual(orchestrator.blockchain_manager.added_batches, [])
    self.assertIn("no blockchain oracle nodes found", orchestrator._messages[-1][0].lower())

  def test_apply_cached_node_oracles_applies_each_refresh_once(self):
    orchestrator = self._make_orchestrator(whitelist=[])
    setattr(orchestrator, self.CACHED_ATTR, ["oracle_1"])
    setattr(orchestrator, self.REFRESH_VERSION_ATTR, 1)

    self.assertEqual(orchestrator.apply_cached_node_oracles_to_whitelist(), 1)
    self.assertEqual(orchestrator.apply_cached_node_oracles_to_whitelist(), 0)
    self.assertEqual(orchestrator.blockchain_manager.added_batches, [["oracle_1"]])

    setattr(orchestrator, self.CACHED_ATTR, ["oracle_1", "oracle_2"])
    setattr(orchestrator, self.REFRESH_VERSION_ATTR, 2)
    self.assertEqual(orchestrator.apply_cached_node_oracles_to_whitelist(), 1)
    self.assertEqual(
      orchestrator.blockchain_manager.added_batches,
      [["oracle_1"], ["oracle_2"]],
    )

  def test_stop_joins_node_oracle_refresh_thread(self):
    orchestrator = self._make_orchestrator()
    async_thread = _FakeThread(alive=True)
    refresh_thread = _FakeThread(alive=True)
    orchestrator._thread_async_comm = async_thread
    orchestrator._thread_node_oracle_refresh = refresh_thread

    orchestrator._stop()

    self.assertTrue(async_thread.joined)
    self.assertTrue(refresh_thread.joined)
    self.assertIn("asynchronous communication thread joined", orchestrator._messages[0][0].lower())
    self.assertIn("node oracle refresh thread joined", orchestrator._messages[1][0].lower())


if __name__ == "__main__":
  unittest.main()

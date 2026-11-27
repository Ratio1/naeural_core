import unittest
from threading import Lock
# Stand-ins for the orchestrator and the managers a start creates, and the patch of the module's names.
from unittest import mock

# The prefix of the node's thread names.
from naeural_core import constants as ct
# The module whose Thread and manager classes a start creates.
from naeural_core.main import orchestrator as orchestrator_module
from naeural_core.main.orchestrator import Orchestrator


class _FakeBCWrapper:
  def __init__(self, oracles):
    self._oracles = oracles

  def get_oracles(self, **kwargs):
    if len(self._oracles) == 0:
      return [], []
    if isinstance(self._oracles[0], (list, tuple)):
      return [item[0] for item in self._oracles], [item[1] for item in self._oracles]
    return list(self._oracles), []


class _FakeBlockchainManager:
  def __init__(self, whitelist=None):
    """
    Stand for the node's blockchain engine: its allowed senders, the batches added to them, and its network.

    Parameters
    ----------
    whitelist : list of str, optional
      The allowed senders it starts with; none by default.

    Returns
    -------
    None
    """
    self.whitelist = list(whitelist or [])
    self.added_batches = []
    # A network other than "VPN": the refresh and its apply skip a VPN fleet, and read this before anything else.
    self.evm_network = "mainnet"

  def _add_prefix(self, addr):
    return addr if addr.startswith("0xai_") else "0xai_" + addr

  def maybe_add_prefix(self, addr):
    return self._add_prefix(addr)

  def add_address_to_allowed(self, addresses):
    if isinstance(addresses, str):
      addresses = [addresses]
    self.added_batches.append(list(addresses))
    for addr in addresses:
      addr_no_alias = addr.split()[0]
      if addr_no_alias not in self.whitelist:
        self.whitelist.append(addr_no_alias)
    return True


class _FakeThread:
  def __init__(self, alive=True):
    self._alive = alive
    self.joined = False

  def is_alive(self):
    return self._alive

  def join(self, timeout=None):
    """
    Stand for ``Thread.join``: the thread is joined at once and is no longer alive.

    Parameters
    ----------
    timeout : float, optional
      The bound the stop passes; not waited for.

    Returns
    -------
    None
    """
    self.joined = True
    self._alive = False
    return


class TestOrchestratorNodeOracleRefresh(unittest.TestCase):
  CACHED_ATTR = "_Orchestrator__cached_node_oracles"
  REFRESH_VERSION_ATTR = "_Orchestrator__node_oracle_refresh_version"
  APPLY_VERSION_ATTR = "_Orchestrator__node_oracle_apply_version"
  LOCK_ATTR = "_Orchestrator__node_oracle_refresh_lock"

  def _make_orchestrator(self, whitelist=None, oracles=None):
    """
    Build an orchestrator with only what the oracle refresh, its apply and the stop read.

    Parameters
    ----------
    whitelist : list of str, optional
      The allowed senders of its blockchain engine; none by default.
    oracles : list, optional
      What its block engine wrapper's ``get_oracles`` finds: addresses, or (address, alias) pairs; none by default.

    Returns
    -------
    Orchestrator
      The orchestrator, its log lines recorded in ``_messages`` as (text, color, other keyword arguments).
    """
    orchestrator = Orchestrator.__new__(Orchestrator)
    # The stop reads the admin pipeline's poll seconds from the config: none set, so its default.
    orchestrator.config_data = {}
    orchestrator._blockchain_manager = _FakeBlockchainManager(whitelist=whitelist)
    orchestrator._bc = _FakeBCWrapper(oracles or [])
    orchestrator._use_summary_oracle_refresh = False
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
      oracles=[("oracle_1", "oracle_a"), ("oracle_2", "oracle_b")],
    )

    refreshed_count = orchestrator.refresh_node_oracles_cache()

    self.assertEqual(refreshed_count, 2)
    self.assertEqual(getattr(orchestrator, self.CACHED_ATTR), [("oracle_1", "oracle_a"), ("oracle_2", "oracle_b")])
    self.assertEqual(getattr(orchestrator, self.REFRESH_VERSION_ATTR), 1)

  def test_summary_refresh_requires_threshold_and_adds_only_new_registered_nodes(self):
    node = self._make_orchestrator(whitelist=["0xai_seen"])
    node._use_summary_oracle_refresh = True
    registry = [f"0x{i:040x}" for i in range(14)]
    node._bc = mock.Mock()
    node._bc.get_eth_oracles.return_value = registry
    node._network_monitor = mock.Mock()
    resolved = ["0xai_seen"] + [f"0xai_node_{i}" for i in range(11)]
    node.netmon.resolve_summary_oracles.return_value = resolved
    self.assertEqual(node.refresh_node_oracles_cache(), 12)
    node._bc.get_eth_oracles.assert_called_once_with()
    node.netmon.resolve_summary_oracles.assert_called_once_with(registry)
    self.assertEqual(node.apply_cached_node_oracles_to_whitelist(), 11)
    node.netmon.resolve_summary_oracles.return_value = ["0xai_insufficient"]
    self.assertEqual(node.refresh_node_oracles_cache(), 0)
    self.assertEqual(node.apply_cached_node_oracles_to_whitelist(), 0)
    node._bc.get_eth_oracles.side_effect = TimeoutError("registry deadline")
    self.assertEqual(node.refresh_node_oracles_cache(), 0)
    self.assertEqual(node.whitelist_full, resolved)
    self.assertTrue(any("registry read failed" in msg for msg, _, _ in node._messages))
    node._bc.get_oracles.assert_not_called()
    # A fresh result waiting on a busy main loop cannot authorize keys after its summary expires.
    node._bc.get_eth_oracles.side_effect = None
    node.netmon.resolve_summary_oracles.return_value = resolved + ["0xai_late"]
    self.assertEqual(node.refresh_node_oracles_cache(), 13)
    node.netmon.resolve_summary_oracles.return_value = []
    self.assertEqual(node.apply_cached_node_oracles_to_whitelist(), 0)
    self.assertNotIn("0xai_late", node.whitelist_full)

  def test_appends_only_missing_oracles(self):
    orchestrator = self._make_orchestrator(whitelist=["existing_node"])
    setattr(
      orchestrator,
      self.CACHED_ATTR,
      [("existing_node", "existing_alias"), ("oracle_1", "oracle_a"), ("oracle_1", "oracle_dup"), ("oracle_2", "oracle_b")],
    )
    setattr(orchestrator, self.REFRESH_VERSION_ATTR, 1)

    added_count = orchestrator.apply_cached_node_oracles_to_whitelist()

    self.assertEqual(added_count, 2)
    self.assertEqual(orchestrator.blockchain_manager.added_batches, [["oracle_1 oracle_a", "oracle_2 oracle_b"]])
    self.assertEqual(orchestrator.blockchain_manager.whitelist, ["existing_node", "oracle_1", "oracle_2"])
    self.assertEqual(getattr(orchestrator, self.APPLY_VERSION_ATTR), 1)

  def test_noop_when_all_oracles_already_whitelisted(self):
    orchestrator = self._make_orchestrator(whitelist=["oracle_1", "oracle_2"])
    setattr(orchestrator, self.CACHED_ATTR, [("oracle_1", "oracle_a"), ("oracle_2", "oracle_b")])
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
    setattr(orchestrator, self.CACHED_ATTR, [("oracle_1", "oracle_a")])
    setattr(orchestrator, self.REFRESH_VERSION_ATTR, 1)

    self.assertEqual(orchestrator.apply_cached_node_oracles_to_whitelist(), 1)
    self.assertEqual(orchestrator.apply_cached_node_oracles_to_whitelist(), 0)
    self.assertEqual(orchestrator.blockchain_manager.added_batches, [["oracle_1 oracle_a"]])

    setattr(orchestrator, self.CACHED_ATTR, [("oracle_1", "oracle_a"), ("oracle_2", "oracle_b")])
    setattr(orchestrator, self.REFRESH_VERSION_ATTR, 2)
    self.assertEqual(orchestrator.apply_cached_node_oracles_to_whitelist(), 1)
    self.assertEqual(
      orchestrator.blockchain_manager.added_batches,
      [["oracle_1 oracle_a"], ["oracle_2 oracle_b"]],
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


class _RecordedThread:
  """Stands for ``threading.Thread`` during a start: records each thread made and started, and runs none."""

  # Every thread made, in order, by its name.
  made = []

  def __init__(self, target=None, args=(), name=None, daemon=None):
    """
    Record a thread the start makes.

    Parameters
    ----------
    target : callable, optional
      What the thread would run; not run.
    args : tuple, optional
      Its arguments.
    name : str, optional
      The thread's name, which the test reads.
    daemon : bool, optional
      Whether it is a daemon thread.

    Returns
    -------
    None
    """
    # The name, and whether start was called.
    self.name, self.started = name, False
    # Kept for the test.
    _RecordedThread.made.append(self)

  def start(self):
    """
    Record that the start started this thread.

    Returns
    -------
    None
    """
    # Started, without running anything.
    self.started = True


def start_node(receives_heartbeats, whitelist, summaries=False):
  """
  Run the real ``_init_all_processes`` on a stand-in node and give the threads it started and the lines it logged.

  Every collaborator of the start is a ``MagicMock`` (its managers, dAuth, the network monitor); only the comm
  manager's answer to whether this node receives peer heartbeats, the allowed senders after dAuth and the admin
  pipeline's dispatch switch (off) are set.

  Parameters
  ----------
  receives_heartbeats : bool
    False for a node whose heartbeat receive the comm manager removed (a non-supervisor in oracle-only heartbeat
    mode).
  whitelist : list of str
    The allowed senders after start-up dAuth.

  Returns
  -------
  tuple
    ``(started, lines)``: the names of the threads made and started, without the node's thread prefix, and
    each logged line as (text, color).

  Examples
  --------
  >>> start_node(False, [])[0]
  ['async_comm']
  """
  # The stand-in node: every attribute and method a MagicMock unless set below.
  node = mock.MagicMock()
  # Its lines.
  lines = []
  # Recorded instead of printed.
  node.P = lambda msg, color=None, **kwargs: lines.append((msg, color))
  # No extra comm environment for the network monitor, and no admin pipeline collection thread.
  node.cfg_communication_environment, node.cfg_admin_pipeline_async_dispatch = {}, False
  # The comm manager's flag: set when it removed the heartbeat receive.
  node._comm_manager.should_register_local_self_heartbeat = not receives_heartbeats
  # The allowed senders dAuth left.
  node.whitelist = whitelist
  # No thread recorded yet.
  _RecordedThread.made = []
  # The start's own classes: threads recorded, managers and monitors stand-ins.
  with mock.patch.object(orchestrator_module, "Thread", _RecordedThread), \
       mock.patch.object(orchestrator_module, "MainLoopDataHandler", mock.MagicMock()), \
       mock.patch.object(orchestrator_module, "ApplicationMonitor", mock.MagicMock()), \
       mock.patch.object(orchestrator_module, "NetworkMonitor", mock.MagicMock()):
    # Pass the configured summary capability to the monitor constructed during startup.
    orchestrator_module.NetworkMonitor.return_value.network_summary_status_enabled = summaries
    Orchestrator._init_all_processes(node)
  # The threads started, by name without the node's thread prefix, and the lines.
  return [thread.name[len(ct.THREADS_PREFIX):] for thread in _RecordedThread.made if thread.started], lines


class TestNodeOracleRefreshStart(unittest.TestCase):
  """The oracle refresh starts only on a node that receives peer heartbeats; one that does not says why, and says
  in red when its allowed senders are empty."""

  def test_a_node_that_receives_no_peer_heartbeats_starts_no_refresh_and_says_why(self):
    """A non-supervisor whose heartbeat receive was removed starts the comm thread and no oracle refresh, with one
    yellow line saying why; with allowed senders from dAuth no red line (before, the refresh started and failed
    at every pass with a red traceback, 0 of 14 oracles mapped)."""
    # A worker in oracle-only heartbeat mode whose dAuth gave allowed senders.
    started, lines = start_node(receives_heartbeats=False, whitelist=["0xai_oracle"])
    # Only the comm thread.
    self.assertEqual(started, ["async_comm"])
    # One yellow line about the refresh, and no red line.
    self.assertEqual([(color, "receives no peer heartbeats" in text) for text, color in lines if color in ("y", "r")],
                     [("y", True)])

  def test_such_a_node_with_no_allowed_senders_after_dauth_says_so_in_red(self):
    """The same node with an empty allowed-senders list after dAuth (its dAuth failed) logs one red line, after
    the yellow one: it refuses every oracle command until it restarts (before, nothing said so)."""
    # A worker in oracle-only heartbeat mode whose dAuth gave nothing.
    started, lines = start_node(receives_heartbeats=False, whitelist=[])
    # No refresh either.
    self.assertEqual(started, ["async_comm"])
    # The yellow line, then the red one.
    self.assertEqual([(color, "No allowed command senders" in text) for text, color in lines if color in ("y", "r")],
                     [("y", False), ("r", True)])

  def test_summary_worker_starts_refresh_and_keeps_the_empty_startup_diagnostic(self):
    started, lines = start_node(receives_heartbeats=False, whitelist=[], summaries=True)
    self.assertEqual(started, ["async_comm", "node_oracle_refresh"])
    self.assertTrue(any("No allowed command senders" in message and color == "r" for message, color in lines))

  def test_a_node_that_receives_peer_heartbeats_starts_the_refresh_as_before(self):
    """An oracle, or any node whose heartbeat receive is kept, starts the comm thread and the oracle refresh and
    logs neither line, also with no allowed senders (the refresh adds the oracles)."""
    # A node that receives heartbeats, with an empty allowed list.
    started, lines = start_node(receives_heartbeats=True, whitelist=[])
    # Both threads.
    self.assertEqual(started, ["async_comm", "node_oracle_refresh"])
    # No yellow or red line.
    self.assertEqual([color for _, color in lines if color in ("y", "r")], [])


if __name__ == "__main__":
  unittest.main()

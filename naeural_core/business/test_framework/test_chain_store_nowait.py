"""
Offline tests of the chain store's opt-in non-blocking write (``wait=False``): the same checks and local apply as a
blocking write, an immediate return, and the store's own re-sends at the blocking wait's schedule.
"""
# The send queue a test interleaves a second writer into.
import collections
# Another thread that holds the persistence lock, or asks for it during a write.
import threading
# The wall time a write takes while the lock is held elsewhere.
import time
# The test framework.
import unittest

# The counter attribute.
from test_chain_store_accounting import STATS
# The plugin API's harness, the chain store's harness (for its key helpers) and the composer of a hash key.
from test_chain_store_hsync import _ChainStoreRuntimeHarness, _PeerSelectionHarness, _hset_key
# The order tests' store builder, deliveries, records, confirmation and refusal helpers and constants.
from test_chain_store_order import BUCKET, KEY, NOW, NOW_MS, confirm, deliver, make, record, refusal

# The table of the writes owed a re-send, by its private name.
UNWAITED = "_ChainStoreBasePlugin__unwaited"
# The store's persistence lock, by its private name.
LOCK = "_ChainStoreBasePlugin__persistence_lock"


def write(store, key, value, wait=False, **kwargs):
  """
  Make one local write on ``store`` (non-blocking by default), then send what it queued, as the plugin loop does.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The writer.
  key : str
    Composed key.
  value : any
    The value; None for a delete.
  wait : bool, optional
    False (the default) for a non-blocking write; True for a blocking one that waits one round of 0.1 s.
  **kwargs
    More arguments of ``_set_value`` (``token``, for example).

  Returns
  -------
  tuple
    ``(result, outcome, sent)``: what ``_set_value`` returned, its ``result_info`` outcome, and how many stores the
    plugin loop then sent.

  Examples
  --------
  >>> write(make("report"), KEY, "v")
  (True, 'changed', 1)
  """
  # The caller's dict.
  info = {}
  # Only what this write sends.
  before = len(store.sent_payloads)
  # The write; a blocking one waits one round and does not retry.
  result = store._set_value(key, value, owner=store.ee_addr, wait=wait, result_info=info,
                            **({} if not wait else {"timeout": .1, "max_retries": 0}), **kwargs)
  # The plugin loop sends what it queued.
  store._ChainStoreBasePlugin__maybe_broadcast()
  # The three.
  return result, info["outcome"], len(store.sent_payloads) - before


def lap(store, at):
  """
  Run one plugin lap (``process``) of ``store`` at ``at`` seconds after the tests' wall clock.

  The lap runs the re-send step from ``process``, as the plugin loop does, then sends what it queued. The peer
  refresh and the ``CSTORE_STATS`` line are timed from the tests' clock, so neither is due within the tests' 30 s.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The writer.
  at : float
    Seconds after ``NOW``.

  Returns
  -------
  int
    How many stores the lap sent.

  Examples
  --------
  >>> lap(store, 10)
  1
  """
  # The lap's wall clock.
  store._now = NOW + at
  # Only what this lap sends.
  before = len(store.sent_payloads)
  # The peer refresh and the CSTORE_STATS line counted from the tests' clock: neither runs within these tests.
  store._ChainStoreBasePlugin__last_chain_peers_refresh = store._ChainStoreBasePlugin__cs_stats_last = NOW
  # One plugin lap: the re-send step, then the send of what it queued.
  store.process()
  # How many went.
  return len(store.sent_payloads) - before


class TestNonBlockingWrite(unittest.TestCase):
  """wait=False checks and applies as a blocking write, returns at once and leaves the re-sends to the plugin."""

  def test_a_non_blocking_write_returns_at_once_stored_and_queued(self):
    """A wait=False write returns True with its own outcome (changed) without moving the clock (no wait), stores the
    same record a blocking write stores, sends one store, and counts changed only."""
    # A writer in 'report' with two peers that never answer.
    store = make("report")
    # The write: True at once, changed, one store sent.
    self.assertEqual(write(store, KEY, "v"), (True, "changed", 1))
    # No time passed (a wait sleeps 0.1 s per round on the fake clock), and the record is an own record.
    self.assertEqual((store._now, store.storage[KEY]["confirms"], store.storage[KEY]["target_peers"],
                      store.storage[KEY]["ver"]), (NOW, 0, ["peer1", "peer2"], [NOW_MS, "node"]))
    # Counted as a change; one entry owed a re-send.
    self.assertEqual((getattr(store, STATS), list(getattr(store, UNWAITED))),
                     ({BUCKET + "/local/changed": 1}, [KEY]))

  def test_refusals_are_those_of_a_blocking_write(self):
    """A write with no target peer, and one over a record with another token, are refused by wait=False with the
    blocking write's bool and word, and queue nothing."""
    # Each case: the store's peers and records.
    cases = {"no_peers": ((), {}), "token": (("peer1",), {KEY: {**record(KEY, "v", "peer1"), "token": "t"}})}
    # Each case.
    for name, (peers, storage) in cases.items():
      # One sub-test each.
      with self.subTest(case=name):
        # Two identical stores, one per form of the write.
        blocking, non_blocking = make("report", peers=peers, storage=storage), make("report", peers=peers,
                                                                                   storage=storage)
        # The same bool and word, nothing sent, nothing owed.
        self.assertEqual((write(non_blocking, KEY, "w"), getattr(non_blocking, UNWAITED)),
                         ((write(blocking, KEY, "w", wait=True)[0], "refused_" + name, 0), {}))

  def test_wait_false_is_refused_in_each_protected_namespace(self):
    """In each of the six protected namespaces wait=False raises ValueError, counted error, and stores and queues
    nothing."""
    # Each protected namespace.
    for namespace in make("report").CS_PROTECTED_NAMESPACES:
      # One sub-test each.
      with self.subTest(namespace=namespace):
        # A writer.
        store = make("report")
        # A key of the namespace, and the caller's dict.
        key, info = _hset_key(namespace, "f"), {}
        # Raised.
        with self.assertRaises(ValueError):
          # The write.
          store._set_value(key, {"x": 1}, owner="node", wait=False, result_info=info)
        # Counted as an error; nothing stored, queued or owed.
        self.assertEqual((info, key in store.storage, len(store._ChainStoreBasePlugin__ops), getattr(store, UNWAITED)),
                         ({"outcome": "error"}, False, 0, {}))

  def test_an_unconfirmed_write_is_resent_at_10_and_20_s_then_counted_unconfirmed(self):
    """With the defaults (timeout 10 s, 2 retries) an unconfirmed wait=False write is sent at 0, 10 and 20 s, as a
    blocking write, and counted unconfirmed at 30 s."""
    # A writer whose peers never answer.
    store = make("report")
    # The write: one store.
    write(store, KEY, "v")
    # Laps at 5, 10, 15, 20 and 30 s: re-sends at 10 and 20 s only.
    self.assertEqual([lap(store, at) for at in (5, 10, 15, 20, 30)], [0, 1, 0, 1, 0])
    # Counted unconfirmed, nothing owed any more.
    self.assertEqual((getattr(store, STATS).get(BUCKET + "/local/unconfirmed"), getattr(store, UNWAITED)), (1, {}))

  def test_a_confirmation_ends_the_resends(self):
    """A confirmation of the write ends its re-sends without a wait-end count."""
    # A writer.
    store = make("report")
    # The write.
    write(store, KEY, "v")
    # peer1 confirms.
    confirm(store, KEY, "peer1", value="v", owner="node")
    # No re-send at 10 s; nothing owed, nothing counted beyond the write.
    self.assertEqual((lap(store, 10), getattr(store, UNWAITED), getattr(store, STATS)),
                     (0, {}, {BUCKET + "/local/changed": 1}))

  def test_a_newer_write_replaces_the_entry_and_an_overwritten_write_stops(self):
    """A second wait=False write of the key replaces the owed store; a peer's write that replaces the value then ends
    the re-sends, counted overwritten."""
    # A writer.
    store = make("report")
    # Two writes of the key.
    write(store, KEY, "v1")
    # The second.
    write(store, KEY, "v2")
    # The owed store is the second's.
    self.assertEqual(getattr(store, UNWAITED)[KEY][0]["value"], "v2")
    # peer1's newer write replaces the value.
    deliver(store, KEY, "w", "peer1", [NOW_MS + 1000, "peer1"])
    # No re-send at 10 s, counted overwritten.
    self.assertEqual((lap(store, 10), getattr(store, STATS).get(BUCKET + "/local/overwritten")), (0, 1))

  def test_an_older_write_never_replaces_a_newer_writes_entry(self):
    """When a newer wait=False write of the key runs whole between an older one's apply and its registration (two
    threads writing one key), the entry stays the newer one's: the value held is re-sent at 10 and 20 s."""
    # A writer whose peers never answer.
    store = make("report")

    class Interleaved(collections.deque):
      """The send queue, which runs a second writer's whole write of "v2" right after the first writer queues "v1"."""

      def append(self, op):
        """
        Queue ``op``; after the store of "v1", run the second writer's whole wait=False write of "v2".

        Parameters
        ----------
        op : dict
          The store queued.

        Returns
        -------
        None
        """
        # Queued.
        super().append(op)
        if op["value"] == "v1":
          # The second writer, between the first one's queueing and its registration.
          store._set_value(KEY, "v2", owner="node", wait=False)
    # The store's send queue.
    store._ChainStoreBasePlugin__ops = Interleaved()
    # The first writer's write of "v1".
    write(store, KEY, "v1")
    # The entry is the second write's, and its value is re-sent at 10 and 20 s.
    self.assertEqual((getattr(store, UNWAITED)[KEY][0]["value"], [lap(store, at) for at in (10, 20, 30)],
                      store.sent_payloads[-1]["data"]["CHAIN_STORE_DATA"]["value"]), ("v2", [1, 1, 0], "v2"))

  def test_refusal_by_every_target_ends_the_resends(self):
    """When both targets answer with a refusal, the re-sends end, counted refused_by_peers."""
    # A writer.
    store = make("report")
    # The write.
    write(store, KEY, "v")
    # Both peers refuse it.
    for peer in ("peer1", "peer2"):
      # The answer echoes the write's stamp.
      refusal(store, KEY, peer, [NOW_MS, "node"])
    # No re-send at 10 s, counted refused_by_peers.
    self.assertEqual((lap(store, 10), getattr(store, STATS).get(BUCKET + "/local/refused_by_peers")), (0, 1))

  def test_after_the_plugin_loop_ended_a_non_blocking_write_is_not_ready(self):
    """Once the store's plugin loop ended (a stop, or a restart before the next instance starts), a wait=False write
    through its setter returns False with the outcome not_ready, and stores, queues and owes nothing."""
    # A writer whose plugin loop was told to stop.
    store = make("report")
    # As stop_thread sets it.
    store.done_loop = True
    # False, not ready, nothing sent; nothing stored or owed.
    self.assertEqual((write(store, KEY, "v"), KEY in store.storage, getattr(store, UNWAITED)),
                     ((False, "not_ready", 0), False, {}))

  def test_a_non_blocking_write_ends_not_ready_while_another_thread_holds_the_lock(self):
    """While another thread holds the persistence lock (as a merge holds it for its whole answer), a wait=False write
    returns False with the outcome not_ready within a fraction of a second, and stores, queues, owes and counts
    nothing; once the lock is free the same write is stored and queued, and the lock is left free."""
    # A writer, and its lock.
    store = make("report")
    # The lock the other thread holds.
    lock = getattr(store, LOCK)
    # Set once the other thread holds the lock, and to let it go.
    taken, done = threading.Event(), threading.Event()

    def hold():
      """
      Stand for a merge: hold the persistence lock until the test is done with it, 2 s at most.

      Returns
      -------
      None
      """
      # The lock, as the merge's with statement takes it.
      with lock:
        # Held.
        taken.set()
        # Until the write returned (or 2 s, so a write that waits for the lock ends too).
        done.wait(2)
    # The holder, started and holding.
    holder = threading.Thread(target=hold)
    # Running.
    holder.start()
    # Held from here.
    taken.wait()
    # The caller's dict, and the wall time the write starts at.
    info, started = {}, time.monotonic()
    # The write, made while the lock is held.
    result = store._set_value(KEY, "v", owner="node", wait=False, result_info=info)
    # How long it took.
    elapsed = time.monotonic() - started
    # The holder lets go.
    done.set()
    # And ends.
    holder.join()
    # Not ready, at once; nothing stored, queued, owed or counted.
    self.assertEqual((result, info, elapsed < 0.5, KEY in store.storage, len(store._ChainStoreBasePlugin__ops),
                      getattr(store, UNWAITED), getattr(store, STATS)), (False, {"outcome": "not_ready"}, True,
                                                                         False, 0, {}, {}))
    # With the lock free, the same write is stored and queued.
    self.assertEqual(write(store, KEY, "v"), (True, "changed", 1))
    # Whether another thread then gets the lock at once: the writer let go of it.
    free = []
    # That thread: the lock within 0.5 s, given back at once (release() gives None).
    checker = threading.Thread(target=lambda: free.append(lock.acquire(timeout=0.5) and lock.release() is None))
    # Run.
    checker.start()
    # Ended.
    checker.join()
    # It got the lock.
    self.assertEqual(free, [True])

  def test_a_non_blocking_write_that_ends_before_its_bounded_wait_counts_within_the_bound(self):
    """While another thread holds the persistence lock, a wait=False write with no target peer (refused_no_peers)
    and one with a bad timeout (error) end within a fraction of a second with their own words and are not counted
    (their count waited for the whole hold before); with the lock free the same refusal is counted, and the lock is
    left free."""
    # A writer with no replication peer, and its lock.
    store = make("report", peers=())
    # The lock the other thread holds.
    lock = getattr(store, LOCK)
    # Set once the other thread holds the lock, and to let it go.
    taken, done = threading.Event(), threading.Event()

    def hold():
      """
      Stand for a merge: hold the persistence lock until the test is done with it, 2 s at most.

      Returns
      -------
      None
      """
      # The lock, as the merge's with statement takes it.
      with lock:
        # Held.
        taken.set()
        # Until both writes returned (or 2 s, so a count that waits for the lock ends too).
        done.wait(2)
    # The holder.
    holder = threading.Thread(target=hold)
    # Running.
    holder.start()
    # Held from here.
    taken.wait()
    # Each write's caller dict, and the wall time the writes start at.
    no_peers, bad_timeout, started = {}, {}, time.monotonic()
    # No target peer: refused before the bounded wait.
    store._set_value(KEY, "v", owner="node", wait=False, result_info=no_peers)
    # A bad timeout raises before it.
    with self.assertRaises(ValueError):
      # The write.
      store._set_value(KEY, "v", owner="node", wait=False, timeout=-1, result_info=bad_timeout)
    # How long both took.
    elapsed = time.monotonic() - started
    # The holder lets go.
    done.set()
    # And ends.
    holder.join()
    # Both ended at once with their own words; neither was counted.
    self.assertEqual((no_peers, bad_timeout, elapsed < 0.5, getattr(store, STATS)),
                     ({"outcome": "refused_no_peers"}, {"outcome": "error"}, True, {}))
    # With the lock free the same refusal is counted.
    self.assertEqual((write(store, KEY, "v"), getattr(store, STATS)),
                     ((False, "refused_no_peers", 0), {BUCKET + "/local/refused_no_peers": 1}))
    # Whether another thread then gets the lock at once: the writes let go of it.
    free = []
    # That thread: the lock within 0.5 s, given back at once (release() gives None).
    checker = threading.Thread(target=lambda: free.append(lock.acquire(timeout=0.5) and lock.release() is None))
    # Run.
    checker.start()
    # Ended.
    checker.join()
    # It got the lock.
    self.assertEqual(free, [True])

  def test_a_non_blocking_write_keeps_the_lock_from_its_apply_to_its_count(self):
    """A thread that asks for the persistence lock while a wait=False write is between its apply and the
    registration of its re-sends gets it only after the write registered them and counted itself; the write does
    not wait for that thread a second time and ends at once, stored, queued and owed a re-send."""
    # A writer.
    store = make("report")
    # The lock the other thread asks for.
    lock = getattr(store, LOCK)
    # What the other thread saw when it got the lock: the entry owed a re-send and the count.
    seen = []

    def take():
      """
      Stand for a merge that starts during the write: take the lock, note what the write had done, hold it 0.3 s.

      Returns
      -------
      None
      """
      # Waits while the write holds the lock (2 s at most, so a write that never lets go fails instead of hanging).
      got = lock.acquire(timeout=2)
      # Whether it got the lock, and the write's entry and count as far as they exist now.
      seen.append((got, list(getattr(store, UNWAITED)), dict(getattr(store, STATS))))
      # Held a while, as a merge holds it.
      time.sleep(0.3)
      if got:
        # Given back.
        lock.release()
    # The other thread, started when the write queues its store (after its apply, before its registration).
    taker = threading.Thread(target=take)

    class Queue(collections.deque):
      """The send queue, which starts the other thread when the write queues its store and lets it ask for the lock."""

      def append(self, op):
        """
        Queue ``op``, then start the thread that asks for the lock and give it 0.1 s to ask.

        Parameters
        ----------
        op : dict
          The store queued.

        Returns
        -------
        None
        """
        # Queued.
        super().append(op)
        # The other thread asks for the lock now.
        taker.start()
        # Time for it to reach the lock (a lock let go here would be taken by it within this time).
        time.sleep(0.1)
    # The store's send queue.
    store._ChainStoreBasePlugin__ops = Queue()
    # The wall time the write starts at.
    started = time.monotonic()
    # The write: stored, queued and owed a re-send, in well under the other thread's 0.3 s hold.
    self.assertEqual((store._set_value(KEY, "v", owner="node", wait=False), time.monotonic() - started < 0.3,
                      list(getattr(store, UNWAITED))), (True, True, [KEY]))
    # The other thread ends.
    taker.join()
    # It got the lock, only after the write's registration and count.
    self.assertEqual(seen, [(True, [KEY], {BUCKET + "/local/changed": 1})])

  def test_a_delete_is_queued_once_and_owes_no_resend(self):
    """A wait=False delete returns True with its outcome (changed), sends one store and owes no re-send."""
    # A writer holding KEY.
    store = make("report", storage={KEY: record(KEY, "v", "node")})
    # The delete.
    self.assertEqual((write(store, KEY, None), getattr(store, UNWAITED)), ((True, "changed", 1), {}))


class TestPluginApi(unittest.TestCase):
  """chainstore_set and chainstore_hset pass wait on; wait=False never waits for the store to start."""

  def test_wait_false_never_waits_for_the_store_and_says_not_ready(self):
    """With no setter published, wait=False returns False at once with the outcome not_ready, while wait=True waits
    (here until the setter appears); chainstore_hset passes wait and result_info to the setter."""
    # The plugin API with no chain store started.
    api = _PeerSelectionHarness()
    # The key helpers chainstore_hset composes a key with.
    api.get_hash, api.str_to_base64 = _ChainStoreRuntimeHarness.get_hash, _ChainStoreRuntimeHarness.str_to_base64
    # Not published.
    setter = api.plugins_shmem.pop("__chain_storage_set")
    # The caller's dict.
    info = {}
    # False at once, not ready, no time waited.
    self.assertEqual((api.chainstore_set("k", 1, wait=False, result_info=info), info, api._now),
                     (False, {"outcome": "not_ready"}, 0.0))
    # A sleep that publishes the setter on its third call, as a start would.
    sleeps = []

    def sleep(seconds):
      """
      Stand for the API's sleep while it waits for the store: publish the setter on the third call.

      Parameters
      ----------
      seconds : float
        The sleep asked for.

      Returns
      -------
      None
      """
      # Counted.
      sleeps.append(seconds)
      if len(sleeps) == 3:
        # The store started.
        api.plugins_shmem["__chain_storage_set"] = setter
    # Used by the wait for the store.
    api.sleep = sleep
    # The blocking call waits, then reaches the setter.
    api.chainstore_hset("h", "f", 2, result_info=info)
    # It waited three times; the setter got wait True, and wait False from a non-blocking hset.
    api.chainstore_hset("h", "f", 3, wait=False, result_info=info)
    # The setter's calls.
    self.assertEqual((len(sleeps), [(call[3]["wait"], call[3]["result_info"] is info) for call in api.calls]),
                     (3, [(True, True), (False, True)]))


  def test_a_non_blocking_call_is_timed_under_its_own_timer_name(self):
    """chainstore_set times a wait=False call as chainstore_set_nowait and a blocking one as chainstore_set, so a call
    on the request monitor's thread never shares the timer of a blocking call on the plugin's thread."""
    # The plugin API with the store's setter published.
    api = _PeerSelectionHarness()
    # The timer calls, in order.
    timers = []
    # Each start, recorded.
    api.start_timer = lambda name: timers.append(("start", name))
    # Each end, recorded, with an elapsed time.
    api.end_timer = lambda name: timers.append(("end", name)) or 0.0
    # A non-blocking call, then a blocking one.
    api.chainstore_set("k", 1, wait=False)
    # The blocking one.
    api.chainstore_set("k", 2)
    # Each timed under its own name.
    self.assertEqual(timers, [("start", "chainstore_set_nowait"), ("end", "chainstore_set_nowait"),
                              ("start", "chainstore_set"), ("end", "chainstore_set")])


if __name__ == "__main__":
  # Run this module alone.
  unittest.main()

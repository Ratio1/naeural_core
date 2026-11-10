"""
SerializationManager: background saves through one small worker process,
including the failures it must survive (worker killed, unpicklable snapshot,
unwritable path, slow disk, node killed in the middle of a write, shutdown).
"""
import os
import pickle
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from naeural_core.main import serialization_manager as smod


def _load(path):
  with open(path, "rb") as stream:
    return pickle.load(stream)


def _big(megabytes, tag=0):
  return {"tag": tag, "rows": [os.urandom(1024).hex() for _ in range(megabytes * 512)]}


class _Base(unittest.TestCase):
  manager_kwargs = {}

  def setUp(self):
    self.folder = tempfile.mkdtemp(prefix="sermgr_")
    self.path = os.path.join(self.folder, "sub", "object.pickle")
    self.errors = []
    self.manager = smod.SerializationManager(log=None, interval=0.2, **self.manager_kwargs)
    self.manager.P = lambda msg, color=None: None
    self.manager.start()

  def tearDown(self):
    self.manager.stop(timeout=10)
    shutil.rmtree(self.folder, ignore_errors=True)

  def save(self, obj, name="a", path=None):
    self.assertTrue(self.manager.submit(name, path or self.path, obj, on_error=self.errors.append))
    self.assertTrue(self.manager.flush(timeout=20))


class TestWorkerProcess(_Base):

  def test_saves_through_the_worker_and_leaves_no_temp_file(self):
    self.save({"a": 1})
    self.assertEqual(_load(self.path), {"a": 1})
    self.assertEqual(os.listdir(os.path.dirname(self.path)), ["object.pickle"])
    stats = self.manager.get_stats()
    self.assertEqual((stats["saves"], stats["inline"], stats["errors"]), (1, 0, 0))
    self.assertTrue(stats["worker_alive"])

  def test_worker_is_a_small_process_that_does_not_import_the_node(self):
    self.save(_big(4))
    pid = self.manager._proc.pid
    with open("/proc/%d/status" % pid) as stream:
      rss_kb = int(stream.read().split("VmRSS:")[1].split()[0])
    with open("/proc/%d/cmdline" % pid, "rb") as stream:
      cmdline = stream.read()
    self.assertLess(rss_kb, 40 * 1024, "the worker must stay small whatever it writes")
    self.assertNotIn(b"multiprocessing", cmdline)
    self.assertEqual(len(os.listdir("/proc/%d/task" % pid)), 1)

  def test_latest_snapshot_wins_and_saves_are_spaced(self):
    self.manager.interval = 0.5
    for idx in range(200):
      self.manager.submit("a", self.path, {"v": idx})
    self.assertTrue(self.manager.flush(timeout=20))
    self.assertEqual(_load(self.path), {"v": 199})
    stats = self.manager.get_stats()
    self.assertLessEqual(stats["saves"], 3)
    self.assertGreaterEqual(stats["superseded"], 197)

  def test_interval_spaces_saves_of_one_name(self):
    self.manager.interval = 0.6
    self.save({"v": 0})
    start = time.time()
    self.manager.submit("a", self.path, {"v": 1})
    while _load(self.path) != {"v": 1} and time.time() - start < 5:
      time.sleep(0.02)
    self.assertGreaterEqual(time.time() - start, 0.4)

  def test_names_are_independent(self):
    other = os.path.join(self.folder, "other.pkl")
    self.manager.submit("a", self.path, {"v": "a"})
    self.manager.submit("b", other, {"v": "b"})
    self.assertTrue(self.manager.flush(timeout=20))
    self.assertEqual((_load(self.path), _load(other)), ({"v": "a"}, {"v": "b"}))

  def test_killed_worker_falls_back_inline_then_restarts(self):
    self.manager.respawn_delay = 0.5
    self.save({"v": 1})
    first = self.manager._proc.pid
    os.kill(first, signal.SIGKILL)
    time.sleep(0.1)
    self.save({"v": 2})
    self.assertEqual(_load(self.path), {"v": 2})
    self.assertEqual(self.manager.get_stats()["inline"], 1)
    time.sleep(0.6)
    self.save({"v": 3})
    self.assertEqual(_load(self.path), {"v": 3})
    stats = self.manager.get_stats()
    self.assertEqual((stats["worker_starts"], stats["errors"]), (2, 0))
    self.assertNotEqual(self.manager._proc.pid, first)

  def test_worker_killed_in_the_middle_of_a_write_keeps_the_old_file(self):
    self.save({"v": "old"})
    pid = self.manager._proc.pid
    os.kill(pid, signal.SIGSTOP)                 # the worker stops reading: the feeder blocks mid-stream
    self.manager.submit("a", self.path, _big(8, tag="new"))
    time.sleep(0.5)
    self.assertEqual(_load(self.path), {"v": "old"})
    os.kill(pid, signal.SIGKILL)
    self.assertTrue(self.manager.flush(timeout=30))
    self.assertEqual(_load(self.path)["tag"], "new")   # written by the feeder thread instead
    self.assertEqual(self.errors, [])

  def test_worker_that_never_acknowledges_is_replaced(self):
    self.manager.ack_timeout = 0.5
    self.save({"v": 1})
    os.kill(self.manager._proc.pid, signal.SIGSTOP)
    self.save({"v": 2})
    self.assertEqual(_load(self.path), {"v": 2})
    self.assertEqual(self.manager.get_stats()["inline"], 1)

  def test_unpicklable_snapshot_reports_and_keeps_the_old_file(self):
    self.save({"v": "old"})
    self.save({"v": threading.Lock()})
    self.assertEqual(_load(self.path), {"v": "old"})
    self.assertEqual(len(self.errors), 1)
    self.assertEqual(self.manager.get_stats()["errors"], 1)
    self.save({"v": "next"})                     # the worker is still usable afterwards
    self.assertEqual(_load(self.path), {"v": "next"})
    self.assertEqual(self.manager.get_stats()["worker_starts"], 1)

  def test_large_unpicklable_snapshot_does_not_corrupt_the_next_save(self):
    self.save({"v": "old"})
    # Megabytes are already streamed to the worker when the pickler meets the lock.
    self.save({"rows": _big(4), "bad": threading.Lock(), "more": _big(1)})
    self.assertEqual(len(self.errors), 1)
    self.assertEqual(_load(self.path), {"v": "old"})
    for idx in range(3):
      self.save({"v": idx})
      self.assertEqual(_load(self.path), {"v": idx})
    stats = self.manager.get_stats()
    self.assertEqual((stats["worker_starts"], stats["inline"]), (1, 0))

  def test_interrupt_meant_for_the_node_does_not_stop_the_worker(self):
    self.save({"v": 1})
    pid = self.manager._proc.pid
    self.assertNotEqual(os.getsid(pid), os.getsid(0))    # its own session: a terminal Ctrl-C does not reach it
    os.kill(pid, signal.SIGINT)
    time.sleep(0.2)
    self.save({"v": 2})
    stats = self.manager.get_stats()
    self.assertEqual((stats["worker_starts"], stats["inline"], _load(self.path)), (1, 0, {"v": 2}))

  def test_default_is_to_save_as_soon_as_the_feeder_is_free(self):
    self.assertEqual(smod.SerializationManager().interval, 0)

  def test_snapshot_submitted_while_stopping_is_written_after_the_pending_one(self):
    os.kill(self._warm_worker(), signal.SIGSTOP)          # the feeder is now stuck in the middle of a save
    self.manager.submit("a", self.path, _big(4, tag="older"))
    time.sleep(0.3)
    stopper = threading.Thread(target=self.manager.stop, kwargs={"timeout": 30})
    stopper.start()
    time.sleep(0.2)
    self.assertTrue(self.manager.submit("a", self.path, {"tag": "newest"}))   # still accepted: the feeder orders it
    os.kill(self.manager._proc.pid, signal.SIGCONT)
    stopper.join(timeout=40)
    self.assertFalse(stopper.is_alive())
    self.assertEqual(_load(self.path), {"tag": "newest"})
    self.assertFalse(self.manager.submit("a", self.path, {"tag": "late"}))    # refused only once the feeder is done

  def _warm_worker(self):
    self.save({"v": 0})
    return self.manager._proc.pid

  def test_unwritable_path_reports(self):
    self.save({"v": 1}, name="bad", path="/proc/does-not-exist/x.pkl")
    self.assertEqual(len(self.errors), 1)
    self.save({"v": 2})
    self.assertEqual(_load(self.path), {"v": 2})

  def test_stop_writes_what_is_pending_and_refuses_new_work(self):
    self.manager.interval = 60
    self.save({"v": 0})
    self.manager.submit("a", self.path, {"v": "last"})   # not due for a minute
    self.assertTrue(self.manager.stop(timeout=20))
    self.assertEqual(_load(self.path), {"v": "last"})
    self.assertFalse(self.manager.submit("a", self.path, {"v": "late"}))

  def test_worker_exits_when_the_node_goes_away(self):
    self.save({"v": 1})
    proc = self.manager._proc
    proc.stdin.close()                           # what the worker sees when its parent dies
    self.assertEqual(proc.wait(timeout=10), 0)

  def test_submit_never_blocks_while_a_large_save_is_running(self):
    self.manager.submit("big", os.path.join(self.folder, "big.pkl"), _big(16))
    slowest = 0.0
    for idx in range(2000):
      start = time.perf_counter()
      self.manager.submit("a", self.path, {"v": idx})
      slowest = max(slowest, time.perf_counter() - start)
    self.assertLess(slowest, 0.25)
    self.assertTrue(self.manager.flush(timeout=60))
    self.assertEqual(_load(self.path), {"v": 1999})

  def test_many_threads_submit_and_flush_without_deadlock(self):
    done = []

    def work(idx):
      path = os.path.join(self.folder, "t%d.pkl" % (idx % 4))
      for step in range(50):
        self.manager.submit("t%d" % (idx % 4), path, {"thread": idx, "step": step})
        if step % 10 == 0:
          self.manager.flush(timeout=20, name="t%d" % (idx % 4))
      done.append(idx)
    threads = [threading.Thread(target=work, args=(idx,)) for idx in range(16)]
    for thread in threads:
      thread.start()
    for thread in threads:
      thread.join(timeout=60)
    self.assertEqual(len(done), 16, "a submitting thread is stuck")
    self.assertTrue(self.manager.flush(timeout=30))
    for idx in range(4):
      self.assertEqual(_load(os.path.join(self.folder, "t%d.pkl" % idx))["step"], 49)


class TestThreadOnly(_Base):
  manager_kwargs = {"use_process": False}

  def test_saves_without_a_worker_process(self):
    self.save({"a": 1})
    self.assertEqual(_load(self.path), {"a": 1})
    self.assertIsNone(self.manager._proc)
    self.assertEqual(self.manager.get_stats()["saves"], 1)


class TestNodeKilledMidWrite(unittest.TestCase):

  def test_file_is_always_loadable(self):
    """Kill a writing node at random moments: the file on disk is always a complete earlier or later snapshot."""
    folder = tempfile.mkdtemp(prefix="sermgr_kill_")
    path = os.path.join(folder, "object.pickle")
    # The module is loaded by path: importing the whole node package would take longer than the kill delays.
    script = (
      "import importlib.util, sys\n"
      "spec = importlib.util.spec_from_file_location('smod', %r)\n"
      "smod = importlib.util.module_from_spec(spec)\n"
      "spec.loader.exec_module(smod)\n"
      "m = smod.SerializationManager(interval=0)\n"
      "m.P = lambda msg, color=None: None\n"
      "m.start()\n"
      "n = 0\n"
      "while True:\n"
      "  n += 1\n"
      "  m.submit('a', %r, {'n': n, 'rows': [str(n) * 50] * 20000})\n"
      "  m.flush(10)\n"
    ) % (os.path.abspath(smod.__file__), path)
    try:
      seen = []
      for delay in (0.8, 1.0, 1.3, 1.7, 2.1):
        proc = subprocess.Popen([sys.executable, "-c", script], stderr=subprocess.DEVNULL)
        time.sleep(delay)
        proc.kill()
        proc.wait()
        time.sleep(0.3)                          # the orphaned worker notices the closed pipe and exits
        if os.path.exists(path):
          data = _load(path)
          self.assertEqual(len(data["rows"]), 20000)
          self.assertEqual(data["rows"][0], str(data["n"]) * 50)
          seen.append(data["n"])
      self.assertTrue(seen, "no snapshot was written at all")
    finally:
      shutil.rmtree(folder, ignore_errors=True)


class _Log:
  def __init__(self, folder):
    self.folder, self.inline = folder, []

  def get_data_folder(self):
    return self.folder

  def P(self, msg, color=None):
    return

  def save_pickle_to_data(self, data, fn, subfolder_path=None, **kwargs):
    self.inline.append((fn, subfolder_path, kwargs))
    return "inline"


class TestDropInHelper(unittest.TestCase):

  def setUp(self):
    self.folder = tempfile.mkdtemp(prefix="sermgr_helper_")
    self.log = _Log(self.folder)
    self.previous = os.environ.pop(smod.ENV_SWITCH, None)

  def tearDown(self):
    smod.stop_global(timeout=10)
    if self.previous is not None:
      os.environ[smod.ENV_SWITCH] = self.previous
    else:
      os.environ.pop(smod.ENV_SWITCH, None)
    shutil.rmtree(self.folder, ignore_errors=True)

  def test_without_a_manager_the_logger_saves_as_before(self):
    self.assertEqual(smod.save_pickle_to_data(self.log, data={"a": 1}, fn="db.pkl", subfolder_path="network_monitor"), "inline")
    self.assertEqual(self.log.inline, [("db.pkl", "network_monitor", {})])

  def test_with_a_manager_the_snapshot_is_queued_at_the_same_path(self):
    manager = smod.start_global(log=self.log)
    self.assertIs(smod.get_serialization_manager(), manager)
    self.assertTrue(smod.save_pickle_to_data(self.log, data={"a": 1}, fn="db.pkl", subfolder_path="network_monitor"))
    self.assertTrue(manager.flush(timeout=20))
    self.assertEqual(_load(os.path.join(self.folder, "network_monitor", "db.pkl")), {"a": 1})
    self.assertEqual(self.log.inline, [])

  def test_compressed_files_stay_with_the_logger(self):
    smod.start_global(log=self.log)
    self.assertEqual(smod.save_pickle_to_data(self.log, data={"a": 1}, fn="db.pklz"), "inline")
    self.assertEqual(smod.save_pickle_to_data(self.log, data={"a": 1}, fn="db.pkl", compressed=True), "inline")

  def test_environment_switch(self):
    os.environ[smod.ENV_SWITCH] = "0"
    self.assertIsNone(smod.start_global(log=self.log))
    os.environ[smod.ENV_SWITCH] = "thread"
    manager = smod.start_global(log=self.log)
    self.assertFalse(manager.use_process)
    smod.stop_global(timeout=10)
    self.assertIsNone(smod.get_serialization_manager())
    self.assertEqual(smod.save_pickle_to_data(self.log, data={"a": 1}, fn="db.pkl"), "inline")

  def test_stop_global_writes_pending_snapshots(self):
    manager = smod.start_global(log=self.log)
    manager.interval = 60
    smod.save_pickle_to_data(self.log, data={"v": 1}, fn="db.pkl")
    manager.flush(timeout=20)
    smod.save_pickle_to_data(self.log, data={"v": 2}, fn="db.pkl")
    smod.stop_global(timeout=20)
    self.assertEqual(_load(os.path.join(self.folder, "db.pkl")), {"v": 2})


if __name__ == "__main__":
  unittest.main()

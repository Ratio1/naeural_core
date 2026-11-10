"""
The plugin cache save (``cacheapi_save_pickle``) through the serialization
manager: what reaches the disk, in which order, and what happens around
start and stop of the manager.
"""
import json
import os
import pickle
import shutil
import tempfile
import threading
import time
import unittest

from naeural_core.main import serialization_manager as smod
from naeural_core.utils.plugins_base.persistence_serialization_mixin import _PersistenceSerializationMixin


class _Log:
  def __init__(self, folder):
    self.folder = folder

  def get_data_folder(self):
    return self.folder

  def P(self, msg, color=None):
    return


class _Plugin(_PersistenceSerializationMixin):
  cfg_serialization_signature = "sig-1"

  def __init__(self, folder):
    super().__init__()
    self.log = _Log(folder)

  @staticmethod
  def sanitize_name(name):
    return name

  @property
  def _cache_folder(self):
    return "cache_plugins"                            # as business plugins define it

  @property
  def file(self):
    return os.path.join(self.log.folder, self._maybe_setup_persistence_fld().lstrip("/"), "object.pickle")

  def on_disk(self):
    with open(self.file, "rb") as stream:
      return pickle.load(stream)


class TestBackgroundCacheSave(unittest.TestCase):

  def setUp(self):
    self.folder = tempfile.mkdtemp(prefix="cache_bg_")
    self.previous = os.environ.pop(smod.ENV_SWITCH, None)
    self.plugin = _Plugin(self.folder)
    self.errors = []

  def tearDown(self):
    smod.stop_global(timeout=10)
    if self.previous is not None:
      os.environ[smod.ENV_SWITCH] = self.previous
    shutil.rmtree(self.folder, ignore_errors=True)

  def test_without_a_manager_the_save_is_inline_and_complete(self):
    self.assertIsNone(self.plugin.cacheapi_save_pickle({"v": 1}, strict=True, background=True))
    self.assertEqual(self.plugin.on_disk(), {"v": 1})
    with open(os.path.join(os.path.dirname(self.plugin.file), "metadata.json")) as stream:
      self.assertEqual(json.load(stream), {"SERIALIZATION_SIGNATURE": "sig-1"})

  def test_background_save_reaches_the_same_file(self):
    manager = smod.start_global(log=self.plugin.log)
    path = self.plugin.cacheapi_save_pickle({"v": 1}, strict=True, background=True, on_error=self.errors.append)
    self.assertEqual(path, self.plugin.file)
    self.assertTrue(manager.flush(timeout=20, name=path))
    self.assertEqual(self.plugin.on_disk(), {"v": 1})
    self.assertTrue(os.path.exists(os.path.join(os.path.dirname(path), "metadata.json")))
    self.assertEqual(self.errors, [])
    self.assertEqual(manager.get_stats()["saves"], 1)

  def test_signature_file_is_written_once(self):
    manager = smod.start_global(log=self.plugin.log)
    self.plugin.cacheapi_save_pickle({"v": 1}, strict=True, background=True)
    meta = os.path.join(os.path.dirname(self.plugin.file), "metadata.json")
    first = os.stat(meta).st_mtime_ns
    time.sleep(0.05)
    for idx in range(5):
      self.plugin.cacheapi_save_pickle({"v": idx}, strict=True, background=True)
    manager.flush(timeout=20)
    self.assertEqual(os.stat(meta).st_mtime_ns, first)
    self.assertEqual(self.plugin.on_disk(), {"v": 4})

  def test_caller_is_not_held_by_a_slow_disk(self):
    manager = smod.start_global(log=self.plugin.log)
    self.plugin.cacheapi_save_pickle({"v": 0}, strict=True, background=True)
    manager.flush(timeout=20)
    original = smod.save_file_atomic
    manager.use_process = False                       # write from the feeder thread, through a slow "disk"
    smod.save_file_atomic = lambda path, obj: (time.sleep(1.0), original(path, obj))[1]
    try:
      start = time.perf_counter()
      self.plugin.cacheapi_save_pickle({"v": "slow"}, strict=True, background=True)
      self.assertLess(time.perf_counter() - start, 0.2)
      self.assertTrue(manager.flush(timeout=20))
      self.assertEqual(self.plugin.on_disk(), {"v": "slow"})
    finally:
      smod.save_file_atomic = original

  def test_strict_save_without_background_stays_inline(self):
    manager = smod.start_global(log=self.plugin.log)
    self.assertIsNone(self.plugin.cacheapi_save_pickle({"v": 1}, strict=True))
    self.assertEqual(self.plugin.on_disk(), {"v": 1})
    self.assertEqual(manager.get_stats()["saves"], 0)

  def test_save_after_the_manager_stopped_is_inline_and_is_the_last_word(self):
    manager = smod.start_global(log=self.plugin.log)
    self.plugin.cacheapi_save_pickle({"v": "queued"}, strict=True, background=True)
    smod.stop_global(timeout=20)
    self.assertEqual(self.plugin.on_disk(), {"v": "queued"})
    self.assertIsNone(self.plugin.cacheapi_save_pickle({"v": "after stop"}, strict=True, background=True))
    self.assertEqual(self.plugin.on_disk(), {"v": "after stop"})

  def test_save_during_shutdown_does_not_let_an_older_snapshot_land_last(self):
    """A plugin that closes late saves while the manager is draining: the newest snapshot must win."""
    manager = smod.start_global(log=self.plugin.log)
    self.plugin.cacheapi_save_pickle({"v": 0}, strict=True, background=True)
    manager.flush(timeout=20)
    original = smod.save_file_atomic
    manager.use_process = False
    smod.save_file_atomic = lambda path, obj: (time.sleep(0.6), original(path, obj))[1]
    try:
      self.plugin.cacheapi_save_pickle({"v": "older"}, strict=True, background=True)
      time.sleep(0.1)                                 # the feeder is writing "older"
      stopper = threading.Thread(target=smod.stop_global, kwargs={"timeout": 20})
      stopper.start()
      time.sleep(0.1)
      self.plugin.cacheapi_save_pickle({"v": "newest"}, strict=True, background=True)
      stopper.join(timeout=30)
    finally:
      smod.save_file_atomic = original
    self.assertEqual(self.plugin.on_disk(), {"v": "newest"})

  def test_failed_background_save_calls_back(self):
    manager = smod.start_global(log=self.plugin.log)
    self.plugin.cacheapi_save_pickle({"v": threading.Lock()}, strict=True, background=True, on_error=self.errors.append)
    manager.flush(timeout=20)
    self.assertEqual(len(self.errors), 1)


if __name__ == "__main__":
  unittest.main()

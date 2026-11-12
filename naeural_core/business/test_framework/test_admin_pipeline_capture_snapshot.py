"""Regression tests for the admin lane's capture-safe configuration snapshot."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from unittest.mock import patch

from naeural_core import constants as ct
from naeural_core.config.runtime_secret_resolution import (
  DAUTH_SECRET_PLACEHOLDER,
  build_capture_pipeline_config,
)
from naeural_core.main import orchestrator as orchestrator_module


class _Uncopyable:
  """Reject traversal of unrelated configuration or resolved runtime values."""

  def __deepcopy__(self, memo):
    """Fail when a snapshot visits a forbidden value.

    Parameters
    ----------
    memo : dict
        Copy memo supplied by ``copy.deepcopy``.

    Raises
    ------
    AssertionError
        Always: the admin lane must not copy this value.
    """
    raise AssertionError("Admin snapshot traversed unrelated or runtime state")


class AdminPipelineCaptureSnapshotTests(unittest.TestCase):
  """Check selection, secret boundaries, detached ownership and lock scope."""

  def setUp(self):
    """Build an unstarted orchestrator with the production state initializer."""
    self.admin = ct.CONST_ADMIN_PIPELINE_NAME
    self.orchestrator = orchestrator_module.Orchestrator.__new__(
      orchestrator_module.Orchestrator,
    )
    self.orchestrator._current_dct_config_streams = {}
    self.orchestrator._ensure_runtime_secret_resolution_state()
    self.canonical = {
      "NAME": self.admin,
      "URL": "$EE_ADMIN_TEST_CAPTURE",
      "METADATA": {"revision": [1]},
      "PLUGINS": [{"SIGNATURE": "TEST", "INSTANCES": [{
        "INSTANCE_ID": "test",
        "ENV": {
          "DAUTH": DAUTH_SECRET_PLACEHOLDER,
          "LOCAL": "$EE_ADMIN_TEST_PLUGIN",
        },
      }]}],
    }
    self.orchestrator._current_dct_config_streams[self.admin] = self.canonical
    # Eligibility is name membership only; capture must never read these values.
    self.orchestrator._runtime_dct_config_streams[self.admin] = _Uncopyable()

  def test_absent_or_ineligible_admin_returns_empty_without_building(self):
    """Require both canonical presence and runtime eligibility, independently."""
    for canonical_present, eligible in ((False, False), (False, True), (True, False)):
      with self.subTest(canonical_present=canonical_present, eligible=eligible):
        self.orchestrator._current_dct_config_streams = {"unrelated": _Uncopyable()}
        self.orchestrator._runtime_dct_config_streams = {"unrelated": _Uncopyable()}
        if canonical_present:
          self.orchestrator._current_dct_config_streams[self.admin] = self.canonical
        if eligible:
          self.orchestrator._runtime_dct_config_streams[self.admin] = _Uncopyable()
        with patch.object(orchestrator_module, "build_capture_pipeline_config") as builder:
          self.assertEqual(self.orchestrator._get_admin_pipeline_running_streams_snapshot(), {})
        builder.assert_not_called()

  def test_only_selected_admin_is_copied_and_built(self):
    """Pin constant selection cost without a flaky wall-clock threshold."""
    self.orchestrator._current_dct_config_streams["unrelated"] = _Uncopyable()
    self.orchestrator._runtime_dct_config_streams["unrelated"] = _Uncopyable()
    with patch.object(orchestrator_module, "deepcopy", wraps=deepcopy) as copier:
      with patch.object(
        orchestrator_module, "build_capture_pipeline_config",
        wraps=build_capture_pipeline_config,
      ) as builder:
        result = self.orchestrator._get_admin_pipeline_running_streams_snapshot()
    self.assertEqual(set(result), {self.admin})
    copier.assert_called_once_with(self.canonical)
    self.assertIs(copier.call_args.args[0], self.canonical)
    builder.assert_called_once_with(self.canonical)
    self.assertIsNot(builder.call_args.args[0], self.canonical)

  def test_present_empty_admin_remains_present(self):
    """Do not confuse a present empty mapping with a missing admin pipeline."""
    self.orchestrator._current_dct_config_streams[self.admin] = {}
    self.assertEqual(
      self.orchestrator._get_admin_pipeline_running_streams_snapshot(),
      {self.admin: {}},
    )

  def test_invalid_admin_retains_capture_builder_failure(self):
    """Preserve the existing failure for invalid configuration, not silent success."""
    self.orchestrator._current_dct_config_streams[self.admin] = None
    with self.assertRaises(AttributeError):
      self.orchestrator._get_capture_streams_snapshot()
    with self.assertRaises(AttributeError):
      self.orchestrator._get_admin_pipeline_running_streams_snapshot()

  def test_capture_environment_resolves_but_plugin_secrets_stay_canonical(self):
    """Match the existing capture builder without exposing runtime plaintext."""
    runtime = deepcopy(self.canonical)
    runtime["PLUGINS"][0]["INSTANCES"][0]["ENV"] = {
      "DAUTH": "synthetic-runtime-secret", "LOCAL": "synthetic-plugin-secret",
    }
    self.orchestrator._runtime_dct_config_streams[self.admin] = runtime
    with patch.dict(os.environ, {
      "EE_ADMIN_TEST_CAPTURE": "synthetic://capture-a",
      "EE_ADMIN_TEST_PLUGIN": "synthetic-plugin-secret",
    }):
      result = self.orchestrator._get_admin_pipeline_running_streams_snapshot()
      self.assertEqual(result, self.orchestrator._get_capture_streams_snapshot())
    self.assertEqual(result[self.admin]["URL"], "synthetic://capture-a")
    self.assertEqual(result[self.admin]["PLUGINS"], self.canonical["PLUGINS"])
    self.assertNotIn("synthetic-runtime-secret", repr(result))
    self.assertNotIn("synthetic-plugin-secret", repr(result))

  def test_each_snapshot_is_detached_and_observes_fresh_config_and_environment(self):
    """Keep nested ownership and freshness without introducing a cache."""
    with patch.dict(os.environ, {"EE_ADMIN_TEST_CAPTURE": "synthetic://capture-a"}):
      first = self.orchestrator._get_admin_pipeline_running_streams_snapshot()
    first[self.admin]["PLUGINS"][0]["INSTANCES"][0]["ENV"]["LOCAL"] = "caller edit"
    self.assertEqual(
      self.canonical["PLUGINS"][0]["INSTANCES"][0]["ENV"]["LOCAL"],
      "$EE_ADMIN_TEST_PLUGIN",
    )
    with self.orchestrator._runtime_config_lock:
      self.canonical["METADATA"]["revision"][0] = 2
    with patch.dict(os.environ, {"EE_ADMIN_TEST_CAPTURE": "synthetic://capture-b"}):
      second = self.orchestrator._get_admin_pipeline_running_streams_snapshot()
    self.assertEqual(first[self.admin]["METADATA"]["revision"], [1])
    self.assertEqual(first[self.admin]["URL"], "synthetic://capture-a")
    self.assertEqual(second[self.admin]["METADATA"]["revision"], [2])
    self.assertEqual(second[self.admin]["URL"], "synthetic://capture-b")
    self.assertEqual(second[self.admin]["PLUGINS"], self.canonical["PLUGINS"])

  def test_copy_is_locked_but_builder_allows_concurrent_canonical_update(self):
    """Use a real competing writer to pin the snapshot's lock boundary."""
    lock = self.orchestrator._runtime_config_lock

    def copy_under_lock(value):
      """Check lock ownership while copying the selected canonical value.

      Parameters
      ----------
      value : dict
          Canonical configuration selected by the production method.

      Returns
      -------
      dict
          Detached copy made while a competing lock acquisition is excluded.
      """
      # A second thread cannot acquire even a reentrant lock owned by this thread.
      def try_lock():
        """Probe from a separate thread, releasing any unexpectedly acquired lock."""
        acquired = lock.acquire(blocking=False)
        if acquired:
          lock.release()
        return acquired

      self.assertFalse(worker.submit(try_lock).result(timeout=3))
      return deepcopy(value)

    def update_canonical():
      """Mutate nested canonical state and eligibility, with a bounded lock wait.

      Returns
      -------
      bool
          Whether the writer acquired the production lock before its deadline.
      """
      if not lock.acquire(timeout=1):
        return False
      try:
        self.canonical["METADATA"]["revision"][0] = 2
        self.orchestrator._runtime_dct_config_streams.pop(self.admin)
      finally:
        lock.release()
      return True

    def build_after_update(config):
      """Allow a real writer to finish before transforming the detached snapshot.

      Parameters
      ----------
      config : dict
          Canonical admin copy supplied to the existing capture builder.

      Returns
      -------
      dict
          Capture-safe configuration from the pre-update snapshot.
      """
      self.assertTrue(worker.submit(update_canonical).result(timeout=3))
      return build_capture_pipeline_config(config)

    with ThreadPoolExecutor(max_workers=1) as worker:
      with patch.object(orchestrator_module, "deepcopy", side_effect=copy_under_lock):
        with patch.object(
          orchestrator_module, "build_capture_pipeline_config", side_effect=build_after_update,
        ):
          result = self.orchestrator._get_admin_pipeline_running_streams_snapshot()
    self.assertEqual(result[self.admin]["METADATA"]["revision"], [1])
    self.assertEqual(self.canonical["METADATA"]["revision"], [2])
    self.assertEqual(self.orchestrator._get_admin_pipeline_running_streams_snapshot(), {})

  def test_all_stream_snapshot_still_includes_other_eligible_pipelines(self):
    """Keep the generic capture path's broader selection contract unchanged."""
    self.orchestrator._current_dct_config_streams["other"] = {"NAME": "other"}
    self.orchestrator._runtime_dct_config_streams["other"] = {}
    with patch.object(
      orchestrator_module, "build_capture_pipeline_config", wraps=build_capture_pipeline_config,
    ) as builder:
      result = self.orchestrator._get_capture_streams_snapshot()
    self.assertEqual(set(result), {self.admin, "other"})
    self.assertEqual(builder.call_count, 2)


if __name__ == "__main__":
  unittest.main()

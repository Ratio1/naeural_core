"""Offline tests of durable node admission, aborts and lifecycle observations."""
import base64
import collections
import gc
import hashlib
import importlib.util
import json
import os
import tempfile
import threading
import unittest
import weakref
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from ratio1.const.base import BCct
from naeural_core.main.serialization_manager import SerializationManager

from test_chain_store_hsync import _ChainStoreRuntimeHarness, _hset_key, _hset_record

MODULE_PATH = Path(__file__).resolve().parents[1] / "deployment_lifecycle.py"
spec = importlib.util.spec_from_file_location("deployment_lifecycle", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
DeploymentLifecycle = module.DeploymentLifecycle


def config(action="start", attempt="deploy", revision=2):
  return {
    "NAME": "app", "PLUGINS": [{"SIGNATURE": "WORKER", "INSTANCES": [{"INSTANCE_ID": "worker"}]}],
    "DEEPLOY_OPERATION": {"operation_id": "operation", "attempt": attempt,
      "revision": revision, "coordinator": "oracle", "peers": ["oracle", "other"], "action": action},
  }


def plugin(ready=False, alive=True, external=False, stopped=False):
  return SimpleNamespace(is_plugin_ready=lambda: ready, thread=SimpleNamespace(is_alive=lambda: alive),
    _deployment_external_workload=external, _deployment_stopped=stopped,
    _deployment_cleanup_complete=stopped)


def report(runtime, status, code=None):
  """
  Drive a fake plugin the way `report_deployment_status` drives a real one.

  The real method stores the status and the error code, and makes the plugin ready only for "ready".
  Of a plugin's reported state, the lifecycle reads these three things. A container or worker runner
  never reports "ready": it reports "starting" and later makes the plugin ready by itself, which a
  test models by replacing `is_plugin_ready` after the report.

  Parameters
  ----------
  runtime : types.SimpleNamespace
    A fake plugin made by `plugin`.
  status : str
    The workload status the runner reports, for example "failed", "pulling_image" or "ready".
  code : str or None
    The error code reported with "failed", or None.

  Returns
  -------
  types.SimpleNamespace
    The same fake plugin, so a test can build it and report in one expression.

  Examples
  --------
  >>> runtime = report(plugin(), "failed", "WORKLOAD_START_FAILED")
  >>> runtime.is_plugin_ready(), runtime._deployment_error_code
  (False, 'WORKLOAD_START_FAILED')
  """
  # The status and the error code, set together as the real method sets them.
  runtime._deployment_status, runtime._deployment_error_code = status, code
  # Ready only for "ready", as the real method sets the plugin's readiness.
  runtime.is_plugin_ready = lambda: status == "ready"
  # The same fake plugin, for use in one expression.
  return runtime


class DeploymentLifecycleTest(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.path = os.path.join(self.directory.name, "journal.json")
    self.lifecycle = DeploymentLifecycle(self.path, "node", source_modes=True)
    self.writes = []

  def test_private_repair_fingerprint_normalizes_only_delivery_metadata(self):
    instance = {"INSTANCE_ID": "i", "ENV": {"COUNT": 1}, "BUILD_AND_RUN_COMMANDS": ["a", "b"]}
    config = {"PLUGINS": [{"INSTANCES": [instance]}], "NAME": "app", "OWNER": "owner"}
    expected = module.repair_config_fingerprint(config)
    decorated = deepcopy(config)
    decorated.update(TIME=1, SESSION_ID="s", LAST_UPDATE_TIME=2, plugins=[], INITIATOR_ID="first",
                     INITIATOR_ADDR="before", MODIFIED_BY_ID="next", MODIFIED_BY_ADDR="after", VALIDATED=True,
                     DEEPLOY_OPERATION=None)
    decorated["PLUGINS"][0]["INSTANCES"][0].update(CHAINSTORE_RESPONSE_KEY="callback", DEEPLOY_RUNTIME_ARTIFACTS=None)
    self.assertEqual(module.repair_config_fingerprint(decorated), expected)
    self.assertIn("CHAINSTORE_RESPONSE_KEY", decorated["PLUGINS"][0]["INSTANCES"][0])
    for changed in ({"BUILD_AND_RUN_COMMANDS": ["b", "a"]}, {"ENV": {"COUNT": "1"}},
                    {"ENV": {"COUNT": True}}, {"DEEPLOY_RUNTIME_ARTIFACTS": {}}):
      with patch.dict(instance, changed):
        self.assertNotEqual(module.repair_config_fingerprint(config), expected)
    self.assertNotEqual(module.repair_config_fingerprint(dict(config, DEEPLOY_OPERATION={})), expected)

  def publish(self, succeed=True):
    """
    Run one publication pass with a setter that confirms every write, or leaves every write unconfirmed.

    Parameters
    ----------
    succeed : bool, optional
      True (the default): the setter stores and confirms. False: the write is not confirmed by the peers.

    Returns
    -------
    dict
      The record of the last write the pass made.
    """
    def setter(key, value, **kwargs):
      """
      Record a write and answer as the chain-store setter does: the bool, and the outcome word for the caller.

      Parameters
      ----------
      key : str
        The storage key.
      value : dict
        The signed record.
      **kwargs
        The write's options; ``result_info`` receives the outcome word.

      Returns
      -------
      bool
        Whether the write was stored and confirmed.

      Examples
      --------
      >>> setter(key, report, result_info={})
      True
      """
      # Every write of the test, with its options.
      self.writes.append((key, deepcopy(value), kwargs))
      if "result_info" in kwargs:
        # The real setter reports an outcome when the caller requests one; targeted repair only uses the boolean.
        kwargs["result_info"]["outcome"] = "changed" if succeed else "unconfirmed"
      # The bool the real setter returns.
      return succeed
    # One pass of the reporter, with a signer that stamps the record.
    self.lifecycle.publish(setter, lambda record, **kwargs: record.update(EE_SENDER="node", EE_SIGN="signature"))
    # The last record written.
    return self.writes[-1][1]

  def test_rejects_spoofed_coordinator_and_unvalidated_commands(self):
    for sender, valid in (("attacker", True), ("oracle", False)):
      with self.assertRaises(ValueError):
        self.lifecycle.admit(config(), sender, valid)
    self.assertFalse(os.path.exists(self.path))

  def test_abort_survives_restart_and_rejects_late_start(self):
    self.lifecycle.admit(config("stop"), "oracle", True)
    restored = DeploymentLifecycle(self.path, "node")
    self.assertFalse(restored.admit(config(), "oracle", True))
    self.assertFalse(restored.allowed(config()))

  def test_restore_is_a_distinct_attempt(self):
    self.lifecycle.admit(config("stop"), "oracle", True)
    self.assertTrue(self.lifecycle.admit(config(attempt="restore", revision=1), "oracle", True))
    self.assertTrue(self.lifecycle.allowed(config(attempt="restore", revision=1)))
    self.assertFalse(self.lifecycle.allowed(config()))

  def test_reports_load_failure_without_constructed_plugin(self):
    self.lifecycle.admit(config(), "oracle", True)
    self.lifecycle.fail(config(), "WORKER", "worker", "PLUGIN_LOAD_FAILED")
    observed = self.publish()
    self.assertEqual(observed["status"], "failed")
    self.assertEqual(observed["instances"][0]["error"]["code"], "PLUGIN_LOAD_FAILED")
    self.assertNotIn("peers", observed)
    self.assertNotIn("possibly_started", observed["instances"][0])
    self.assertEqual(observed["EE_SIGN"], "signature")

  def test_ready_requires_every_instance(self):
    pipeline = config()
    pipeline["PLUGINS"][0]["INSTANCES"].append({"INSTANCE_ID": "other"})
    self.lifecycle.admit(pipeline, "oracle", True)
    self.lifecycle.attach(pipeline, "WORKER", "worker", plugin(ready=True))
    self.assertEqual(self.publish()["status"], "starting")
    self.lifecycle.attach(pipeline, "WORKER", "other", plugin(ready=True))
    self.assertEqual(self.publish()["status"], "ready")

  def test_external_workload_needs_positive_shutdown_evidence(self):
    self.lifecycle.admit(config(), "oracle", True)
    instance = plugin(alive=False, external=True)
    self.lifecycle.attach(config(), "WORKER", "worker", instance)
    self.lifecycle.admit(config("stop"), "oracle", True)
    self.assertEqual(self.publish()["status"], "stopping")
    self.assertIs(self.lifecycle.plugins[("operation|deploy", "WORKER", "worker")], instance)
    instance._deployment_stopped = True
    self.assertEqual(self.publish()["status"], "stopped")

  def test_repair_retirement_is_hidden_until_durable(self):
    legacy = config(revision=0)
    legacy.pop("DEEPLOY_OPERATION")
    legacy["DEEPLOY_SPECS"] = {"job_id": 31}
    retirement = config("retire", "retire", 0)["DEEPLOY_OPERATION"]
    retirement.update(job_id=31)
    evidence = dict(parent_operation_id="parent", challenge="a"*32, order_digest="b"*64,
                    config_seal_hash="c"*64, git_commit="d"*40, job_id=31)
    cleanup = []
    self.lifecycle._cleanup = lambda *args: cleanup.append(args) or True
    for target, method in ((module.os, "replace"), (self.lifecycle, "_sync_journal_directory")):
      with self.subTest(failing=method):
        with patch.object(target, method, side_effect=OSError("fixture journal failure")):
          with self.assertRaises(OSError):
            self.lifecycle.retire("app", legacy, retirement, "oracle", True, repair_evidence=evidence)
        self.assertTrue(self.lifecycle.allowed(legacy))
        self.assertEqual(self.lifecycle.records, {})
        self.lifecycle.publish(lambda *args, **kwargs: self.fail("Undurable STOP"), lambda *a, **kw: None)
        self.assertEqual(cleanup, [])
        self.assertEqual(Path(self.path).exists(), method == "_sync_journal_directory")

    # A restart seeing the replaced file must sync its directory before that snapshot can act.
    with patch.object(DeploymentLifecycle, "_sync_journal_directory", side_effect=OSError("still unavailable")):
      with self.assertRaises(OSError):
        DeploymentLifecycle(self.path, "node")
    self.lifecycle = DeploymentLifecycle(self.path, "node")
    self.assertFalse(self.lifecycle.allowed(legacy))
    self.lifecycle._cleanup = lambda *args: cleanup.append(args) or True
    self.assertEqual(self.publish()["status"], "stopped")
    self.assertEqual(len(cleanup), 1)

  def test_repair_retirement_can_retry_after_a_failed_replace(self):
    legacy = config(revision=0)
    legacy.pop("DEEPLOY_OPERATION")
    legacy["DEEPLOY_SPECS"] = {"job_id": 31}
    retire = config("retire", "retire", 0)["DEEPLOY_OPERATION"]
    retire.update(job_id=31)
    evidence = dict(parent_operation_id="parent", challenge="a"*32, order_digest="b"*64,
                    config_seal_hash="c"*64, git_commit="d"*40, job_id=31)
    with patch.object(module.os, "replace", side_effect=OSError("fixture rename failure")):
      with self.assertRaises(OSError):
        self.lifecycle.retire("app", legacy, retire, "oracle", True, repair_evidence=evidence)
    self.assertTrue(self.lifecycle.retire("app", legacy, retire, "oracle", True, repair_evidence=evidence))
    self.assertFalse(self.lifecycle.allowed(legacy))
    self.lifecycle._cleanup = lambda *args: True
    self.assertEqual(self.publish()["status"], "stopped")

  def test_stopped_retirements_release_all_plugin_aliases_but_keep_evidence(self):
    class Runtime:
      pass
    references = []
    for revision in range(24):
      previous = config(revision=revision)
      previous["DEEPLOY_OPERATION"]["operation_id"] = "previous-" + str(revision)
      previous["DEEPLOY_SPECS"] = {"job_id": 7, "revision": revision}
      self.lifecycle.admit(previous, "oracle", True)
      runtime = Runtime()
      runtime.__dict__.update(vars(plugin(alive=False, stopped=True)))
      runtime.get_deployment_runtime_artifacts = lambda: {"image_id": "sha256:" + "a" * 64}
      references.append(weakref.ref(runtime))
      self.lifecycle.attach(previous, "WORKER", "worker", runtime)
      retirement = config("retire", "retire", revision)
      retirement["DEEPLOY_OPERATION"].update(
        operation_id="retire-" + str(revision), job_id=7,
        expected_previous_operation_id=previous["DEEPLOY_OPERATION"]["operation_id"],
      )
      self.lifecycle.retire("app", previous, retirement["DEEPLOY_OPERATION"], "oracle", True)
      self.lifecycle.attach(retirement, "WORKER", "worker", runtime)
      if revision == 0:
        with patch.object(self.lifecycle, "_save", side_effect=OSError("disk unavailable")):
          with self.assertRaises(OSError):
            self.publish()
        self.assertEqual(len(self.lifecycle.plugins), 2)
        self.assertEqual(self.writes, [])
      self.assertEqual(self.publish()["status"], "stopped")
      del runtime
    gc.collect()
    self.assertTrue(all(reference() is None for reference in references))
    self.assertEqual(self.lifecycle.plugins, {})
    self.lifecycle._cleanup = lambda *args: self.fail("Confirmed stop must not be reaped again")
    self.lifecycle.publish(lambda *args, **kwargs: True, lambda *args, **kwargs: None)
    restored = DeploymentLifecycle(self.path, "node")
    for revision in range(24):
      record = restored.records["retire-" + str(revision) + "|retire"]
      self.assertTrue(record["aborted"])
      self.assertEqual(record["status"], "stopped")
      self.assertEqual(record["instances"][0]["runtime_artifacts"]["image_id"], "sha256:" + "a" * 64)

  def test_throwing_cleanup_does_not_withhold_another_jobs_observations(self):
    healthy = config()
    self.lifecycle.admit(healthy, "oracle", True)
    self.lifecycle.attach(healthy, "WORKER", "worker", plugin(ready=True))
    broken = config()
    broken["NAME"] = "broken"
    broken["DEEPLOY_OPERATION"]["operation_id"] = "broken"
    self.lifecycle.admit(broken, "oracle", True)
    self.lifecycle.attach(broken, "WORKER", "worker", None)
    broken["DEEPLOY_OPERATION"]["action"] = "stop"
    self.lifecycle.admit(broken, "oracle", True)
    def cleanup(*args):
      raise RuntimeError("Docker client unavailable")
    self.lifecycle._cleanup = cleanup
    for now in (100, 111):
      with patch.object(module.time, "time", return_value=now):
        with self.assertRaisesRegex(RuntimeError, "workload observation failed"):
          self.publish()
    # One confirmed ready snapshot (current + slot key); it is not refreshed.
    self.assertEqual(len(self.writes), 2)
    self.assertTrue(all(value["app_id"] == "app" and value["status"] == "ready"
                        for _, value, _ in self.writes))
    self.assertEqual(self.lifecycle.records["broken|deploy"]["status"], "stopping")

  def test_successful_reaper_releases_business_manager_restore_barrier(self):
    import ast
    self.lifecycle.admit(config(), "oracle", True)
    instance = plugin(alive=False, external=True)
    instance._deployment_operation_key = "operation|deploy"
    instance.stop_thread = lambda: None
    self.lifecycle.attach(config(), "WORKER", "worker", instance)
    self.lifecycle.admit(config("stop"), "oracle", True)
    source = Path(__file__).resolve().parents[1] / "business_manager.py"
    tree = ast.parse(source.read_text())
    manager_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BusinessManager")
    method = next(node for node in manager_class.body if isinstance(node, ast.FunctionDef) and node.name == "_stop_plugin")
    namespace = {"sleep": lambda _: None, "gc": SimpleNamespace(collect=lambda: None)}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
    manager = SimpleNamespace(_dct_current_instances={"instance": instance}, _dct_stop_timings={}, _run_on_threads=True)
    stop = namespace["_stop_plugin"]
    self.assertFalse(stop(manager, "instance", require_confirmation=True))
    self.lifecycle._cleanup = lambda *args: True
    self.assertEqual(self.publish()["status"], "stopped")
    self.assertTrue(stop(manager, "instance", require_confirmation=True))
    self.assertEqual(manager._dct_current_instances, {})

  def test_live_thread_is_not_stopped_even_after_workload_ack(self):
    self.lifecycle.admit(config(), "oracle", True)
    self.lifecycle.attach(config(), "WORKER", "worker", plugin(alive=True, stopped=True))
    self.lifecycle.admit(config("stop"), "oracle", True)
    self.assertEqual(self.publish()["status"], "stopping")

  def test_never_started_instance_can_confirm_abort(self):
    self.lifecycle.admit(config(), "oracle", True)
    self.lifecycle.admit(config("stop"), "oracle", True)
    self.assertEqual(self.publish()["status"], "stopped")

  def test_restart_cannot_claim_orphan_workload_stopped(self):
    self.lifecycle.admit(config(), "oracle", True)
    self.lifecycle.attach(config(), "WORKER", "worker", plugin(ready=True))
    self.lifecycle.admit(config("stop"), "oracle", True)
    self.lifecycle = DeploymentLifecycle(self.path, "node")
    self.assertEqual(self.publish()["status"], "stopping")

  def restart_process(self):
    with patch.object(module, "PROCESS_ID", "next-process", create=True):
      self.lifecycle = DeploymentLifecycle(self.path, "node")

  def test_native_stop_completes_after_process_exit(self):
    self.lifecycle.admit(config(), "oracle", True)
    self.lifecycle.attach(config(), "WORKER", "worker", plugin(ready=True))
    self.lifecycle.admit(config("stop"), "oracle", True)
    self.restart_process()
    observed = self.publish()
    self.assertEqual(observed["status"], "stopped")
    for private in ("process_id", "process_bound", "possibly_started"):
      self.assertNotIn(private, observed["instances"][0])
    self.assertFalse(self.lifecycle.admit(config(), "oracle", True))

  def test_native_constructor_failure_requires_process_exit(self):
    self.lifecycle.admit(config(), "oracle", True)
    native_class = type("NativePlugin", (), {})
    self.lifecycle.attach(config(), "WORKER", "worker", None, plugin_class=native_class)
    self.lifecycle.fail(config(), "WORKER", "worker", "PLUGIN_INIT_FAILED")
    self.lifecycle.admit(config("stop"), "oracle", True)
    self.assertEqual(self.publish()["status"], "stopping")
    self.lifecycle = DeploymentLifecycle(self.path, "node")
    self.assertEqual(self.publish()["status"], "stopping")
    self.restart_process()
    self.assertEqual(self.publish()["status"], "stopped")

  def test_process_exit_does_not_confirm_external_or_unknown_workloads(self):
    for external in (True, None):
      with self.subTest(external=external):
        self.path = os.path.join(self.directory.name, str(external) + ".json")
        self.lifecycle = DeploymentLifecycle(self.path, "node")
        self.lifecycle.admit(config(), "oracle", True)
        runtime = plugin(external=True) if external else None
        self.lifecycle.attach(config(), "WORKER", "worker", runtime)
        self.lifecycle.admit(config("stop"), "oracle", True)
        self.restart_process()
        self.assertEqual(self.publish()["status"], "stopping")
        self.lifecycle._cleanup = lambda *args: True
        self.assertEqual(self.publish()["status"], "stopped")

  def test_retirement_preserves_native_process_exit_evidence(self):
    previous = config(revision=1)
    previous["DEEPLOY_SPECS"] = {"job_id": 7, "revision": 1}
    self.lifecycle.admit(previous, "oracle", True)
    self.lifecycle.attach(previous, "WORKER", "worker", plugin(ready=True))
    self.restart_process()
    operation = config(action="retire", attempt="retire", revision=1)["DEEPLOY_OPERATION"]
    operation.update(job_id=7, expected_previous_operation_id="operation")
    self.assertTrue(self.lifecycle.retire("app", previous, operation, "oracle", True))
    self.assertEqual(self.publish()["status"], "stopped")

  def test_publication_retries_and_sequence_survives_restart(self):
    self.lifecycle.admit(config(), "oracle", True)
    first = self.publish(succeed=False)
    self.lifecycle = DeploymentLifecycle(self.path, "node")
    second = self.publish()
    self.assertGreater(second["sequence"], first["sequence"])
    # The unconfirmed recent-slot write skips the current-snapshot write.
    self.assertEqual(len(self.writes), 3)
    self.assertNotEqual(self.writes[0][0], self.writes[1][0])

  def test_publication_keys_decode_without_restoring_base64_padding(self):
    for attempt in ("deploy", "restore", "retire"):
      for operation_id in ("operation", "operation1", "operation12"):
        pipeline = config(action="retire" if attempt == "retire" else "start", attempt=attempt)
        pipeline["DEEPLOY_OPERATION"]["operation_id"] = operation_id
        self.lifecycle.admit(pipeline, "oracle", True)
        self.publish()
    for key, record, _ in self.writes:
      with self.subTest(key=key):
        # Match the production CStore decoder; the store test harness repairs padding.
        field = base64.urlsafe_b64decode(key.split(":", 2)[2]).decode()
        expected = record["operation_id"] + "|" + record["attempt"] + "|node"
        self.assertIn(field, (expected, expected + "|" + str(record["sequence"] % 8)))

  def test_abort_is_published_immediately_after_ready(self):
    self.lifecycle.admit(config(), "oracle", True)
    self.lifecycle.attach(config(), "WORKER", "worker", plugin(ready=True))
    ready = self.publish()
    self.assertEqual(ready["status"], "ready")
    self.lifecycle.admit(config("stop"), "oracle", True)
    stopping = self.publish()
    self.assertEqual(stopping["status"], "stopping")
    self.assertGreater(stopping["sequence"], ready["sequence"])

  def test_unchanged_observations_are_not_republished(self):
    self.lifecycle.admit(config(), "oracle", True)
    self.lifecycle.attach(config(), "WORKER", "worker", plugin())
    self.assertEqual(self.publish()["status"], "starting")
    written = len(self.writes)
    with patch.object(module.time, "time", return_value=module.time.time() + 3600):
      self.lifecycle.publish(lambda *args, **kwargs: self.fail("unchanged snapshot republished"),
                             lambda *args, **kwargs: None)
    self.assertEqual(len(self.writes), written)

  def test_confirmed_pinned_ready_ends_reporting_across_restart(self):
    pipeline = config()
    group = pipeline["PLUGINS"][0]
    group.update(SIGNATURE="WORKER_APP_RUNNER")
    group["INSTANCES"][0].update(PINNED_VERSION=True, VCS_DATA={"COMMIT": "a" * 40})
    runtime = plugin(ready=True)
    self.lifecycle.admit(pipeline, "oracle", True)
    self.lifecycle.attach(pipeline, group["SIGNATURE"], "worker", runtime)
    ready = self.publish()
    self.assertEqual((ready["status"], ready["instances"][0]["source_mode"]), ("ready", "pinned"))
    self.assertTrue(all(kwargs["timeout"] == module.PUBLISH_TIMEOUT for _, _, kwargs in self.writes))
    self.assertNotIn("ready_reported", self.writes[-1][1])
    silent = lambda *args, **kwargs: self.fail("ready attempt reported again")
    runtime.thread.is_alive = lambda: False
    self.lifecycle.publish(silent, lambda *args, **kwargs: None)
    self.lifecycle = DeploymentLifecycle(self.path, "node")
    self.lifecycle.publish(silent, lambda *args, **kwargs: None)

  def test_existing_following_worker_reports_recovery_and_each_new_commit(self):
    pipeline = config()
    pipeline["PLUGINS"][0]["SIGNATURE"] = "WORKER_APP_RUNNER"
    self.lifecycle.admit(pipeline, "oracle", True)
    runtime = plugin()
    # On an upgrade the old journal can already have a confirmed ready observation.
    self.lifecycle.records["operation|deploy"]["ready_reported"] = True
    del self.lifecycle.records["operation|deploy"]["instances"][0]["source_mode"]
    runtime._deployment_source_mode = "following"
    runtime._deployment_error_code = "WORKLOAD_START_FAILED"
    artifacts = {"git_commit": "a" * 40}
    runtime.get_deployment_runtime_artifacts = lambda: artifacts.copy()
    self.lifecycle.attach(pipeline, "WORKER_APP_RUNNER", "worker", runtime)
    failed = self.publish()
    self.assertEqual((failed["status"], failed["instances"][0]["source_mode"]), ("failed", "following"))
    self.assertEqual(failed["instances"][0]["runtime_artifacts"], artifacts)
    runtime._deployment_error_code = None
    self.assertEqual(self.publish()["status"], "starting")
    runtime.is_plugin_ready = lambda: True
    runtime._deployment_source_error = "SOURCE_UNREACHABLE"
    ready = self.publish()
    self.assertEqual((ready["status"], ready["instances"][0]["error"]), ("ready", None))
    self.assertEqual(ready["instances"][0]["source_error"], "SOURCE_UNREACHABLE")
    runtime._deployment_source_error = None
    artifacts["git_commit"] = "b" * 40
    changed = self.publish()
    self.assertGreater(changed["sequence"], ready["sequence"])
    self.assertEqual(changed["instances"][0]["runtime_artifacts"], artifacts)
    self.assertNotIn("source_error", changed["instances"][0])
    self.lifecycle.publish(lambda *args, **kwargs: self.fail("unchanged report"), lambda *args, **kwargs: None)
    self.lifecycle.fail(pipeline, code="PLUGIN_EXECUTION_FAILED")
    self.assertEqual(self.publish()["status"], "failed")

  def test_following_report_replays_exact_current_field_to_missing_or_restarted_peer(self):
    pipeline = config()
    pipeline["PLUGINS"][0]["SIGNATURE"] = "WORKER_APP_RUNNER"
    self.lifecycle.admit(pipeline, "oracle", True)
    runtime = plugin(ready=True)
    self.lifecycle.attach(pipeline, "WORKER_APP_RUNNER", "worker", runtime)
    sessions = {"oracle": (True, 100), "other": (True, 200)}
    self.lifecycle._peer_session = sessions.__getitem__
    self.lifecycle._confirmed_peers = lambda key: ["other"]  # The bulk write succeeded without the coordinator.
    with patch.object(module.time, "monotonic", return_value=10) as clock:
      self.publish()
      current_field, signed_record, _ = self.writes[-1]
      initial_count = len(self.writes)
      self.publish()
      self.assertEqual(len(self.writes), initial_count)
      clock.return_value += module.PUBLISH_TIMEOUT
      self.publish()
      self.assertEqual(self.writes[-1][:2], (current_field, signed_record))
      self.assertEqual(self.writes[-1][2]["peers"], ["oracle"])
      self.assertEqual(len(self.writes), initial_count + 1)
      # Reconnects and fast process restarts each repair the confirmed copy exactly once.
      for interrupted, returned in (((False, None), (True, 210)), ((True, 250), (True, 1))):
        count = len(self.writes)
        sessions["other"] = interrupted
        self.publish()
        self.assertEqual(len(self.writes), count)
        sessions["other"] = returned
        self.publish()
        self.assertEqual(len(self.writes), count + 1)
        self.assertEqual(self.writes[-1][:2], (current_field, signed_record))
        self.assertEqual(self.writes[-1][2]["peers"], ["other"])
        self.publish()
        self.assertEqual(len(self.writes), count + 1)
      # Retiring an attempt discards its pending deliveries and never replays an obsolete ready snapshot.
      self.lifecycle.admit(config("stop"), "oracle", True)
      self.publish()
      self.assertFalse(self.lifecycle._source_delivery)

  def test_ack_after_quorum_suppresses_redundant_source_replay(self):
    pipeline = config()
    pipeline["PLUGINS"][0]["SIGNATURE"] = "CONTAINER_APP_RUNNER"
    self.lifecycle.admit(pipeline, "oracle", True)
    self.lifecycle.attach(pipeline, "CONTAINER_APP_RUNNER", "worker", plugin(ready=True))
    confirmations = ["other"]
    self.lifecycle._confirmed_peers = lambda key: confirmations
    self.lifecycle._peer_session = lambda peer: (True, 10)
    with patch.object(module.time, "monotonic", return_value=10) as clock:
      self.publish()
      writes = len(self.writes)
      confirmations.append("oracle")
      clock.return_value += module.PUBLISH_TIMEOUT
      self.publish()
      self.assertEqual(len(self.writes), writes)

  def test_replay_budget_is_per_peer_and_resets_when_peer_returns(self):
    pipeline = config()
    pipeline["PLUGINS"][0]["SIGNATURE"] = "CONTAINER_APP_RUNNER"
    self.lifecycle.admit(pipeline, "oracle", True)
    self.lifecycle.attach(pipeline, "CONTAINER_APP_RUNNER", "worker", plugin(ready=True))
    sessions = {"oracle": (True, 100), "other": (True, 200)}
    self.lifecycle._peer_session = sessions.__getitem__
    self.lifecycle._confirmed_peers = lambda key: ["other"]
    with patch.object(module.time, "monotonic", return_value=10) as clock:
      self.publish()
      current_field, signed_record, _ = self.writes[-1]
      def peer_session(peer):
        if peer == "other":
          raise RuntimeError("NetMon lookup failed")
        return sessions[peer]
      self.lifecycle._peer_session = peer_session
      # A failing lookup for "other" leaves "oracle" eligible for its bounded retries.
      for _ in range(module.SOURCE_REPLAY_LIMIT + 2):
        clock.return_value += module.SOURCE_REPLAY_INTERVAL
        self.publish(succeed=False)
      attempts = self.writes[2:]
      self.assertEqual(len(attempts), module.SOURCE_REPLAY_LIMIT)
      self.assertTrue(all((key, value) == (current_field, signed_record) for key, value, _ in attempts))
      self.assertTrue(all(call["peers"] == ["oracle"] and call["require_target_ack"] for _, _, call in attempts))
      sessions["oracle"] = (True, 1)  # Restart without an intervening offline observation.
      self.publish()
      self.assertEqual(len(self.writes[2:]), module.SOURCE_REPLAY_LIMIT + 1)

  def test_repair_backoff_preserves_live_report_priority_and_replaces_a_superseded_version(self):
    pipeline = config()
    pipeline["PLUGINS"][0]["SIGNATURE"] = "CONTAINER_APP_RUNNER"
    self.lifecycle.admit(pipeline, "oracle", True)
    runtime = plugin(ready=True)
    self.lifecycle.attach(pipeline, "CONTAINER_APP_RUNNER", "worker", runtime)
    self.lifecycle._peer_session = lambda peer: (True, 5)
    self.lifecycle._confirmed_peers = lambda key: []
    with patch.object(module.time, "monotonic", return_value=10) as clock:
      self.publish()
      clock.return_value += module.PUBLISH_TIMEOUT
      def failing_setter(key, value, **kwargs):
        self.writes.append((key, value, kwargs))
        raise RuntimeError("CStore repair failed")
      with self.assertRaisesRegex(RuntimeError, "CStore repair failed"):
        self.lifecycle.publish(failing_setter, lambda *args, **kwargs: None)
      first_peer = self.writes[-1][2]["peers"]
      count = len(self.writes)
      self.publish()
      self.assertEqual(len(self.writes), count + 1)
      self.assertNotEqual(self.writes[-1][2]["peers"], first_peer)
      runtime._deployment_error_code = "WORKLOAD_START_FAILED"
      updated = self.publish()
      self.assertEqual(updated["status"], "failed")
      clock.return_value += 35
      self.publish()
      self.assertEqual(self.writes[-1][1], updated)  # The old ready record is never resent.

  def test_mixed_pipeline_preserves_bound_report_while_following_worker_changes(self):
    pipeline = config()
    group = pipeline["PLUGINS"][0]
    group.update(SIGNATURE="WORKER_APP_RUNNER")
    group["INSTANCES"].append({"INSTANCE_ID": "fixed", "PINNED_VERSION": True, "VCS_DATA": {"COMMIT": "a" * 40}})
    self.lifecycle.admit(pipeline, "oracle", True)
    following, fixed = plugin(ready=True), plugin(ready=True)
    for name, runtime in (("worker", following), ("fixed", fixed)):
      self.lifecycle.attach(pipeline, group["SIGNATURE"], name, runtime)
    first = self.publish()
    fixed.thread.is_alive = lambda: False
    following._deployment_error_code = "IMAGE_PULL_FAILED"
    failed = self.publish()
    self.assertEqual(first["status"], "ready")
    self.assertEqual([item["status"] for item in failed["instances"]], ["failed", "ready"])
    following._deployment_error_code = None
    self.assertEqual(self.publish()["status"], "ready")

  def test_new_runner_supersedes_a_previous_confirmed_threads_exit(self):
    pipeline = config()
    pipeline["PLUGINS"][0]["SIGNATURE"] = "CONTAINER_APP_RUNNER"
    self.lifecycle.admit(pipeline, "oracle", True)
    old = plugin(ready=True)
    self.lifecycle.attach(pipeline, "CONTAINER_APP_RUNNER", "worker", old)
    self.assertEqual(self.publish()["status"], "ready")
    old.thread.is_alive = lambda: False
    self.assertEqual(self.publish()["instances"][0]["error"]["code"], "PLUGIN_EXECUTION_FAILED")
    self.lifecycle = DeploymentLifecycle(self.path, "node", source_modes=True)
    new = plugin()
    new._deployment_source_mode = "following"
    new.thread = SimpleNamespace(is_alive=lambda: False, ident=None)  # start_thread has not started it yet.
    self.lifecycle.attach(pipeline, "CONTAINER_APP_RUNNER", "worker", new)
    self.assertEqual(self.publish()["status"], "starting")
    new.thread = SimpleNamespace(is_alive=lambda: True, ident=123)
    new.is_plugin_ready = lambda: True
    self.assertEqual(self.publish()["status"], "ready")

  def test_only_capable_generic_runners_advertise_following(self):
    pipeline = config()
    group = pipeline["PLUGINS"][0]
    group["SIGNATURE"] = "CONTAINER_APP_RUNNER"
    self.lifecycle.source_modes = False  # A new Core can be installed under an older edge runtime.
    self.lifecycle.admit(pipeline, "oracle", True)
    self.assertNotIn("source_mode", self.publish()["instances"][0])
    for job_type, expected in (("generic", "following"), ("service", "service")):
      pipeline["DEEPLOY_OPERATION"]["operation_id"] = job_type
      pipeline["DEEPLOY_SPECS"] = {"job_app_type": job_type}
      self.lifecycle.source_modes = True
      self.lifecycle.admit(pipeline, "oracle", True)
      self.assertEqual(self.publish()["instances"][0]["source_mode"], expected)
    self.assertIsNone(module.deployment_source_mode("NATIVE_APP", {}, {}))

  def test_close_halts_observation_even_when_write_outlasts_join(self):
    from threading import Event, Thread
    pipeline = config()
    pipeline["PLUGINS"][0]["SIGNATURE"] = "CONTAINER_APP_RUNNER"
    self.lifecycle.admit(pipeline, "oracle", True)
    runtime = plugin(ready=True)
    self.lifecycle.attach(pipeline, "CONTAINER_APP_RUNNER", "worker", runtime)
    write_started, release_write = Event(), Event()
    self.addCleanup(release_write.set)
    def setter(key, record, **kwargs):
      self.writes.append((key, record, kwargs))
      write_started.set()
      return release_write.wait(2)
    reporter = Thread(target=self.lifecycle.publish, args=(setter, lambda *args, **kwargs: None), daemon=True)
    self.lifecycle._thread = reporter
    reporter.start()
    self.assertTrue(write_started.wait(1))
    with patch.object(reporter, "join") as timeout:
      self.lifecycle.close()  # Simulate the join returning while CStore is still waiting.
      timeout.assert_called_once_with(timeout=3)
    runtime.thread.is_alive = lambda: False
    release_write.set()
    reporter.join(1)
    self.assertFalse(reporter.is_alive())
    self.lifecycle.publish(setter, lambda *args, **kwargs: None)
    self.assertEqual(len(self.writes), 1)
    self.assertEqual(self.lifecycle.records["operation|deploy"]["status"], "ready")

  def test_close_does_not_wait_on_admission_lock_held_by_orphan_cleanup(self):
    from threading import Event, Thread
    pipeline = config()
    pipeline["PLUGINS"][0]["INSTANCES"].append({"INSTANCE_ID": "second"})
    self.lifecycle.admit(pipeline, "oracle", True)
    for instance in ("worker", "second"):
      self.lifecycle.attach(pipeline, "WORKER", instance, plugin(ready=True))
    stopped = deepcopy(pipeline)
    stopped["DEEPLOY_OPERATION"]["action"] = "stop"
    self.lifecycle.admit(stopped, "oracle", True)
    self.lifecycle = DeploymentLifecycle(self.path, "node")  # The old plugin objects are unavailable.
    cleanup_started, release_cleanup, closed = Event(), Event(), Event()
    self.addCleanup(release_cleanup.set)
    calls = []
    def cleanup(app, signature, instance):
      calls.append(instance)
      cleanup_started.set()
      return release_cleanup.wait(5)
    self.lifecycle._cleanup = cleanup
    def setter(*args, **kwargs):
      self.writes.append(args)
      return True
    reporter = Thread(target=self.lifecycle.publish, args=(setter, lambda *args, **kwargs: None), daemon=True)
    self.lifecycle._thread = reporter
    reporter.start()
    self.assertTrue(cleanup_started.wait(2))
    def close():
      self.lifecycle.close()
      closed.set()
    with patch.object(reporter, "join") as timeout:
      closer = Thread(target=close, daemon=True)
      closer.start()
      # Simulate the join expiring with Docker cleanup still holding admission.
      self.assertTrue(closed.wait(2))
      timeout.assert_called_once_with(timeout=3)
    release_cleanup.set()
    reporter.join(2)
    self.assertFalse(reporter.is_alive())
    self.assertEqual(calls, ["worker"])
    self.assertFalse(self.writes)

  def test_unconfirmed_ready_is_retried_until_confirmed(self):
    self.lifecycle.admit(config(), "oracle", True)
    self.lifecycle.attach(config(), "WORKER", "worker", plugin(ready=True))
    first = self.publish(succeed=False)
    second = self.publish()
    self.assertEqual((first["status"], second["status"]), ("ready", "ready"))
    self.assertGreater(second["sequence"], first["sequence"])
    written = len(self.writes)
    self.lifecycle.publish(lambda *args, **kwargs: self.fail("confirmed ready reported again"),
                           lambda *args, **kwargs: None)
    self.assertEqual(len(self.writes), written)

  def test_stop_and_failure_reports_are_written_first(self):
    def pipeline(operation_id, action="start"):
      result = config(action)
      result["NAME"] = operation_id
      result["DEEPLOY_OPERATION"]["operation_id"] = operation_id
      return result
    for operation_id in ("operation", "stopped", "failed"):
      self.lifecycle.admit(pipeline(operation_id), "oracle", True)
    self.lifecycle.admit(pipeline("stopped", "stop"), "oracle", True)
    self.lifecycle.fail(pipeline("failed"))
    self.publish(succeed=False)
    self.assertEqual([record["operation_id"] for _, record, _ in self.writes],
                     ["stopped", "failed", "operation"])

  def test_start_failure_moves_to_ready_once_the_workload_runs(self):
    """
    A start or image failure stays while the plugin holds its code or is not ready; it moves to ready, and is
    published, once the runner reports "starting" and then makes the plugin ready, as a container runner does.
    """
    # The two codes a node leaves; the test names them so a change of that list is seen here.
    for code in ("WORKLOAD_START_FAILED", "IMAGE_PULL_FAILED"):
      # One subtest per code.
      with self.subTest(code=code):
        # A fresh journal per code, so each attempt starts unreported.
        self.path = os.path.join(self.directory.name, code + ".json")
        # The lifecycle that keeps that journal.
        self.lifecycle = DeploymentLifecycle(self.path, "node")
        # The coordinator's start of the attempt.
        self.lifecycle.admit(config(), "oracle", True)
        # The runner reports that the workload could not start.
        runtime = report(plugin(), "failed", code)
        # Its plugin joins the attempt.
        self.lifecycle.attach(config(), "WORKER", "worker", runtime)
        # The failure is published.
        failed = self.publish()
        # As "failed", with the runner's code.
        self.assertEqual((failed["status"], failed["instances"][0]["error"]["code"]), ("failed", code))
        # The writes so far.
        written = len(self.writes)
        # The plugin becomes ready with no new report, so it still holds the code (a build command that failed in
        # a container whose health check passes looks like this).
        runtime.is_plugin_ready = lambda: True
        # Observed again.
        self.publish()
        # Still "failed", and nothing new was written.
        self.assertEqual((len(self.writes), self.lifecycle.records["operation|deploy"]["status"]), (written, "failed"))
        # While the runner retries, a new report clears the code, but the workload is not ready.
        report(runtime, "pulling_image")
        # Observed again.
        self.publish()
        # Still "failed", and nothing new was written.
        self.assertEqual((len(self.writes), self.lifecycle.records["operation|deploy"]["status"]), (written, "failed"))
        # The workload starts: the runner reports "starting", which clears the code ...
        report(runtime, "starting")
        # ... and its health check later makes the plugin ready, without another report.
        runtime.is_plugin_ready = lambda: True
        # Observed again.
        ready = self.publish()
        # The attempt and its instance left "failed", with no error.
        self.assertEqual((ready["status"], ready["instances"][0]["status"], ready["instances"][0]["error"]),
                         ("ready", "ready", None))
        # Published as a newer snapshot.
        self.assertGreater(ready["sequence"], failed["sequence"])

  def test_restart_starts_a_persisted_start_failure_again(self):
    """After a node restart a persisted start failure reports again from "starting", then ready."""
    # The coordinator's start of the attempt.
    self.lifecycle.admit(config(), "oracle", True)
    # The workload could not start before the node restarted.
    self.lifecycle.attach(config(), "WORKER", "worker", report(plugin(), "failed", "WORKLOAD_START_FAILED"))
    # The failure is published.
    failed = self.publish()
    # As "failed".
    self.assertEqual(failed["status"], "failed")
    # The restarted node starts the attempt again, so the persisted failure is no longer its state.
    self.restart_process()
    # Published again after the restart.
    starting = self.publish()
    # As "starting", its instance "initializing" with no error.
    self.assertEqual((starting["status"], starting["instances"][0]["status"], starting["instances"][0]["error"]),
                     ("starting", "initializing", None))
    # As a newer snapshot.
    self.assertGreater(starting["sequence"], failed["sequence"])
    # The new process's plugin brings the workload up: the usual ready report follows.
    self.lifecycle.attach(config(), "WORKER", "worker", report(plugin(), "ready"))
    # Published as ready.
    self.assertEqual(self.publish()["status"], "ready")

  def test_other_failures_stay_failed_beside_a_recovered_start(self):
    """Only a start or image failure is left: any other failure keeps its instance and the attempt failed."""
    # One attempt.
    pipeline = config()
    # Two instances of it.
    pipeline["PLUGINS"][0]["INSTANCES"].append({"INSTANCE_ID": "other"})
    # The coordinator's start of the attempt.
    self.lifecycle.admit(pipeline, "oracle", True)
    # One fails to start.
    started = report(plugin(), "failed", "WORKLOAD_START_FAILED")
    # The other fails its health check, a code that is kept.
    unhealthy = report(plugin(), "failed", "WORKLOAD_UNHEALTHY")
    # The first plugin joins the attempt.
    self.lifecycle.attach(pipeline, "WORKER", "worker", started)
    # The second one too.
    self.lifecycle.attach(pipeline, "WORKER", "other", unhealthy)
    # Both failures are published.
    self.publish()
    # The first workload runs again.
    report(started, "ready")
    # The second one too.
    report(unhealthy, "ready")
    # Observed again.
    observed = self.publish()
    # The start failure is left; the other failure stays.
    self.assertEqual([(item["status"], (item["error"] or {}).get("code")) for item in observed["instances"]],
                     [("ready", None), ("failed", "WORKLOAD_UNHEALTHY")])
    # And keeps the attempt failed.
    self.assertEqual(observed["status"], "failed")
    # The node restarts.
    self.restart_process()
    # A restart does not reset an attempt that holds a kept failure.
    self.assertEqual(self.lifecycle.records["operation|deploy"]["status"], "failed")

  def test_restart_keeps_a_rejected_command_failed(self):
    """A rejected command's record, failed with no failed instance, is published as failed again after a restart."""
    # The live revision 3 of job 7.
    previous = config(revision=3)
    # Its job and revision, which a retirement must name.
    previous["DEEPLOY_SPECS"] = {"job_id": 7, "revision": 3}
    # The coordinator's start of it.
    self.lifecycle.admit(previous, "oracle", True)
    # A retirement of revision 1, not the live one.
    operation = config(action="retire", attempt="retire", revision=1)["DEEPLOY_OPERATION"]
    # Of the same job, after the live operation.
    operation.update(job_id=7, expected_previous_operation_id="operation")
    # Rejected: its record is failed with the error on the record, and none of its instances failed.
    self.assertFalse(self.lifecycle.retire("app", previous, operation, "oracle", True))
    # The rejection is published.
    self.publish()
    # The node restarts.
    self.restart_process()
    # Keep only what is published from now on.
    self.writes = []
    # The restarted node publishes its journal again.
    self.publish()
    # What it published for the retirement.
    observed = [record for _, record, _ in self.writes if record["attempt"] == "retire"]
    # Published again (each snapshot goes to two keys), always as failed with the rejection, never as "starting".
    self.assertEqual({(record["status"], record["error"]["code"]) for record in observed},
                     {("failed", "CONFIGURATION_REJECTED")})

  def test_restart_keeps_a_stopped_attempt_stopping(self):
    """A restart starts a start failure again only for a running attempt; a stopped one keeps its stop."""
    # Two apps.
    stopped, running = config(), config()
    # With their own names and operations.
    running["NAME"] = running["DEEPLOY_OPERATION"]["operation_id"] = "running"
    # Each started, and each external workload failed to start.
    for pipeline in (stopped, running):
      # The coordinator's start of the app.
      self.lifecycle.admit(pipeline, "oracle", True)
      # Its plugin, which reports the failed start, joins the attempt.
      self.lifecycle.attach(pipeline, "WORKER", "worker",
                            report(plugin(external=True), "failed", "WORKLOAD_START_FAILED"))
    # Both failures are published.
    self.publish()
    # The coordinator stops the first one.
    self.lifecycle.admit(config("stop"), "oracle", True)
    # The node restarts before that stop is proven.
    self.restart_process()
    # The journal after the restart.
    records = self.lifecycle.records
    # The stopped attempt keeps its stop and its instance's failure.
    self.assertEqual((records["operation|deploy"]["status"], records["operation|deploy"]["instances"][0]["status"]),
                     ("stopping", "failed"))
    # The running one starts again.
    self.assertEqual(records["running|deploy"]["status"], "starting")
    # Published after the restart.
    self.publish()
    # Nothing the stopped app reports says starting or ready.
    self.assertEqual({record["status"] for _, record, _ in self.writes if record["app_id"] == "app"},
                     {"failed", "stopping"})
    # Its stop is proven now.
    self.lifecycle._cleanup = lambda *args: True
    # Observed again.
    self.publish()
    # It reports stopped.
    self.assertEqual(records["operation|deploy"]["status"], "stopped")

  def test_recent_snapshot_storage_is_bounded(self):
    self.lifecycle.admit(config(), "oracle", True)
    for _ in range(20):
      self.lifecycle._published.clear()
      self.publish()
    self.assertEqual(len({key for key, _, _ in self.writes}), 9)

  def test_restart_reaper_confirms_only_the_active_aborted_attempt(self):
    self.lifecycle.admit(config(), "oracle", True)
    self.lifecycle.attach(config(), "WORKER", "worker", plugin(ready=True))
    self.lifecycle.admit(config("stop"), "oracle", True)
    self.lifecycle = DeploymentLifecycle(self.path, "node")
    calls = []
    self.lifecycle._cleanup = lambda *args: calls.append(args) or True
    self.assertEqual(self.publish()["status"], "stopped")
    self.assertEqual(calls, [("app", "WORKER", "worker")])
    calls.clear()
    self.lifecycle.admit(config(attempt="restore", revision=1), "oracle", True)
    self.publish()
    self.assertEqual(calls, [])

  def test_a_node_reports_where_a_registry_serves_its_image_and_nothing_else(self):
    # The image id names an image only inside one Docker store. The registry reference is what lets a
    # restored revision be pulled again; it passes only as a plain repository and digest.
    image_id, reference = "sha256:" + "a" * 64, "ghcr.io/acme/shop@sha256:" + "d" * 64
    def reported(**artifacts):
      return DeploymentLifecycle._runtime_artifacts(SimpleNamespace(get_deployment_runtime_artifacts=lambda: artifacts))
    self.assertEqual(reported(image_id=image_id, image_ref=reference), {"image_id": image_id, "image_ref": reference})
    # A ":" passes only as the port of the registry.
    with_port = "registry:5000/team/app@sha256:" + "d" * 64
    self.assertEqual(reported(image_id=image_id, image_ref=with_port), {"image_id": image_id, "image_ref": with_port})
    for unsafe in ("ghcr.io/acme/shop:stable", "user:secret@ghcr.io/acme/shop@sha256:" + "d" * 64,
                   "ghcr.io/acme/shop:stable@sha256:" + "d" * 64, "https://ghcr.io/acme/shop@sha256:" + "d" * 64,
                   "ghcr.io/acme/shop@sha256:short", reference + " --privileged", 7, None):
      self.assertEqual(reported(image_id=image_id, image_ref=unsafe, environment={"TOKEN": "x"}), {"image_id": image_id})

  def test_runtime_artifacts_survive_retirement_restart_and_stopped_report(self):
    previous = config(revision=1)
    previous["DEEPLOY_SPECS"] = {"job_id": 7, "revision": 1}
    self.lifecycle.admit(previous, "oracle", True)
    runtime = plugin(ready=True)
    runtime.get_deployment_runtime_artifacts = lambda: {"image_id": "sha256:" + "a" * 64,
                                                       "git_commit": "b" * 40, "secret": "must-not-publish"}
    self.lifecycle.attach(previous, "WORKER", "worker", runtime)
    self.lifecycle = DeploymentLifecycle(self.path, "node")
    operation = config(action="retire", attempt="retire", revision=1)["DEEPLOY_OPERATION"]
    operation.update(job_id=7, expected_previous_operation_id="operation")
    self.lifecycle.retire("app", previous, operation, "oracle", True)
    self.lifecycle._cleanup = lambda *args: True
    report = self.publish()
    self.assertEqual(report["status"], "stopped")
    self.assertEqual(report["instances"][0]["runtime_artifacts"],
                     {"image_id": "sha256:" + "a" * 64, "git_commit": "b" * 40})
    self.assertNotIn("must-not-publish", str(report))

  def test_retire_validates_prior_revision_and_aborts_old_attempt(self):
    previous = config(revision=1)
    previous["DEEPLOY_SPECS"] = {"job_id": 7, "revision": 1}
    self.lifecycle.admit(previous, "oracle", True)
    operation = config(action="retire", attempt="retire", revision=1)["DEEPLOY_OPERATION"]
    operation.update(job_id=7, expected_previous_operation_id="operation")
    self.assertTrue(self.lifecycle.retire("app", previous, operation, "oracle", True))
    self.assertFalse(self.lifecycle.admit(previous, "oracle", True))
    self.assertTrue(self.lifecycle.records["operation|retire"]["instances"][0]["possibly_started"])

  def test_retire_after_config_removal_still_reaps_journaled_workload(self):
    previous = config(revision=1)
    self.lifecycle.admit(previous, "oracle", True)
    self.lifecycle.attach(previous, "WORKER", "worker", plugin(ready=True))
    operation = config(action="retire", attempt="retire", revision=1)["DEEPLOY_OPERATION"]
    operation.update(job_id=7, expected_previous_operation_id="operation")
    self.lifecycle = DeploymentLifecycle(self.path, "node")
    self.assertTrue(self.lifecycle.retire("app", {}, operation, "oracle", True))
    self.assertEqual(self.publish()["status"], "stopping")
    calls = []
    self.lifecycle._cleanup = lambda *args: calls.append(args) or True
    self.assertEqual(self.publish()["status"], "stopped")
    self.assertEqual(calls, [("app", "WORKER", "worker")])

  def test_retire_absent_config_cannot_adopt_newer_journaled_attempt(self):
    self.lifecycle.admit(config(revision=3), "oracle", True)
    operation = config(action="retire", attempt="retire", revision=1)["DEEPLOY_OPERATION"]
    operation.update(expected_previous_operation_id="operation")
    self.assertFalse(self.lifecycle.retire("app", {}, operation, "oracle", True))
    self.assertTrue(self.lifecycle.records["operation|deploy"]["active"])

  def test_rejects_mixed_retirement_action_and_attempt(self):
    for candidate in (config(action="retire"), config(attempt="retire")):
      with self.assertRaises(ValueError):
        self.lifecycle.admit(candidate, "oracle", True)

  def test_retire_mismatch_does_not_take_ownership_or_reap_newer_workload(self):
    previous = config(revision=3)
    previous["DEEPLOY_SPECS"] = {"job_id": 7, "revision": 3}
    self.lifecycle.admit(previous, "oracle", True)
    operation = config(action="retire", attempt="retire", revision=1)["DEEPLOY_OPERATION"]
    operation.update(job_id=7, expected_previous_operation_id="operation")
    self.assertFalse(self.lifecycle.retire("app", previous, operation, "oracle", True))
    self.assertTrue(self.lifecycle.records["operation|deploy"]["active"])
    self.assertFalse(self.lifecycle.records["operation|retire"]["active"])
    self.publish()
    # The rejection is a failure report, so it is written before the live deploy.
    observed = next(record for _, record, _ in self.writes if record["attempt"] == "retire")
    self.assertEqual(observed["status"], "failed")
    self.assertEqual(observed["error"]["code"], "CONFIGURATION_REJECTED")

  def test_admission_propagates_disk_failure(self):
    with patch.object(module.os, "fsync", side_effect=OSError("disk full")):
      with self.assertRaises(OSError):
        self.lifecycle.admit(config(), "oracle", True)

  def test_corrupt_journal_fails_closed(self):
    Path(self.path).write_text("broken")
    with self.assertRaises(json.JSONDecodeError):
      DeploymentLifecycle(self.path, "node")


class ChainStoreAcknowledgmentTest(unittest.TestCase):
  def make_store(self):
    store = _ChainStoreRuntimeHarness("node", default_peers=["peer1", "peer2"])
    store._ChainStoreBasePlugin__ops = collections.deque()
    store.cfg_min_confirmations = 2
    return store

  def test_same_value_retry_does_not_claim_missing_replication(self):
    store = self.make_store()
    self.assertFalse(store._set_value("key", "value", owner="node", timeout=.1, max_retries=0))
    self.assertFalse(store._set_value("key", "value", owner="node", timeout=.1, max_retries=0))
    self.assertEqual(store.storage["key"][store.CS_CONFIRMATIONS], 0)

  def test_confirmations_are_distinct_authorized_transport_peers(self):
    store = self.make_store()
    store._set_value("key", "value", owner="node", timeout=.1, max_retries=0)
    data = {store.CS_KEY: "key", store.CS_VALUE: "value", store.CS_OWNER: "node"}
    for sender in ("attacker", "peer1", "peer1"):
      store._ChainStoreBasePlugin__exec_received_confirm(data, sender=sender)
    self.assertEqual(store.storage["key"][store.CS_CONFIRMATIONS], 1)
    store._ChainStoreBasePlugin__exec_received_confirm(data, sender="peer2")
    self.assertEqual(store.storage["key"][store.CS_CONFIRMATIONS], 2)

  def test_target_replay_requires_the_new_targets_acknowledgement(self):
    store = self.make_store()
    confirm = store._ChainStoreBasePlugin__exec_received_confirm
    data = {store.CS_KEY: "key", store.CS_VALUE: "value", store.CS_OWNER: "node"}
    store._set_value("key", "value", owner="node", timeout=.2, max_retries=0)
    confirm(data, sender="peer2")
    # An old ack for the same value, or an ack from another peer, cannot satisfy this replay.
    def other_peer(seconds):
      store._now += seconds
      confirm(data, sender="peer1")
    store.sleep = other_peer
    kwargs = dict(owner="node", peers=["peer2"], include_default_peers=False, timeout=.2, max_retries=0)
    self.assertFalse(store._set_value("key", "value", require_target_ack=True, **kwargs))
    def target_peer(seconds):
      other_peer(seconds)
      confirm(data, sender="peer2")
    store.sleep = target_peer
    self.assertFalse(store._set_value("key", "value", **kwargs))  # Normal write still uses its configured quorum.
    self.assertTrue(store._set_value("key", "value", require_target_ack=True, **kwargs))

  def test_writes_are_saved_by_the_flush_outside_persistence_lock_and_never_by_process(self):
    # The SerializationManager calls the flush on its own thread every CS_SAVE_INTERVAL seconds.
    store = self.make_store()
    flush = store._ChainStoreBasePlugin__maybe_flush_chain_storage
    lock = store._ChainStoreBasePlugin__persistence_lock
    saves = []
    store.cacheapi_save_pickle = lambda obj, *args, **kwargs: saves.append((lock._is_owned(), sorted(obj)))
    for key in ("key1", "key2"):
      self.assertTrue(store._set_value(key, "value", owner="node", local_sync_storage_op=True))
    store._now += 10 * store.CS_SAVE_INTERVAL
    store.process()
    self.assertEqual(saves, [])  # the plugin thread does not save, however long ago the last save was
    flush()
    self.assertEqual(saves, [(False, ["key1", "key2"])])
    flush()
    self.assertEqual(len(saves), 1)  # nothing changed: nothing written
    self.assertTrue(store._set_value("key3", "value", owner="node", local_sync_storage_op=True))
    flush()
    self.assertEqual(len(saves), 2)
    self.assertIn("key3", saves[-1][1])

  def test_same_value_remote_write_does_not_mark_store_dirty(self):
    store = self.make_store()
    store._set_value("key", "value", owner="node", local_sync_storage_op=True)
    store._ChainStoreBasePlugin__chain_storage_dirty = False
    self.assertTrue(store._set_value("key", "value", owner="node", local_sync_storage_op=True))
    self.assertFalse(store._ChainStoreBasePlugin__chain_storage_dirty)

  def test_close_saves_pending_writes_and_ends_the_periodic_save(self):
    manager = SerializationManager.instance = SerializationManager(log=None)  # the save's thread would first run 5 s after on_init; each store is closed before
    self.addCleanup(setattr, SerializationManager, "instance", None)
    saved = []
    for pending in (False, True):
      store = self.make_store()
      flush = store._ChainStoreBasePlugin__maybe_flush_chain_storage
      store.cacheapi_save_pickle = lambda obj, *args, **kwargs: saved.append(obj)
      manager.register(flush, every=store.CS_SAVE_INTERVAL)
      if pending:
        store._set_value("key", "value", owner="node", local_sync_storage_op=True)
      store.on_close()
      self.assertEqual(len(saved), int(pending))
      self.assertNotIn(flush, manager._saves)  # the closed store is no longer registered with the manager
    self.assertIn("key", saved[0])
    self.assertFalse(store._ChainStoreBasePlugin__chain_storage_dirty)

  def test_close_waits_for_a_save_in_progress_and_writes_again_when_it_failed(self):
    # The manager's thread is inside a save when the plugin closes, and that write then fails.
    from threading import Event, Thread
    manager = SerializationManager.instance = SerializationManager(log=None)
    self.addCleanup(setattr, SerializationManager, "instance", None)
    store = self.make_store()
    flush = store._ChainStoreBasePlugin__maybe_flush_chain_storage
    manager.register(flush, every=store.CS_SAVE_INTERVAL)
    writing, fail_now, attempts = Event(), Event(), []
    def failing_then_fine(obj, *args, **kwargs):
      attempts.append(sorted(obj))
      if len(attempts) == 1:
        writing.set()
        fail_now.wait(5)
        raise OSError("disk full")
    store.cacheapi_save_pickle = failing_then_fine
    store._set_value("key", "value", owner="node", local_sync_storage_op=True)
    manager_save = Thread(target=flush)
    manager_save.start()
    self.assertTrue(writing.wait(5))
    closing = Thread(target=store.on_close)
    closing.start()
    closing.join(0.3)
    self.assertTrue(closing.is_alive())  # on_close waits for the write in progress
    fail_now.set()
    closing.join(5)
    manager_save.join(5)
    self.assertFalse(closing.is_alive())
    self.assertEqual(attempts, [["key"], ["key"]])  # the failed write was made again by on_close
    self.assertFalse(store._ChainStoreBasePlugin__chain_storage_dirty)

  def test_save_writes_a_snapshot_not_the_live_store(self):
    store = self.make_store()
    saved = []
    store.cacheapi_save_pickle = lambda obj, *args, **kwargs: saved.append(obj)
    store._set_value("key", "value", owner="node", local_sync_storage_op=True)
    store._ChainStoreBasePlugin__maybe_flush_chain_storage()
    live = store.storage["key"]
    snapshot = saved[-1]["key"]
    self.assertIsNot(saved[-1], store.storage)
    self.assertIsNot(snapshot, live)
    live["confirmed_peers"] = live.get("confirmed_peers", [])
    live["confirmed_peers"].append("peer1")
    self.assertNotIn("peer1", snapshot.get("confirmed_peers", []))

  def test_failed_save_is_retried_by_the_next_flush(self):
    store = self.make_store()
    flush = store._ChainStoreBasePlugin__maybe_flush_chain_storage
    attempts = []
    def flaky_save(obj, *args, **kwargs):
      attempts.append(obj)
      if len(attempts) == 1:
        raise OSError("disk full")
    store.cacheapi_save_pickle = flaky_save
    self.assertTrue(store._set_value("key", "value", owner="node", local_sync_storage_op=True))
    flush()
    self.assertEqual(len(attempts), 1)
    self.assertTrue(store._ChainStoreBasePlugin__chain_storage_dirty)  # so the manager's next call retries
    flush()
    self.assertEqual(len(attempts), 2)
    self.assertIn("key", attempts[-1])
    self.assertFalse(store._ChainStoreBasePlugin__chain_storage_dirty)

  def test_old_ack_cannot_confirm_a_concurrent_replacement(self):
    from threading import Event, Thread
    store = self.make_store()
    store._set_value("key", "old", owner="node", timeout=.1, max_retries=0)
    entered, release, writer_started, writer_done = Event(), Event(), Event(), Event()
    original = store._ChainStoreBasePlugin__get_key_value
    errors = []
    def paused_read(key):
      value = original(key)
      if not entered.is_set():
        entered.set()
        if not release.wait(2):
          raise RuntimeError("Test release timed out")
      return value
    store._ChainStoreBasePlugin__get_key_value = paused_read
    def acknowledge():
      try:
        store._ChainStoreBasePlugin__exec_received_confirm(
          {store.CS_KEY: "key", store.CS_VALUE: "old", store.CS_OWNER: "node"}, sender="peer1")
      except Exception as error:
        errors.append(error)
    def replace():
      writer_started.set()
      try:
        store._set_value("key", "new", owner="node", timeout=.1, max_retries=0)
      except Exception as error:
        errors.append(error)
      finally:
        writer_done.set()
    acknowledgment = Thread(target=acknowledge)
    acknowledgment.start()
    self.assertTrue(entered.wait(1))
    writer = Thread(target=replace)
    writer.start()
    self.assertTrue(writer_started.wait(1))
    self.assertFalse(writer_done.is_set())
    release.set()
    acknowledgment.join(2)
    writer.join(2)
    self.assertFalse(acknowledgment.is_alive())
    self.assertFalse(writer.is_alive())
    self.assertEqual(errors, [])
    self.assertEqual(store.storage["key"][store.CS_VALUE], "new")
    self.assertEqual(store.storage["key"][store.CS_CONFIRMATIONS], 0)

  def test_wait_does_not_accept_replacement_values_confirmations(self):
    store = self.make_store()
    def replace_during_wait(seconds):
      store._now += seconds
      with store._ChainStoreBasePlugin__persistence_lock:
        store._ChainStoreBasePlugin__set_key_value("key", "other", "node")
      for peer in ("peer1", "peer2"):
        store._ChainStoreBasePlugin__exec_received_confirm(
          {store.CS_KEY: "key", store.CS_VALUE: "other", store.CS_OWNER: "node"}, sender=peer)
    store.sleep = replace_during_wait
    self.assertFalse(store._set_value("key", "desired", owner="node", timeout=.1, max_retries=0))
    self.assertEqual(store.storage["key"][store.CS_VALUE], "other")
    self.assertEqual(store.storage["key"][store.CS_CONFIRMATIONS], 2)


class ProtectedChainStoreTest(unittest.TestCase):
  def store(self, address="registry"):
    store = _ChainStoreRuntimeHarness(address, default_peers=["registry-other"])
    store._ChainStoreBasePlugin__ops = collections.deque()
    store.get_instance_path = lambda: [address, "admin_pipeline", "CHAIN_STORE", "instance"]
    store.bc = SimpleNamespace(
      node_address_to_eth_address=lambda addr: addr,
      get_eth_dauth_oracles=lambda: ["registry", "registry-other"],
      get_eth_oracles=lambda: ["oracle"],
      verify=lambda value, **kwargs: SimpleNamespace(valid=value.get("EE_SIGN") == "valid"),
    )
    return store

  def test_node_key_rotations_keep_the_first_proven_new_key(self):
    store = self.store()
    def verify_node_key_rotation(proof):
      if proof.get("EE_SIGN") != "valid":
        raise ValueError("invalid")
      return proof["node_key_rotation"]["EE_SENDER"], proof["node_key_rotation"]["new_node"]
    store.bc.verify_node_key_rotation = verify_node_key_rotation
    def proof(new_node, old_node="old", nonce="1", sign="valid"):
      return {"node_key_rotation": {"EE_SENDER": old_node, "new_node": new_node}, "nonce": nonce, "EE_SIGN": sign}
    key = _hset_key("NODE_KEY_ROTATIONS", "old")

    for bad in ("new", None, proof("new", sign="forged"), proof("new", old_node="other")):
      self.assertFalse(store._set_value(key, bad, local_sync_storage_op=True))
    store._ChainStoreBasePlugin__exec_store({store.CS_KEY: key, store.CS_VALUE: proof("new")}, peers="outsider")
    self.assertNotIn(key, store.storage)

    self.assertTrue(store._set_value(key, proof("new"), local_sync_storage_op=True))
    for bad in (None, proof("attacker")):
      self.assertFalse(store._set_value(key, bad, local_sync_storage_op=True))
    _, record = _hset_record("NODE_KEY_ROTATIONS", "old", proof("attacker"), "registry-other")
    store._ChainStoreBasePlugin__merge_hset_snapshot("NODE_KEY_ROTATIONS", {key: record})
    self.assertEqual(store.storage[key][store.CS_VALUE], proof("new"))
    self.assertTrue(store._set_value(key, proof("new", nonce="2"), local_sync_storage_op=True))

    outsider = self.store("outsider")
    outsider.bc.verify_node_key_rotation = verify_node_key_rotation
    self.assertFalse(outsider._set_value(key, proof("new"), timeout=.1, max_retries=0))

  def test_job_generations_prevent_delete_resurrection_and_allow_explicit_reuse(self):
    for namespace, version_key in (("DEEPLOY_DEPLOYED_JOBS", "revision"), ("DAUTH_JOB_SECRETS", "deployment_revision")):
      with self.subTest(namespace=namespace):
        store = self.store()
        key = _hset_key(namespace, "7")
        def value(generation, deleted=False):
          return {"job_id": "7", version_key: generation, "operation_id": "op-" + str(generation),
                  "coordinator": "oracle", "EE_SENDER": "oracle", "EE_SIGN": "valid",
                  "pipeline_cid": None if deleted else "cid-" + str(generation), "deleted": deleted}
        first, tombstone, reused = value(1), value(2, deleted=True), value(3)
        self.assertTrue(store._set_value(key, first, local_sync_storage_op=True))
        self.assertTrue(store._set_value(key, tombstone, local_sync_storage_op=True))
        for stale in (first, "old-raw-cid", None):
          self.assertFalse(store._set_value(key, stale, local_sync_storage_op=True))
        _, stale_record = _hset_record(namespace, "7", first, "attacker")
        store._ChainStoreBasePlugin__merge_hset_snapshot(namespace, {key: stale_record})
        self.assertEqual(store.storage[key][store.CS_VALUE], tombstone)
        conflicting = dict(tombstone, deleted=False, pipeline_cid="changed-same-generation")
        self.assertFalse(store._set_value(key, conflicting, local_sync_storage_op=True))
        self.assertTrue(store._set_value(key, reused, local_sync_storage_op=True))
        self.assertFalse(store._set_value(key, tombstone, local_sync_storage_op=True))

  def test_concurrent_job_writes_cannot_validate_then_regress_generation(self):
    from threading import Event, Thread
    store = self.store()
    key = _hset_key("DEEPLOY_DEPLOYED_JOBS", "7")
    entered, release, newer_started, newer_done = Event(), Event(), Event(), Event()
    errors = []
    def value(generation):
      return {"job_id": "7", "revision": generation, "operation_id": "op-" + str(generation),
              "coordinator": "oracle", "EE_SENDER": "oracle", "EE_SIGN": "valid",
              "pipeline_cid": "cid-" + str(generation), "deleted": False}
    def verify(record, **kwargs):
      if record["revision"] == 2:
        entered.set()
        if not release.wait(2):
          raise RuntimeError("Test release timed out")
      return SimpleNamespace(valid=True)
    store.bc.verify = verify
    def write(generation):
      try:
        store._set_value(key, value(generation), local_sync_storage_op=True)
      except Exception as error:
        errors.append(error)
    def newer():
      newer_started.set()
      write(3)
      newer_done.set()
    old_thread = Thread(target=write, args=(2,))
    old_thread.start()
    self.assertTrue(entered.wait(1))
    new_thread = Thread(target=newer)
    new_thread.start()
    self.assertTrue(newer_started.wait(1))
    self.assertFalse(newer_done.is_set())
    release.set()
    old_thread.join(2)
    new_thread.join(2)
    self.assertEqual(errors, [])
    self.assertEqual(store.storage[key][store.CS_VALUE]["revision"], 3)

  def test_dauth_can_attest_secret_custody_but_cannot_sign_coordinator_pointer(self):
    store = self.store()
    value = dict(job_id="7", deployment_revision=2, operation_id="op", coordinator="oracle",
                 EE_SENDER="registry", EE_SIGN="valid", EE_ETH_SENDER="0xregistry", EE_ETH_SIGN="eth1",
                 pipeline_cid="cid", deleted=False, job_secrets={"token": "private"})
    key = _hset_key("DAUTH_JOB_SECRETS", "7")
    self.assertTrue(store._set_value(key, value, local_sync_storage_op=True))
    # Repair/retry on another registered custodian keeps the same semantic value.
    second = dict(value, EE_SENDER="registry-other", EE_ETH_SENDER="0xother", EE_ETH_SIGN="eth2")
    second[BCct.SIGN_CANON_V] = "v2"
    self.assertTrue(store._set_value(key, second, local_sync_storage_op=True))
    for changed in (dict(second, EE_SENDER="attacker"), dict(second, EE_SIGN="forged"),
                    dict(second, coordinator="attacker"), dict(second, job_secrets={"token": "changed"}),
                    dict(second, deployment_revision=1)):
      self.assertFalse(store._set_value(key, changed, local_sync_storage_op=True))
    pointer = dict(value, revision=2)
    pointer.pop("deployment_revision")
    self.assertFalse(store._set_value(_hset_key("DEEPLOY_DEPLOYED_JOBS", "7"), pointer, local_sync_storage_op=True))
    pointer.update(EE_SENDER="oracle")
    self.assertTrue(store._set_value(_hset_key("DEEPLOY_DEPLOYED_JOBS", "7"), pointer, local_sync_storage_op=True))

  def test_job_generation_signature_and_field_binding_cannot_be_forged(self):
    store = self.store()
    key = _hset_key("DEEPLOY_DEPLOYED_JOBS", "7")
    value = {"job_id": "7", "revision": 1, "operation_id": "op", "coordinator": "oracle",
             "EE_SENDER": "oracle", "EE_SIGN": "valid", "pipeline_cid": "cid", "deleted": False}
    for changed in (dict(value, job_id="8"), dict(value, EE_SIGN="forged"),
                    dict(value, coordinator="attacker", EE_SENDER="attacker")):
      self.assertFalse(store._set_value(key, changed, local_sync_storage_op=True))
    self.assertNotIn(key, store.storage)

  def test_non_registry_hsync_request_cannot_export_secrets(self):
    store = self.store()
    hkey = "DAUTH_JOB_SECRET_REVISIONS"
    key, record = _hset_record(hkey, "7|revision", {"secret": "private"}, "oracle")
    store.storage[key] = record
    store.default_handler(store.make_request_payload(sender="ordinary-node", hkey=hkey))
    self.assertEqual(store.sent_payloads, [])
    store.default_handler(store.make_request_payload(sender="registry-other", hkey=hkey))
    self.assertEqual(len(store.sent_payloads), 1)

  def test_unregistered_sender_cannot_write_secret_namespace(self):
    store = self.store()
    key = _hset_key("DAUTH_JOB_SECRET_REVISIONS", "7|revision")
    data = {store.CS_KEY: key, store.CS_VALUE: {"secret": "forged"}, store.CS_OWNER: "oracle"}
    store._ChainStoreBasePlugin__exec_store(data, peers="ordinary-node")
    self.assertNotIn(key, store.storage)
    store._ChainStoreBasePlugin__exec_store(data, peers="oracle")
    self.assertIn(key, store.storage)

  def test_non_registry_recipient_rejects_secret_replication(self):
    store = self.store(address="ordinary-node")
    key = _hset_key("DAUTH_JOB_SECRETS", "7")
    store._ChainStoreBasePlugin__exec_store(
      {store.CS_KEY: key, store.CS_VALUE: {"secret": "private"}, store.CS_OWNER: "oracle"}, peers="oracle",
    )
    self.assertNotIn(key, store.storage)

  def test_snapshot_export_never_reveals_token_protected_values(self):
    store = self.store()
    key, record = _hset_record("players", "private", "secret", "owner", token="access-token")
    store.storage[key] = record
    self.assertEqual(store._ChainStoreBasePlugin__export_hset_snapshot("players"), {})

  def test_revoked_revision_cannot_be_resurrected_by_stale_snapshot(self):
    for revision in ("revision", "revisionx", "revisionxx"):
      with self.subTest(revision=revision):
        store = self.store()
        hkey = "DAUTH_JOB_SECRET_REVISIONS"
        marker_key, marker = _hset_record(hkey, "7|" + revision + "|revoked", {"job_id": "7", "revision": revision}, "oracle")
        payload_key, payload = _hset_record(hkey, "7|" + revision, {"secret": "old"}, "oracle")
        store.storage[marker_key] = marker
        self.assertFalse(store._set_value(payload_key, {"secret": "old"}, owner="oracle", local_sync_storage_op=True))
        store._ChainStoreBasePlugin__merge_hset_snapshot(hkey, {payload_key: payload})
        self.assertNotIn(payload_key, store.storage)
        self.assertEqual(store.storage[marker_key][store.CS_VALUE], marker[store.CS_VALUE])
        self.assertFalse(store._set_value(marker_key, None, owner="oracle", local_sync_storage_op=True))

  def test_snapshot_revocation_wins_regardless_of_field_order(self):
    for marker_first in (False, True):
      with self.subTest(marker_first=marker_first):
        store = self.store()
        hkey = "DAUTH_JOB_SECRET_REVISIONS"
        marker_key, marker = _hset_record(hkey, "7|revisionx|revoked", {"job_id": "7", "revision": "revisionx"}, "oracle")
        payload_key, payload = _hset_record(hkey, "7|revisionx", {"secret": "old"}, "oracle")
        store.storage[payload_key] = payload
        snapshot = {payload_key: payload, marker_key: marker}
        if marker_first:
          snapshot = dict(reversed(list(snapshot.items())))
        store._ChainStoreBasePlugin__merge_hset_snapshot(hkey, snapshot)
        self.assertNotIn(payload_key, store.storage)
        self.assertIn(marker_key, store.storage)

  def test_signed_sequence_prevents_slot_regression_and_forged_sequence(self):
    store = self.store()
    hkey = "DEEPLOY_NODE_OPERATIONS"
    key, current = _hset_record(hkey, "op|deploy|worker|0", {"operation_id": "op", "attempt": "deploy", "sequence": 16, "node_addr": "worker", "EE_SENDER": "worker", "EE_SIGN": "valid"}, "worker")
    store.storage[key] = current
    stale = deepcopy(current)
    stale[store.CS_VALUE]["sequence"] = 8
    forged = deepcopy(current)
    forged[store.CS_VALUE].update(sequence=100, EE_SIGN="forged")
    for candidate in (stale, forged):
      store._ChainStoreBasePlugin__merge_hset_snapshot(hkey, {key: candidate})
    self.assertEqual(store.storage[key][store.CS_VALUE]["sequence"], 16)

  def test_inspect_report_uses_the_signed_worker_namespace_and_separate_attempt(self):
    from naeural_core import Logger
    from ratio1.bc import DefaultBlockEngine
    with tempfile.TemporaryDirectory() as directory:
      log = Logger("WORKER", base_folder=directory, app_folder="_local_cache", no_folders_no_save=True, DEBUG=False)
      engine = DefaultBlockEngine(name="repair-" + os.path.basename(directory), log=log, eth_enabled=False, verbosity=0,
                                  config={"PEM_FILE": os.path.join(directory, "fixture.pem")})
      store = self.store()
      store.bc.verify = engine.verify
      value = {"operation_id": "op", "attempt": "inspect", "sequence": 1, "node_addr": engine.address,
               "status": "observed", "order_digest": "a" * 64, "challenge": "b" * 32}
      engine.sign(value, add_data=True, use_digest=True)
      key = _hset_key("DEEPLOY_NODE_OPERATIONS", "op|inspect|" + engine.address)
      self.assertTrue(store._set_value(key, value, owner=engine.address, local_sync_storage_op=True))
      for field in ("op|retire|" + engine.address, "op|inspect|other"):
        self.assertFalse(store._set_value(_hset_key("DEEPLOY_NODE_OPERATIONS", field), value, owner=engine.address,
                                          local_sync_storage_op=True))
      self.assertFalse(store._set_value(key, dict(value, challenge="c" * 32, sequence=2), owner=engine.address,
                                        local_sync_storage_op=True))  # A forged newer observation cannot replace the receipt.

  def test_signed_node_cannot_preempt_another_nodes_field(self):
    store = self.store()
    key = _hset_key("DEEPLOY_NODE_OPERATIONS", "op|deploy|victim|0")
    value = {"operation_id": "op", "attempt": "deploy", "sequence": 8,
      "node_addr": "attacker", "EE_SENDER": "attacker", "EE_SIGN": "valid"}
    self.assertFalse(store._set_value(key, value, owner="victim", readonly=True, local_sync_storage_op=True))
    self.assertNotIn(key, store.storage)

  def test_revocation_retry_and_exact_erased_tombstone_are_idempotent(self):
    for revision in ("revision", "revisionx", "revisionxx"):
      with self.subTest(revision=revision):
        store = self.store()
        hkey = "DAUTH_JOB_SECRET_REVISIONS"
        marker_key = _hset_key(hkey, "7|" + revision + "|revoked")
        marker = {"job_id": "7", "revision": revision}
        for _ in range(2):
          self.assertTrue(store._set_value(marker_key, marker, owner="oracle", local_sync_storage_op=True))
        self.assertFalse(store._set_value(marker_key, dict(marker, revision="other"), owner="oracle", local_sync_storage_op=True))
        payload_key = _hset_key(hkey, "7|" + revision)
        tombstone = dict(marker, revoked=True)
        self.assertTrue(store._set_value(payload_key, tombstone, owner="oracle", local_sync_storage_op=True))
        self.assertFalse(store._set_value(payload_key, dict(tombstone, secret="leak"), owner="oracle", local_sync_storage_op=True))
        store.storage.pop(payload_key)
        _, erased = _hset_record(hkey, "7|" + revision, tombstone, "oracle")
        store._ChainStoreBasePlugin__merge_hset_snapshot(hkey, {payload_key: erased})
        self.assertEqual(store.storage[payload_key][store.CS_VALUE], tombstone)

  def test_signed_operation_requires_registered_coordinator(self):
    store = self.store()
    key = _hset_key("DEEPLOY_OPERATIONS", "op|0")
    value = {"operation_id": "op", "sequence": 8, "coordinator": "ordinary-node", "EE_SENDER": "ordinary-node", "EE_SIGN": "valid"}
    self.assertFalse(store._set_value(key, value, owner="ordinary-node", local_sync_storage_op=True))


class DeploymentCommandTest(unittest.TestCase):
  def test_scoped_stop_cannot_delete_restoration(self):
    lifecycle = DeploymentLifecycleTest()
    lifecycle.setUp()
    self.addCleanup(lifecycle.doCleanups)
    service = lifecycle.lifecycle
    service.admit(config(), "oracle", True)
    service.admit(config(attempt="restore", revision=1), "oracle", True)
    harness = self.harness(service, config(attempt="restore", revision=1))
    harness.update_config_stream(self.command(config("stop")), "oracle-id", "session")
    self.assertIn("app", harness.dct_config_streams)
    self.assertTrue(service.records["operation|deploy"]["aborted"])

  def test_retire_adopts_live_instance_before_removing_configuration(self):
    with tempfile.TemporaryDirectory() as directory:
      service = DeploymentLifecycle(os.path.join(directory, "journal"), "node")
      previous = config(revision=1)
      previous["DEEPLOY_SPECS"] = {"job_id": 7, "revision": 1}
      service.admit(previous, "oracle", True)
      runtime = plugin(ready=True)
      runtime.get_stream_id = lambda: "app"
      runtime.get_signature = lambda: "WORKER"
      runtime.get_instance_id = lambda: "worker"
      harness = self.harness(service, previous, runtime)
      command = config("retire", "retire", 1)
      command["DEEPLOY_OPERATION"].update(job_id=7, expected_previous_operation_id="operation")
      harness.update_config_stream(self.command(command), "oracle-id", "session")
      self.assertNotIn("app", harness.dct_config_streams)
      self.assertIs(service.plugins[("operation|retire", "WORKER", "worker")], runtime)
      self.assertEqual(runtime._deployment_operation_key, "operation|retire")

  def test_plugin_wrapper_signs_eip191_text_with_the_node_key(self):
    from eth_account import Account
    from eth_account.messages import encode_defunct
    from naeural_core import Logger
    from naeural_core.utils.plugins_base.bc_wrapper import BCWrapper
    from ratio1.bc import DefaultBlockEngine

    with tempfile.TemporaryDirectory() as directory:
      log = Logger("WORKER", base_folder=directory, no_folders_no_save=True, DEBUG=False)
      engine = DefaultBlockEngine("signer-" + os.path.basename(directory), log=log, eth_enabled=False,
                                  verbosity=0, config={"PEM_FILE": os.path.join(directory, "oracle.pem")})
      wrapper = BCWrapper(engine, owner=None)
      message = "Ratio1 Deeploy replacement refusal v1\n[\"devnet\",\"31\"]"
      signature = wrapper.eth_sign_text(message, no_hash=True)
      self.assertEqual(Account.recover_message(encode_defunct(text=message), signature=signature), wrapper.eth_address)

  def test_legacy_worker_inspection_and_conditional_retirement(self):
    from naeural_core import Logger
    from naeural_core.utils.plugins_base.bc_wrapper import BCWrapper
    from ratio1.bc import DefaultBlockEngine
    with tempfile.TemporaryDirectory() as directory, patch.object(module.time, "time", return_value=1000):
      journal = os.path.join(directory, "journal")
      log = Logger("WORKER", base_folder=directory, app_folder="_local_cache", no_folders_no_save=True, DEBUG=False)
      oracle, node = [BCWrapper(DefaultBlockEngine(name=label + os.path.basename(directory), log=log, eth_enabled=False,
        verbosity=0, config={"PEM_FILE": os.path.join(directory, label + ".pem")}), owner=None)
        for label in ("oracle", "node")]  # Exercise the SDK encryption/decryption used by production peers.
      service = DeploymentLifecycle(journal, node.address)
      worker = plugin(ready=False)  # Repair must also work before the runner's readiness check caches Git metadata.
      worker.get_stream_id = lambda: "app"
      worker.get_signature = lambda: "WORKER_APP_RUNNER"
      worker.get_instance_id = lambda: "worker"
      worker.cfg_build_and_run_commands = ["prepare", "serve"]
      worker.cfg_chainstore_peers = ["two", "one"]
      observed_commit = ["e" * 40]
      worker.read_deployment_git_commit = lambda: observed_commit[0]
      image_id = "sha256:" + "a" * 64
      cached_bindings = {"image_id": image_id}  # Readiness can lag while Docker already exposes the image.
      worker.get_deployment_runtime_artifacts = lambda: cached_bindings.copy()
      current = {"NAME": "app", "DEEPLOY_SPECS": {"job_id": 31, "current_target_nodes": ["old2", "old1"]},
                 "PLUGINS": [{"SIGNATURE": "WORKER_APP_RUNNER", "INSTANCES": [{"INSTANCE_ID": "worker",
                   "BUILD_AND_RUN_COMMANDS": ["prepare", "serve"], "CHAINSTORE_PEERS": ["two", "one"],
                   "ENV": {"SECRET": "private"}}]}]}
      harness = self.harness(service, current, worker)
      harness.owner.bc = node
      member = node.node_address_to_eth_address(oracle.address)
      harness.owner.bc.get_eth_oracles = lambda: [member]
      prefix = hashlib.sha256(oracle.address.encode()).hexdigest()[:16] + "-"
      fingerprint = module.repair_config_fingerprint(current)
      seal = oracle.encrypt_str(fingerprint, node.address)
      request = {"operation_id": prefix + "a" * 32, "attempt": "inspect", "action": "inspect_worker_v1",
                 "instance_id": "worker", "job_id": 31, "revision": 0, "coordinator": oracle.address, "node_addr": node.address,
                 "peers": [oracle.address], "expires_at": 1110, "challenge": "c" * 32, "config_seal": seal}
      def send(op, name="app", **transport):
        envelope = self.command({"NAME": name, "DEEPLOY_OPERATION": op})
        envelope["EE_SENDER"] = oracle.address
        harness.update_config_stream(dict(envelope, **transport), "oracle-id", "session")
      def publish():
        writes = []
        service.publish(lambda key, value, **kwargs: writes.append(deepcopy(value)) or True,
                        lambda record, **kwargs: record.update(EE_SENDER="node", EE_SIGN="fixture"))
        return writes
      # A handler using the pre-repair lifecycle admission rejects both commands before modifying streams.
      for attempt, action in (("inspect", "inspect_worker_v1"), ("retire", "retire_worker_v1")):
        with self.assertRaises(ValueError):
          service.admit({"NAME": "app", "DEEPLOY_OPERATION": dict(request, attempt=attempt, action=action)}, oracle.address, True)
      # Every bad scope must be refused before reading Docker or writing the journal.
      for override in ({"node_addr": "other"}, {"job_id": 99}, {"instance_id": "other"}, {"revision": False},
                       {"expires_at": 999}, {"expires_at": 1181}, {"challenge": "bad"}, {"coordinator": "other"},
                       {"config_seal": None}, {"config_seal": oracle.encrypt_str("0"*64, node.address)},
                       {"config_seal": fingerprint}):
        send(dict(request, **override))
      send(request, VALIDATED=False)
      harness.owner.bc.get_eth_oracles = lambda: []
      send(request)
      self.assertEqual(service.records, {})
      harness.owner.bc.get_eth_oracles = lambda: [member]
      with patch.object(module.time, "time", return_value=970), patch.object(module.time, "monotonic", return_value=50):
        send(request)  # A node 30s behind the coordinator still accepts a normal 110s challenge.
        send(dict(request, challenge="d"*32, expires_at=1111))
        self.assertEqual(service.records[request["operation_id"] + "|inspect"]["challenge"], request["challenge"])
        other = deepcopy(current)
        other["NAME"] = "other"
        other["DEEPLOY_SPECS"]["job_id"] = 32
        harness.dct_config_streams["other"] = other
        other_worker = deepcopy(worker)
        other_worker.get_stream_id = lambda: "other"
        worker_registry = harness.owner._business_manager.current_instances
        worker_registry["other"] = other_worker
        other_request = dict(request, job_id=32, operation_id=prefix + "d"*32,
                             config_seal=oracle.encrypt_str(module.repair_config_fingerprint(other), node.address))
        send(other_request, name="other")  # A different job on the same worker is not throttled.
        self.assertIn(other_request["operation_id"] + "|inspect", service.records)
        worker_registry.pop("other")
        harness.dct_config_streams.pop("other")
      report = next(r for r in publish() if r["operation_id"] == request["operation_id"])
      self.assertEqual((report["status"], report["challenge"]), ("observed", request["challenge"]))
      self.assertNotIn("private", str(report))
      self.assertNotIn("prepare", str(report))
      self.assertEqual(report["config_seal_hash"], hashlib.sha256(seal.encode()).hexdigest())
      self.assertNotIn(fingerprint, str(report))
      self.assertNotIn(seal, str(report))
      self.assertNotIn(fingerprint, Path(journal).read_text())
      self.assertNotIn(seal, Path(journal).read_text())
      self.assertEqual(report["order_digest"], hashlib.sha256(
        b'[["old2","old1"],["prepare","serve"],["two","one"]]').hexdigest())
      self.assertEqual(harness.dct_config_streams["app"], current)
      send(request)  # Same challenge cannot cause repeated Docker or journal writes.
      self.assertEqual(publish(), [])
      service = DeploymentLifecycle(journal, node.address)
      harness.shmem["deeploy_lifecycle"] = service
      retire = dict(request, action="retire_worker_v1", attempt="retire", operation_id=prefix + "b" * 32,
                    parent_operation_id=request["operation_id"], order_digest=report["order_digest"], git_commit="e" * 40)
      for override in ({"parent_operation_id": prefix + "f"*32}, {"order_digest": "x"}, {"challenge": "d"*32},
                       {"git_commit": "d"*40}, {"instance_id": "wrong"},
                       {"config_seal": oracle.encrypt_str(fingerprint, node.address)}):
        send(dict(retire, **override))
        self.assertIn("app", harness.dct_config_streams)
      observed_commit[0] = "f" * 40
      send(retire)
      self.assertIn("app", harness.dct_config_streams)
      observed_commit[0] = "e" * 40
      instance = current["PLUGINS"][0]["INSTANCES"][0]
      for field, change in (("ENV", {"SECRET": "changed"}), ("VOLUMES", {"data": "/var/data"}),
                            ("BUILD_AND_RUN_COMMANDS", ["serve", "prepare"])):
        with patch.dict(instance, {field: change}):
          send(retire)  # Every post-inspection config change, even a single private scalar, must cancel the stop.
          self.assertIn("app", harness.dct_config_streams)
      current["DEEPLOY_SPECS"]["current_target_nodes"].reverse()  # A later config invalidates the observed order.
      send(retire)
      self.assertIn("app", harness.dct_config_streams)
      current["DEEPLOY_SPECS"]["current_target_nodes"].reverse()

      lock_released = threading.Event()
      probe_threads = []
      def probe_during_config_change():
        def concurrently_change():
          with service.lock:
            instance["ENV"] = {"SECRET": "changed while Docker was running"}
            lock_released.set()
        thread = threading.Thread(target=concurrently_change)
        probe_threads.append(thread)
        thread.start()
        return observed_commit[0] if lock_released.wait(2) else None
      worker.read_deployment_git_commit = probe_during_config_change
      send(retire)
      for thread in probe_threads:
        thread.join(timeout=2)
      self.assertTrue(lock_released.is_set(), "workload probing must not hold the lifecycle lock")
      self.assertIn("app", harness.dct_config_streams, "a raced configuration change must block retirement")
      instance["ENV"] = {"SECRET": "private"}
      worker.read_deployment_git_commit = lambda: observed_commit[0]
      send(retire)
      self.assertNotIn("app", harness.dct_config_streams)
      bindings = {"image_id": image_id, "git_commit": "e" * 40}
      self.assertEqual(service.records[retire["operation_id"] + "|retire"]["instances"][0]["runtime_artifacts"], bindings)
      # The old stream file may survive a process exit between durable admission and deletion.
      restarted = DeploymentLifecycle(journal, node.address)
      self.assertFalse(restarted.allowed(current))
      other_job = dict(current, DEEPLOY_SPECS=dict(current["DEEPLOY_SPECS"], job_id=32))
      self.assertTrue(restarted.allowed(other_job))
      worker.thread.is_alive = lambda: False
      worker._deployment_cleanup_complete = True
      cached_bindings["git_commit"] = "f" * 40  # A stale cache must not replace the checked-out commit when publishing.
      stop = next(r for r in publish() if r["attempt"] == "retire")
      self.assertEqual((stop["status"], stop["parent_operation_id"], stop["order_digest"]),
                       ("stopped", request["operation_id"], report["order_digest"]))
      self.assertEqual(stop["instances"][0]["runtime_artifacts"], bindings)
      self.assertEqual(stop["config_seal_hash"], report["config_seal_hash"])
      service = DeploymentLifecycle(journal, node.address)
      harness.shmem["deeploy_lifecycle"] = service
      with patch.object(module.time, "time", return_value=1200):
        send(retire)  # Expired retry against an absent pipeline must keep its complete receipt.
        again = next(r for r in publish() if r["attempt"] == "retire")
        self.assertEqual(again["instances"], stop["instances"])
        self.assertEqual(again["status"], "stopped")

  def test_conditional_tombstone_prevents_merging_a_surviving_file_after_two_restarts(self):
    from naeural_core import constants as ct
    from test_chain_store_hsync import _load_class_from_source
    source = Path(__file__).resolve().parents[2] / "config" / "config_manager_commands.py"
    handlers = _load_class_from_source(source, "ConfigCommandHandlers", extra_globals={
      "OPERATION_KEY": module.OPERATION_KEY, "SERVICE_KEY": module.SERVICE_KEY,
      "STATELESS_REPLACEMENT_START": module.STATELESS_REPLACEMENT_START,
    }, strip_text=("from naeural_core.business.deployment_lifecycle import OPERATION_KEY, SERVICE_KEY, STATELESS_REPLACEMENT_START\n",))
    with tempfile.TemporaryDirectory() as directory:
      journal = os.path.join(directory, "journal")
      retired = config("retire", "retire", 0)
      retired.update(DEEPLOY_SPECS={"job_id": 31}, OBSOLETE_SETTING="old")
      surviving = deepcopy(retired)
      del surviving["DEEPLOY_OPERATION"]  # The old file was never removed before process exit.
      proof = dict(parent_operation_id="parent", challenge="c"*32, order_digest="a"*64,
                   config_seal_hash="b"*64, git_commit="e"*40, job_id=31)
      service = DeploymentLifecycle(journal, "node")
      self.assertTrue(service.admit(retired, "oracle", True, repair_evidence=proof))
      persisted = config(revision=1)
      persisted["DEEPLOY_SPECS"] = {"job_id": 31, "revision": 1}
      first_restart = DeploymentLifecycle(journal, "node")
      self.assertFalse(first_restart.allowed(surviving))
      self.assertTrue(first_restart.admit(persisted, "oracle", True))  # Crash before writing the new config.
      service = DeploymentLifecycle(journal, "node")
      self.assertFalse(service.allowed(surviving))
      self.assertTrue(service.allowed(persisted))
      harness = SimpleNamespace(shmem={module.SERVICE_KEY: service}, admin_pipeline_name="admin",
        dct_config_streams={"app": surviving}, _check_duplicate_last=lambda **kw: False,
        P=lambda *args, **kw: None, _create_notification=lambda **kw: None, keep_good_stream=lambda value: value,
        _save_stream_config=lambda value: None)
      def apply_delta(old, new):
        self.assertEqual(old, {})  # The real handler must discard the obsolete file before entering delta merge.
        return dict(old, **new)
      harness._apply_delta_to_config = apply_delta
      envelope = dict(persisted)
      envelope[ct.CONFIG_STREAM.INITIATOR_ID] = "oracle-id"
      envelope[ct.COMMS.COMM_RECV_MESSAGE.K_VALIDATED] = True
      envelope[ct.COMMS.COMM_RECV_MESSAGE.K_SENDER_ADDR] = "oracle"
      handlers.update_config_stream(harness, envelope, "oracle-id", "session")
      self.assertNotIn("OBSOLETE_SETTING", harness.dct_config_streams["app"])
      self.assertEqual(harness.dct_config_streams["app"]["DEEPLOY_OPERATION"], persisted["DEEPLOY_OPERATION"])

  def test_repaired_start_requires_local_stop_and_fences_historical_legacy_writes(self):
    with tempfile.TemporaryDirectory() as directory:
      journal = os.path.join(directory, "journal")
      service = DeploymentLifecycle(journal, "node")
      parent_id = hashlib.sha256(b"oracle").hexdigest()[:16] + "-" + "a"*32
      start = config(revision=1)
      start["PLUGINS"][0]["SIGNATURE"] = "WORKER_APP_RUNNER"
      start["DEEPLOY_OPERATION"].update(action="start_repaired_worker_v1", parent_operation_id=parent_id, job_id=31)
      start["DEEPLOY_SPECS"] = {"job_id": 31, "revision": 1, "secret_revision": "operation:repair_v1:deploy"}
      self.assertFalse(service.admit(start, "oracle", True))  # Historical STOP must exist locally even after restart.
      legacy = deepcopy(start)
      del legacy["DEEPLOY_OPERATION"]
      legacy["DEEPLOY_SPECS"] = {"job_id": 31}
      parent = config("retire", "retire", 0)["DEEPLOY_OPERATION"]
      parent.update(operation_id=parent_id, job_id=31)
      self.assertTrue(service.retire("app", legacy, parent, "oracle", True))
      proof = service.records[parent_id + "|retire"]
      proof["instances"][0]["runtime_artifacts"] = {"git_commit": "a"*40}
      service._cleanup = lambda *args: True  # Simulate the original node's verified external cleanup.
      service.publish(lambda *a, **kw: True, lambda *a, **kw: None)
      request = dict(parent, attempt="inspect", action="inspect_worker_v1", challenge="d"*32,
                     expires_at=int(module.time.time()) + 100, confirm_prior_stop=True, instance_id="worker", node_addr="node")
      inspection = self.harness(service, {})
      inspection.owner.bc = SimpleNamespace(node_address_to_eth_address=lambda _: "eth", get_eth_oracles=lambda: ["eth"])
      def confirm(**overrides):
        inspection.update_config_stream(self.command(dict(NAME="app", DEEPLOY_OPERATION=dict(request, **overrides))),
                                        "oracle-id", "session")
      key = parent_id + "|inspect"
      inspection.dct_config_streams["app"] = legacy
      confirm()
      self.assertNotIn(key, service.records)
      inspection.dct_config_streams.clear()
      confirm(instance_id="wrong")
      self.assertNotIn(key, service.records)
      active = SimpleNamespace(get_stream_id=lambda: "app", thread=SimpleNamespace(is_alive=lambda: True))
      inspection.owner._business_manager.current_instances["worker"] = active
      confirm()
      self.assertNotIn(key, service.records)
      active.thread = None  # A half-created worker makes prior-stop confirmation inconclusive.
      confirm()
      self.assertNotIn(key, service.records)
      inspection.owner._business_manager.current_instances.clear()
      with patch.object(service, "_save", side_effect=OSError("journal unavailable")):
        with self.assertRaises(OSError):
          confirm()
      self.assertNotIn(key, service.records)  # The publisher cannot advertise an undurable inspection.
      self.assertTrue(service.allows_untracked_update(legacy, {}))
      confirm()
      receipt = service.records[key]
      self.assertTrue(receipt["confirm_prior_stop"])
      self.assertEqual(receipt["instances"][0]["runtime_artifacts"], {"git_commit": "a"*40})
      self.assertNotIn("config_seal_hash", receipt)
      self.assertNotIn("order_digest", receipt)
      self.assertEqual(inspection.dct_config_streams, {})
      messages = []
      inspection.P = lambda msg, **kw: messages.append(msg)
      rebooted = DeploymentLifecycle(journal, "node")
      inspection.shmem[module.SERVICE_KEY] = rebooted
      inspection.update_config_stream(self.command(legacy), "oracle-id", "session")
      self.assertEqual(inspection.dct_config_streams, {})  # The STOP confirmation fences replay before start.
      self.assertIn("'app'", messages[0])
      target = dict(NAME="app", SIGNATURE="WORKER_APP_RUNNER", INSTANCE_ID="worker")
      command = dict(target, INSTANCE_CONFIG={"INSTANCE_COMMAND": {"action": "status"}})
      inspection.dct_config_streams["app"] = deepcopy(legacy)  # A crash can leave a retired file in place.
      notifications = []
      inspection._create_notification = lambda **kwargs: notifications.append(kwargs)
      self.assertIsNone(inspection.update_pipeline_instance(command, "oracle-id", "session"))
      inspection.batch_update_pipeline_instance([command], "oracle-id", "session")
      self.assertEqual(notifications[-1]["BATCH_UPDATE_PIPELINE_INSTANCE"], [])
      self.assertEqual(inspection.dct_config_streams["app"], legacy)
      inspection.dct_config_streams["app"].pop("DEEPLOY_SPECS")  # A legacy file without job metadata is still fenced.
      self.assertIsNone(inspection.update_pipeline_instance(command, "oracle-id", "session"))
      for attempt, revision in (("deploy", 1), ("restore", 0)):
        start["DEEPLOY_SPECS"].update(revision=revision, secret_revision="operation:repair_v1:" + attempt)
        start["DEEPLOY_OPERATION"].update(attempt=attempt, revision=revision)
        service = DeploymentLifecycle(journal, "node")
        self.assertTrue(service.admit(start, "oracle", True))
        service = DeploymentLifecycle(journal, "node")
        self.assertTrue(service.allowed(start))  # Tracking remains usable after loading the worker journal again.
        harness = self.harness(service, deepcopy(start))  # Preserve the deployment envelope for the next start.
        messages = []
        harness.P = lambda msg, **kw: messages.append(msg)
        harness.update_config_stream(self.command(legacy), "oracle-id", "session")
        harness.update_config_stream(self.command({"NAME": "app", "PLUGINS": []}), "oracle-id", "session")
        harness.update_config_stream(self.command({"NAME": "app", "PLUGINS.0.INSTANCES.0.ENV": "do-not-log"}),
                                     "oracle-id", "session")
        harness.update_config_stream(self.command({"NAME": "app", "DEEPLOY_SPECS.revision": 99}), "oracle-id", "session")
        self.assertEqual(harness.dct_config_streams["app"], start)
        self.assertEqual(len(messages), 4)
        self.assertTrue(all("UPDATE_CONFIG" in msg and "do-not-log" not in msg for msg in messages))
        notifications = []
        harness._create_notification = lambda **kwargs: notifications.append(kwargs)
        instance = harness.dct_config_streams["app"]["PLUGINS"][0]["INSTANCES"][0]
        harness._get_plugin_instance = lambda **kwargs: instance
        harness._apply_delta_to_config = lambda original, delta, **kwargs: original.update(delta)
        harness._save_stream_config = lambda value: None
        self.assertEqual(harness.update_pipeline_instance(command, "oracle-id", "session"),
                         ("app", "WORKER_APP_RUNNER", "worker"))
        self.assertEqual(instance["INSTANCE_COMMAND"], {"action": "status"})
        update = dict(target, INSTANCE_CONFIG={"PROCESS_DELAY": 1})
        harness.batch_update_pipeline_instance([update], "oracle-id", "session")
        self.assertEqual(notifications[-1]["BATCH_UPDATE_PIPELINE_INSTANCE"], [("app", "WORKER_APP_RUNNER", "worker")])
        self.assertEqual(instance["PROCESS_DELAY"], 1)
        self.assertEqual(harness.dct_config_streams["app"]["DEEPLOY_OPERATION"], start["DEEPLOY_OPERATION"])
        stop = dict(start, DEEPLOY_OPERATION=dict(start["DEEPLOY_OPERATION"], action="stop"))
        self.assertTrue(service.admit(stop, "oracle", True))  # A stop can be journaled before the pipeline file is removed.
        self.assertIsNone(harness.update_pipeline_instance(update, "oracle-id", "session"))
        harness.batch_update_pipeline_instance([update], "oracle-id", "session")
        self.assertEqual(notifications[-1]["BATCH_UPDATE_PIPELINE_INSTANCE"], [])
        self.assertFalse(service.allowed(legacy))
        self.assertTrue(service.allows_untracked_update(dict(legacy, NAME="unrelated"), {}))
      later = config(revision=2)
      later["DEEPLOY_OPERATION"]["operation_id"] = "later"
      self.assertTrue(service.admit(later, "oracle", True))  # Subsequent ordinary tracked updates still work.

  def test_stateless_replacement_applies_complete_config_without_a_stop_and_fences_replay(self):
    with tempfile.TemporaryDirectory() as directory:
      journal = os.path.join(directory, "journal")
      service = DeploymentLifecycle(journal, "node")
      replacement = config(revision=3)
      replacement.update(OWNER="owner", DEEPLOY_SPECS={"job_id": 31, "revision": 3, "secret_revision": "new:deploy"})
      replacement["PLUGINS"][0]["SIGNATURE"] = "WORKER_APP_RUNNER"
      replacement["PLUGINS"].append({"SIGNATURE": "NATIVE_FIXTURE", "INSTANCES": [{"INSTANCE_ID": "native"}]})
      replacement["DEEPLOY_OPERATION"].update(
        action=module.STATELESS_REPLACEMENT_START, operation_id="new", job_id=31,
      )
      previous = deepcopy(replacement)
      previous["DEEPLOY_SPECS"] = {"job_id": 31, "revision": 2}
      del previous["DEEPLOY_OPERATION"]
      previous["REMOVED_SETTING"] = "must not survive delta merge"
      harness = self.harness(service, previous)
      harness.owner.bc = SimpleNamespace(node_address_to_eth_address=lambda _: "eth", get_eth_oracles=lambda: ["eth"])
      harness.P = harness._create_notification = lambda *args, **kwargs: None
      harness._check_duplicate_last = lambda **kwargs: False
      harness._apply_delta_to_config = lambda base, delta: dict(base, **delta)
      harness.keep_good_stream = lambda pipeline: pipeline
      harness._save_stream_config = lambda pipeline: None
      harness.update_config_stream(self.command(replacement), "oracle-id", "session")
      applied = harness.dct_config_streams["app"]
      self.assertNotIn("REMOVED_SETTING", applied)
      self.assertEqual(service.records["new|deploy"]["instances"][1]["signature"], "NATIVE_FIXTURE")
      self.assertEqual(applied["DEEPLOY_OPERATION"]["action"], "start")
      self.assertTrue(service.allowed(applied))
      self.assertNotIn("new|retire", service.records)
      rebooted = DeploymentLifecycle(journal, "node")
      self.assertFalse(rebooted.allowed(previous))
      self.assertTrue(rebooted.allowed(applied))
      self.assertTrue(rebooted.allows_instance_update(applied))
      delayed = config(revision=2)
      delayed["DEEPLOY_SPECS"] = {"job_id": 31, "revision": 2}
      self.assertFalse(rebooted.admit(delayed, "oracle", True))

      # Only a strictly newer rollout can restore older canonical content, after its own stop.
      restore = config(revision=2, attempt="restore")
      restore["DEEPLOY_SPECS"] = {"job_id": 31, "revision": 2}
      restore["DEEPLOY_OPERATION"].update(operation_id="later", job_id=31)
      self.assertFalse(rebooted.admit(restore, "oracle", True))
      stop = config(action="stop", revision=4)
      stop["DEEPLOY_OPERATION"].update(operation_id="later", job_id=31)
      self.assertTrue(rebooted.admit(stop, "oracle", True))
      self.assertFalse(rebooted.admit(restore, "oracle", True))
      rebooted.records["later|deploy"]["status"] = "stopped"
      self.assertTrue(rebooted.admit(restore, "oracle", True))

  def test_stateless_replacement_refuses_different_local_occupant(self):
    with tempfile.TemporaryDirectory() as directory:
      service = DeploymentLifecycle(os.path.join(directory, "journal"), "node")
      ready = config(revision=3)
      ready["PLUGINS"][0]["SIGNATURE"] = "NATIVE_FIXTURE"
      ready["DEEPLOY_OPERATION"].update(job_id=31, action=module.STATELESS_REPLACEMENT_START)
      ready.update(OWNER="owner", DEEPLOY_SPECS={"job_id": 31, "revision": 3, "secret_revision": "operation:deploy"})
      previous = deepcopy(ready)
      previous["DEEPLOY_SPECS"]["revision"] = 2
      del previous["DEEPLOY_OPERATION"]
      self.assertTrue(service.can_replace_stateless_worker(ready, previous))
      self.assertFalse(service.can_replace_stateless_worker(ready, dict(previous, OWNER="other")))
      wrong_signature = deepcopy(previous)
      wrong_signature["PLUGINS"][0]["SIGNATURE"] = "DIFFERENT_NATIVE_FIXTURE"
      self.assertFalse(service.can_replace_stateless_worker(ready, wrong_signature))
      previous["PLUGINS"][0]["INSTANCES"][0]["FIXED_SIZE_VOLUMES"] = {"db": {"SIZE": "100M"}}
      self.assertFalse(service.can_replace_stateless_worker(ready, previous))
      instance = previous["PLUGINS"][0]["INSTANCES"][0]
      instance.pop("FIXED_SIZE_VOLUMES")
      instance["PER_NODE_CONFIG"] = {"byNode": {"node": {"volumes": {"/mnt/data": "/data"}}}}
      self.assertFalse(service.can_replace_stateless_worker(ready, previous))
      instance["PER_NODE_CONFIG"] = {"node": {"file_volumes": {"local.conf": "/app/local.conf"}}}
      self.assertFalse(service.can_replace_stateless_worker(ready, previous))
      instance["PER_NODE_CONFIG"] = {"default": {"ENV": {"ROLE": "web"}}}
      self.assertTrue(service.can_replace_stateless_worker(ready, previous))
      self.assertFalse(service.records)

  def test_stateless_replacement_accepts_an_empty_name_after_a_historical_retire(self):
    with tempfile.TemporaryDirectory() as directory:
      service = DeploymentLifecycle(os.path.join(directory, "journal"), "node")
      retired = config("retire", "retire", 0)  # Old journals kept the app name without a job ID.
      self.assertTrue(service.admit(retired, "oracle", True))
      incoming = config(revision=2)
      incoming["DEEPLOY_OPERATION"].update(job_id=31, action=module.STATELESS_REPLACEMENT_START)
      self.assertFalse(service.can_replace_stateless_worker(incoming, {}))
      service.records["operation|retire"]["status"] = "stopped"  # The historical occupant no longer owns the name.
      self.assertTrue(service.can_replace_stateless_worker(incoming, {}))

  def test_stateless_replacement_cannot_displace_newer_retirement_without_config(self):
    with tempfile.TemporaryDirectory() as directory:
      service = DeploymentLifecycle(os.path.join(directory, "journal"), "node")
      retired = config("retire", "retire", 3)
      retired["DEEPLOY_OPERATION"].update(job_id=31, operation_id="newer")
      self.assertTrue(service.admit(retired, "oracle", True))
      incoming = config(revision=2)
      incoming["DEEPLOY_OPERATION"].update(job_id=31, action=module.STATELESS_REPLACEMENT_START)
      # A stopped receipt still fences older first deliveries when the config file vanished.
      service.records["newer|retire"]["status"] = "stopped"
      self.assertFalse(service.can_replace_stateless_worker(incoming, {}))
      incoming["DEEPLOY_OPERATION"]["revision"] = 4
      self.assertTrue(service.can_replace_stateless_worker(incoming, {}))

  @staticmethod
  def command(config):
    config = deepcopy(config)
    config.update(VALIDATED=True, EE_SENDER="oracle", INITIATOR_ID="oracle-id")
    return config

  @staticmethod
  def harness(service, current, runtime=None):
    from test_chain_store_hsync import _load_class_from_source
    source = Path(__file__).resolve().parents[2] / "config" / "config_manager_commands.py"
    constants = SimpleNamespace(
      CONFIG_STREAM=SimpleNamespace(NAME="NAME", INITIATOR_ID="INITIATOR_ID",
                                    K_MODIFIED_BY_ID="MODIFIED_BY_ID", K_MODIFIED_BY_ADDR="MODIFIED_BY_ADDR",
                                    K_INITIATOR_ADDR="INITIATOR_ADDR", K_INITIATOR_ID="INITIATOR_ID"),
      COMMS=SimpleNamespace(COMM_RECV_MESSAGE=SimpleNamespace(K_VALIDATED="VALIDATED", K_SENDER_ADDR="EE_SENDER")),
      PAYLOAD_DATA=SimpleNamespace(NAME="NAME", INSTANCE_CONFIG="INSTANCE_CONFIG", SESSION_ID="SESSION_ID"),
      PAYLOAD_CT=SimpleNamespace(COMMANDS=SimpleNamespace(UPDATE_CONFIG="UPDATE_CONFIG")),
      PLUGIN_INFO=SimpleNamespace(SIGNATURE="SIGNATURE", INSTANCE_ID="INSTANCE_ID"),
      STATUS_TYPE=SimpleNamespace(STATUS_EXCEPTION="error", STATUS_NORMAL="normal"),
      NOTIFICATION_CODES=SimpleNamespace(PIPELINE_FAILED="failed", PIPELINE_OK="ok"),
    )
    commands = _load_class_from_source(source, "ConfigCommandHandlers", extra_globals={
      "ct": constants, "OPERATION_KEY": "DEEPLOY_OPERATION", "SERVICE_KEY": "deeploy_lifecycle",
      "STATELESS_REPLACEMENT_START": module.STATELESS_REPLACEMENT_START,
    }, strip_text=("from naeural_core import constants as ct\n", "from naeural_core.business.deployment_lifecycle import OPERATION_KEY, SERVICE_KEY, STATELESS_REPLACEMENT_START\n"))
    harness = commands()
    harness.shmem = {"deeploy_lifecycle": service}
    harness.dct_config_streams = {"app": current}
    harness.admin_pipeline_name = "admin_pipeline"
    harness.owner = SimpleNamespace(_business_manager=SimpleNamespace(current_instances={"instance": runtime} if runtime else {}))
    harness.delete_config_stream = lambda name, *args, **kwargs: harness.dct_config_streams.pop(name)
    return harness


class ConstructionBarrierTest(unittest.TestCase):
  def manager(self, lifecycle, constructor):
    import ast
    import traceback
    from collections import deque
    from time import perf_counter
    from naeural_core import constants as ct
    source = Path(__file__).resolve().parents[1] / "business_manager.py"
    tree = ast.parse(source.read_text())
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BusinessManager")
    method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "_check_instances")
    namespace = {"ct": ct, "SERVICE_KEY": "deeploy_lifecycle", "OPERATION_KEY": "DEEPLOY_OPERATION",
                 "deployment_source_mode": module.deployment_source_mode,
                 "perf_counter": perf_counter, "deque": deque, "traceback": traceback}
    wrapper = ast.ClassDef(name="BusinessManager", bases=[], keywords=[], body=[method], decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), str(source), "exec"), namespace)
    manager = namespace["BusinessManager"]()
    manager.set_loop_stage = lambda *args: None
    manager.get_current_jobs = lambda **kwargs: [(None, None, None, None, None, "app", "WORKER", "worker", {})]
    manager.config_data = {"PLUGINS_DEBUG_LOAD_TIMINGS": False}
    manager.log = SimpleNamespace(hash_object=lambda *args, **kwargs: "instance")
    manager._dct_hash_mappings, manager._dct_current_instances, manager._dct_instance_hash_log = {}, {}, {}
    manager._dct_config_streams = {"app": config()}
    manager.shmem = {"deeploy_lifecycle": lifecycle}
    manager.comm_shared_memory = {"payloads": {}, "commands": {}}
    manager.plugins_shmem, manager._environment_variables = {}, {}
    manager.owner = SimpleNamespace(__version__="test", runs_in_docker=False, docker_source="test",
                                    get_pipelines_view=lambda: {})
    manager._run_on_threads = True
    manager.P = lambda *args, **kwargs: None
    manager._get_module_name_and_class = lambda **kwargs: (None, None, constructor, {})
    manager._BusinessManager__maybe_register_special_plugin_instance_hash = lambda **kwargs: None
    return manager

  def test_stop_cannot_acknowledge_an_inflight_constructor(self):
    from threading import Event, Thread
    with tempfile.TemporaryDirectory() as folder:
      lifecycle = DeploymentLifecycle(os.path.join(folder, "journal.json"), "node")
      lifecycle.admit(config(), "oracle", True)
      entered, release, stop_started, stopped = Event(), Event(), Event(), Event()
      runtime = plugin(alive=True, external=True)
      runtime.cfg_runs_only_on_supervisor_node = False
      runtime.start_thread = lambda: None
      def constructor(**kwargs):
        entered.set()
        self.assertTrue(release.wait(2))
        return runtime
      manager = self.manager(lifecycle, constructor)
      build = Thread(target=manager._check_instances)
      build.start()
      self.assertTrue(entered.wait(1))
      def abort():
        stop_started.set()
        lifecycle.admit(config("stop"), "oracle", True)
        stopped.set()
      stop = Thread(target=abort)
      stop.start()
      self.assertTrue(stop_started.wait(1))
      self.assertFalse(stopped.is_set())
      release.set()
      build.join(2)
      stop.join(2)
      self.assertTrue(stopped.is_set())
      lifecycle._observe("operation|deploy", lifecycle.records["operation|deploy"])
      self.assertEqual(lifecycle.records["operation|deploy"]["status"], "stopping")
      self.assertIs(lifecycle.plugins[("operation|deploy", "WORKER", "worker")], runtime)

  def test_startup_limits_source_modes_to_deeploy_pipelines(self):
    for operation, specs, pinned, expected in (
      (None, None, False, None),
      (None, {}, True, None),
      (None, {"job_id": "1"}, False, "following"),
      (None, {"job_id": "1", "job_app_type": "service"}, False, "service"),
      ("restore", None, False, "following"),
      ("deploy", None, True, "pinned"),
    ):
      with self.subTest(operation=operation, specs=specs, pinned=pinned), tempfile.TemporaryDirectory() as folder:
        lifecycle = DeploymentLifecycle(os.path.join(folder, "journal.json"), "node", source_modes=True)
        pipeline = config(attempt=operation or "deploy")
        if operation is None:
          pipeline.pop("DEEPLOY_OPERATION")
        pipeline["DEEPLOY_SPECS"] = specs
        group = pipeline["PLUGINS"][0]
        group["SIGNATURE"] = "CONTAINER_APP_RUNNER"
        upstream_config = group["INSTANCES"][0]
        upstream_config["PINNED_VERSION"] = pinned
        if operation:
          lifecycle.admit(pipeline, "oracle", True)
        runtime = plugin(ready=True)
        runtime.cfg_runs_only_on_supervisor_node = False
        runtime.start_thread = lambda: None
        manager = self.manager(lifecycle, lambda **kwargs: runtime)
        manager._dct_config_streams = {"app": pipeline}
        manager.get_current_jobs = lambda **kwargs: [
          (None, None, None, None, None, "app", group["SIGNATURE"], "worker", upstream_config)]
        self.assertEqual(manager._check_instances(), ["instance"])
        self.assertIs(manager._dct_current_instances["instance"], runtime)
        self.assertEqual(getattr(runtime, "_deployment_source_mode", None), expected)
        if operation:
          self.assertEqual(lifecycle.records["operation|" + operation]["instances"][0]["source_mode"], expected)

  def test_blocked_code_execution_plugins_are_never_constructed(self):
    from naeural_core import constants as ct
    for signature in ct.PLUGIN_SEARCH.BLOCKED_BIZ_PLUGINS + ["custom_exec_01"]:
      with self.subTest(signature=signature), tempfile.TemporaryDirectory() as folder:
        pipeline = config()
        pipeline["PLUGINS"][0]["SIGNATURE"] = signature
        lifecycle = DeploymentLifecycle(os.path.join(folder, "journal.json"), "node")
        lifecycle.admit(pipeline, "oracle", True)
        lookups, notifications = [], []
        manager = self.manager(lifecycle, lambda **kwargs: self.fail("blocked plugin constructed"))
        manager._dct_config_streams = {"app": pipeline}
        manager.get_current_jobs = lambda **kwargs: [(None, None, None, None, None, "app", signature, "worker", {})]
        manager._get_module_name_and_class = lambda **kwargs: lookups.append(kwargs)
        manager._create_notification = lambda **kwargs: notifications.append(kwargs)
        manager._check_instances()
        self.assertEqual(lookups, [])
        self.assertEqual(manager._dct_current_instances, {"instance": None})
        self.assertEqual(len(notifications), 1)
        record = lifecycle.records["operation|deploy"]
        self.assertEqual(record["status"], "failed")
        self.assertEqual(
          [instance["error"]["code"] for instance in record["instances"]],
          ["CONFIGURATION_REJECTED"],
        )

  def test_stale_filtered_job_cannot_construct_after_abort(self):
    with tempfile.TemporaryDirectory() as folder:
      lifecycle = DeploymentLifecycle(os.path.join(folder, "journal.json"), "node")
      lifecycle.admit(config("stop"), "oracle", True)
      calls = []
      manager = self.manager(lifecycle, lambda **kwargs: calls.append(kwargs))
      manager._check_instances()
      self.assertEqual(calls, [])
      self.assertEqual(manager._dct_current_instances, {})


class StrictPersistenceTest(unittest.TestCase):
  def test_atomic_cache_save_roundtrip_and_propagated_fsync_failure(self):
    import pickle
    from test_chain_store_hsync import _load_class_from_source
    source = Path(__file__).resolve().parents[2] / "utils" / "plugins_base" / "persistence_serialization_mixin.py"
    persistence = _load_class_from_source(
      source, "_PersistenceSerializationMixin",
      strip_text=("from naeural_core import constants as ct\n",),
    )
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "cache.pickle")
      persistence._save_cache_file_strict(path, {"sequence": 3})
      with open(path, "rb") as stream:
        self.assertEqual(pickle.load(stream), {"sequence": 3})
      with patch("os.fsync", side_effect=OSError("disk unavailable")):
        with self.assertRaises(OSError):
          persistence._save_cache_file_strict(path, {"sequence": 4})
      with open(path, "rb") as stream:
        self.assertEqual(pickle.load(stream), {"sequence": 3})

  def test_pickle_is_written_in_one_write(self):
    # Each write gives up the interpreter lock; pickle.dump would issue one per 64 KB frame.
    import pickle
    from test_chain_store_hsync import _load_class_from_source
    source = Path(__file__).resolve().parents[2] / "utils" / "plugins_base" / "persistence_serialization_mixin.py"
    persistence = _load_class_from_source(
      source, "_PersistenceSerializationMixin",
      strip_text=("from naeural_core import constants as ct\n",),
    )
    data = {f"key-{idx}": "x" * 100 for idx in range(10000)}  # about 1.2 MB: many pickle frames
    writes, real_fdopen = [], os.fdopen

    class _CountingStream:
      def __init__(self, stream):
        self.stream = stream

      def write(self, chunk):
        writes.append(len(chunk))
        return self.stream.write(chunk)

      def __getattr__(self, name):
        return getattr(self.stream, name)

      def __enter__(self):
        return self

      def __exit__(self, *exc):
        return self.stream.__exit__(*exc)

    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "cache.pickle")
      with patch("os.fdopen", side_effect=lambda *a, **k: _CountingStream(real_fdopen(*a, **k))):
        persistence._save_cache_file_strict(path, data)
      with open(path, "rb") as stream:
        written = stream.read()
    self.assertEqual(len(writes), 1)
    self.assertEqual(written, pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL))


class RefusedReportTest(unittest.TestCase):
  """A report write the node's own chain store refuses: reported once, and retried only when a retry can help."""

  def setUp(self):
    """
    Create a journal with one admitted attempt, written by a node whose address is "node".

    Returns
    -------
    None
    """
    # A folder for the journal, removed after the test.
    self.directory = tempfile.TemporaryDirectory()
    # Removed even when the test fails.
    self.addCleanup(self.directory.cleanup)
    # The journal file.
    self.path = os.path.join(self.directory.name, "journal.json")
    # The node, at the address its records are written under.
    self.lifecycle = DeploymentLifecycle(self.path, "node")
    # One attempt admitted from its coordinator.
    self.lifecycle.admit(config(), "oracle", True)
    # The storage keys the setter was called with, and the lines the lifecycle reported with their color.
    self.calls, self.lines = [], []

  def use(self, node_addr="node"):
    """
    Load the journal as a node with the given address would after a restart, and catch its report lines.

    Parameters
    ----------
    node_addr : str, optional
      The node's current address. Default "node", the address the journal's records carry.

    Returns
    -------
    None
    """
    # A new process: nothing is remembered about earlier refusals.
    self.lifecycle = DeploymentLifecycle(self.path, node_addr)
    # What start() sets from the business manager's log method.
    self.lifecycle._report = lambda text, color=None: self.lines.append((text, color))

  def passes(self, outcome, count=1):
    """
    Run publication passes whose every write ends with the given outcome word.

    Parameters
    ----------
    outcome : str
      The chain-store outcome word of each write; "changed" stores and confirms, any other word fails it.
    count : int, optional
      Number of passes. Default 1.

    Returns
    -------
    None
    """
    def setter(key, value, **kwargs):
      """
      Count the write and answer with the outcome under test, as the chain-store setter does.

      Parameters
      ----------
      key : str
        The storage key.
      value : dict
        The signed record.
      **kwargs
        The write's options; ``result_info`` receives the outcome word.

      Returns
      -------
      bool
        True only for the outcome "changed".
      """
      # One more write attempt, with the sequence it carried.
      self.calls.append(value["sequence"])
      # The word of the way out.
      kwargs["result_info"]["outcome"] = outcome
      # Stored and confirmed only for "changed".
      return outcome == "changed"
    # The passes, one after another as the reporter thread makes them.
    for _ in range(count):
      # One pass of the reporter thread, with a signer that stamps the record.
      self.lifecycle.publish(setter, lambda record, **kwargs: record.update(EE_SENDER="node", EE_SIGN="signature"))

  def sequence(self):
    """
    The sequence of the attempt as the journal file holds it.

    Returns
    -------
    int
      The persisted sequence.
    """
    # The journal as it is on disk.
    with open(self.path, encoding="utf-8") as stream:
      # The journal is one JSON mapping of records.
      return json.load(stream)["operation|deploy"]["sequence"]

  def test_a_refusal_no_sequence_can_clear_is_reported_once_and_not_written_again(self):
    """A record written under another node address is refused once: one red line, then no bump, save or write."""
    # The node now runs with another address than the one its journal record carries.
    self.use(node_addr="node-now")
    # Five passes of the reporter; the store refuses the record by the signed-record rule.
    self.passes("refused_versioned", count=5)
    # One write attempt in five passes; before, one per pass.
    self.assertEqual(len(self.calls), 1)
    # The sequence was bumped for that one attempt only, and the journal was not saved again.
    self.assertEqual(self.sequence(), self.calls[0])
    # One red line.
    self.assertEqual([color for _, color in self.lines], ['r'])
    # It names the attempt, the sequence, the reason word, that the address differs and that it is not retried.
    for part in ("operation|deploy", "sequence {}".format(self.calls[0]), "refused_versioned", "differs", "not retried"):
      # Each part is in the one line.
      self.assertIn(part, self.lines[0][0])
    # The coordinator stops the attempt: the snapshot changes.
    self.lifecycle.admit(config("stop"), "oracle", True)
    # Three more passes with the same refusal.
    self.passes("refused_versioned", count=3)
    # Exactly one new attempt for the changed snapshot, and one new line.
    self.assertEqual((len(self.calls), len(self.lines)), (2, 2))

  def test_a_token_or_readonly_refusal_is_not_written_again(self):
    """A record refused for the stored token or for another owner's readonly mark is final at the node's own address."""
    # The two words that say the stored record belongs to another writer.
    for outcome in ("refused_token", "refused_readonly"):
      # Each word is reported on its own.
      with self.subTest(outcome=outcome):
        # The node at the address of its record, in a new process.
        self.use()
        # No write counted yet for this word.
        self.calls.clear()
        # Four passes with this refusal.
        self.passes(outcome, count=4)
        # One write attempt.
        self.assertEqual(len(self.calls), 1)
        # Reported once, with the word, as final.
        self.assertTrue(outcome in self.lines[-1][0] and "not retried" in self.lines[-1][0])

  def test_a_refusal_a_higher_sequence_can_clear_keeps_its_retry_and_is_reported_once(self):
    """At the node's own address a signed-record refusal is retried every pass with a higher sequence; one red line."""
    # The node at the address of its record.
    self.use()
    # Four passes: the store holds this record at a higher sequence, which the bumps can catch up with.
    self.passes("refused_versioned", count=4)
    # One attempt per pass, each with the next sequence.
    self.assertEqual(self.calls, [self.calls[0] + step for step in range(4)])
    # One red line for the four refusals, which says the write is retried.
    self.assertEqual(len(self.lines), 1)
    # The address matches, and the retry goes on.
    self.assertTrue("matches" in self.lines[0][0] and "retried each pass" in self.lines[0][0])
    # The next pass catches up: the write is stored and confirmed.
    self.passes("changed")
    # Two more writes (the recent slot and the current snapshot), and nothing after that for this snapshot.
    written = len(self.calls)
    # A further pass.
    self.passes("changed")
    # Published: not written again.
    self.assertEqual(len(self.calls), written)

  def test_a_peer_refusal_keeps_its_retry_and_an_unconfirmed_write_is_not_reported(self):
    """A refusal by the peers is retried and reported once; a write that only went unconfirmed is retried silently."""
    # The node at the address of its record.
    self.use()
    # Three passes whose write the peers do not confirm.
    self.passes("unconfirmed", count=3)
    # Retried every pass, with no line: nothing was refused.
    self.assertEqual((len(self.calls), self.lines), (3, []))
    # Three passes whose write the peers refuse.
    self.passes("refused_by_peers", count=3)
    # Still retried every pass, and reported once.
    self.assertEqual((len(self.calls), len(self.lines)), (6, 1))

  def test_a_refusal_for_want_of_a_target_peer_keeps_its_retry_and_is_reported_once(self):
    """A write refused because it has no peer to go to is not one of the final refusals: retried every pass, one red line."""
    # The node at the address of its record.
    self.use()
    # Three passes whose write the store refuses for want of a target peer.
    self.passes("refused_no_peers", count=3)
    # One attempt per pass, and one line for the three refusals.
    self.assertEqual((len(self.calls), len(self.lines)), (3, 1))
    # The line names the word and says that the write is retried.
    self.assertTrue("refused_no_peers" in self.lines[0][0] and "retried each pass" in self.lines[0][0])

  def test_the_reporter_thread_writes_the_refusal_line_through_the_log_method_given_to_start(self):
    """Started with start(), the reporter thread reports a refused report in one red line and does not fail its pass."""
    # The stop event of the reporter, and the signal this test waits on.
    from threading import Event

    class QuickTick(Event):
      """
      The reporter's stop event with a tick of 10 ms in place of the reporter's one second.

      Parameters
      ----------
      None

      Returns
      -------
      None
      """

      def wait(self, timeout=None):
        """
        Wait 10 ms for the stop, whatever time the reporter asks for.

        Parameters
        ----------
        timeout : float, optional
          The time the caller asked for; not used. Default None.

        Returns
        -------
        bool
          Whether the stop was asked.
        """
        # The same wait, shortened.
        return Event.wait(self, 0.01)

    # The node now runs with another address than the one its journal record carries.
    self.lifecycle = DeploymentLifecycle(self.path, "node-now")
    # The reporter makes a pass every 10 ms.
    self.lifecycle._stop = QuickTick()
    # Set when the reporter begins its third pass; one entry per pass.
    third_pass, passes = Event(), []

    def setter(key, value, **kwargs):
      """
      Count the write and refuse it by the signed-record rule, as the chain store does for another node address.

      Parameters
      ----------
      key : str
        The storage key.
      value : dict
        The signed record.
      **kwargs
        The write's options; ``result_info`` receives the outcome word.

      Returns
      -------
      bool
        False: the write was refused.
      """
      # One more write attempt.
      self.calls.append(value["sequence"])
      # The word of the signed-record rule.
      kwargs["result_info"]["outcome"] = "refused_versioned"
      # Refused.
      return False

    def provider():
      """
      Count the reporter's pass and hand out the setter, as the shared memory does once the chain store is ready.

      Returns
      -------
      callable
        The refusing setter.
      """
      # One more pass.
      passes.append(1)
      if len(passes) == 3:
        # Two passes are complete.
        third_pass.set()
      # The setter of this pass.
      return setter

    # Started as the business manager starts it: a setter provider, a signer and its log method.
    self.lifecycle.start(provider, lambda record, **kwargs: record.update(EE_SENDER="node", EE_SIGN="signature"),
                         lambda text, color=None: self.lines.append((text, color)))
    # Stopped also when an assertion below fails.
    self.addCleanup(self.lifecycle.close)
    # The reporter reaches its third pass.
    self.assertTrue(third_pass.wait(10))
    # Stopped before its lines are read.
    self.lifecycle.close()
    # One write in all its passes: the refusal is final.
    self.assertEqual(len(self.calls), 1)
    # One red line and nothing else; without the log method the pass failed and wrote "publication failed".
    self.assertEqual([color for _, color in self.lines], ['r'])
    # It is the refusal line.
    self.assertTrue("refused_versioned" in self.lines[0][0] and "not retried" in self.lines[0][0])

  def test_a_refused_record_does_not_hold_back_another_record_s_end_of_reporting(self):
    """In one pass, one attempt is refused for good and another is confirmed ready: the second one's end is saved."""
    # A second attempt, of another app, on the same node.
    second = config()
    # Its own app name and operation.
    second["NAME"], second["DEEPLOY_OPERATION"]["operation_id"] = "app-two", "second"
    # Admitted from the same coordinator.
    self.lifecycle.admit(second, "oracle", True)
    # Its workload is ready.
    self.lifecycle.attach(second, "WORKER", "worker", plugin(ready=True))
    # The lifecycle's report lines.
    self.lifecycle._report = lambda text, color=None: self.lines.append((text, color))

    def setter(key, value, **kwargs):
      """
      Refuse the first attempt's record for its token and confirm the second attempt's.

      Parameters
      ----------
      key : str
        The storage key.
      value : dict
        The signed record.
      **kwargs
        The write's options; ``result_info`` receives the outcome word.

      Returns
      -------
      bool
        True for the second attempt's record.
      """
      # The second attempt is the one that is stored.
      stored = value["operation_id"] == "second"
      # The outcome word of each.
      kwargs["result_info"]["outcome"] = "changed" if stored else "refused_token"
      # The bool the real setter returns.
      return stored
    # One pass with both records pending.
    self.lifecycle.publish(setter, lambda record, **kwargs: record.update(EE_SENDER="node", EE_SIGN="signature"))
    # The journal as it is on disk.
    with open(self.path, encoding="utf-8") as stream:
      # The journal after the pass.
      saved = json.load(stream)
    # The confirmed ready attempt has its end of reporting persisted.
    self.assertTrue(saved["second|deploy"]["ready_reported"])
    # The refused one was reported.
    self.assertEqual(len(self.lines), 1)


if __name__ == "__main__":
  unittest.main()

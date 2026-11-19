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


class DeploymentLifecycleTest(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.path = os.path.join(self.directory.name, "journal.json")
    self.lifecycle = DeploymentLifecycle(self.path, "node")
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
    def setter(key, value, **kwargs):
      self.writes.append((key, deepcopy(value), kwargs))
      return succeed
    self.lifecycle.publish(setter, lambda record, **kwargs: record.update(EE_SENDER="node", EE_SIGN="signature"))
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

  def test_confirmed_ready_ends_reporting_across_restart(self):
    self.lifecycle.admit(config(), "oracle", True)
    self.lifecycle.attach(config(), "WORKER", "worker", plugin(ready=True))
    self.assertEqual(self.publish()["status"], "ready")
    self.assertTrue(all(kwargs["timeout"] == module.PUBLISH_TIMEOUT for _, _, kwargs in self.writes))
    self.assertNotIn("ready_reported", self.writes[-1][1])
    silent = lambda *args, **kwargs: self.fail("ready attempt reported again")
    self.lifecycle.plugins[("operation|deploy", "WORKER", "worker")] = plugin(alive=False)
    self.lifecycle.publish(silent, lambda *args, **kwargs: None)
    self.lifecycle = DeploymentLifecycle(self.path, "node")
    self.lifecycle.publish(silent, lambda *args, **kwargs: None)

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
    }, strip_text=("from naeural_core.business.deployment_lifecycle import OPERATION_KEY, SERVICE_KEY\n",))
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
                                    K_MODIFIED_BY_ID="MODIFIED_BY_ID", K_MODIFIED_BY_ADDR="MODIFIED_BY_ADDR"),
      COMMS=SimpleNamespace(COMM_RECV_MESSAGE=SimpleNamespace(K_VALIDATED="VALIDATED", K_SENDER_ADDR="EE_SENDER")),
      PAYLOAD_DATA=SimpleNamespace(NAME="NAME", INSTANCE_CONFIG="INSTANCE_CONFIG", SESSION_ID="SESSION_ID"),
      PLUGIN_INFO=SimpleNamespace(SIGNATURE="SIGNATURE", INSTANCE_ID="INSTANCE_ID"),
      STATUS_TYPE=SimpleNamespace(STATUS_EXCEPTION="error", STATUS_NORMAL="normal"),
      NOTIFICATION_CODES=SimpleNamespace(PIPELINE_FAILED="failed", PIPELINE_OK="ok"),
    )
    commands = _load_class_from_source(source, "ConfigCommandHandlers", extra_globals={
      "ct": constants, "OPERATION_KEY": "DEEPLOY_OPERATION", "SERVICE_KEY": "deeploy_lifecycle",
    }, strip_text=("from naeural_core import constants as ct\n", "from naeural_core.business.deployment_lifecycle import OPERATION_KEY, SERVICE_KEY\n"))
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


if __name__ == "__main__":
  unittest.main()

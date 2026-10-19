"""Offline tests of durable node admission, aborts and lifecycle observations."""
import collections
import importlib.util
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
    instance._deployment_stopped = True
    self.assertEqual(self.publish()["status"], "stopped")

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
    self.assertNotEqual(self.writes[0][0], self.writes[2][0])
    self.assertEqual(self.writes[1][0], self.writes[3][0])

  def test_abort_is_published_immediately_after_ready(self):
    self.lifecycle.admit(config(), "oracle", True)
    self.lifecycle.attach(config(), "WORKER", "worker", plugin(ready=True))
    ready = self.publish()
    self.assertEqual(ready["status"], "ready")
    self.lifecycle.admit(config("stop"), "oracle", True)
    stopping = self.publish()
    self.assertEqual(stopping["status"], "stopping")
    self.assertGreater(stopping["sequence"], ready["sequence"])

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
    observed = self.publish()
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

  def test_remote_retransmission_retries_failed_persistence(self):
    store = self.make_store()
    store.cacheapi_save_pickle = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full"))
    for _ in range(2):
      with self.assertRaises(ValueError):
        store._set_value("key", "value", owner="node", local_sync_storage_op=True)

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
    store = self.store()
    hkey = "DAUTH_JOB_SECRET_REVISIONS"
    marker_key, marker = _hset_record(hkey, "7|revision|revoked", {"job_id": "7", "revision": "revision"}, "oracle")
    payload_key, payload = _hset_record(hkey, "7|revision", {"secret": "old"}, "oracle")
    store.storage[marker_key] = marker
    store._ChainStoreBasePlugin__merge_hset_snapshot(hkey, {payload_key: payload})
    self.assertNotIn(payload_key, store.storage)
    self.assertEqual(store.storage[marker_key][store.CS_VALUE], marker[store.CS_VALUE])
    self.assertFalse(store._set_value(marker_key, None, owner="oracle", local_sync_storage_op=True))
    self.assertFalse(store._set_value(payload_key, {"secret": "old"}, owner="oracle", local_sync_storage_op=True))

  def test_snapshot_revocation_wins_regardless_of_field_order(self):
    store = self.store()
    hkey = "DAUTH_JOB_SECRET_REVISIONS"
    marker_key, marker = _hset_record(hkey, "7|revision|revoked", {"job_id": "7", "revision": "revision"}, "oracle")
    payload_key, payload = _hset_record(hkey, "7|revision", {"secret": "old"}, "oracle")
    store.storage[payload_key] = payload
    store._ChainStoreBasePlugin__merge_hset_snapshot(hkey, {payload_key: payload, marker_key: marker})
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

  def test_signed_node_cannot_preempt_another_nodes_field(self):
    store = self.store()
    key = _hset_key("DEEPLOY_NODE_OPERATIONS", "op|deploy|victim|0")
    value = {"operation_id": "op", "attempt": "deploy", "sequence": 8,
      "node_addr": "attacker", "EE_SENDER": "attacker", "EE_SIGN": "valid"}
    self.assertFalse(store._set_value(key, value, owner="victim", readonly=True, local_sync_storage_op=True))
    self.assertNotIn(key, store.storage)

  def test_revocation_retry_and_exact_erased_tombstone_are_idempotent(self):
    store = self.store()
    marker_key = _hset_key("DAUTH_JOB_SECRET_REVISIONS", "7|revision|revoked")
    marker = {"job_id": "7", "revision": "revision"}
    for _ in range(2):
      self.assertTrue(store._set_value(marker_key, marker, owner="oracle", local_sync_storage_op=True))
    payload_key = _hset_key("DAUTH_JOB_SECRET_REVISIONS", "7|revision")
    tombstone = dict(marker, revoked=True)
    self.assertTrue(store._set_value(payload_key, tombstone, owner="oracle", local_sync_storage_op=True))
    self.assertFalse(store._set_value(payload_key, dict(tombstone, secret="leak"), owner="oracle", local_sync_storage_op=True))

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
      CONFIG_STREAM=SimpleNamespace(NAME="NAME", INITIATOR_ID="INITIATOR_ID"),
      COMMS=SimpleNamespace(COMM_RECV_MESSAGE=SimpleNamespace(K_VALIDATED="VALIDATED", K_SENDER_ADDR="EE_SENDER")),
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


if __name__ == "__main__":
  unittest.main()

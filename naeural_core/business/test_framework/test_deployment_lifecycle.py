"""Offline tests of durable node admission, aborts and lifecycle observations."""
import base64
import collections
import gc
import importlib.util
import json
import os
import tempfile
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
    self.lifecycle = DeploymentLifecycle(self.path, "node")
    self.writes = []

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
      """
      # Every write of the test, with its options.
      self.writes.append((key, deepcopy(value), kwargs))
      # The word of the way out, which the real setter sets on every path: stored, or not confirmed by the peers.
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

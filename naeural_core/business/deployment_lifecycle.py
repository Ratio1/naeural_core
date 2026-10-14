"""Node-owned deployment observations and durable, attempt-scoped aborts.

The coordinator identity is the authenticated command sender. This module does
not elect coordinators or serialize competing oracles. Its journal only protects
one node against replay of a deployment attempt that it has already stopped.
"""
import base64
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from copy import deepcopy


OPERATION_KEY = "DEEPLOY_OPERATION"
SERVICE_KEY = "deeploy_lifecycle"
OBSERVATION_HKEY = "DEEPLOY_NODE_OPERATIONS"
ERRORS = {
  "PLUGIN_LOAD_FAILED": "Plugin code could not be loaded.",
  "PLUGIN_INIT_FAILED": "Plugin initialization failed.",
  "PLUGIN_EXECUTION_FAILED": "Plugin execution failed.",
  "PLUGIN_STOP_FAILED": "Workload shutdown could not be confirmed.",
  "CONFIGURATION_REJECTED": "Pipeline configuration was rejected.",
  "WORKLOAD_START_FAILED": "Workload startup failed.",
  "WORKLOAD_UNHEALTHY": "Workload readiness check failed.",
  "IMAGE_PULL_FAILED": "Container image could not be downloaded.",
  "SECRET_RESOLUTION_FAILED": "Protected configuration could not be resolved.",
}


class DeploymentLifecycle:
  """Track operation attempts independently of plugin execution threads.

  Parameters
  ----------
  path : str
      Private local journal path. Unreadable/corrupt journals fail closed.
  node_addr : str
      Blockchain address used for signed observations.
  """

  def __init__(self, path, node_addr):
    self.path = path
    self.node_addr = node_addr
    self.lock = threading.RLock()
    self.records = {}
    self.plugins = {}
    self._published = {}
    self._stop = threading.Event()
    self._thread = None
    self._cleanup = None
    if os.path.exists(path):
      with open(path, encoding="utf-8") as stream:
        self.records = json.load(stream)
      if not isinstance(self.records, dict):
        raise ValueError("Invalid deployment lifecycle journal")
      # A persisted readiness observation is not evidence after node restart.
      for record in self.records.values():
        if record["status"] not in ("stopping", "stopped", "failed"):
          record["status"] = "starting"
          for instance in record["instances"]:
            instance["status"] = "initializing"

  def _save(self):
    """Atomically persist the journal, propagating every durability failure."""
    folder = os.path.dirname(self.path)
    os.makedirs(folder, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".deeploy-", dir=folder)
    try:
      with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(self.records, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
      os.replace(temporary, self.path)
      directory = os.open(folder, os.O_RDONLY)
      try:
        os.fsync(directory)
      finally:
        os.close(directory)
    finally:
      if os.path.exists(temporary):
        os.unlink(temporary)

  @staticmethod
  def key(operation):
    """Return the identity of one deployment or restoration attempt."""
    return operation["operation_id"] + "|" + operation["attempt"]

  def admit(self, config, sender, validated, rejected=False):
    """Persist a command before configuration mutation or plugin construction.

    Parameters
    ----------
    config : dict
        Canonical pipeline configuration or scoped stop envelope.
    sender : str
        Authenticated transport sender, never a user-supplied owner field.
    validated : bool
        Transport authorization result.

    Returns
    -------
    bool
        Whether the command may be applied. Aborted starts return False.
    """
    operation = config[OPERATION_KEY]
    if not isinstance(operation, dict):
      raise ValueError("Invalid deployment operation")
    if not validated or operation.get("coordinator") != sender:
      raise ValueError("Deployment coordinator does not match authenticated sender")
    if operation.get("attempt") not in ("deploy", "restore", "retire"):
      raise ValueError("Invalid deployment attempt")
    if operation.get("action") not in ("start", "stop", "retire"):
      raise ValueError("Invalid deployment action")
    if (operation["action"] == "retire") != (operation["attempt"] == "retire"):
      raise ValueError("Retirement requires its own action and attempt")
    if not isinstance(operation.get("operation_id"), str) or not operation["operation_id"] or "|" in operation["operation_id"]:
      raise ValueError("Invalid deployment identity")
    if not isinstance(operation.get("revision"), int) or isinstance(operation["revision"], bool):
      raise ValueError("Invalid deployment revision")
    if not isinstance(operation.get("peers"), list) or any(not isinstance(peer, str) or not peer for peer in operation["peers"]):
      raise ValueError("Invalid deployment reporting peers")
    key = self.key(operation)
    with self.lock:
      current = self.records.get(key)
      if current and (current["coordinator"] != sender or current["app_id"] != config["NAME"] or current["revision"] != operation["revision"]):
        raise ValueError("Deployment attempt identity conflict")
      if current and operation["action"] == "start" and (current["aborted"] or not current.get("active", False)):
        return False
      if current is None:
        other_active = any(record.get("active", False) and record["app_id"] == config["NAME"] for record in self.records.values())
        if operation["action"] in ("start", "retire") and not rejected:
          for record in self.records.values():
            if record["app_id"] == config["NAME"]:
              record["active"] = False
        instances = [
          {"signature": plugin["SIGNATURE"], "instance_id": instance["INSTANCE_ID"], "status": "initializing", "error": None, "possibly_started": operation["action"] == "retire"}
          for plugin in config.get("PLUGINS", []) for instance in plugin.get("INSTANCES", [])
        ]
        current = {
          "operation_id": operation["operation_id"], "attempt": operation["attempt"],
          "revision": operation["revision"], "coordinator": sender,
          "node_addr": self.node_addr, "app_id": config["NAME"],
          "status": "accepted", "instances": instances, "sequence": 0,
          "updated_at": time.time(), "aborted": False,
          "active": not rejected and (operation["action"] in ("start", "retire") or not other_active),
          "peers": list(dict.fromkeys(operation["peers"] + [sender])),
        }
        self.records[key] = current
      if operation["action"] in ("stop", "retire") and not rejected:
        current["aborted"] = True
        if current["status"] != "stopped":
          current["status"] = "stopping"
      if rejected:
        current["status"] = "failed"
        current["error"] = {"code": "CONFIGURATION_REJECTED", "message": ERRORS["CONFIGURATION_REJECTED"]}
      self._save()
    return True

  def retire(self, name, current_config, operation, sender, validated):
    """Bind a retirement to the exact prior revision before stopping anything.

    Parameters
    ----------
    name : str
        Stable pipeline identity.
    current_config : dict
        Canonical local pipeline, or an empty mapping when absent.
    operation : dict
        Authenticated retire command metadata and prior revision precondition.
    sender : str
        Authenticated command sender.
    validated : bool
        Transport authorization result.

    Returns
    -------
    bool
        Whether removal of the current canonical configuration is authorized.
    """
    specs = current_config.get("DEEPLOY_SPECS", {})
    previous = current_config.get(OPERATION_KEY)
    mismatch = current_config and (
      str(specs.get("job_id")) != str(operation.get("job_id"))
      or specs.get("revision", 0) != operation["revision"]
      or (previous or {}).get("operation_id") != operation.get("expected_previous_operation_id")
    )
    bound = deepcopy(current_config) if current_config else {"NAME": name}
    bound[OPERATION_KEY] = operation
    with self.lock:
      if mismatch:
        # Publish the failed precondition without taking ownership of or
        # deleting the newer workload which caused the mismatch.
        bound["PLUGINS"] = []
        self.admit(bound, sender, validated, rejected=True)
        return False
      if not current_config and self.key(operation) not in self.records:
        # Configuration removal can precede workload shutdown. Recover its
        # identities from the durable journal instead of acknowledging absence.
        active = next((record for record in self.records.values()
                       if record.get("active", False) and record["app_id"] == name), None)
        if active is not None:
          if active["operation_id"] != operation.get("expected_previous_operation_id") or active["revision"] != operation["revision"]:
            self.admit(bound, sender, validated, rejected=True)
            return False
          previous = active
          bound["PLUGINS"] = [{"SIGNATURE": instance["signature"],
                               "INSTANCES": [{"INSTANCE_ID": instance["instance_id"]}]}
                              for instance in active["instances"]]
      self.admit(bound, sender, validated)
      if previous:
        previous_key = self.key(previous)
        prior_instances = {(item["signature"], item["instance_id"]): item
                           for item in self.records.get(previous_key, {}).get("instances", [])}
        for instance in self.records[self.key(operation)]["instances"]:
          prior = prior_instances.get((instance["signature"], instance["instance_id"]), {})
          if prior.get("runtime_artifacts"):
            instance["runtime_artifacts"] = deepcopy(prior["runtime_artifacts"])
        if previous_key not in self.records:
          tombstone = deepcopy(self.records[self.key(operation)])
          tombstone.update(operation_id=previous["operation_id"], attempt=previous["attempt"],
                           revision=previous["revision"], coordinator=previous["coordinator"])
          self.records[previous_key] = tombstone
        self.records[previous_key]["aborted"] = True
        self.records[previous_key]["active"] = False
      self._save()
    return True

  def allowed(self, config):
    """Reject persisted configurations whose attempt was already aborted."""
    operation = config.get(OPERATION_KEY)
    if operation is None:
      return True
    with self.lock:
      record = self.records.get(self.key(operation))
      return record is not None and record.get("active", False) and not record["aborted"]

  @staticmethod
  def _runtime_artifacts(plugin):
    """Read cached workload bindings, retaining only safe exact identifiers."""
    getter = getattr(plugin, "get_deployment_runtime_artifacts", None)
    if not callable(getter):
      return {}
    try:
      artifacts = getter()
      if not isinstance(artifacts, dict):
        return {}
      result = {}
      image_id = artifacts.get("image_id")
      commit = artifacts.get("git_commit")
      if isinstance(image_id, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        result["image_id"] = image_id
      if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit):
        result["git_commit"] = commit
      return result
    except Exception:
      return {}

  def attach(self, config, signature, instance_id, plugin):
    """Associate a runtime instance with the accepted attempt for observation."""
    operation = config.get(OPERATION_KEY)
    if operation is None:
      return
    with self.lock:
      key = self.key(operation)
      self.plugins[(key, signature, instance_id)] = plugin
      for instance in self.records[key]["instances"]:
        if (instance["signature"], instance["instance_id"]) == (signature, instance_id):
          instance["possibly_started"] = True
          artifacts = self._runtime_artifacts(plugin)
          if artifacts:
            instance["runtime_artifacts"] = artifacts
      self._save()

  def fail(self, config, signature=None, instance_id=None, code="PLUGIN_INIT_FAILED"):
    """Record a safe lifecycle failure without copying exception/secret text."""
    operation = config.get(OPERATION_KEY)
    if operation is None:
      return
    with self.lock:
      record = self.records.get(self.key(operation))
      if record is None or record["aborted"]:
        return
      record["status"] = "failed"
      for instance in record["instances"]:
        if signature is None or (instance["signature"], instance["instance_id"]) == (signature, instance_id):
          instance["status"] = "failed"
          instance["error"] = {"code": code, "message": ERRORS[code]}
          if code in ("PLUGIN_LOAD_FAILED", "CONFIGURATION_REJECTED"):
            instance["possibly_started"] = False
      self._save()

  def _observe(self, key, record):
    """Aggregate plugin evidence without executing plugin workload methods."""
    for instance in record["instances"]:
      plugin = self.plugins.get((key, instance["signature"], instance["instance_id"]))
      artifacts = self._runtime_artifacts(plugin)
      if artifacts:
        instance["runtime_artifacts"] = artifacts
      if plugin is None:
        if record["aborted"]:
          # No constructor succeeded in this process. A restart can leave an
          # external workload alive, so only never-started instances qualify.
          if not instance.get("possibly_started", False):
            instance["status"] = "stopped"
          elif record.get("active", False) and self._cleanup is not None:
            # Hold the admission lock across scoped orphan cleanup: a new
            # attempt with the same deterministic workload name cannot be
            # admitted while the old attempt is being reaped.
            if self._cleanup(record["app_id"], instance["signature"], instance["instance_id"]):
              instance["status"] = "stopped"
        continue
      thread = getattr(plugin, "thread", None)
      thread_dead = thread is not None and not thread.is_alive()
      if record["aborted"]:
        stopped = getattr(plugin, "_deployment_stopped", False)
        if not getattr(plugin, "_deployment_external_workload", False):
          stopped = getattr(plugin, "_deployment_cleanup_complete", False)
        if thread_dead and not stopped and record.get("active", False) and self._cleanup is not None:
          stopped = self._cleanup(record["app_id"], instance["signature"], instance["instance_id"])
          if stopped:
            # BusinessManager's replacement barrier reads this same evidence.
            plugin._deployment_stopped = True
        instance["status"] = "stopped" if thread_dead and stopped else "stopping"
        continue
      if instance["status"] == "failed":
        continue
      failure = getattr(plugin, "_deployment_error_code", None)
      if failure or thread_dead:
        failure = failure if failure in ERRORS else "PLUGIN_EXECUTION_FAILED"
        instance["status"] = "failed"
        instance["error"] = {"code": failure, "message": ERRORS[failure]}
      elif getattr(plugin, "_deployment_status", None) == "failed":
        instance["status"] = "failed"
        instance["error"] = {"code": "WORKLOAD_START_FAILED", "message": ERRORS["WORKLOAD_START_FAILED"]}
      elif plugin.is_plugin_ready():
        instance["status"] = "ready"
      else:
        instance["status"] = getattr(plugin, "_deployment_status", "starting")
    statuses = [instance["status"] for instance in record["instances"]]
    if record["aborted"]:
      record["status"] = "stopped" if all(status == "stopped" for status in statuses) else "stopping"
    elif record["status"] != "failed":
      record["status"] = "failed" if "failed" in statuses else "ready" if statuses and all(status == "ready" for status in statuses) else "starting"

  def publish(self, setter, signer):
    """Persist, sign and retry node snapshots outside the main/plugin loops.

    Parameters
    ----------
    setter : callable
        CStore runtime setter; bounded waits and explicit peers are supplied.
    signer : callable
        Blockchain signing function accepting add_data and use_digest.
    """
    with self.lock:
      pending = []
      for key, record in self.records.items():
        # Do not refresh timestamps for historical readiness without observing
        # that workload. Only a rejected command needs an inactive failure report.
        if not record.get("active", False) and "error" not in record:
          continue
        if record.get("active", False):
          self._observe(key, record)
        fingerprint = {name: value for name, value in record.items() if name not in ("sequence", "updated_at")}
        last_time, last_value = self._published.get(key, (0, None))
        if last_value == fingerprint and (time.time() - last_time < 10 or record["status"] in ("stopped", "failed") or not record.get("active", False)):
          continue
        record["sequence"] += 1
        record["updated_at"] = time.time()
        pending.append((key, deepcopy(record), deepcopy(fingerprint)))
      if pending:
        self._save()
    for key, record, fingerprint in pending:
      peers = record.pop("peers")
      record.pop("aborted")
      record.pop("active")
      for instance in record["instances"]:
        instance.pop("possibly_started", None)
      signer(record, add_data=True, use_digest=True)
      field = key + "|" + self.node_addr
      prefix = "hs:" + hashlib.sha256(OBSERVATION_HKEY.encode()).hexdigest()[:10] + ":"
      success = True
      for field_name in (field + "|" + str(record["sequence"] % 8), field):
        storage_key = prefix + base64.urlsafe_b64encode(field_name.encode()).decode().rstrip("=")
        success = setter(storage_key, record, owner=self.node_addr, readonly=True,
                         peers=peers, include_default_peers=False, timeout=1, max_retries=0) and success
      if success:
        self._published[key] = (time.time(), fingerprint)

  def start(self, setter_provider, signer, report_error, cleanup=None):
    """Start an independent bounded publication worker once CStore is ready."""
    if self._thread is not None:
      return
    self._cleanup = cleanup
    def run():
      last_error_report = 0
      while not self._stop.wait(1):
        try:
          setter = setter_provider()
          if setter is not None:
            self.publish(setter, signer)
        except Exception:
          if time.time() - last_error_report >= 60:
            report_error("Deployment status publication failed; retrying.")
            last_error_report = time.time()
    self._thread = threading.Thread(target=run, name="deeploy_node_reporter", daemon=True)
    self._thread.start()

  def close(self):
    """Stop publication without discarding pending observations or tombstones."""
    self._stop.set()
    if self._thread is not None:
      self._thread.join(timeout=3)

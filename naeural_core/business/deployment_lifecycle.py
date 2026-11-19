"""Node-owned deployment observations and durable, attempt-scoped aborts.

The coordinator identity is the authenticated command sender. This module does
not elect coordinators or serialize competing oracles. Its journal only protects
one node against replay of a deployment attempt that it has already stopped.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import tempfile
import threading
import time
import uuid
from copy import deepcopy


OPERATION_KEY = "DEEPLOY_OPERATION"
SERVICE_KEY = "deeploy_lifecycle"
OBSERVATION_HKEY = "DEEPLOY_NODE_OPERATIONS"
# The coordinator issues 110-second challenges. Allow a worker's clock to lag by up to 70 seconds.
REPAIR_MAX_FUTURE_SECONDS = 180
REPAIR_INSPECTION_INTERVAL = 5
# Seconds a report write waits for peer confirmation before the next pass retries it.
PUBLISH_TIMEOUT = 15
# Shared by lifecycle objects in this interpreter; recreating a journal reader
# is not evidence that the process owning native plugin threads has exited.
PROCESS_ID = uuid.uuid4().hex
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


def repair_config_fingerprint(config):
  """Fingerprint the private canonical configuration checked by both repair participants.

  Object keys are ordered for serialization. All configured values and array
  positions survive, apart from worker transport and reporting metadata.

  Parameters
  ----------
  config : dict
      Legacy canonical worker configuration.

  Returns
  -------
  str
      Local-only SHA-256 hex. Encrypt for the intended worker before transport.

  Examples
  --------
  >>> repair_config_fingerprint({"PLUGINS": []}) == repair_config_fingerprint({"TIME": 42, "PLUGINS": []})
  True
  """
  canonical = deepcopy(config)  # Normalization must never alter the worker or future deployment.
  for field in ("TIME", "SESSION_ID", "LAST_UPDATE_TIME", "plugins", "INITIATOR_ID", "INITIATOR_ADDR",
                "MODIFIED_BY_ID", "MODIFIED_BY_ADDR", "VALIDATED"):
    canonical.pop(field, None)  # These fields are stamped by the worker or transport.
  if canonical.get(OPERATION_KEY) is None:
    canonical.pop(OPERATION_KEY, None)  # Preserve any non-null metadata, including empty containers.
  for plugin in canonical["PLUGINS"]:
    for instance in plugin["INSTANCES"]:
      instance.pop("CHAINSTORE_RESPONSE_KEY", None)  # This callback key is generated per command.
      if instance.get("DEEPLOY_RUNTIME_ARTIFACTS") is None:
        instance.pop("DEEPLOY_RUNTIME_ARTIFACTS", None)  # Populated artifact records remain significant.
  encoded = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
  return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _matches_repair_fingerprint(config, expected):
  """Check a local pipeline against the decrypted repair fingerprint.

  Parameters
  ----------
  config : dict
      Current local pipeline.
  expected : str
      Validated configuration digest from the scoped coordinator command.

  Returns
  -------
  bool
      Whether the local pipeline can be encoded and matches the digest.

  Examples
  --------
  >>> _matches_repair_fingerprint({}, "a" * 64)
  False
  """
  try:
    return hmac.compare_digest(expected, repair_config_fingerprint(config))
  except (TypeError, ValueError, KeyError, AttributeError):
    return False  # A stream can become incomplete during configuration replacement.


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
    self.process_id = PROCESS_ID
    self.lock = threading.RLock()
    self.records = {}
    self._legacy_repair_aborts = {}  # Index only versioned conditional retirements for fast legacy startup checks.
    self.plugins = {}
    self._published = {}
    self._last_repair_inspection_by_job = {}  # A slow inspection only throttles another probe of the same job.
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
        if (record.get("parent_operation_id") or record.get("confirm_prior_stop") is True) and "job_id" in record:
          self._legacy_repair_aborts.setdefault(record["app_id"], set()).add((str(record["job_id"]), 0))
        if record["attempt"] != "inspect" and record["status"] not in ("stopping", "stopped", "failed"):
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

  def admit(self, config, sender, validated, rejected=False, repair_evidence=None):
    """Persist a command before configuration mutation or plugin construction.

    Parameters
    ----------
    config : dict
        Canonical pipeline configuration or scoped stop envelope.
    sender : str
        Authenticated transport sender, never a user-supplied owner field.
    validated : bool
        Transport authorization result.
    rejected : bool, optional
      Record a failed admission while retaining any previously active workload.
    repair_evidence : dict, optional
      Worker-verified evidence for a conditional retirement.

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
    repair_start = operation.get("action") == "start_repaired_worker_v1"
    if repair_start:
      operation = dict(operation, action="start")  # Give a verified repair the regular tracked runtime lifecycle.
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
      if repair_start:
        parent = operation.get("parent_operation_id")
        job_id = operation.get("job_id")
        specs = config.get("DEEPLOY_SPECS") or {}
        if (not isinstance(parent, str) or not parent or "|" in parent or parent == operation["operation_id"]
            or type(job_id) is not int or str(specs.get("job_id")) != str(job_id)
            or (current and (current.get("parent_operation_id") != parent or current.get("job_id") != job_id))
            or specs.get("revision") != operation["revision"]
            or specs.get("secret_revision") != operation["operation_id"] + ":repair_v1:" + operation["attempt"]):
          return False  # Unversioned or differently scoped credentials cannot start a repaired workload.
        proof = self.records.get(operation["operation_id"] + "|retire") or self.records.get(parent + "|retire") or {}
        instances = [(p["SIGNATURE"], i["INSTANCE_ID"]) for p in config.get("PLUGINS", []) for i in p["INSTANCES"]]
        if (len(instances) != 1 or instances[0][0] != "WORKER_APP_RUNNER"
            or self._repair_stop_commit(proof, config["NAME"], job_id, instances[0][1], parent, sender) is None):
          return False  # Require this worker's own matching prior stop on either side of the repair.
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
        if repair_evidence is not None:
          # Fsync scoped proof and Git binding in the same journal write as the abort, including after a crash.
          current.update({field: repair_evidence[field] for field in (
            "parent_operation_id", "challenge", "order_digest", "config_seal_hash", "job_id",
          )})
          current["instances"][0]["runtime_artifacts"] = {"git_commit": repair_evidence["git_commit"]}
        if repair_start:
          current.update(parent_operation_id=parent, job_id=job_id)  # The already-stopped worker also fences legacy replay.
        self.records[key] = current
      if operation["action"] in ("stop", "retire") and not rejected:
        current["aborted"] = True
        if current["status"] != "stopped":
          current["status"] = "stopping"
      if rejected:
        current["status"] = "failed"
        current["error"] = {"code": "CONFIGURATION_REJECTED", "message": ERRORS["CONFIGURATION_REJECTED"]}
      self._save()
      if repair_evidence is not None or repair_start:
        # Preserve the tombstone when a tracked deployment takes over, including before its config-file write.
        self._legacy_repair_aborts.setdefault(current["app_id"], set()).add((str(current["job_id"]), 0))
    return True

  def _repair_stop_commit(self, proof, name, job_id, instance_id, parent, sender):
    """Read the exact local STOP shared by repair preflight and tracked start.

    Parameters
    ----------
    proof : dict
        This worker's durable retirement record.
    name, instance_id, parent, sender : str
        Expected app, worker instance, prior operation and authenticated oracle.
    job_id : int
        Customer job in the signed command.

    Returns
    -------
    str or None
        The prior worker Git commit when every recorded scope matches.

    Examples
    --------
    >>> service._repair_stop_commit({}, "app", 31, "w", "parent", "oracle") is None
    True
    """
    items = proof.get("instances") or []
    # Older direct parent STOPs have no job ID. New conditional STOPs must name it explicitly.
    scope_matches = (type(proof["job_id"]) is int and proof["job_id"] == job_id) if "job_id" in proof else (
      proof.get("operation_id") == parent and "parent_operation_id" not in proof
    )
    if (proof.get("status") != "stopped" or proof.get("app_id") != name or proof.get("revision") != 0
        or proof.get("node_addr") != self.node_addr or proof.get("coordinator") != sender
        or proof.get("parent_operation_id", proof.get("operation_id")) != parent or not scope_matches
        or len(items) != 1 or items[0].get("signature") != "WORKER_APP_RUNNER"
        or items[0].get("instance_id") != instance_id or items[0].get("status") != "stopped"):
      return None  # Only this worker's own confirmed prior Git instance can authorize a start.
    commit = (items[0].get("runtime_artifacts") or {}).get("git_commit")
    return commit if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit) else None

  def _save_worker_inspection(self, name, operation, commit, **binding):
    """Queue a node-signed, single-challenge repair observation for replication.

    Parameters
    ----------
    name, commit : str
        Local pipeline and verified Git revision.
    operation, binding : dict
        Authenticated request and safe evidence fields for this inspection.

    Returns
    -------
    None
        Persists the observation for the reporting thread.

    Examples
    --------
    >>> service._save_worker_inspection(name, operation, commit, confirm_prior_stop=True)  # doctest: +SKIP
    """
    key = operation["operation_id"] + "|inspect"
    previous = self.records.get(key)
    self.records[key] = dict(operation_id=operation["operation_id"], attempt="inspect", app_id=name,
      job_id=operation["job_id"], revision=operation["revision"], coordinator=operation["coordinator"], node_addr=self.node_addr,
      challenge=operation["challenge"], expires_at=operation["expires_at"], sequence=(previous or {}).get("sequence", 0),
      updated_at=time.time(), peers=[operation["coordinator"]], aborted=False, active=False, status="observed",
      instances=[dict(signature="WORKER_APP_RUNNER", instance_id=operation["instance_id"], status="observed",
                      runtime_artifacts={"git_commit": commit})], **binding)
    try:
      self._save()  # Signing and CStore publication run outside the command handler.
    except OSError:
      # A failed journal write must not leave an observation available for publication.
      if previous is None:
        self.records.pop(key, None)
      else:
        self.records[key] = previous
      raise
    if binding.get("confirm_prior_stop") is True:
      # The exact stopped legacy instance must remain fenced while the coordinator prepares its start.
      self._legacy_repair_aborts.setdefault(name, set()).add((str(operation["job_id"]), 0))

  def check_worker_repair(self, name, get_current, operation, sender, validated, blockchain, business, retire_current):
    """Inspect a legacy worker, or check the same live evidence before retirement.

    Verify the runtime without holding the lifecycle lock during Docker or
    registry requests. Recheck the journal and configuration after the probe
    and retire only while the final checks and config change are under the lock.

    Parameters
    ----------
    name, sender : str
      Pipeline name and authenticated transport identity.
    get_current, retire_current : callable
      Read the canonical pipeline and apply a verified retirement respectively.
    operation : dict
      Authenticated coordinator command with the repair scope.
    validated : bool
      Transport signature verification result.
    blockchain, business : object
      Local registry reader and live plugin registry.

    Returns
    -------
    None
      Queues signed observations and invokes retirement only on exact evidence.

    Examples
    --------
    >>> service.check_worker_repair(name, get_current, command, sender, True, bc, manager, retire_current)  # doctest: +SKIP
    """
    inspect = operation.get("action") == "inspect_worker_v1"
    if validated is not True or not isinstance(sender, str) or sender != operation.get("coordinator"):
      return None  # Unauthenticated callers may neither observe nor retire a worker.
    if operation.get("node_addr") != self.node_addr or operation.get("attempt") != ("inspect" if inspect else "retire"):
      return None  # A forwarded command cannot target a different recipient or attempt.
    prefix = hashlib.sha256(sender.encode()).hexdigest()[:16] + "-"
    operation_id = operation.get("operation_id")
    parent = operation_id if inspect else operation.get("parent_operation_id")
    if any(not isinstance(value, str) or not re.fullmatch(prefix + r"[0-9a-f]{32}", value)
           for value in (parent, operation_id)):
      return None  # Each operation ID belongs to this sender, including the parent.
    if not inspect and parent == operation_id:
      return None  # A conditional successor cannot rewrite the blocked parent.
    challenge, expires = operation.get("challenge"), operation.get("expires_at")
    if not isinstance(challenge, str) or not re.fullmatch(r"[0-9a-f]{32}", challenge):
      return None
    if type(expires) is not int or not 0 < expires - time.time() <= REPAIR_MAX_FUTURE_SECONDS:
      return None  # The future bound accommodates clock skew and an expired request never acts locally.
    if type(operation.get("revision")) is not int or operation["revision"] != 0:
      return None  # This bounded repair handles untracked workers only.
    instance_id = operation.get("instance_id")
    if (not isinstance(instance_id, str) or not instance_id or type(operation.get("job_id")) is not int
        or business is None or operation.get("expected_previous_operation_id") is not None):
      return None
    # These registry and crypto calls are read-only and do not need the journal lock.
    try:
      member = blockchain.node_address_to_eth_address(sender).lower()
      if member not in {addr.lower() for addr in blockchain.get_eth_oracles() or []}:
        return None  # A regular whitelisted node cannot inspect or retire another workload.
      confirm_stop = operation.get("confirm_prior_stop") is True
      if not confirm_stop:
        seal = operation.get("config_seal")
        if not isinstance(seal, str) or not 1 <= len(seal) <= 2048 or not seal.isascii():
          return None  # Accept only bounded SDK ciphertexts.
        seal_hash = hashlib.sha256(seal.encode("ascii")).hexdigest()
        expected = blockchain.decrypt_str(seal, sender)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
          return None
    except Exception:
      return None  # Missing live registry or invalid ciphertext cannot authorize a repair.

    def workers_for_pipeline():
      # Snapshot the manager's instance map before matching this pipeline.
      return [p for p in list(business.current_instances.values()) if p is not None and p.get_stream_id() == name]

    def worker_is_running(worker):
      thread = getattr(worker, "thread", None)
      return thread is not None and thread.is_alive()

    inspection_key = parent + "|inspect"
    retire_key = operation_id + "|retire"
    job_id = operation["job_id"]
    with self.lock:
      current = get_current()
      observed = self.records.get(inspection_key)
      if current.get(OPERATION_KEY) or time.time() >= expires or (inspect and observed and expires <= observed["expires_at"]):
        return None  # Discard superseded challenges and keep tracked workers out of repair preflight.
      workers = workers_for_pipeline()
      if confirm_stop:
        if not inspect or current or any(getattr(p, "thread", None) is None or worker_is_running(p) for p in workers) or any(
            r["app_id"] == name and r.get("active") and not r["aborted"] for r in self.records.values()):
          return None  # A past stop cannot attest that this worker is still vacant.
        proof = self.records.get(parent + "|retire", {})
        commit = self._repair_stop_commit(proof, name, job_id, instance_id, parent, sender)
        if commit:
          self._save_worker_inspection(name, operation, commit, confirm_prior_stop=True)
        return None  # The previously stopped worker needs no Docker inspection or config change.
      specs = current.get("DEEPLOY_SPECS", {})
      if (not isinstance(specs, dict) or current.get("NAME") != name
          or str(specs.get("job_id")) != str(job_id) or type(specs.get("revision", 0)) is not int
          or specs.get("revision", 0) != operation["revision"]):
        return None  # The exact prior job and revision must still occupy the named pipeline.
      plugins = current.get("PLUGINS", [])
      if (not isinstance(plugins, list) or len(plugins) != 1
          or not isinstance(plugins[0], dict) or plugins[0].get("SIGNATURE") != "WORKER_APP_RUNNER"
          or not isinstance(plugins[0].get("INSTANCES"), list) or len(plugins[0]["INSTANCES"]) != 1):
        return None  # Multiple plugins or instances require a different proof.
      instance = plugins[0]["INSTANCES"][0]
      if not isinstance(instance, dict) or instance.get("INSTANCE_ID") != instance_id or len(workers) != 1:
        return None
      worker = workers[0]
      if worker.get_signature() != "WORKER_APP_RUNNER" or worker.get_instance_id() != instance_id or not worker_is_running(worker):
        return None  # A stale BusinessManager entry cannot attest to a live worker.
      lists = [specs.get("current_target_nodes"), instance.get("BUILD_AND_RUN_COMMANDS"), instance.get("CHAINSTORE_PEERS")]
      if any(not isinstance(items, list) or not items or not all(isinstance(v, str) and v for v in items) for items in lists):
        return None  # Strictly limit the digest to the three documented ordered string lists.
      if worker.cfg_build_and_run_commands != lists[1] or worker.cfg_chainstore_peers != lists[2]:
        return None  # The persisted pipeline and actual runner must agree.
      if not _matches_repair_fingerprint(current, expected):
        return None  # Compare all private workload settings locally.
      encoded = json.dumps(lists, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
      digest = hashlib.sha256(encoded).hexdigest()
      prior_retire = self.records.get(retire_key)
      if inspect:
        now = time.monotonic()
        self._last_repair_inspection_by_job = {
          job: last for job, last in self._last_repair_inspection_by_job.items() if now - last < REPAIR_INSPECTION_INTERVAL
        }
        if job_id in self._last_repair_inspection_by_job:
          return None  # A broken or slow runtime must not receive repeated probes of the same job.
        self._last_repair_inspection_by_job[job_id] = now
      else:
        if prior_retire is not None and (prior_retire.get("status") == "stopped"
            or prior_retire.get("parent_operation_id") != parent or prior_retire.get("challenge") != challenge):
          return None  # A late retry cannot re-adopt a workload after the original receipt.
        if not observed or (observed.get("challenge"), observed.get("expires_at"), observed.get("order_digest"),
                             observed.get("config_seal_hash"), observed.get("app_id"), observed.get("job_id"),
                             observed["instances"][0]["instance_id"]) != (challenge, expires, digest, seal_hash, name, job_id, instance_id):
          return None  # Conditional retirement needs the same observation in the journal.
        if digest != operation.get("order_digest"):
          return None
      container = getattr(worker, "container", None)  # Snapshot local runtime identity before releasing the lock.

    try:
      commit = worker.read_deployment_git_commit()  # The bounded Docker read does not hold the journal lock.
    except Exception:
      return None
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
      return None
    with self.lock:
      workers = workers_for_pipeline()
      if (time.time() >= expires or get_current() is not current or len(workers) != 1 or workers[0] is not worker
          or getattr(worker, "container", None) is not container or not worker_is_running(worker)
          or worker.cfg_build_and_run_commands != lists[1] or worker.cfg_chainstore_peers != lists[2]
          or not _matches_repair_fingerprint(current, expected)
          or self.records.get(inspection_key) is not observed):
        return None  # A probe racing reconfiguration, replacement or a fresh challenge must not act.
      if inspect:
        self._save_worker_inspection(name, operation, commit, order_digest=digest, config_seal_hash=seal_hash)
        return None
      if (self.records.get(retire_key) is not prior_retire or (prior_retire and prior_retire["status"] == "stopped")
          or commit != operation.get("git_commit") or commit != observed["instances"][0]["runtime_artifacts"]["git_commit"]):
        return None  # Preserve any newer stop evidence and leave a changed Git workload running.
      evidence = {"parent_operation_id": parent, "challenge": challenge, "order_digest": digest, "config_seal_hash": seal_hash,
                  "git_commit": commit, "job_id": job_id}
      retire_current(current, dict(operation, action="retire"), evidence)

  def retire(self, name, current_config, operation, sender, validated, repair_evidence=None):
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
      self.admit(bound, sender, validated, repair_evidence=repair_evidence)
      if previous:
        previous_key = self.key(previous)
        prior_instances = {(item["signature"], item["instance_id"]): item
                           for item in self.records.get(previous_key, {}).get("instances", [])}
        for instance in self.records[self.key(operation)]["instances"]:
          prior = prior_instances.get((instance["signature"], instance["instance_id"]), {})
          for field in ("process_id", "process_bound"):
            if field in prior:
              instance[field] = prior[field]
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
      retired = self._legacy_repair_aborts.get(config.get("NAME"))
      if retired is None:
        return True  # The common path for untracked jobs avoids the lifecycle lock entirely.
      with self.lock:
        specs = config.get("DEEPLOY_SPECS") or {}
        return (str(specs.get("job_id")), specs.get("revision", 0)) not in retired
    with self.lock:
      record = self.records.get(self.key(operation))
      return record is not None and record.get("active", False) and not record["aborted"]

  def allows_untracked_update(self, delta, current):
    """Check a whole legacy pipeline before it can merge into a repaired worker.

    Parameters
    ----------
    delta, current : dict
        Incoming untracked command and its current local pipeline.

    Returns
    -------
    bool
        Whether the unversioned command may reach ordinary config handling.

    Examples
    --------
    >>> service.allows_untracked_update({"NAME": "unrelated", "PLUGINS": []}, {})
    True
    """
    retired = self._legacy_repair_aborts.get(delta.get("NAME"))
    if retired is None:
      return True  # Preserve ordinary pipelines and partial runtime commands.
    with self.lock:
      incoming = (delta.get("DEEPLOY_SPECS") or {}).get("job_id")
      existing = (current.get("DEEPLOY_SPECS") or {}).get("job_id")
      # An old payload can omit its specs or inherit the tracked operation via a delta merge.
      return incoming is not None and (str(incoming), 0) not in retired and (str(existing), 0) not in retired

  def allows_instance_update(self, current):
    """Check the current target of an instance update after a repair.

    Instance deltas use the pipeline and instance names to address the local target.

    Parameters
    ----------
    current : dict
        The existing local pipeline configuration.

    Returns
    -------
    bool
        Whether the target accepts ordinary instance updates.

    Examples
    --------
    >>> service.allows_instance_update({"NAME": "unrelated"})
    True
    """
    # Ordinary targets retain their existing handling.
    if current.get("NAME") not in self._legacy_repair_aborts:
      return True
    # A stream with a repair fence only accepts instance deltas on an active tracked attempt.
    return current.get(OPERATION_KEY) is not None and self.allowed(current)

  @staticmethod
  def _runtime_artifacts(plugin):
    """
    Read the workload's cached runtime binding and keep only its exact, safe identifiers.

    A containerized plugin exposes what it actually runs through `get_deployment_runtime_artifacts`.
    The node signs these values into its report; the coordinator keeps them for a retired revision and
    sends them back when that revision must be restored. Only three fields pass, each only in its
    strict form, so nothing else a plugin returns (environment, credentials, free text) can leave
    the node this way.

    Parameters
    ----------
    plugin : object
      A business plugin instance, or any object; one without the getter has no binding.

    Returns
    -------
    dict
      The fields that are present and well formed among `image_id` (`sha256:` and 64 hex digits, the
      image's id in the node's Docker store), `image_ref` (`repository@sha256:...`, where a registry
      serves exactly that image) and `git_commit` (40 hex digits). Empty when the plugin exposes
      nothing or its getter raises.

    Notes
    -----
    `image_ref` is what lets a restored revision be pulled again on a node whose Docker store no
    longer holds the image: the image id alone names it only inside the store that had it. A
    coordinator or node on an older version ignores the field and behaves as before.

    Examples
    --------
    >>> DeploymentLifecycle._runtime_artifacts(container_app)
    {'image_id': 'sha256:9f2c...', 'image_ref': 'ghcr.io/acme/shop@sha256:4f1c...'}
    >>> DeploymentLifecycle._runtime_artifacts(native_plugin)
    {}
    """
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
      # Where a registry serves exactly that image. Reported so that a restoration can pull the image
      # again when the node's Docker store lost it; before, only the store-local id travelled.
      image_ref = artifacts.get("image_ref")
      if isinstance(image_ref, str) and re.fullmatch(
          r"(?:[\w.\-]+(?::\d+)?/)?[\w.\-/]+@sha256:[0-9a-f]{64}", image_ref, re.ASCII):
        # A repository (with an optional registry host and port) and a digest, and nothing else: a ":"
        # is accepted only as the registry port, so no tag, no credentials and no free text pass.
        result["image_ref"] = image_ref
      if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit):
        result["git_commit"] = commit
      return result
    except Exception:
      return {}

  def attach(self, config, signature, instance_id, plugin, plugin_class=None):
    """Journal construction ownership and associate a runtime instance.

    Parameters
    ----------
    config : dict
        Accepted pipeline carrying the operation identity.
    signature : str
        Resolved plugin signature.
    instance_id : str
        Instance identity within the pipeline.
    plugin : object or None
        Constructed runtime, or None before construction begins.
    plugin_class : type or None
        Successfully resolved class, supplied before its constructor runs.
        External workload ownership must be declared on the class.
    """
    operation = config.get(OPERATION_KEY)
    if operation is None:
      return
    with self.lock:
      key = self.key(operation)
      self.plugins[(key, signature, instance_id)] = plugin
      record = self.records[key]
      for instance in record["instances"]:
        if (instance["signature"], instance["instance_id"]) == (signature, instance_id):
          instance["possibly_started"] = True
          workload_owner = plugin if plugin is not None else plugin_class
          if workload_owner is not None:
            instance["process_id"] = self.process_id
            instance["process_bound"] = getattr(workload_owner, "_deployment_external_workload", False) is False
          self._capture_runtime_artifacts(record, instance, plugin)
      self._save()

  def _capture_runtime_artifacts(self, record, instance, plugin):
    """Capture cached runtime artifacts without replacing an inspected repair pin.

    Parameters
    ----------
    record, instance : dict
        Local operation and its instance within the durable journal.
    plugin : object or None
        Runtime exposing the cached artifact getter.

    Returns
    -------
    None
        Updates the instance bindings when the runtime has safe identifiers.

    Examples
    --------
    >>> service._capture_runtime_artifacts(record, instance, plugin)  # doctest: +SKIP
    """
    artifacts = self._runtime_artifacts(plugin)  # The cache can be incomplete before readiness.
    if not artifacts:
      return
    if record["attempt"] == "retire" and record.get("parent_operation_id"):
      # The live Git check at conditional retirement has precedence over cached bindings.
      artifacts.pop("git_commit", None)
      instance.setdefault("runtime_artifacts", {}).update(artifacts)
    else:
      instance["runtime_artifacts"] = artifacts

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
      if record["aborted"] and instance["status"] == "stopped":
        # Stop evidence remains valid after releasing the runtime object.
        continue
      plugin = self.plugins.get((key, instance["signature"], instance["instance_id"]))
      self._capture_runtime_artifacts(record, instance, plugin)
      if plugin is None:
        if record["aborted"]:
          # Native threads from an earlier process cannot survive its exit.
          # Unknown classes and external runners still need scoped cleanup.
          if not instance.get("possibly_started", False):
            instance["status"] = "stopped"
          elif (instance.get("process_bound") is True and instance.get("process_id")
                and instance["process_id"] != self.process_id):
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

    A snapshot is published only when its observable state changes, and is
    retried until a write is confirmed. Once a ready snapshot of an attempt is
    confirmed, nothing more is published for it unless the coordinator stops or
    retires it: deployment reporting is over and NetMon owns workload liveness.

    Parameters
    ----------
    setter : callable
        CStore runtime setter; bounded waits and explicit peers are supplied.
    signer : callable
        Blockchain signing function accepting add_data and use_digest.
    """
    with self.lock:
      pending = []
      stopped_plugins = set()
      observation_failed = False
      for key, record in self.records.items():
        # Do not refresh timestamps for historical readiness without observing
        # that workload. Only a rejected command needs an inactive failure report.
        if record["attempt"] == "inspect":
          if time.time() >= record["expires_at"]:
            continue  # Expired previews have no dispatch or retry obligations.
        elif not record.get("active", False) and "error" not in record:
          continue
        if record.get("active", False):
          try:
            self._observe(key, record)
          except Exception:
            # One workload's observer/reaper cannot withhold other jobs' reports.
            observation_failed = True
            continue
        if record["aborted"]:
          for instance in record["instances"]:
            if instance["status"] == "stopped":
              plugin = self.plugins.get((key, instance["signature"], instance["instance_id"]))
              if plugin is not None:
                stopped_plugins.add(id(plugin))
        elif record.get("ready_reported", False):
          continue
        fingerprint = {name: value for name, value in record.items() if name not in ("sequence", "updated_at")}
        if self._published.get(key) == fingerprint:
          continue
        record["sequence"] += 1
        record["updated_at"] = time.time()
        pending.append((key, deepcopy(record), deepcopy(fingerprint)))
      if pending:
        self._save()
      if stopped_plugins:
        # Retirement may retain the same object under both deploy and retire
        # identities. Release all aliases only after stop evidence is persisted.
        self.plugins = {key: plugin for key, plugin in self.plugins.items()
                        if id(plugin) not in stopped_plugins}
    # Stop and failure reports drive coordinator decisions; do not queue them
    # behind writes that may each wait PUBLISH_TIMEOUT for unreachable peers.
    pending.sort(key=lambda item: not (item[1]["aborted"] or item[1]["status"] == "failed"))
    ready_reported = []
    for key, record, fingerprint in pending:
      ready = record["status"] == "ready" and not record["aborted"]
      peers = record.pop("peers")
      record.pop("aborted")
      record.pop("active")
      record.pop("ready_reported", None)
      for instance in record["instances"]:
        instance.pop("possibly_started", None)
        instance.pop("process_id", None)
        instance.pop("process_bound", None)
      signer(record, add_data=True, use_digest=True)
      field = key + "|" + self.node_addr
      prefix = "hs:" + hashlib.sha256(OBSERVATION_HKEY.encode()).hexdigest()[:10] + ":"
      success = True
      for field_name in (field + "|" + str(record["sequence"] % 8), field):
        storage_key = prefix + base64.urlsafe_b64encode(field_name.encode()).decode()
        # One unconfirmed write already fails the snapshot; skip the second wait.
        success = success and setter(storage_key, record, owner=self.node_addr, readonly=True,
                                     peers=peers, include_default_peers=False,
                                     timeout=PUBLISH_TIMEOUT, max_retries=0)
      if success:
        self._published[key] = fingerprint
        if ready:
          ready_reported.append(key)
    if ready_reported:
      # Persist the end of reporting so a node restart does not re-announce it.
      with self.lock:
        for key in ready_reported:
          self.records[key]["ready_reported"] = True
        self._save()
    if observation_failed:
      # Preserve the worker's existing throttled error notification after all
      # independently observed jobs have had their publication opportunity.
      raise RuntimeError("Deployment workload observation failed; retrying.")

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

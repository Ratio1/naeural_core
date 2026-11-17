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
import uuid
from copy import deepcopy


OPERATION_KEY = "DEEPLOY_OPERATION"
SERVICE_KEY = "deeploy_lifecycle"
OBSERVATION_HKEY = "DEEPLOY_NODE_OPERATIONS"
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
# The failures that a later start of the workload can clear (a start, an image fetch). An instance leaves one of
# them once its plugin no longer holds the error code (the runner reported a new status after the failure) and is
# ready, or when a node restart starts the attempt again; before, every failure was kept for the attempt although
# the app ran again. Every other code is still kept for the attempt.
RECOVERABLE_ERRORS = ("WORKLOAD_START_FAILED", "IMAGE_PULL_FAILED")


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
    """
    Load the node's deployment journal and reset the observations a restart makes stale.

    Reads the private journal when it exists. A persisted readiness is not evidence after a node restart,
    so such an attempt reports again from "starting", with its instances "initializing". The same holds
    for a failed attempt that is not aborted, has at least one failed instance, and whose failed instances
    all hold a start or image failure (`RECOVERABLE_ERRORS`): the restarted node starts its workload
    again, and the plugin reports the failure again if the workload still cannot start. Stopping and
    stopped attempts, a failed attempt without a failed instance (a rejected command, whose error is on
    the record), and a failed attempt holding any other failure keep their persisted status.

    Parameters
    ----------
    path : str
      Private local journal path. Unreadable/corrupt journals fail closed.
    node_addr : str
      Blockchain address used for signed observations.

    Returns
    -------
    None
      The journal records and empty runtime maps are set on the new object.

    Raises
    ------
    ValueError
      If the journal does not hold a mapping of records.
    json.JSONDecodeError
      If the journal is not valid JSON.
    OSError
      If the journal exists but cannot be read.

    Examples
    --------
    >>> lifecycle = DeploymentLifecycle(os.path.join(data_folder, "deeploy_node_operations.json"), node_addr)
    >>> lifecycle.records["operation|deploy"]["status"]  # persisted "failed" with WORKLOAD_START_FAILED
    'starting'
    """
    self.path = path
    self.node_addr = node_addr
    self.process_id = PROCESS_ID
    self.lock = threading.RLock()
    self.records = {}
    self.plugins = {}
    self._published = {}
    # Attempt key -> (snapshot, final) of the last report write a chain store refused: the snapshot is reported
    # once, and one refused for a reason no higher sequence can clear (final) is not written again while it is
    # unchanged. Before, such a snapshot was bumped, saved, signed and refused again at every pass, once a second.
    self._refused = {}
    # The log method start() receives; publish() reports a refused snapshot through it.
    self._report = None
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
        # The codes of the attempt's failed instances, read to tell a retried start from a kept failure.
        failures = [instance["error"]["code"] for instance in record["instances"] if instance["status"] == "failed"]
        # As before, reset an attempt that is not stopping, stopped or failed; now also a failed one that is not
        # aborted, has a failed instance, and whose failures are all start or image failures, since the restarted
        # node starts it again (before, it stayed "failed" while the workload ran again). Any other failure is
        # kept, and so is a rejected command, whose record is failed without a failed instance.
        if record["status"] not in ("stopping", "stopped", "failed") or (
            not record["aborted"] and failures and all(code in RECOVERABLE_ERRORS for code in failures)):
          record["status"] = "starting"
          for instance in record["instances"]:
            # The error goes with the failed status: an instance reported again from the start carries none.
            instance.update(status="initializing", error=None)

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
      return True
    with self.lock:
      record = self.records.get(self.key(operation))
      return record is not None and record.get("active", False) and not record["aborted"]

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
      for instance in self.records[key]["instances"]:
        if (instance["signature"], instance["instance_id"]) == (signature, instance_id):
          instance["possibly_started"] = True
          workload_owner = plugin if plugin is not None else plugin_class
          if workload_owner is not None:
            instance["process_id"] = self.process_id
            instance["process_bound"] = getattr(workload_owner, "_deployment_external_workload", False) is False
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
    """
    Aggregate plugin evidence into one attempt's record without executing plugin workload methods.

    An aborted attempt moves its instances to "stopping", and to "stopped" once the stop is proven.
    Otherwise each instance takes its plugin's state: "failed" with a fixed error code, "ready", or the
    plugin's own progress status. A failure is kept for the attempt, except a start or image failure
    (`RECOVERABLE_ERRORS`) whose plugin is ready again and no longer holds an error code: the runner
    retried, reported a new status after the failure (which clears the code) and the workload runs, so
    the instance leaves "failed" and the record's status is computed again from its instances. A ready
    plugin that still holds an error code (it reported nothing new since the failure, as after a build
    command that failed) stays "failed" with the code it holds.

    Parameters
    ----------
    key : str
      The attempt identity, "operation_id|attempt".
    record : dict
      The journal record of that attempt; it is updated in place.

    Returns
    -------
    None
      Statuses, errors and runtime artifacts are written into `record`.

    Raises
    ------
    Exception
      Whatever the scoped cleanup callback or a plugin's readiness check raises; `publish` isolates it to
      this record.

    Examples
    --------
    >>> lifecycle._observe("operation|deploy", record)  # a failed start, then "starting" and ready
    >>> record["status"], record["instances"][0]["error"]
    ('ready', None)

    Notes
    -----
    A record whose ready report was confirmed is no longer published (NetMon owns workload liveness from
    then on), so a later change of its status stays in the node's journal.
    """
    # Set when this pass takes an instance off a start or image failure, so the record's status follows.
    recovered = False
    for instance in record["instances"]:
      if record["aborted"] and instance["status"] == "stopped":
        # Stop evidence remains valid after releasing the runtime object.
        continue
      plugin = self.plugins.get((key, instance["signature"], instance["instance_id"]))
      artifacts = self._runtime_artifacts(plugin)
      if artifacts:
        instance["runtime_artifacts"] = artifacts
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
        # A start or image failure can be left once the plugin is ready again, if the checks below find its error
        # code cleared by a new report. Before, every failure stayed for the attempt. Any other failure, or a
        # workload not ready yet, stays.
        if instance["error"]["code"] not in RECOVERABLE_ERRORS or not plugin.is_plugin_ready():
          continue
        # The checks below set the instance's status, and a new error only if the plugin still reports one.
        instance["error"] = None
        # The record's "failed" is computed again below; another failed instance keeps it.
        recovered = True
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
    # A failed record stays failed, unless this pass took an instance off a start or image failure.
    elif record["status"] != "failed" or recovered:
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

    Returns
    -------
    None

    Raises
    ------
    RuntimeError
        After every record had its publication opportunity, when observing a workload failed.

    Notes
    -----
    A write that ends in a refusal (the setter's outcome word starts with ``refused_``) is reported in one red
    line per snapshot: the attempt, its sequence, the word, and whether the record's node address is this
    node's. What follows depends on whether a higher sequence can clear the refusal:

    - it cannot for ``refused_token`` and ``refused_readonly`` (the stored record belongs to another writer)
      and for ``refused_versioned`` on a record whose node address is not this node's (the signed-record rule
      refuses the address itself): that snapshot is not bumped, saved, signed or written again until it
      changes or the node restarts;
    - it can for ``refused_versioned`` at the node's own address (the store holds the record at a higher
      sequence, which the bumps catch up with) and it may for a refusal by the peers: those keep the retry of
      every pass, as a write that was only left unconfirmed does.

    Before, every refused snapshot was retried at every pass for good: one sequence bump, one journal save
    with its two fsyncs, one signature and one refused write per second, with no line in the log.

    Examples
    --------
    >>> lifecycle.publish(setter, signer)   # the reporter thread, once a second
    """
    with self.lock:
      pending = []
      stopped_plugins = set()
      observation_failed = False
      for key, record in self.records.items():
        # Do not refresh timestamps for historical readiness without observing
        # that workload. Only a rejected command needs an inactive failure report.
        if not record.get("active", False) and "error" not in record:
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
        if self._refused.get(key) == (fingerprint, True):
          # Refused for a reason no higher sequence can clear, and unchanged since: no bump, save, sign or write.
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
      # The setter writes here how the last write ended (its outcome word), on every path.
      info = {}
      for field_name in (field + "|" + str(record["sequence"] % 8), field):
        storage_key = prefix + base64.urlsafe_b64encode(field_name.encode()).decode()
        # One unconfirmed write already fails the snapshot; skip the second wait.
        success = success and setter(storage_key, record, owner=self.node_addr, readonly=True,
                                     peers=peers, include_default_peers=False,
                                     # result_info: the outcome word tells a refusal from a write that was only
                                     # left unconfirmed.
                                     timeout=PUBLISH_TIMEOUT, max_retries=0, result_info=info)
      if success:
        self._published[key] = fingerprint
        if ready:
          ready_reported.append(key)
      elif info["outcome"].startswith("refused_"):
        # Whether the record carries this node's address: with another one, the signed-record rule refuses it
        # whatever its sequence.
        own = record["node_addr"] == self.node_addr
        # Final: the stored record has another token or owner, or the address is not this node's. A refusal at
        # the node's own address (the store holds a higher sequence) or by the peers keeps its retry.
        final = info["outcome"] in ("refused_token", "refused_readonly") or (
          info["outcome"] == "refused_versioned" and not own)
        if self._refused.get(key) != (fingerprint, final):
          # Remembered first, so each refused snapshot is reported once, not at every pass.
          self._refused[key] = (fingerprint, final)
          # One red line: the attempt, its sequence, the store's word, whether the address is this node's (the
          # address itself is not printed), and what happens next.
          self._report("Deployment report {} (sequence {}) was refused by the chain store: {}; node address {}; {}.".format(
            key, record["sequence"], info["outcome"], "matches" if own else "differs",
            "not retried until the report changes" if final else "retried each pass"), color='r')
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
    """
    Start an independent bounded publication worker once CStore is ready.

    The worker runs ``publish`` once a second for as long as the chain-store setter is available. Calling this
    again while the worker exists does nothing.

    Parameters
    ----------
    setter_provider : callable
        Returns the chain-store setter, or None while the chain store is not ready.
    signer : callable
        Blockchain signing function, passed to ``publish``.
    report_error : callable
        Log method taking a text and a ``color`` keyword. It gets the throttled "publication failed" line
        (at most one per 60 s) and one red line per report snapshot the chain store refuses.
    cleanup : callable, optional
        Workload cleanup used when an aborted attempt's plugin is gone. Default None.

    Returns
    -------
    None

    Examples
    --------
    >>> lifecycle.start(lambda: shmem.get("__chain_storage_set"), blockchain_manager.sign, self.P)
    """
    if self._thread is not None:
      return
    self._cleanup = cleanup
    # publish() reports a refused snapshot through the same log method as the worker's errors.
    self._report = report_error
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

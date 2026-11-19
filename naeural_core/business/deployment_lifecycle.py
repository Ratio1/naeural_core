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
# Repair the current field at most three times per recipient until its session or the snapshot changes.
SOURCE_REPLAY_LIMIT = 3
SOURCE_REPLAY_INTERVAL = 30
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
  "SOURCE_UNREACHABLE": "The configured source could not be reached.",
  "IMAGE_PULL_FAILED": "Container image could not be downloaded.",
  "SECRET_RESOLUTION_FAILED": "Protected configuration could not be resolved.",
}


# Only these runner errors can be superseded by evidence of a later source start.
SOURCE_FAILURES = {"WORKLOAD_START_FAILED", "WORKLOAD_UNHEALTHY", "IMAGE_PULL_FAILED", "SOURCE_UNREACHABLE"}


def deployment_source_mode(signature, pipeline, instance):
  """Determine how a runner selects the version to start.

  A persisted pipeline carries the same job type and instance fields as a new deployment.

  Parameters
  ----------
  signature : str
      Plugin signature from the canonical pipeline.
  pipeline : dict
      Pipeline containing the deployment specifications.
  instance : dict
      Configuration of this particular instance.

  Returns
  -------
  str or None
      The source mode, or None for a plugin outside the source policy.

  Examples
  --------
  >>> deployment_source_mode("WORKER_APP_RUNNER", {}, {})
  'following'
  """
  if signature.upper() not in ("WORKER_APP_RUNNER", "CONTAINER_APP_RUNNER"):
    return None
  specs = pipeline.get("DEEPLOY_SPECS")
  if isinstance(specs, dict) and specs.get("job_app_type") == "service":
    return "service"
  return "pinned" if instance.get("PINNED_VERSION") is True else "following"


class DeploymentLifecycle:
  """Track operation attempts independently of plugin execution threads.

  Parameters
  ----------
  path : str
      Private local journal path. Unreadable/corrupt journals fail closed.
  node_addr : str
      Blockchain address used for signed observations.
  source_modes : bool
      Whether the installed edge runtime supports following and pinned runners.
  """

  def __init__(self, path, node_addr, source_modes=False):
    self.path = path
    self.node_addr = node_addr
    self.source_modes = source_modes
    self.process_id = PROCESS_ID
    self.lock = threading.RLock()
    # Shutdown waits for live plugin observation, independently of journal I/O and orphan cleanup.
    self._observation_lock = threading.Lock()
    self.records = {}
    self.plugins = {}
    self._published = {}
    # Keep just the latest confirmed signed report and outstanding recipients for each active following attempt.
    self._source_delivery = {}
    self._peer_sessions = {}
    self._peer_session = None
    self._confirmed_peers = None
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
            # A confirmed bound instance retains its final observation in a mixed pipeline.
            if not record.get("ready_reported") or instance.get("source_mode") == "following":
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
        instances = []
        for group in config.get("PLUGINS", []):
          for item in group.get("INSTANCES", []):
            observation = dict(signature=group["SIGNATURE"], instance_id=item["INSTANCE_ID"], status="initializing",
                               error=None, possibly_started=operation["action"] == "retire")
            if self.source_modes:
              mode = deployment_source_mode(group["SIGNATURE"], config, item)
              if mode:
                observation["source_mode"] = mode  # A readiness deadline can expire before construction.
            instances.append(observation)
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
    specs = current_config.get("DEEPLOY_SPECS") or {}
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
          for field in ("process_id", "process_bound", "source_mode"):
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
            # Old journals have no mode. BusinessManager reads it from the persisted pipeline at startup.
            if self.source_modes and plugin is not None and getattr(plugin, "_deployment_source_mode", None):
              instance["source_mode"] = plugin._deployment_source_mode
              # Older nodes could journal a dead thread during shutdown after confirming readiness.
              # A freshly constructed runner now owns that workload and will report its own health.
              if (plugin._deployment_source_mode == "following" and self.records[key].get("ready_reported")
                  and (instance.get("error") or {}).get("code") == "PLUGIN_EXECUTION_FAILED"):
                instance.update(status="initializing", error=None)
            elif not self.source_modes:
              instance.pop("source_mode", None)  # A runtime downgrade withdraws the capability.
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
    """Aggregate plugin evidence without executing plugin workload methods."""
    for instance in record["instances"]:
      if self._stop.is_set():
        return  # An in-progress Docker cleanup may finish, but shutdown must not start another.
      if record["aborted"] and instance["status"] == "stopped":
        # Stop evidence remains valid after releasing the runtime object.
        continue
      if not record["aborted"] and record.get("ready_reported") and instance.get("source_mode") != "following":
        continue  # Only the source-following members of a confirmed mixed rollout keep reporting.
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
      # A newly allocated Thread has no ident until start() and supplies no exit evidence.
      thread_dead = thread is not None and not thread.is_alive() and getattr(thread, "ident", True) is not None
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
      follows_source = instance.get("source_mode") == "following"
      if instance["status"] == "failed" and not (
        follows_source and (instance.get("error") or {}).get("code") in SOURCE_FAILURES
      ):
        continue  # Infrastructure and bound workload failures remain terminal.
      instance["error"] = None  # A recovered workload must not carry the previous failure.
      if follows_source:
        if not artifacts:
          instance.pop("runtime_artifacts", None)  # Do not display the previous process as running.
        branch = getattr(plugin, "branch", None) if instance["signature"].upper() == "WORKER_APP_RUNNER" else None
        if isinstance(branch, str) and branch:
          instance["source_branch"] = branch
        if getattr(plugin, "_deployment_source_error", None) == "SOURCE_UNREACHABLE":
          instance["source_error"] = "SOURCE_UNREACHABLE"  # Polling trouble can coexist with readiness.
        else:
          instance.pop("source_error", None)
      failure = getattr(plugin, "_deployment_error_code", None)
      if failure or thread_dead:
        if failure not in ERRORS or (thread_dead and follows_source and failure in SOURCE_FAILURES):
          failure = "PLUGIN_EXECUTION_FAILED"  # A dead runner cannot observe a new source.
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
    elif record["status"] != "failed" or (not record.get("error") and any(
      item.get("source_mode") == "following" for item in record["instances"]
    )):
      record["status"] = "failed" if "failed" in statuses else "ready" if statuses and all(status == "ready" for status in statuses) else "starting"

  def publish(self, setter, signer):
    """Persist, sign and retry node snapshots outside the main/plugin loops.

    A snapshot is published only when its observable state changes, and is
    retried until a write is confirmed. Once a ready snapshot of an attempt is
    confirmed for a bound workload, only retirement and stop remain reportable.
    Source-following attempts continue to publish changes in status and version.

    Parameters
    ----------
    setter : callable
        CStore runtime setter; bounded waits and explicit peers are supplied.
    signer : callable
        Blockchain signing function accepting add_data and use_digest.

    Returns
    -------
    None
        Stops taking observations when shutdown begins.

    Examples
    --------
    >>> journal.publish(setter, signer)
    """
    with self.lock:
      pending = []
      stopped_plugins = set()
      observation_failed = False
      for key, record in self.records.items():
        if self._stop.is_set():
          break
        # Do not refresh timestamps for historical readiness without observing
        # that workload. Only a rejected command needs an inactive failure report.
        if not record.get("active", False) and "error" not in record:
          continue
        if record.get("active", False):
          try:
            if record["aborted"]:
              self._observe(key, record)  # Orphan cleanup holds admission, but cannot report execution failure.
            else:
              with self._observation_lock:
                if self._stop.is_set():
                  break
                self._observe(key, record)
          except Exception:
            # One workload's observer/reaper cannot withhold other jobs' reports.
            observation_failed = True
            continue
          if self._stop.is_set():
            break  # Do not publish a snapshot interrupted partway through observation.
        if record["aborted"]:
          for instance in record["instances"]:
            if instance["status"] == "stopped":
              plugin = self.plugins.get((key, instance["signature"], instance["instance_id"]))
              if plugin is not None:
                stopped_plugins.add(id(plugin))
        elif record.get("ready_reported", False) and not any(
          item.get("source_mode") == "following" for item in record["instances"]
        ):
          continue
        fingerprint = {name: value for name, value in record.items() if name not in ("sequence", "updated_at", "ready_reported")}
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
      # Retiring an attempt also retires its replay cache, even if its next write cannot be confirmed.
      for key in list(self._source_delivery):
        if self.records[key]["aborted"] or not self.records[key].get("active"):
          del self._source_delivery[key]
    # Stop and failure reports drive coordinator decisions; do not queue them
    # behind writes that may each wait PUBLISH_TIMEOUT for unreachable peers.
    pending.sort(key=lambda item: not (item[1]["aborted"] or item[1]["status"] == "failed"))
    ready_reported = []
    for key, record, fingerprint in pending:
      if self._stop.is_set():
        break  # Shutdown should wait only for the current write.
      # A superseded snapshot must never be replayed while its replacement awaits confirmation.
      self._source_delivery.pop(key, None)
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
        if self._stop.is_set():
          success = False  # A partial publication is retried on the next node start.
          break
        storage_key = prefix + base64.urlsafe_b64encode(field_name.encode()).decode()
        # One unconfirmed write already fails the snapshot; skip the second wait.
        success = success and setter(storage_key, record, owner=self.node_addr, readonly=True,
                                     peers=peers, include_default_peers=False,
                                     timeout=PUBLISH_TIMEOUT, max_retries=0)
      if success:
        self._published[key] = fingerprint
        if (self._peer_session is not None and fingerprint.get("active") and not fingerprint["aborted"]
            and any(i.get("source_mode") == "following" for i in record["instances"])):
          # A bulk write can succeed with only one acknowledgement. Keep the exact signed current field
          # for the other recipients; never refresh its sequence, timestamp or eight history slots.
          confirmed = self._confirmed_peers(storage_key)
          due = {peer: None if peer in confirmed else (time.monotonic() + PUBLISH_TIMEOUT, 0, False)
                 for peer in peers if peer != self.node_addr}
          self._source_delivery[key] = (storage_key, record, due)
        if ready:
          ready_reported.append(key)
    if ready_reported:
      # Persist the end of reporting so a node restart does not re-announce it.
      with self.lock:
        for key in ready_reported:
          self.records[key]["ready_reported"] = True
        self._save()
    if not self._stop.is_set():
      self._repair_source_delivery(setter)  # New reports always take precedence over a bounded repair.
    if observation_failed:
      # Preserve the worker's existing throttled error notification after all
      # independently observed jobs have had their publication opportunity.
      raise RuntimeError("Deployment workload observation failed; retrying.")

  def _repair_source_delivery(self, setter):
    """Replay one current field to an oracle that missed it or restarted.

    Parameters
    ----------
    setter : callable
      CStore writer supporting a single destination and bounded confirmation wait.

    Returns
    -------
    None
      A failed transfer remains queued up to the repair limit per snapshot and peer session.

    Examples
    --------
    >>> journal._repair_source_delivery(setter)
    """
    now = time.monotonic()
    peers = {peer for _, _, recipients in self._source_delivery.values() for peer in recipients}
    sessions = {}
    self._peer_sessions = {peer: session for peer, session in self._peer_sessions.items() if peer in peers}
    returning = set()
    for peer in peers:
      try:
        online, uptime = self._peer_session(peer)
      except Exception:
        # A monitor error says nothing about this peer's session and cannot block other destinations.
        continue
      sessions[peer] = (online, uptime)
      previous = self._peer_sessions.get(peer)
      if previous is not None and online and (
        not previous[0] or (uptime is not None and previous[1] is not None and uptime < previous[1])
      ):
        returning.add(peer)  # A peer can restart between heartbeats without appearing offline.
    self._peer_sessions.update(sessions)
    transfer = None
    for storage_key, report, recipients in self._source_delivery.values():
      for peer, pending in recipients.items():
        if peer in returning:
          pending = recipients[peer] = (now, 0, True)  # Ignore confirmations predating a peer restart.
        if pending is None or pending[0] > now or not sessions.get(peer, (False, None))[0]:
          continue
        due, tries, force = pending
        if not force and peer in self._confirmed_peers(storage_key):
          recipients[peer] = None  # The broadcast acknowledgement arrived after quorum; no replay needed.
        elif transfer is None or due < transfer[0]:
          transfer = (due, peer, storage_key, report, recipients, tries)
    if transfer is not None and not self._stop.is_set():
      _, peer, storage_key, report, recipients, tries = transfer
      # Debiting the budget before calling the setter also bounds failures that raise.
      tries += 1
      recipients[peer] = (now + SOURCE_REPLAY_INTERVAL, tries, True) if tries < SOURCE_REPLAY_LIMIT else None
      if setter(storage_key, report, owner=self.node_addr, readonly=True, peers=[peer],
                include_default_peers=False, timeout=2, max_retries=0, require_target_ack=True):
        recipients[peer] = None

  def start(self, setter_provider, signer, report_error, cleanup=None, peer_session=None, confirmed_peers=None):
    """Start an independent bounded publication worker once CStore is ready."""
    if self._thread is not None:
      return
    self._cleanup = cleanup
    self._peer_session = peer_session
    self._confirmed_peers = confirmed_peers
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
    """Stop observing plugins before their shutdown begins.

    A write already in progress can use its remaining timeout. Pending observations and
    tombstones are kept in the journal.

    Parameters
    ----------
    None

    Returns
    -------
    None

    Examples
    --------
    >>> journal.close()
    """
    self._stop.set()
    if self._thread is not None:
      with self._observation_lock:
        pass  # Wait for the last plugin observation before BusinessManager stops plugin threads.
      self._thread.join(timeout=3)  # An in-flight CStore wait or cleanup cannot take new observations.

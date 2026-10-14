"""Run a disposable full Edge SHM session on a Docker host."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import time


EDGE = "ratio1/edge_node@sha256:d62da776263eea1f3e186d40fa95e05b0724c7c0381c570ec99130f605bd2037"
BROKER = "eclipse-mosquitto@sha256:199ea8ef2e35ec2b1b37e59cfd1dbae538ed4dfa4a2251a121a52215a6248a21"


def docker(*args, check=True):
  """Run a bounded Docker command with captured output."""
  return subprocess.run(["docker", *args], check=check, capture_output=True, text=True, timeout=60)


def services():
  """Record only existing container identities and startup timestamps."""
  identifiers = docker("ps", "-q").stdout.split()
  if not identifiers:
    return []
  return sorted((row["Id"], row["Name"], row["State"]["StartedAt"])
    for row in json.loads(docker("inspect", *identifiers).stdout))


def write_json(path, value):
  """Publish control/evidence atomically across container processes."""
  temporary = path.with_suffix(".tmp")
  temporary.write_text(json.dumps(value, indent=2))
  temporary.replace(path)


def main():
  """Exercise real capture, serving, always-on diagnostics and loopback MQTT.

  Returns
  -------
  None
    Persist bounded evidence and clean up only this isolated testbed. The
    retained --diagnostics on spelling is a compatibility alias; off is rejected
    because diagnostics no longer have an activation switch.
  """
  parser = argparse.ArgumentParser()
  parser.add_argument("--root", type=Path, required=True)
  parser.add_argument("--core-source", type=Path, required=True)
  parser.add_argument("--diagnostics", choices=("on", "off"), default="on",
                      help="Legacy alias: on is always-on; off is no longer supported")
  args = parser.parse_args()
  if args.diagnostics == "off":
    parser.error("SHM diagnostics are always on; use the dated core diagnostics guide for baseline benchmarking")
  root = args.root.resolve()
  assert root.parent.name.startswith("r1-pr225-shm-e2e-"), root
  assert not root.exists(), "Use a fresh run directory"
  root.mkdir()
  fixture = Path(__file__).resolve().parent
  source = args.core_source.resolve()
  runtime, evidence, config = (root / name for name in ("runtime", "evidence", "config"))
  for path in (runtime, evidence, config):
    path.mkdir(mode=0o700)
  (config / "streams").mkdir()
  before = services()
  write_json(evidence / "services-before.json", before)
  node = "r1-pr225-shm-" + root.name
  broker = node + "-mqtt"
  for name in (node, broker):
    assert docker("inspect", name, check=False).returncode, "Container name already exists"
  startup = {
    "EE_ID": "shmtest01", "SECURED": True, "WORK_OFFLINE": True,
    "MAIN_LOOP_RESOLUTION": 20, "SECONDS_HEARTBEAT": 2, "PLUGINS_ON_THREADS": True,
    "SHUTDOWN_NO_STREAMS": False, "CHECK_RAM_ON_SHUTDOWN": False,
    "RESET_ADMIN_PIPELINE": True, "SYSTEM_TEMPERATURE_CHECK": False, "COMPRESS_HEARTBEAT": False,
    "SERVING_ENVIRONMENT": {"SHM_MAX_LEN": 50, "SHM_MAX_IMAGE_SHAPE": [64, 64, 3]},
    "BLOCKCHAIN_CONFIG": {"PEM_FILE": "test.pem", "PEM_LOCATION": "data", "PASSWORD": None},
    "CONFIG_RETRIEVE": [{"TYPE": "local", "APP_CONFIG_ENDPOINT": "/test-config/app.json",
      "STREAMS_CONFIGS_ENDPOINT": {"PATH": "/test-config/streams", "IS_RELATIVE_PATH": False}}],
    "ADMIN_PIPELINE": {name: {"FORCED_PAUSE": True, "TUNNEL_ENGINE_ENABLED": False}
      for name in ("PLUGIN_MONITOR_01", "LOCAL_COMMS_01", "NET_CONFIG_MONITOR", "CHAIN_STORE_BASE",
        "NET_MON_01", "UPDATE_MONITOR_01", "ORACLE_SYNC_01", "DEEPLOY_MANAGER_API", "ORACLE_API",
        "NAEURAL_RELEASE_APP", "DAUTH_MANAGER", "CHAIN_DIST_MONITOR", "CSTORE_MANAGER_API",
        "TUNNELS_MANAGER", "LIVENESS_API", "R1FS_MANAGER_API")},
    "COMMUNICATION_ENVIRONMENT": {"ENCRYPTED_COMMS": False, "QOS": 1},
    "HEAVY_OPS_CONFIG": {"ACTIVE_COMM_ASYNC": [], "ACTIVE_ON_COMM_THREAD": [],
      "DISABLE_DEFAULT_SEND_MAIL": True, "DISABLE_DEFAULT_SEND_SMS": True},
  }
  params = {"HOST": "127.0.0.1", "PORT": 1883, "USER": "", "PASS": "", "QOS": 1,
    "SECURED": 0, "SUBTOPIC": "", "CONFIG_CHANNEL": {"TOPIC": "shm-test/{}/config"},
    "CTRL_CHANNEL": {"TOPIC": "shm-test/ctrl"}, "PAYLOADS_CHANNEL": {"TOPIC": "shm-test/payloads"},
    "NOTIF_CHANNEL": {"TOPIC": "shm-test/notif"}}
  app = {"COMMUNICATION": {"TYPE": "mqtt", "PARAMS": params, "INSTANCES": {
    "COMMANDCONTROL": {"RECV_FROM": "CONFIG_CHANNEL"}, "DEFAULT": {"SEND_TO": "PAYLOADS_CHANNEL"},
    "HEARTBEATS": {"RECV_FROM": "CTRL_CHANNEL", "SEND_TO": "CTRL_CHANNEL"},
    "NOTIFICATIONS": {"SEND_TO": "NOTIF_CHANNEL"}}},
    "FILE_UPLOAD": {"TYPE": "minio", "CONFIG_UPLOADER": {}}}
  pipeline = {"NAME": "shm_probe", "TYPE": "ShmFrames", "CAP_RESOLUTION": 10,
    "PLUGINS": [{"SIGNATURE": "SHM_PROBE", "INSTANCES": [
      {"INSTANCE_ID": "direct", "AI_ENGINE": ["shm_covered_detector"]},
      {"INSTANCE_ID": "alias", "AI_ENGINE": ["shm_toy_detector"]}]}]}
  for path, value in ((config / "startup.json", startup), (config / "app.json", app),
      (config / "streams/probe.json", pipeline), (evidence / "control.json", {"phase": "normal", "count": 1})):
    write_json(path, value)
  (config / "mosquitto.conf").write_text("listener 1883 127.0.0.1\nallow_anonymous true\npersistence false\n")
  # The broker has its own unprivileged UID and mounts only this non-secret
  # loopback configuration. Runtime/evidence directories remain private.
  (config / "mosquitto.conf").chmod(0o644)
  created = []
  report = {"passed": False, "diagnostics": "always_on", "edge_image": EDGE,
    "broker_image": BROKER, "phases": {}}
  try:
    command = ["run", "-d", "--name", node, "--network", "none", "--cap-drop", "ALL",
      # With every capability dropped, container root cannot write another
      # user's 0700 bind mounts. Use the test-directory owner's numeric identity.
      "--user", f"{os.getuid()}:{os.getgid()}",
      "--security-opt", "no-new-privileges", "--memory", "3g", "--memory-swap", "3g",
      "--cpus", "2", "--pids-limit", "256", "--entrypoint", "python3", "-w", "/runtime",
      "-e", "PYTHONPATH=/opt/core:/edge_node", "-e", "PYTHONDONTWRITEBYTECODE=1",
      "-e", "AINODE_DOCKER=Yes", "-e", "EE_DD=0", "-e", "EE_ETH_ENABLED=false",
      "-e", "EE_CONFIG=/test-config/startup.json", "-e", "EE_ID=shmtest01",
      "-e", "R1_SHM_EVIDENCE=/evidence", "-e", "R1_SHM_CONTROL=/evidence/control.json",
      "-v", f"{source}:/opt/core:ro", "-v", f"{fixture}:/testbed:ro",
      "-v", f"{config}:/test-config:ro", "-v", f"{runtime}:/runtime", "-v", f"{evidence}:/evidence"]
    for filename, target in (("shm_frames.py", "data"), ("shm_probe.py", "business"),
        ("shm_covered_detector.py", "serving"), ("shm_toy_detector.py", "serving")):
      command.extend(["-v", f"{fixture / filename}:/edge_node/extensions/{target}/{filename}:ro"])
    created.append(node)
    docker(*command, EDGE, "/testbed/launch.py")
    created.append(broker)
    docker("run", "-d", "--name", broker, "--network", "container:" + node,
      "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "128m",
      "--cpus", "0.25", "--user", "1883:1883", "--entrypoint", "mosquitto",
      "-v", f"{config / 'mosquitto.conf'}:/test.conf:ro", BROKER, "-c", "/test.conf")
    for phase, count, minimum in (("normal", 1, 100), ("capacity", 51, 15),
        ("capacity_recovery", 2, 20), ("mismatch", 2, 15), ("mismatch_recovery", 2, 20),
        ("timeout", 1, 0), ("timeout_recovery", 1, 30)):
      started = time.time()
      write_json(evidence / "control.json", {"phase": phase, "count": count})
      deadline = time.monotonic() + (180 if phase == "normal" else 90)
      while time.monotonic() < deadline:
        state = json.loads(docker("inspect", node).stdout)[0]
        assert state["State"]["Running"], "Node exited"
        output = docker("logs", "--tail", "200", node)
        logs = output.stdout + output.stderr
        assert "Plugin init FAILED for business plugin SHM_PROBE" not in logs, "Probe startup failed"
        assert "Start capture 'shm_probe' failed" not in logs, "Capture startup failed"
        if phase == "timeout":
          output = docker("logs", node)
          logs = output.stdout + output.stderr
          if "'SHM_COVERED_DETECTOR' server timeout" in logs or "error: no message returned" in logs:
            break
        else:
          probes = [evidence / ("probe-" + name + ".json") for name in ("direct", "alias")]
          if all(path.exists() for path in probes):
            records = [json.loads(path.read_text()) for path in probes]
            samples = [[row for row in record["samples"] if row["phase"] == phase and row["time"] >= started + 0.5]
              for record in records]
            if all(len(rows) >= minimum for rows in samples):
              expected = [50, 51] if phase == "capacity" else [1, 1] if phase == "mismatch" else [count, count]
              assert all(all(list(row["counts"].values()) == [wanted] and row["scores"] == wanted for row in rows)
                for rows, wanted in zip(samples, expected)), "Unexpected inference cardinality: " + phase
              latency = [row["delivery_seconds"] for rows in samples for row in rows]
              report["phases"][phase] = {"samples": [len(rows) for rows in samples],
                "expected_inference_images": expected, "median_delivery_seconds": statistics.median(latency),
                "p95_delivery_seconds": sorted(latency)[int(len(latency) * .95)],
                "started": started, "ended": time.time()}
              break
        time.sleep(1)
      else:
        raise RuntimeError("Acceptance deadline: " + phase)
      if phase == "timeout":
        report["phases"][phase] = {"observed": True, "started": started, "ended": time.time()}
    mqtt = json.loads((evidence / "mqtt.json").read_text())
    assert len(mqtt["heartbeats"]) >= 10 and mqtt["payloads"] >= 20, mqtt
    assert any(stamp > report["phases"]["timeout"]["ended"] for stamp in mqtt["heartbeats"])
    workers = [json.loads(path.read_text()) for path in evidence.glob("worker-*.json")]
    assert len({row["pid"] for row in workers}) >= 2, "Worker restart was not observed"
    state = json.loads(docker("inspect", node).stdout)[0]
    assert state["HostConfig"]["NetworkMode"] == "none"
    assert not state["HostConfig"]["Privileged"] and not state["HostConfig"]["PortBindings"]
    assert not state["State"]["OOMKilled"] and state["RestartCount"] == 0
    output = docker("logs", node)
    logs = output.stdout + output.stderr
    diagnostics = [json.loads(line.split("[SHM_DIAG] ", 1)[1].split("\x1b", 1)[0])
      for line in logs.splitlines() if "[SHM_DIAG] " in line]
    assert {"startup", "configuration", "send", "receive", "result", "consumer", "timeout"} <= {row["event"] for row in diagnostics}
    assert any(row.get("requested_server") == "SHM_TOY_DETECTOR" for row in diagnostics)
    assert any(row.get("reason") == "no_free_slot" or row.get("fail_no_free_slot", 0) > 0 for row in diagnostics)
    assert any(row.get("reason") == "cardinality" or row.get("fail_cardinality", 0) > 0 for row in diagnostics)
    assert any(row["event"] == "timeout" and row.get("expected_cmd_id") is not None
      and row.get("worker_alive") is True for row in diagnostics)
    # During timeout teardown the alias temporarily has no physical worker.
    # Consumer absence evidence then names its requested handle; transport and
    # configuration evidence must still resolve the physical worker identity.
    assert all(row.get("target") == "SHM_COVERED_DETECTOR" or (
      row["event"] == "consumer" and row.get("reason") == "absent_model"
      and row.get("target") == row.get("requested_server") == "SHM_TOY_DETECTOR"
    ) for row in diagnostics if row["event"] != "startup")
    assert len([row for row in diagnostics if row["event"] == "configuration"]) == len(workers), "Configuration repeated within one worker lifecycle"
    assert any(row.get("anomaly") and row.get("recent") for row in diagnostics), "Failure context missing"
    provenance = json.loads((evidence / "provenance.json").read_text())
    assert all(hashlib.sha256((source / name).read_bytes()).hexdigest() == digest
      for name, digest in provenance["source_sha256"].items())
    report.update(passed=True, mqtt=mqtt, workers=workers, provenance=provenance,
      diagnostics_records=len(diagnostics), isolation=json.loads((evidence / "isolation.json").read_text()),
      stats=docker("stats", "--no-stream", "--format", "{{json .}}", node, broker).stdout)
    write_json(evidence / "diagnostics.json", diagnostics)
    write_json(evidence / "control.json", {"phase": "shutdown", "count": 1})
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
      state = json.loads(docker("inspect", node).stdout)[0]
      if not state["State"]["Running"]:
        break
      time.sleep(1)
    else:
      raise RuntimeError("Graceful node shutdown deadline exceeded")
    output = docker("logs", node)
    assert "All servers stopped." in output.stdout + output.stderr, "Serving shutdown missing"
    assert state["State"]["ExitCode"] == 0 and not state["State"]["OOMKilled"], "Unclean node exit"
    report["graceful_shutdown"] = {"exit_code": state["State"]["ExitCode"],
      "serving_stopped": True, "oom_killed": state["State"]["OOMKilled"]}
  except Exception as error:
    report["passed"] = False
    report["failure"] = {"type": type(error).__name__, "message": str(error)}
    raise
  finally:
    cleanup = []
    for name in reversed(created):
      if docker("inspect", name, check=False).returncode:
        continue
      logs = docker("logs", name, check=False)
      (evidence / (name + ".log")).write_text(logs.stdout + logs.stderr)
      result = docker("rm", "-f", name, check=False)
      if result.returncode:
        cleanup.append(name)
    after = services()
    write_json(evidence / "services-after.json", after)
    report.update(cleanup_errors=cleanup, existing_services_unchanged=before == after)
    report["passed"] = report["passed"] and not cleanup and before == after
    if not cleanup:
      shutil.rmtree(runtime)
    write_json(evidence / "result.json", report)
    assert not cleanup and before == after, "Cleanup or existing service check failed"
    print(json.dumps({key: value for key, value in report.items() if key != "provenance"}, indent=2))


if __name__ == "__main__":
  main()

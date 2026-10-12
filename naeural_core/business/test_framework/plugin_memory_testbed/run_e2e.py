"""Disposable, fail-closed full Edge testbed; run on a Docker-equipped test host."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from memory_load import GIB, MAX_SCAN_SECONDS, MIN_OWNED_BYTES, PRESSURE_FRACTION


EDGE_IMAGE = "ratio1/edge_node@sha256:d62da776263eea1f3e186d40fa95e05b0724c7c0381c570ec99130f605bd2037"
BROKER_IMAGE = "eclipse-mosquitto@sha256:199ea8ef2e35ec2b1b37e59cfd1dbae538ed4dfa4a2251a121a52215a6248a21"


def docker(*args, check=True):
  """Run Docker with structured arguments and a bounded command deadline."""
  return subprocess.run(
    ["docker", *args], check=check, capture_output=True, text=True, timeout=45,
  )


def existing_services():
  """Capture identities and start times without exposing container environments."""
  identifiers = docker("ps", "-q").stdout.split()
  if not identifiers:
    return []
  records = json.loads(docker("inspect", *identifiers).stdout)
  return sorted((row["Id"], row["Name"], row["State"]["StartedAt"]) for row in records)


def pressure_acceptance(probe, mqtt):
  """Validate heavy-load accounting, pressure liveness and release evidence.

  Parameters
  ----------
  probe : dict
    Incremental report from the real business plugin.
  mqtt : dict
    Independently observed loopback heartbeat timestamps.

  Returns
  -------
  dict or None
    Pressure summary once complete and externally observed recovery is ready.
  """
  pressure = probe["pressure"]
  if not pressure or not pressure["complete"]:
    return None
  assert pressure["peak_owned_payload_bytes"] >= MIN_OWNED_BYTES
  assert pressure["released"] - pressure["started"] >= 30
  assert len(pressure["growth_samples"]) >= 12
  assert len(pressure["diagnostics"]) >= 3
  assert max(row["memory"]["utilization"] for row in pressure["growth_samples"]) >= PRESSURE_FRACTION
  assert all(row["relative_error"] < 0.05 for row in pressure["growth_samples"])
  assert all(row["relative_error"] < 0.05 for row in pressure["diagnostics"])
  violations = list(pressure["latency_violations"])
  main_scans = [row for row in probe["main_scan_samples"] if row["phase"] == "hold"]
  assert main_scans, "No actual main-thread diagnostic at pressure"
  deliveries = [row for row in probe["main_delivery_samples"] if row["phase"] == "hold"]
  assert deliveries, "Whole overflow-handler timing was not captured"
  assert all(row["seconds"] < 8 for row in deliveries), "Whole diagnostic blocked the main loop"
  for row in main_scans:
    assert row["owned_payload_bytes"] >= MIN_OWNED_BYTES
    assert row["memory"]["utilization"] >= PRESSURE_FRACTION - 0.02
    if row["seconds"] >= MAX_SCAN_SECONDS:
      violations.append({"phase": "main_thread", "seconds": row["seconds"],
        "owned_payload_bytes": row["owned_payload_bytes"], "limit_seconds": MAX_SCAN_SECONDS})
    error = abs(row["estimate_bytes"] - pressure["baseline_estimate_bytes"] - row["owned_payload_bytes"])
    assert error / row["owned_payload_bytes"] < 0.05
  heartbeats = mqtt["heartbeat_times"]
  during = [stamp for stamp in heartbeats if pressure["started"] <= stamp <= pressure["released"]]
  after = [stamp for stamp in heartbeats if stamp > pressure["released"]]
  if len(after) < 3:
    return None
  assert len(during) >= 10, "Heartbeats did not continue during pressure"
  window = [pressure["started"], *during, pressure["released"]]
  max_gap = max(later - earlier for earlier, later in zip(window, window[1:]))
  assert max_gap < 8, "Memory diagnosis stalled main-loop heartbeats"
  assert probe["process_count"] - pressure["release_process_count"] >= 12
  return {
    "pressure_heartbeats": len(during), "recovery_heartbeats": len(after),
    "max_pressure_heartbeat_gap_seconds": max_gap,
    "pressure_hold_seconds": pressure["released"] - pressure["started"],
    "latency_violations": violations,
    "max_main_delivery_seconds": max(row["seconds"] for row in deliveries),
  }


def main():
  """Create, validate and remove a test node and loopback-only broker.

  Returns
  -------
  None
    Writes a sanitized acceptance/cleanup report or raises on failure.
  """
  parser = argparse.ArgumentParser()
  parser.add_argument("--root", type=Path, required=True)
  parser.add_argument("--core-source", type=Path)
  parser.add_argument("--scenario", choices=("boundary", "memory-growth"), default="boundary")
  arguments = parser.parse_args()
  memory_limit = 4 * GIB if arguments.scenario == "memory-growth" else 3 * GIB
  root = arguments.root.resolve()
  assert root.name.startswith("r1-plugin-memory-scan-"), "Use a task-specific directory"
  fixture = Path(__file__).resolve().parent
  source_root = (arguments.core_source or fixture.parents[3]).resolve()
  runtime = root / "runtime"
  evidence = root / "evidence"
  assert not runtime.exists(), "Refuse to reuse a cache or overwrite existing state"
  assert not evidence.exists(), "Use a fresh evidence directory"
  evidence.mkdir(parents=True)
  runtime.mkdir(mode=0o700)
  config = runtime / "config"
  config.mkdir()
  streams = config / "streams"
  streams.mkdir()
  node = "r1-memory-edge-20261009"
  broker = "r1-memory-mqtt-20261009"
  for name in (node, broker):
    assert docker("inspect", name, check=False).returncode != 0, "Container name already exists"
  before = existing_services()
  (evidence / "services-before.json").write_text(json.dumps(before, indent=2))
  startup = {
    "EE_ID": "memfix01", "SECURED": True, "WORK_OFFLINE": True,
    "MAIN_LOOP_RESOLUTION": 20, "SECONDS_HEARTBEAT": 2,
    "PLUGINS_ON_THREADS": True, "SHUTDOWN_NO_STREAMS": False,
    "CHECK_RAM_ON_SHUTDOWN": False, "RESET_ADMIN_PIPELINE": True,
    "SYSTEM_TEMPERATURE_CHECK": False, "COMPRESS_HEARTBEAT": False,
    "BLOCKCHAIN_CONFIG": {"PEM_FILE": "test.pem", "PEM_LOCATION": "data", "PASSWORD": None},
    "CONFIG_RETRIEVE": [{"TYPE": "local", "APP_CONFIG_ENDPOINT": "/test-config/app.json",
      "STREAMS_CONFIGS_ENDPOINT": {"PATH": "/test-config/streams", "IS_RELATIVE_PATH": False}}],
    "ADMIN_PIPELINE": {name: {"FORCED_PAUSE": True, "TUNNEL_ENGINE_ENABLED": False}
      for name in ("PLUGIN_MONITOR_01", "LOCAL_COMMS_01", "NET_CONFIG_MONITOR",
        "CHAIN_STORE_BASE", "NET_MON_01", "UPDATE_MONITOR_01", "ORACLE_SYNC_01",
        "DEEPLOY_MANAGER_API", "ORACLE_API", "NAEURAL_RELEASE_APP", "DAUTH_MANAGER",
        "CHAIN_DIST_MONITOR", "CSTORE_MANAGER_API", "TUNNELS_MANAGER", "LIVENESS_API",
        "R1FS_MANAGER_API")},
    "COMMUNICATION_ENVIRONMENT": {"ENCRYPTED_COMMS": False, "QOS": 1},
    "HEAVY_OPS_CONFIG": {"ACTIVE_COMM_ASYNC": [], "ACTIVE_ON_COMM_THREAD": [],
      "DISABLE_DEFAULT_SEND_MAIL": True, "DISABLE_DEFAULT_SEND_SMS": True},
  }
  params = {
    "HOST": "127.0.0.1", "PORT": 1883, "USER": "", "PASS": "", "QOS": 1,
    "SECURED": 0, "SUBTOPIC": "",
    "CONFIG_CHANNEL": {"TOPIC": "memory-test/{}/config"},
    "CTRL_CHANNEL": {"TOPIC": "memory-test/ctrl"},
    "PAYLOADS_CHANNEL": {"TOPIC": "memory-test/payloads"},
    "NOTIF_CHANNEL": {"TOPIC": "memory-test/notif"},
  }
  app = {
    "COMMUNICATION": {"TYPE": "mqtt", "PARAMS": params, "INSTANCES": {
      "COMMANDCONTROL": {"RECV_FROM": "CONFIG_CHANNEL"},
      "DEFAULT": {"SEND_TO": "PAYLOADS_CHANNEL"},
      "HEARTBEATS": {"RECV_FROM": "CTRL_CHANNEL", "SEND_TO": "CTRL_CHANNEL"},
      "NOTIFICATIONS": {"SEND_TO": "NOTIF_CHANNEL"},
    }},
    "FILE_UPLOAD": {"TYPE": "minio", "CONFIG_UPLOADER": {}},
  }
  pipeline = {"NAME": "memory_probe", "TYPE": "MemoryCounter", "CAP_RESOLUTION": 20,
    "PLUGINS": [{"SIGNATURE": "MEMORY_SCAN_PROBE", "INSTANCES": [{"INSTANCE_ID": "probe"}]}]}
  for path, value in ((config / "startup.json", startup), (config / "app.json", app),
                      (streams / "probe.json", pipeline)):
    path.write_text(json.dumps(value, indent=2))
  (config / "mosquitto.conf").write_text(
    "listener 1883 127.0.0.1\nallow_anonymous true\npersistence false\n"
  )
  created = []
  report = {"passed": False, "edge_image": EDGE_IMAGE, "broker_image": BROKER_IMAGE,
    "scenario": arguments.scenario, "memory_limit_bytes": memory_limit}
  try:
    created.append(node)
    docker("run", "-d", "--name", node, "--network", "none", "--cap-drop", "ALL",
      "--security-opt", "no-new-privileges", "--memory", str(memory_limit), "--cpus", "2",
      "--memory-swap", str(memory_limit),
      "--pids-limit", "256", "--entrypoint", "python3", "-w", "/runtime",
      "-e", "PYTHONPATH=/opt/core:/edge_node", "-e", "PYTHONDONTWRITEBYTECODE=1",
      "-e", "AINODE_DOCKER=Yes", "-e", "EE_DD=0", "-e", "EE_ETH_ENABLED=false",
      "-e", "EE_CONFIG=/test-config/startup.json", "-e", "EE_ID=memfix01",
      "-e", "R1_MEMORY_EVIDENCE=/evidence",
      "-e", "R1_MEMORY_SCENARIO=" + arguments.scenario,
      "-e", "R1_MEMORY_LIMIT_BYTES=" + str(memory_limit),
      "-v", f"{source_root}:/opt/core:ro", "-v", f"{fixture}:/testbed:ro",
      "-v", f"{config}:/test-config:ro", "-v", f"{runtime}:/runtime",
      "-v", f"{evidence}:/evidence",
      "-v", f"{fixture / 'memory_scan_probe.py'}:/edge_node/extensions/business/memory_scan_probe.py:ro",
      "-v", f"{fixture / 'memory_counter.py'}:/edge_node/extensions/data/memory_counter.py:ro",
      EDGE_IMAGE, "/testbed/launch.py")
    created.append(broker)
    docker("run", "-d", "--name", broker, "--network", "container:" + node,
      "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "128m",
      "--cpus", "0.25", "--user", "1883:1883", "--entrypoint", "mosquitto",
      "-v", f"{config / 'mosquitto.conf'}:/test.conf:ro", BROKER_IMAGE, "-c", "/test.conf")
    deadline = time.monotonic() + (300 if arguments.scenario == "memory-growth" else 180)
    while time.monotonic() < deadline:
      state = json.loads(docker("inspect", node).stdout)[0]
      assert state["State"]["Running"], "Node exited before acceptance"
      recent_logs = docker("logs", "--tail", "200", node)
      if "Plugin init FAILED for business plugin MEMORY_SCAN_PROBE" in recent_logs.stdout + recent_logs.stderr:
        raise RuntimeError("Test plugin failed startup validation; see captured node log")
      if (evidence / "failure.json").exists():
        raise AssertionError((evidence / "failure.json").read_text())
      if (evidence / "probe.json").exists() and (evidence / "mqtt.json").exists():
        probe = json.loads((evidence / "probe.json").read_text())
        mqtt = json.loads((evidence / "mqtt.json").read_text())
        pressure = pressure_acceptance(probe, mqtt) if arguments.scenario == "memory-growth" else {}
        if (probe["checks"]["passed"] and probe["main_thread_scans"] > 0
            and probe["queue_overflows"] > 0 and probe["process_count"] >= 12
            and mqtt["heartbeats"] >= 3 and mqtt["last_heartbeat"] > probe["first_overflow_time"]
            and pressure is not None):
          if arguments.scenario == "boundary":
            assert probe["max_scan_seconds"] < MAX_SCAN_SECONDS, "Diagnostic stalled the main loop"
          assert state["HostConfig"]["NetworkMode"] == "none"
          assert not state["HostConfig"]["Privileged"] and not state["HostConfig"]["PortBindings"]
          assert state["RestartCount"] == 0 and not state["State"]["OOMKilled"]
          assert state["HostConfig"]["Memory"] == memory_limit
          assert state["HostConfig"]["MemorySwap"] == memory_limit
          provenance = json.loads((evidence / "provenance.json").read_text())
          expected_source = source_root / "naeural_core/business/base/base_plugin_biz.py"
          assert provenance["source_sha256"] == hashlib.sha256(expected_source.read_bytes()).hexdigest()
          assert provenance["fixture_sha256"] == {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in fixture.glob("*.py")}
          report.update(passed=True, probe=probe, mqtt=mqtt, provenance=provenance,
            pressure_summary=pressure,
            isolation=json.loads((evidence / "isolation.json").read_text()),
            node_id=state["Id"], restart_count=state["RestartCount"],
            oom_killed=state["State"]["OOMKilled"], privileged=state["HostConfig"]["Privileged"],
            network_mode=state["HostConfig"]["NetworkMode"], port_bindings=state["HostConfig"]["PortBindings"])
          if pressure.get("latency_violations"):
            report["passed"] = False
            raise AssertionError("Diagnostic exceeded two seconds; see retained timing violations")
          print(json.dumps(report, indent=2), flush=True)
          break
      time.sleep(2)
    else:
      raise RuntimeError("Full-runtime acceptance deadline exceeded")
  except Exception as error:
    report["failure"] = {"type": type(error).__name__, "message": str(error)}
    raise
  finally:
    cleanup_errors = []
    for name in reversed(created):
      if docker("inspect", name, check=False).returncode:
        continue
      result = docker("logs", "--tail", "2000", name, check=False)
      (evidence / (name + ".log")).write_text(result.stdout + result.stderr)
      result = docker("rm", "-f", name, check=False)
      if result.returncode:
        cleanup_errors.append({"container": name, "error": result.stderr})
    after = existing_services()
    report.update(cleanup_errors=cleanup_errors, existing_services_unchanged=before == after)
    (evidence / "services-after.json").write_text(json.dumps(after, indent=2))
    if not cleanup_errors:
      shutil.rmtree(runtime)
    (evidence / "result.json").write_text(json.dumps(report, indent=2))
    assert not cleanup_errors and before == after, "Cleanup or existing-service identity check failed"


if __name__ == "__main__":
  main()

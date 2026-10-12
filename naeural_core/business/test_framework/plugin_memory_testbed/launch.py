"""Launch the full production orchestrator inside an interface-free test container."""

import hashlib
import importlib.metadata
import json
import multiprocessing
import os
from pathlib import Path
import socket
import time

import paho.mqtt.client as mqtt


def main():
  """Prove isolation, observe loopback MQTT, then run the real runtime entrypoint.

  Returns
  -------
  int
    Production entrypoint exit code.
  """
  evidence = Path(os.environ["R1_MEMORY_EVIDENCE"])
  interfaces = [name for _, name in socket.if_nameindex()]
  routes = Path("/proc/net/route").read_text().splitlines()[1:]
  assert interfaces == ["lo"], interfaces
  assert not routes, routes
  try:
    with socket.create_connection(("1.1.1.1", 443), timeout=0.5):
      raise AssertionError("External networking is reachable")
  except OSError as error:
    isolation = {"interfaces": interfaces, "routes": routes, "external_errno": error.errno}
  (evidence / "isolation.json").write_text(json.dumps(isolation, indent=2))

  deadline = time.monotonic() + 60
  while True:
    try:
      with socket.create_connection(("127.0.0.1", 1883), timeout=1):
        break
    except OSError:
      if time.monotonic() >= deadline:
        raise RuntimeError("Loopback MQTT broker did not become ready")
      time.sleep(0.5)

  counts = {"heartbeats": 0, "first_heartbeat": None, "last_heartbeat": None,
    "heartbeat_times": []}
  client = mqtt.Client(client_id="memory-test-observer")

  def on_connect(client, userdata, flags, reason_code, *args):
    client.subscribe("memory-test/#", qos=1)

  def on_message(client, userdata, message):
    if message.topic != "memory-test/ctrl":
      return
    payload = json.loads(message.payload)
    if payload.get("EE_EVENT_TYPE") != "HEARTBEAT":
      return
    counts["heartbeats"] += 1
    counts["first_heartbeat"] = counts["first_heartbeat"] or time.time()
    counts["last_heartbeat"] = time.time()
    counts["heartbeat_times"].append(counts["last_heartbeat"])
    counts["heartbeat_times"] = counts["heartbeat_times"][-256:]
    temporary = evidence / "mqtt.tmp"
    temporary.write_text(json.dumps(counts, indent=2))
    temporary.replace(evidence / "mqtt.json")

  client.on_connect = on_connect
  client.on_message = on_message
  client.connect("127.0.0.1", 1883, keepalive=30)
  client.loop_start()
  for name in ("plugins", "extensions"):
    Path(name).symlink_to("/edge_node/" + name, target_is_directory=True)

  from naeural_core.business.base import base_plugin_biz
  from naeural_core.main.entrypoint import main as run_node

  source = Path(base_plugin_biz.__file__)
  provenance = {
    "source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "packages": {name: importlib.metadata.version(name) for name in ("naeural-core", "ratio1")},
    "python": os.sys.version,
    "fixture_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
      for path in Path(__file__).resolve().parent.glob("*.py")},
  }
  (evidence / "provenance.json").write_text(json.dumps(provenance, indent=2))
  exit_code, _ = run_node(additional_packages=[])
  client.loop_stop()
  client.disconnect()
  return exit_code


if __name__ == "__main__":
  multiprocessing.set_start_method("spawn")
  raise SystemExit(main())

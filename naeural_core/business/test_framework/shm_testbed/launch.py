"""Fail-closed launch of the complete Edge runtime and loopback MQTT observer."""

import errno
import hashlib
import importlib.metadata
import json
import multiprocessing
import os
from pathlib import Path
import socket
import sys
import time


def main():
  """Verify isolation before importing runtime code and execute its entrypoint."""
  evidence = Path(os.environ["R1_SHM_EVIDENCE"])
  interfaces = [name for _, name in socket.if_nameindex()]
  routes = Path("/proc/net/route").read_text().splitlines()[1:]
  assert interfaces == ["lo"] and not routes, (interfaces, routes)
  try:
    with socket.create_connection(("1.1.1.1", 443), timeout=0.5):
      raise AssertionError("External networking is reachable")
  except OSError as error:
    assert error.errno == errno.ENETUNREACH, error
    (evidence / "isolation.json").write_text(json.dumps({
      "interfaces": interfaces, "routes": routes, "external_errno": error.errno}))
  deadline = time.monotonic() + 60
  while True:
    try:
      with socket.create_connection(("127.0.0.1", 1883), timeout=1):
        break
    except OSError:
      if time.monotonic() > deadline:
        raise RuntimeError("Loopback MQTT did not become ready")
      time.sleep(0.5)

  import paho.mqtt.client as mqtt
  counts = {"heartbeats": [], "payloads": 0}
  client = mqtt.Client(client_id="shm-test-observer")

  def on_connect(client, userdata, flags, code, *args):
    client.subscribe("shm-test/#", qos=1)

  def on_message(client, userdata, message):
    payload = json.loads(message.payload)
    if payload.get("EE_EVENT_TYPE") == "HEARTBEAT":
      counts["heartbeats"].append(time.time())
      counts["heartbeats"] = counts["heartbeats"][-256:]
    elif payload.get("EE_EVENT_TYPE") == "PAYLOAD":
      counts["payloads"] += 1
    temporary = evidence / "mqtt.tmp"
    temporary.write_text(json.dumps(counts))
    temporary.replace(evidence / "mqtt.json")

  client.on_connect = on_connect
  client.on_message = on_message
  client.connect("127.0.0.1", 1883, keepalive=30)
  client.loop_start()
  for name in ("plugins", "extensions"):
    Path(name).symlink_to("/edge_node/" + name, target_is_directory=True)
  from naeural_core.serving.ai_engines.stable import AI_ENGINES
  AI_ENGINES.update({name: {"SERVING_PROCESS": name}
    for name in ("shm_covered_detector", "shm_toy_detector")})
  from naeural_core.main.entrypoint import main as run_node
  files = ["naeural_core/serving/serving_manager.py", "naeural_core/utils/shm_diagnostics.py",
    "naeural_core/business/base/cv_plugin_executor.py"]
  provenance = {"python": sys.version,
    "packages": {name: importlib.metadata.version(name) for name in ("naeural-core", "ratio1")},
    "source_sha256": {name: hashlib.sha256((Path("/opt/core") / name).read_bytes()).hexdigest()
      for name in files},
    "fixtures": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
      for path in Path(__file__).parent.glob("*.py")}}
  (evidence / "provenance.json").write_text(json.dumps(provenance, indent=2))
  try:
    code, _ = run_node(additional_packages=[])
    return code
  finally:
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
  multiprocessing.set_start_method("spawn")
  raise SystemExit(main())

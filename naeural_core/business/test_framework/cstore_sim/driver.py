"""
Chain-store fleet simulator: starts one process per simulated oracle
(``node.py``), routes their messages with configurable delay, jitter and loss,
injects faults on a timeline and collects each node's measurements.

Run by path so the heavy node package is not imported:

  python driver.py --list
  python driver.py --scenario starved_node --variant new --new-src /path/to/repo
  python driver.py --scenario starved_node --variant base --base-src /path/to/old/repo

``--variant mixed`` runs half of the fleet on each source tree. Set
``CSTORE_SIM_MANAGER=thread`` (or ``off``) to run the new code without the
serialization worker process (or without the manager).
"""
import argparse
import hashlib
import heapq
import json
import os
import queue
import random
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _frame(header, body=b""):
  head = json.dumps(header).encode()
  return struct.pack(">I", len(head)) + head + struct.pack(">I", len(body)) + body


class Node:
  def __init__(self, cfg):
    self.cfg, self.name = cfg, cfg["name"]
    self.addr, self.hello, self.report, self.ready = None, None, None, threading.Event()
    self.outq, self.proc, self.alive = queue.Queue(), None, False

  def start(self, hub):
    self.report, self.alive = None, True
    self.outq = queue.Queue()  # nothing queued for a previous process of this node
    self.ready.clear()
    self.proc = subprocess.Popen([sys.executable, str(HERE / "node.py"), json.dumps(self.cfg)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(Path(self.cfg["data_dir"]) / "stderr.log", "ab"))
    threading.Thread(target=self._reader, args=(hub, self.proc), daemon=True).start()
    threading.Thread(target=self._writer, args=(self.proc,), daemon=True).start()

  def _reader(self, hub, proc):
    """Dispatch one oracle process's frames to the driver and message router.

    Notify the driver on startup, shutdown or receipt of the final report.

    Parameters
    ----------
    hub : Hub
      Router for frames carrying simulated network traffic.
    proc : subprocess.Popen
      Oracle process associated with this reader.

    Returns
    -------
    None

    Examples
    --------
    >>> node._reader(hub, node.proc)  # doctest: +SKIP
    """
    stream = proc.stdout
    while True:
      raw = stream.read(4)
      if len(raw) < 4:
        break
      header = json.loads(stream.read(struct.unpack(">I", raw)[0]))
      body = stream.read(struct.unpack(">I", stream.read(4))[0])
      kind = header["t"]
      if kind == "msg":
        hub.route(self, header["dst"], body)
      elif kind == "hello":
        self.addr, self.hello = header["addr"], header
        self.ready.set()
      elif kind == "ready":
        self.ready.set()
      elif kind == "report":
        self.report = json.loads(body)
        self.ready.set()
    if proc is self.proc:
      self.alive = False
      # A node exiting during initialization should wake the driver immediately.
      self.ready.set()

  def _writer(self, proc):
    while proc.poll() is None:
      try:
        data = self.outq.get(timeout=0.5)
      except queue.Empty:
        continue
      if proc is not self.proc:
        return
      try:
        proc.stdin.write(data)
        proc.stdin.flush()
      except Exception:
        return

  def send(self, header, body=b""):
    self.outq.put(_frame(header, body))

  def steal_loop(self, share, stop):
    """Suspend the oracle to simulate time withheld by the host.

    The process is frozen in 20–80 ms bursts for the requested share of time.

    Parameters
    ----------
    share : float
      Fraction of elapsed time the oracle should be stopped.
    stop : threading.Event
      Signals that no more suspension should be started.

    Returns
    -------
    None

    Examples
    --------
    >>> node.steal_loop(0.4, stop_event)  # doctest: +SKIP
    """
    # Use a stable seed so process-wide hash randomization does not change the suspension schedule.
    rnd = random.Random(self.name)
    while not stop.is_set() and self.proc.poll() is None:
      burst = 0.02 + rnd.random() * 0.06
      try:
        self.proc.send_signal(signal.SIGSTOP)
        time.sleep(burst)
        self.proc.send_signal(signal.SIGCONT)
      except Exception:
        return
      time.sleep(burst * (1 - share) / share)


class Hub:
  """Message router with per-copy delay, jitter and loss; counts what crosses the network."""

  def __init__(self, net, seed=1):
    self.net, self.rnd = net, random.Random(seed)
    self.by_addr, self.heap, self.lock, self.seq = {}, [], threading.Condition(), 0
    self.msgs = self.bytes = self.dropped = 0
    self.isolated, self.stop = set(), False
    threading.Thread(target=self._deliver, daemon=True).start()

  def route(self, src, dst, body):
    now = time.time()
    with self.lock:
      for addr in dst:
        node = self.by_addr.get(addr)
        if node is None or not node.alive or node.name in self.isolated or src.name in self.isolated:
          self.dropped += 1
          continue
        if self.rnd.random() < self.net.get("loss", 0):
          self.dropped += 1
          continue
        delay = (self.net.get("delay_ms", 20) + self.rnd.random() * self.net.get("jitter_ms", 10)) / 1000.0
        self.seq += 1
        heapq.heappush(self.heap, (now + delay, self.seq, node, body))
        self.msgs += 1
        self.bytes += len(body)
      self.lock.notify()

  def _deliver(self):
    while not self.stop:
      with self.lock:
        while not self.heap or self.heap[0][0] > time.time():
          self.lock.wait(timeout=0.5 if not self.heap else max(0.001, self.heap[0][0] - time.time()))
          if self.stop:
            return
        _, _, node, body = heapq.heappop(self.heap)
      node.send({"t": "msg"}, body)


SOURCE_FILES = (
  "naeural_core/business/default/admin/chain_store_base.py", "naeural_core/business/mixins_libs/network_processor_mixin.py",
  "naeural_core/utils/plugins_base/persistence_serialization_mixin.py", "naeural_core/main/serialization_manager.py",
)


def _source_hash(root):
  """Short hash of the source files a node loads from ``root``, to tie a result to the code it ran."""
  digest = hashlib.sha256()
  for name in SOURCE_FILES:
    path = Path(root) / name
    digest.update(name.encode())
    digest.update(path.read_bytes() if path.exists() else b"-")
  return digest.hexdigest()[:16]


def _pct(values, pct):
  values = sorted(values)
  return values[min(len(values) - 1, int(len(values) * pct))] if values else None


def summarize(report, duration, warmup=0.0):
  """Reduce one node report to the numbers that are compared between variants."""
  res, stats = report["results"], report["stats"]
  ticks = [ts for ts in res.get("loop_ts", [])]
  windows = {}
  for ts in ticks:
    windows[int(ts // 5)] = windows.get(int(ts // 5), 0) + 1
  rates = sorted(count / 5.0 for count in list(windows.values())[1:-1]) or [0]
  out = {
    "loop_hz_mean": round(stats.get("iterations", 0) / max(duration, 1e-9), 2),
    "loop_hz_min5s": round(rates[0], 2), "loop_hz_median5s": round(rates[len(rates) // 2], 2),
    "max_queue": stats.get("max_queue", 0), "lost_inputs": report["lost_inputs"], "queue_end": report["queue_now"],
    "received": stats.get("received", 0), "save_calls": report["save_calls"],
    "handled": stats.get("received", 0) - report["queue_now"] - report["lost_inputs"],
    "queue_growth_per_min": round(60.0 * report["queue_now"] / max(duration, 1e-9), 1),
    "save_thread_s": report["save_thread_seconds"], "save_thread_max_s": report["save_thread_max"],
    "cpu_s": report["cpu_seconds"], "sent_copies": stats.get("sent_copies", 0), "sent_mb": round(stats.get("sent_bytes", 0) / 1e6, 2),
    "sent_store": stats.get("sent_SSTORE", 0), "sent_confirm": stats.get("sent_SCONFIRM", 0),
    "sent_hsync_resp_mb": round(stats.get("plain_bytes_SHSYNC_RESP", 0) / 1e6, 3),
    "sent_hsync_req_mb": round(stats.get("plain_bytes_SHSYNC_REQ", 0) / 1e6, 3),
    "loop_errors": stats.get("loop_errors", 0), "rss_mb": report["rss_mb"], "plugin_stats": report.get("plugin_stats"),
    "worker_kills": stats.get("worker_kills", 0), "keys": report.get("keys"), "loaded_keys": stats.get("loaded_keys", 0),
    "manager": report.get("manager"), "worker": report.get("worker"),
  }
  for tag in ("local_write", "op_write", "op_republish"):
    rows = [row for row in res.get(tag, []) if row[0] >= warmup]
    if rows:
      good = [row[1] for row in rows if row[2]]
      out[tag] = {"n": len(rows), "ok": len(good), "ok_pct": round(100.0 * len(good) / len(rows), 1),
                  "p50_s": _pct(good, 0.5), "p95_s": _pct(good, 0.95), "max_s": max(row[1] for row in rows)}
  rows = res.get("hsync", [])
  if rows:
    good = [row for row in rows if row[2]]
    out["hsync"] = {"n": len(rows), "ok": len(good), "p50_s": _pct([row[1] for row in good], 0.5),
                    "merged": sum(row[3] or 0 for row in good), "responses": sum(row[4] or 0 for row in good),
                    "first_ok_at_s": good[0][0] + good[0][1] if good else None}
  if res.get("secret_state"):
    out["secret_state"] = res["secret_state"]
  out["secrets"] = report.get("secrets")
  out["hsync_rows"] = rows
  if res.get("big_save"):
    out["big_save"] = {"n": len(res["big_save"]), "caller_max_s": max(res["big_save"]), "caller_mean_s": round(sum(res["big_save"]) / len(res["big_save"]), 4)}
  return out


def run(spec, base_src=None, new_src=None, variant="new", workdir=None, keep=False, verbose=True):
  """
  Run one scenario and return ``{"nodes": {name: summary}, "net": {...}, "raw": {...}}``.

  Parameters
  ----------
  spec : dict
    Scenario from ``SCENARIOS`` (nodes, duration, network, events).
  base_src, new_src : str
    Repository roots that hold the old and the new plugin sources.
  variant : str
    ``base``, ``new`` or ``mixed`` (odd-numbered nodes run the old sources).
  workdir : str or None, optional
    Retain node files in this directory, or use a fresh temporary directory.
  keep : bool, optional
    Retain the temporary directory after the run.
  verbose : bool, optional
    Print the fault schedule as events execute.

  Returns
  -------
  dict
    Oracle metrics and digests, traffic totals, source identities and output directory.

  Examples
  --------
  >>> run(scenario_healthy(0.12), new_src="/src")  # doctest: +SKIP
  """
  root = Path(workdir or tempfile.mkdtemp(prefix="cstore_sim_"))
  hub = Hub(spec.get("net", {}))
  nodes = []
  for idx, cfg in enumerate(spec["nodes"]):
    cfg = dict(cfg)
    use_new = variant == "new" or (variant == "mixed" and idx % 2 == 0)
    use_new = cfg.pop("force_new", use_new) if variant == "mixed" else use_new
    cfg["source_dir"] = new_src if use_new else base_src
    cfg["variant"] = "new" if use_new else "base"
    cfg.setdefault("serialization_manager", os.environ.get("CSTORE_SIM_MANAGER") or spec.get("serialization_manager", "process"))
    for key, value in list(cfg.items()):
      if key.startswith("new_") and use_new:
        cfg[key[4:]] = value
    cfg["data_dir"] = str(root / cfg["name"])
    os.makedirs(cfg["data_dir"], exist_ok=True)
    nodes.append(Node(cfg))
  by_name = {node.name: node for node in nodes}
  for node in nodes:
    node.start(hub)
  for node in nodes:
    if not node.ready.wait(120) or not node.alive:
      raise RuntimeError("node {} did not start; see {}".format(node.name, node.cfg["data_dir"]))
    hub.by_addr[node.addr] = node
  roster = {node.addr: {"eth": node.hello["eth"], "dauth": node.hello["dauth"], "name": node.name} for node in nodes}
  for node in nodes:
    node.ready.clear()
    node.send({"t": "roster", "nodes": roster})
  for node in nodes:
    if not node.ready.wait(180) or not node.alive:
      raise RuntimeError("node {} did not become ready; see {}".format(node.name, node.cfg["data_dir"]))
  start = time.time()
  steal_stop = threading.Event()
  for node in nodes:
    node.send({"t": "go"})
    if node.cfg.get("steal"):
      threading.Thread(target=node.steal_loop, args=(node.cfg["steal"], steal_stop), daemon=True).start()
  for event in sorted(spec.get("events", []), key=lambda item: item["at"]):
    time.sleep(max(0, start + event["at"] - time.time()))
    kind, target = event["type"], by_name.get(event.get("node"))
    if verbose:
      print("  t={:.0f}s event {} {}".format(event["at"], kind, event.get("node", "")), file=sys.stderr, flush=True)
    if kind == "isolate":
      hub.isolated.add(target.name)
    elif kind == "rejoin":
      hub.isolated.discard(target.name)
    elif kind == "kill_worker":
      target.send({"t": "kill_worker"})
    elif kind == "sigkill":
      target.alive = False
      target.proc.kill()
      target.proc.wait()
    elif kind == "restart":
      target.cfg.update(event.get("cfg", {}))
      target.start(hub)
      if not target.ready.wait(120) or not target.alive:
        raise RuntimeError("node {} did not restart; see {}".format(target.name, target.cfg["data_dir"]))
      target.ready.clear()
      target.send({"t": "roster", "nodes": roster})
      if not target.ready.wait(180) or not target.alive:
        raise RuntimeError("node {} did not become ready after restart; see {}".format(target.name, target.cfg["data_dir"]))
      target.send({"t": "go"})
  time.sleep(max(0, start + spec["duration"] - time.time()))
  duration = time.time() - start
  for node in nodes:
    if node.alive:
      node.send({"t": "quiet"})
  time.sleep(spec.get("settle", 4))  # let in-flight writes land before comparing replicas
  steal_stop.set()
  time.sleep(0.2)
  asked = []
  for node in nodes:
    node.ready.clear()
    if node.alive:
      asked.append(node)
      node.send({"t": "stop"})
  result = {"scenario": spec["name"], "variant": variant, "duration": round(duration, 1), "nodes": {}, "raw": {},
            "sources": {name: _source_hash(src) for name, src in (("base", base_src), ("new", new_src)) if src}}
  for node in nodes:
    if node in asked and node.ready.wait(90) and node.report is not None:
      result["nodes"][node.name] = dict(summarize(node.report, duration, spec.get("warmup", 0)), variant=node.cfg["variant"])
      result["raw"][node.name] = {"digest": node.report["digest"], "keys": node.report["keys"], "pickle_bytes": node.report["pickle_bytes"]}
    else:
      result["nodes"][node.name] = {"error": "no report", "variant": node.cfg["variant"]}
    if node.proc.poll() is None:
      try:
        node.proc.wait(timeout=10)
      except Exception:
        node.proc.kill()
  hub.stop = True
  result["net"] = {"messages": hub.msgs, "mb": round(hub.bytes / 1e6, 2), "dropped": hub.dropped}
  result["workdir"] = str(root)
  if not keep and workdir is None:
    shutil.rmtree(root, ignore_errors=True)
  return result


# ---- scenarios -------------------------------------------------------------------------------

PRELOAD = {"keys": 3657, "value_bytes": 1400}  # the production replica: 3,657 keys, about 5.5 MB pickled


# A healthy production oracle saves its 5.5 MB replica in 0.3-1.9 s, not the 0.1 s of an idle interpreter:
# about one other busy thread. The oracle that collapsed took 5.7-11.2 s per save and looped about once a second.
HEALTHY = {"hogs": 1, "hog_duty": 0.6, "loop_yields": 6}
STARVED = {"hogs": 8, "hog_duty": 1.0, "loop_yields": 10, "fsync_delay_ms": 25}
STOLEN = {"hogs": 5, "hog_duty": 1.0, "loop_yields": 10, "steal": 0.4}


def _fleet(count=5, **common):
  return [dict({"name": "n%d" % idx, "preload": PRELOAD}, **HEALTHY, **common) for idx in range(count)]


# The stuck operation wrote 18,467 snapshots in 32 h (one per 6.2 s) and re-sent the unchanged one on every
# 2 s worker pass. ``rate`` compresses time: 4 means four times those rates.
def _incident(profile, name, rate=1.0, duration=240.0, scale=1.0):
  nodes = _fleet()
  nodes[0]["workloads"] = [{"type": "operation", "period": 6.2 / rate, "republish": 2.0 / rate, "bytes": 20000}]
  for node in nodes[1:4]:
    node["workloads"] = [{"type": "local", "period": 20.0 / rate}]
  nodes[4].update(profile, workloads=[
    {"type": "local", "period": 5.0, "start": 5},                                               # custody-style local writes
    {"type": "hsync", "hkey": "APP_STATE", "period": 30.0, "timeout": 5, "start": 8},           # dAuth-style refresh, 5 s timeout
  ])
  return {"name": name, "duration": duration * scale, "warmup": 10, "nodes": nodes, "net": {"delay_ms": 20, "jitter_ms": 20}}


def scenario_healthy(scale=1.0):
  """Control: five healthy oracles receive the stuck-operation stream at the incident rate."""
  return _incident({}, "healthy", duration=120.0, scale=scale)


def scenario_incident(scale=1.0):
  """
  The incident at its real rate: one oracle whose interpreter is saturated and whose disk is slower (it loops
  about once every few seconds, as measured on the oracle that collapsed) receives the stuck-operation stream
  while a local service writes records and refreshes a namespace.
  """
  return _incident(STARVED, "incident", scale=scale)


def scenario_incident_x4(scale=1.0):
  """The same, time-compressed four times and run long enough for the old code to overflow its input queue."""
  return _incident(STARVED, "incident_x4", rate=4.0, duration=600.0, scale=scale)


def scenario_stolen_cpu(scale=1.0):
  """
  The stream at its real rate into an oracle on a host that withholds 40 % of its CPU time without reporting
  it (measured on one seed oracle), with a busier interpreter.
  """
  return _incident(STOLEN, "stolen_cpu", scale=scale)


def scenario_flood(scale=1.0):
  """Capacity: three writers each publishing a new 20 KB record ten times a second to five healthy oracles."""
  nodes = _fleet()
  for node in nodes[:3]:
    node["workloads"] = [{"type": "operation", "period": 0.1, "republish": 0.05, "bytes": 20000}]
  nodes[4]["workloads"] = [{"type": "local", "period": 1.0}]
  return {"name": "flood", "duration": 45 * scale, "warmup": 5, "nodes": nodes, "net": {"delay_ms": 20, "jitter_ms": 20}}


def scenario_blocking_store(scale=1.0):
  """Alternative cause: every accepted remote store blocks for 0.4 s on one oracle (for example a slow lookup)."""
  spec = _incident({"store_block_ms": 400}, "blocking_store", rate=4.0, duration=120.0, scale=scale)
  return spec


def scenario_big_saves(scale=1.0):
  """A 10 MB manager snapshot (network monitor size class) saved every 5 s on a node with a slow disk."""
  nodes = _fleet(3)
  nodes[0]["workloads"] = [{"type": "operation", "period": 2.0, "republish": 1.0, "bytes": 20000}]
  nodes[2].update(fsync_delay_ms=200, workloads=[{"type": "big_save", "period": 5.0, "nodes": 400}, {"type": "local", "period": 1.0}])
  return {"name": "big_saves", "duration": 45 * scale, "warmup": 5, "nodes": nodes, "net": {"delay_ms": 20, "jitter_ms": 20}}


def scenario_lossy(scale=1.0):
  """Network trouble: 5 % loss, 150 ms delay, one oracle isolated for 20 s and then back."""
  nodes = _fleet()
  nodes[0]["workloads"] = [{"type": "operation", "period": 2.0, "republish": 1.0, "bytes": 20000}]
  for node in nodes[1:]:
    node["workloads"] = [{"type": "local", "period": 1.0}, {"type": "hsync", "hkey": "DEEPLOY_OPERATIONS", "period": 8.0, "timeout": 5, "start": 30}]
  return {"name": "lossy", "duration": 70 * scale, "warmup": 5, "nodes": nodes, "net": {"delay_ms": 150, "jitter_ms": 100, "loss": 0.05},
          "events": [{"at": 10, "type": "isolate", "node": "n3"}, {"at": 30, "type": "rejoin", "node": "n3"}]}


def scenario_restart_sync(scale=1.0):
  """
  Catch-up after an outage: while one oracle is cut off, a custodian revokes 10 of 30 secret revisions and adds
  12 newer ones, and 12 operations write 20 KB snapshots. A second oracle is cut off for part of that time, so
  one peer is itself stale. Both then refresh the two namespaces every 15 s with a 5 s timeout.
  """
  nodes = _fleet()
  nodes[0]["workloads"] = [
    {"type": "secrets", "count": 30, "revoke_at": 25, "revoke": 10, "add": 12},
    {"type": "operation", "period": 0.25, "republish": 30, "bytes": 20000, "ops": 12, "start": 18, "duration": 30},
  ]
  sync = lambda start: [
    {"type": "hsync", "hkey": "DAUTH_JOB_SECRET_REVISIONS", "period": 15.0, "timeout": 5, "start": start, "all_peers": True},
    {"type": "hsync", "hkey": "DEEPLOY_OPERATIONS", "period": 15.0, "timeout": 5, "start": start + 1, "all_peers": True},
  ]
  nodes[3].update(workloads=sync(52), watch_secrets=True)
  nodes[4].update(workloads=sync(62), watch_secrets=True)
  return {"name": "restart_sync", "duration": 110 * scale, "warmup": 0, "nodes": nodes, "net": {"delay_ms": 20, "jitter_ms": 20},
          "events": [{"at": 15, "type": "isolate", "node": "n4"}, {"at": 20, "type": "isolate", "node": "n3"},
                     {"at": 50, "type": "rejoin", "node": "n3"}, {"at": 60, "type": "rejoin", "node": "n4"}]}


def scenario_crash(scale=1.0):
  """Recover a killed oracle while the fleet reports ordering decisions.

  The oracle restarts after six seconds and reloads its replica before
  refreshing two namespaces. The fleet continues writing while the oracle
  refreshes. Older implementations with a separate serialization worker
  also receive worker kill events.

  Parameters
  ----------
  scale : float, optional
    Multiplier for the workload duration. Fault timings stay fixed.

  Returns
  -------
  dict
    Scenario configuration for four oracles and the crash and restart events.

  Examples
  --------
  >>> scenario_crash()["duration"]
  75.0
  """
  # Specify the shipped report behavior explicitly so future switches do not change this comparison.
  nodes = _fleet(4, plugin_config={"CSTORE_ORDER": "report"})
  nodes[0]["workloads"] = [{"type": "operation", "period": 1.0, "republish": 0.5, "bytes": 20000}]
  for node in nodes[1:]:
    node["workloads"] = [{"type": "local", "period": 0.5}]
  back = [{"type": "local", "period": 0.5},
          {"type": "hsync", "hkey": "APP_STATE", "period": 10.0, "timeout": 5, "start": 3, "all_peers": True},
          {"type": "hsync", "hkey": "DEEPLOY_OPERATIONS", "period": 10.0, "timeout": 5, "start": 4, "all_peers": True}]
  return {"name": "crash", "duration": 75 * scale, "warmup": 5, "nodes": nodes, "net": {"delay_ms": 20, "jitter_ms": 20},
          "events": [{"at": 12, "type": "kill_worker", "node": "n2"}, {"at": 24, "type": "kill_worker", "node": "n2"},
                     {"at": 36, "type": "sigkill", "node": "n3"}, {"at": 42, "type": "restart", "node": "n3", "cfg": {"workloads": back}}]}


def scenario_crash_ordered(scale=1.0):
  """Repeat the process crash with record ordering enabled where supported.

  The workload, network and restart schedule match the report-mode scenario.

  Parameters
  ----------
  scale : float, optional
    Multiplier for the workload duration.

  Returns
  -------
  dict
    Crash scenario with enforced record ordering on its oracle processes.

  Examples
  --------
  >>> scenario_crash_ordered()["name"]
  'crash_ordered'
  """
  # Reuse the same fault and workload schedule for the comparison.
  spec = scenario_crash(scale)
  # Label the report so the two modes are distinguishable in saved results.
  spec["name"] = "crash_ordered"
  for node in spec["nodes"]:
    # Older sources ignore the switch and retain their own write admission.
    node["plugin_config"] = {"CSTORE_ORDER": "on"}
  return spec


SCENARIOS = {
  "healthy": scenario_healthy, "incident": scenario_incident, "incident_x4": scenario_incident_x4,
  "stolen_cpu": scenario_stolen_cpu, "flood": scenario_flood,
  "blocking_store": scenario_blocking_store, "big_saves": scenario_big_saves, "lossy": scenario_lossy, "crash": scenario_crash,
  "restart_sync": scenario_restart_sync, "crash_ordered": scenario_crash_ordered,
}


def main():
  """Run the selected scenario and print its JSON report.

  The CLI can also list scenarios without starting any processes.

  Parameters
  ----------
  None

  Returns
  -------
  None

  Examples
  --------
  >>> main()  # doctest: +SKIP
  """
  parser = argparse.ArgumentParser()
  parser.add_argument("--scenario", default="healthy")
  parser.add_argument("--variant", default="new", choices=["base", "new", "mixed"])
  parser.add_argument("--base-src")
  parser.add_argument("--new-src", default=str(HERE.parents[3]))
  parser.add_argument("--scale", type=float, default=1.0)
  parser.add_argument("--out")
  parser.add_argument("--keep", action="store_true")
  parser.add_argument("--workdir", help="keep node folders (logs, replicas) here")
  parser.add_argument("--list", action="store_true")
  args = parser.parse_args()
  if args.list:
    for name, func in SCENARIOS.items():
      print("{:16s} {}".format(name, " ".join((func.__doc__ or "").split())))
    return
  result = run(SCENARIOS[args.scenario](args.scale), base_src=args.base_src, new_src=args.new_src, variant=args.variant, keep=args.keep, workdir=args.workdir)
  raw = result.pop("raw")
  digests = [json.dumps(item["digest"], sort_keys=True) for item in raw.values()]
  # Convergence requires a report from every oracle, including any restarted oracle.
  result["replicas_identical"] = len(digests) == len(result["nodes"]) and len(set(digests)) == 1
  result["replica_keys"] = {name: len(item["digest"]) for name, item in raw.items()}
  text = json.dumps(result, indent=1)
  if args.out:
    Path(args.out).write_text(text)
  print(text)


if __name__ == "__main__":
  signal.signal(signal.SIGINT, lambda *_: os._exit(130))
  main()

"""
Chain-store fleet simulator: starts one process per simulated oracle
(``node.py``), routes their messages with configurable delay, jitter and loss,
injects faults on a timeline and collects each node's measurements.

Run by path so the heavy node package is not imported:

  python driver.py --list
  python driver.py --scenario incident --variant new --new-src /path/to/repo
  python driver.py --scenario incident --variant base --base-src /path/to/old/repo

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
  """Encode one frame for the simulator's subprocess pipes.

  Prefix the header and body with their separate byte counts.

  Parameters
  ----------
  header : dict
    JSON-serializable metadata including the message type.
  body : bytes, optional
    Opaque payload, empty for a control frame.

  Returns
  -------
  bytes
    Two length-prefixed fields, each using a four-byte big-endian size.

  Raises
  ------
  TypeError or ValueError
    Header data cannot be encoded as JSON.
  struct.error
    A field is too long for an unsigned four-byte count.

  Examples
  --------
  >>> len(_frame({"t": "go"}))
  19

  Notes
  -----
  Bytes in the body are forwarded unchanged, including encrypted messages.
  """
  # Use JSON for control metadata while leaving the payload in its existing format.
  head = json.dumps(header).encode()
  # Encode independent lengths so pipes can carry arbitrary binary payloads.
  return struct.pack(">I", len(head)) + head + struct.pack(">I", len(body)) + body


class Node:
  """Manage one simulated oracle process and its pipe traffic.

  Keep the last report, address and readiness state in the driver.
  Reader and writer threads exchange frames with the subprocess.

  Parameters
  ----------
  cfg : dict
    Oracle settings with its name and, before launch, source and data paths.

  Returns
  -------
  Node
    A managed oracle that has yet to start.

  Raises
  ------
  KeyError
    The configuration has no node name.

  Examples
  --------
  >>> Node({"name": "n0"}).name
  'n0'

  Notes
  -----
  The same driver object can start a replacement process with the existing
  oracle data directory.
  """
  def __init__(self, cfg):
    """Store the oracle configuration and initialize its driver state.

    The driver fills the process and address fields after launch.

    Parameters
    ----------
    cfg : dict
      Oracle settings, including its name.

    Returns
    -------
    None

    Raises
    ------
    KeyError
      The oracle configuration lacks its name.

    Examples
    --------
    >>> Node({"name": "n0"}).alive
    False

    Notes
    -----
    The supplied configuration is kept by reference for restart overrides.
    """
    # Keep the scenario configuration available for the next launch.
    self.cfg, self.name = cfg, cfg["name"]
    # One event wakes the driver on startup and on report or exit.
    self.addr, self.hello, self.report, self.ready = None, None, None, threading.Event()
    # No subprocess or outgoing frames exist before launch.
    self.outq, self.proc, self.alive = queue.Queue(), None, False

  def start(self, hub):
    """Launch the oracle and start its pipe handlers.

    Reset the process-local coordination state for the new oracle run.

    Parameters
    ----------
    hub : Hub
      Routes network frames emitted by this oracle.

    Returns
    -------
    None
      Pipe handlers have been started. Readiness is signaled asynchronously.

    Raises
    ------
    OSError
      Opening the diagnostic log or starting the interpreter failed.
    TypeError or ValueError
      Oracle settings could not be encoded for the new process.
    KeyError
      The oracle configuration has no data directory.
    RuntimeError
      A pipe handler thread could not be started.

    Examples
    --------
    >>> node.start(hub)  # doctest: +SKIP

    Notes
    -----
    A restart keeps files in the data directory and discards frames queued
    for the previous process. Standard error is appended to its own log.
    """
    # Clear prior results before the new process can send an observation.
    self.report, self.alive = None, True
    # Discard stale frames instead of handing them to the replacement process.
    self.outq = queue.Queue()
    # Wait for the new oracle's own ready frame.
    self.ready.clear()
    # Keep control traffic on stdin/stdout and diagnostics in the oracle's own file.
    self.proc = subprocess.Popen([sys.executable, str(HERE / "node.py"), json.dumps(self.cfg)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(Path(self.cfg["data_dir"]) / "stderr.log", "ab"))
    # Route incoming frames without blocking the scenario scheduler.
    threading.Thread(target=self._reader, args=(hub, self.proc), daemon=True).start()
    # Drain outbound frames independently of the incoming pipe.
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

    Raises
    ------
    OSError
      Reading the subprocess output failed.
    ValueError
      Frame metadata or a final report is malformed JSON.
    struct.error
      The payload length prefix is incomplete.
    KeyError
      A frame lacks the metadata expected for its type.

    Examples
    --------
    >>> node._reader(hub, node.proc)  # doctest: +SKIP

    Notes
    -----
    Ignore a previous process's exit when updating the current node's
    liveness. A short initial length read ends this reader's loop.
    """
    # Read from this subprocess even if the scenario starts a replacement later.
    stream = proc.stdout
    while True:
      # A short first length read ends this subprocess's frame stream.
      raw = stream.read(4)
      if len(raw) < 4:
        break
      # This JSON header describes how to handle the independent body bytes.
      header = json.loads(stream.read(struct.unpack(">I", raw)[0]))
      # Preserve network payload bytes without re-encoding them.
      body = stream.read(struct.unpack(">I", stream.read(4))[0])
      # Route controls locally and network-message frames through the hub.
      kind = header["t"]
      if kind == "msg":
        # Apply simulated topology and timings to each destination.
        hub.route(self, header["dst"], body)
      elif kind == "hello":
        # Save identity metadata before waking the driver to build the roster.
        self.addr, self.hello = header["addr"], header
        # The driver can now use this oracle's address.
        self.ready.set()
      elif kind == "ready":
        # Release the phase barrier after the oracle initializes its workloads.
        self.ready.set()
      elif kind == "report":
        # Decode the metrics only after the subprocess has finished collecting them.
        self.report = json.loads(body)
        # Resume the driver gathering the final reports.
        self.ready.set()
    if proc is self.proc:
      # An obsolete reader's exit must not drop a replacement oracle.
      self.alive = False
      # A node exiting during initialization should wake the driver immediately.
      self.ready.set()

  def _writer(self, proc):
    """Flush queued frames to one oracle's input pipe.

    End the writer when the process exits or its input stream fails.

    Parameters
    ----------
    proc : subprocess.Popen
      Process selected when the writer thread started.

    Returns
    -------
    None

    Examples
    --------
    >>> node._writer(node.proc)  # doctest: +SKIP

    Notes
    -----
    An obsolete writer leaves when it detects that the node has restarted.
    """
    while proc.poll() is None:
      try:
        # Recheck liveness periodically even when nothing is queued.
        data = self.outq.get(timeout=0.5)
      except queue.Empty:
        continue
      if proc is not self.proc:
        return
      try:
        # Preserve frame order on the subprocess input stream.
        proc.stdin.write(data)
        # Make the frame available without waiting for the pipe buffer to fill.
        proc.stdin.flush()
      except Exception:
        return

  def send(self, header, body=b""):
    """Queue a frame for asynchronous delivery to the oracle.

    Serialize the frame before placing it on the current process's queue.

    Parameters
    ----------
    header : dict
      JSON-serializable frame metadata, including its control or message type.
    body : bytes, optional
      Opaque message data.

    Returns
    -------
    None

    Raises
    ------
    TypeError or ValueError
      The header could not be encoded.
    struct.error
      A frame field exceeds the four-byte length range.

    Examples
    --------
    >>> node.send({"t": "go"})  # doctest: +SKIP

    Notes
    -----
    This is a local enqueue. The writer thread owns the pipe.
    """
    # Enqueue complete frame bytes so the writer can forward them unchanged.
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

    Notes
    -----
    Use a positive share no greater than one. Suspension acts on the
    subprocess and depends on POSIX stop and continue signals.
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
  """Route simulated oracle traffic with configurable delay, jitter and loss.

  Schedule individual destination copies and count queued payload traffic.

  Parameters
  ----------
  net : dict
    Network settings, including optional loss and delay and jitter in milliseconds.
  seed : int, optional
    Seed for the router's random number generator.

  Returns
  -------
  Hub
    A router with a running delivery thread and empty routing tables.

  Raises
  ------
  RuntimeError
    The delivery thread could not start.

  Examples
  --------
  >>> Hub({"loss": 0.05})  # doctest: +SKIP

  Notes
  -----
  The message and byte counters describe traffic queued for eligible peers.
  Loss and unavailable peers count as drops.
  """

  def __init__(self, net, seed=1):
    """Start a delivery loop with empty routing tables and counters.

    Set up one condition for scheduled messages and one seeded loss and
    jitter generator.

    Parameters
    ----------
    net : dict
      Simulated network settings.
    seed : int, optional
      Random generator seed.

    Returns
    -------
    None

    Raises
    ------
    RuntimeError
      The delivery thread could not start.

    Examples
    --------
    >>> router = Hub({"delay_ms": 10})  # doctest: +SKIP

    Notes
    -----
    The router starts before addresses are known. The driver fills the
    address table as oracles report.
    """
    # Keep the configured link behavior and seed its per-copy choices.
    self.net, self.rnd = net, random.Random(seed)
    # One condition protects the timer heap and its sequence numbers.
    self.by_addr, self.heap, self.lock, self.seq = {}, [], threading.Condition(), 0
    # Start accounting before the first oracle announces its address.
    self.msgs = self.bytes = self.dropped = 0
    # All nodes start connected and delivery remains enabled.
    self.isolated, self.stop = set(), False
    # Process scheduled frames independently of the caller's routing loop.
    threading.Thread(target=self._deliver, daemon=True).start()

  def route(self, src, dst, body):
    """Schedule one opaque message for each eligible destination.

    Check reachability and simulated loss before applying delay and jitter.

    Parameters
    ----------
    src : Node
      Sender whose isolation status applies to every copy.
    dst : iterable of str
      Destination oracle addresses.
    body : bytes
      Message payload, forwarded without parsing.

    Returns
    -------
    None

    Examples
    --------
    >>> hub.route(sender, [receiver.addr], b"opaque")  # doctest: +SKIP

    Notes
    -----
    Every unavailable, isolated or randomly dropped destination increments
    the drop counter. Queued copies count toward message and byte totals.
    """
    # Base all copies of one send on the same routing timestamp.
    now = time.time()
    with self.lock:
      for addr in dst:
        # Skip peers that have no current process or cannot exchange traffic.
        node = self.by_addr.get(addr)
        if node is None or not node.alive or node.name in self.isolated or src.name in self.isolated:
          # Count a drop for this destination even though no payload was queued.
          self.dropped += 1
          continue
        if self.rnd.random() < self.net.get("loss", 0):
          # Apply loss independently to each eligible copy.
          self.dropped += 1
          continue
        # Convert the configured delay and random jitter to seconds.
        delay = (self.net.get("delay_ms", 20) + self.rnd.random() * self.net.get("jitter_ms", 10)) / 1000.0
        # Break equal-time heap ties without comparing node objects.
        self.seq += 1
        # Queue the copy by scheduled delivery time.
        heapq.heappush(self.heap, (now + delay, self.seq, node, body))
        # Count one queued copy for this destination.
        self.msgs += 1
        # Add its payload size without counting pipe framing overhead.
        self.bytes += len(body)
      # Wake the delivery thread if the earliest scheduled item changed.
      self.lock.notify()

  def _deliver(self):
    """Forward scheduled payloads once their due times arrive.

    Wait for work under the routing condition and send outside its lock.

    Parameters
    ----------
    None

    Returns
    -------
    None
      The router has requested shutdown.

    Examples
    --------
    >>> hub._deliver()  # doctest: +SKIP

    Notes
    -----
    Recheck the heap when a sender signals an earlier delivery time.
    """
    while not self.stop:
      with self.lock:
        while not self.heap or self.heap[0][0] > time.time():
          # Wake on a scheduling change or when the next payload becomes due.
          self.lock.wait(timeout=0.5 if not self.heap else max(0.001, self.heap[0][0] - time.time()))
          if self.stop:
            return
        # Remove one due delivery while the heap is protected.
        _, _, node, body = heapq.heappop(self.heap)
      # Queue the payload outside the condition so routing can continue.
      node.send({"t": "msg"}, body)


SOURCE_FILES = (
  "naeural_core/business/default/admin/chain_store_base.py", "naeural_core/business/mixins_libs/network_processor_mixin.py",
  "naeural_core/utils/plugins_base/persistence_serialization_mixin.py", "naeural_core/main/serialization_manager.py",
)


def _source_hash(root):
  """Identify the subset of Core source used in the simulator.

  Feed each relative name and its file contents into the same digest.

  Parameters
  ----------
  root : str or os.PathLike
    Repository root used by the simulated nodes.

  Returns
  -------
  str
    First sixteen hexadecimal digits of the SHA-256 digest.

  Raises
  ------
  OSError
    A selected source file exists but cannot be read.

  Examples
  --------
  >>> _source_hash(source_root)  # doctest: +SKIP

  Notes
  -----
  A missing file contributes a sentinel. Other repository files do not
  contribute to this identifier.
  """
  # Accumulate one reproducible source identity instead of hashing checkout metadata.
  digest = hashlib.sha256()
  for name in SOURCE_FILES:
    # Look up each loaded source file under the selected checkout.
    path = Path(root) / name
    # Include names so files with different roles contribute distinct input.
    digest.update(name.encode())
    # Distinguish absent source from an empty file.
    digest.update(path.read_bytes() if path.exists() else b"-")
  return digest.hexdigest()[:16]


def _pct(values, pct):
  """Choose an indexed percentile from sorted samples.

  Use the index given by the sample count times the requested fraction.

  Parameters
  ----------
  values : iterable of float
    Samples for one measurement.
  pct : float
    Fraction from zero to one.

  Returns
  -------
  float or None
    Selected sample, or None for an empty input.

  Examples
  --------
  >>> _pct([0.1, 0.3, 0.5], 0.5)
  0.3

  Notes
  -----
  Selection uses an existing sample without interpolation.
  """
  # Make a sorted copy so the caller's sample order stays intact.
  values = sorted(values)
  # Clamp the endpoint percentile to the last available sample.
  return values[min(len(values) - 1, int(len(values) * pct))] if values else None


def summarize(report, duration, warmup=0.0):
  """Reduce one raw oracle report to comparable scenario measurements.

  Summarize loop rates, queues, save calls, traffic and workload outcomes.

  Parameters
  ----------
  report : dict
    Raw oracle report with counters and recorded sample rows.
  duration : float
    Elapsed scenario seconds used for rate normalization.
  warmup : float, optional
    Ignore local and operation-write samples before this timestamp.

  Returns
  -------
  dict
    Per-oracle measurements and workload summaries.

  Raises
  ------
  KeyError
    A required field is absent from the simulator report.

  Examples
  --------
  >>> summarize(oracle_report, 30.0, warmup=5.0)  # doctest: +SKIP

  Notes
  -----
  Five-second loop bins exclude the first and last observed bins. Successful
  write latency percentiles use only confirmed writes. Namespace refresh
  rows are retained without applying the write warmup filter.
  """
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
  """Run one scenario and collect its oracle and network measurements.

  Start the subprocess fleet, apply scheduled faults, then ask each live
  oracle for a report after allowing in-flight writes to settle.

  Parameters
  ----------
  spec : dict
    Scenario from ``SCENARIOS`` (nodes, duration, network, events).
  base_src : str or None, optional
    Repository root passed to nodes using the baseline implementation.
  new_src : str or None, optional
    Repository root passed to nodes using the comparison implementation.
  variant : str, optional
    ``base``, ``new`` or ``mixed``. Odd-indexed mixed nodes normally use the baseline.
  workdir : str or os.PathLike or None, optional
    Retain node files in this directory, or use a fresh temporary directory.
  keep : bool, optional
    Retain the temporary directory after the run.
  verbose : bool, optional
    Print the fault schedule as events execute.

  Returns
  -------
  dict
    Oracle metrics and digests, traffic totals, source identities and output directory.

  Raises
  ------
  RuntimeError
    An oracle did not start, restart or become ready in the allotted time.
  OSError
    A subprocess, directory, or required source file could not be accessed.
  KeyError
    The scenario or an oracle report lacks a required field.

  Examples
  --------
  >>> run(scenario_healthy(0.12), new_src="/src")  # doctest: +SKIP

  Notes
  -----
  Mixed scenarios can override their source assignment per node. A missing
  final oracle report is recorded as an error entry. Default temporary
  artifacts are removed after collection unless retained.
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
  """Build named oracle configurations with healthy defaults.

  Assign a preload profile and merge the common scenario settings.

  Parameters
  ----------
  count : int, optional
    Number of configurations to create.
  **common
    Settings shared by all returned oracles.

  Returns
  -------
  list of dict
    One configuration per oracle with deterministic names.

  Raises
  ------
  TypeError
    Common settings repeat a key passed by the healthy defaults.

  Examples
  --------
  >>> _fleet(1)[0]["name"]
  'n0'

  Notes
  -----
  The preload profile is shared. Scenarios can replace individual settings
  on their returned node dictionaries.
  """
  # Copy per-oracle settings while keeping the shared preload workload consistent.
  return [dict({"name": "n%d" % idx, "preload": PRELOAD}, **HEALTHY, **common) for idx in range(count)]


# The stuck operation wrote 18,467 snapshots in 32 h (one per 6.2 s) and re-sent the unchanged one on every
# 2 s worker pass. ``rate`` compresses time: 4 means four times those rates.
def _incident(profile, name, rate=1.0, duration=240.0, scale=1.0):
  """Construct the fleet workload used by the oracle stress scenarios.

  Generate an operation stream and local writes while one oracle runs
  with the supplied runtime profile and periodic namespace refresh.

  Parameters
  ----------
  profile : dict
    Runtime overrides for the oracle under test.
  name : str
    Scenario label.
  rate : float, optional
    Multiplier for the operation and background-writer rates.
  duration : float, optional
    Scenario duration in seconds before applying scale.
  scale : float, optional
    Duration multiplier.

  Returns
  -------
  dict
    Workload and network specifications for five oracles.

  Raises
  ------
  ZeroDivisionError
    The workload rate is zero.

  Examples
  --------
  >>> _incident({}, "control", duration=120)["duration"]
  120.0

  Notes
  -----
  Changing the rate preserves the constrained oracle's own write and
  refresh schedule.
  """
  # Give the control and stressed oracles the same initial replica.
  nodes = _fleet()
  # Publish new operation snapshots and resends at separately scaled intervals.
  nodes[0]["workloads"] = [{"type": "operation", "period": 6.2 / rate, "republish": 2.0 / rate, "bytes": 20000}]
  for node in nodes[1:4]:
    # Add lighter background writes on three unaffected oracles.
    node["workloads"] = [{"type": "local", "period": 20.0 / rate}]
  # Add local writes and refresh traffic to the oracle under test.
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

  Raises
  ------
  SystemExit
    Command-line argument parsing failed.
  KeyError
    The requested scenario name is not registered.
  OSError
    The report file could not be written.

  Examples
  --------
  >>> main()  # doctest: +SKIP

  Notes
  -----
  Scenario descriptions are printed verbatim apart from whitespace
  normalization. Replica equality requires a digest from every oracle.
  A JSON report can show nonconvergence even when the command exits normally.
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

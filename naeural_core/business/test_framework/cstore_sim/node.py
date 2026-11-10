"""
One simulated oracle: its own OS process (own interpreter lock) that runs the
real chain-store plugin, the real network-processor mixin and the real
persistence mixin, with real signatures and encryption from the SDK block
engine. Only the plugin base class, the transport and the registry are
replaced.

Started by ``driver.py``: ``python node.py '<json config>'``. Frames on
stdin/stdout are ``>I header-length, header JSON, >I body-length, body``.

What is modelled, and from which production fact:
- the plugin loop: one queued input per iteration, a 20 ms minimum sleep and a
  bounded input queue that evicts the oldest input when full (plugin_loop,
  execute and add_inputs of the base plugin);
- signature check and decryption on the plugin thread, encryption and signing
  on a separate communication thread, as in the node;
- interpreter-lock pressure: ``hogs`` threads of pure Python work plus
  ``loop_yields`` points per iteration where the loop gives up the lock, as the
  real loop does on every sleep, lock and file call;
- a slow disk: ``fsync_delay_ms`` added to every fsync (also in the
  serialization worker);
- an optional blocking call per accepted remote store (``store_block_ms``).
"""
import base64
import hashlib
import json
import os
import pickle
import random
import struct
import sys
import threading
import time
import types
from collections import defaultdict, deque
from copy import deepcopy
from pathlib import Path

CFG = json.loads(sys.argv[1])
SRC = Path(CFG["source_dir"]) / "naeural_core"
DATA = Path(CFG["data_dir"])
FSYNC_DELAY = CFG.get("fsync_delay_ms", 0) / 1000.0
OUT = os.fdopen(os.dup(1), "wb")
INP = os.fdopen(os.dup(0), "rb")
sys.stdout = sys.stderr  # nothing but frames on fd 1
OUT_LOCK = threading.Lock()
T0 = time.time()
LOG = open(DATA / "node.log", "a")


def log(msg):
  LOG.write("{:8.3f} {}\n".format(time.time() - T0, msg))
  LOG.flush()


def send_frame(header, body=b""):
  head = json.dumps(header).encode()
  with OUT_LOCK:
    OUT.write(struct.pack(">I", len(head)) + head + struct.pack(">I", len(body)) + body)
    OUT.flush()


def read_frame():
  raw = INP.read(4)
  if len(raw) < 4:
    return None, None
  header = json.loads(INP.read(struct.unpack(">I", raw)[0]))
  body = INP.read(struct.unpack(">I", INP.read(4))[0])
  return header, body


if FSYNC_DELAY > 0:
  _fsync = os.fsync
  os.fsync = lambda fd: (time.sleep(FSYNC_DELAY), _fsync(fd))[1]


def load_source(path, name, extra=None, strip=()):
  text = Path(path).read_text()
  for needle in strip:
    text = text.replace(needle, "")
  space = {"__name__": "sim_" + name}
  space.update(extra or {})
  exec(compile(text, str(path), "exec"), space)
  return space[name]


# Light stand-ins for the package, so loading three source files does not import the whole node.
for mod in ("naeural_core", "naeural_core.main", "naeural_core.business", "naeural_core.business.base"):
  sys.modules.setdefault(mod, types.ModuleType(mod)).__path__ = []
sys.modules["naeural_core"].constants = types.SimpleNamespace(CACHE_SERVING="serving")
SM = None
if (SRC / "main" / "serialization_manager.py").exists():
  import importlib.util
  spec = importlib.util.spec_from_file_location("naeural_core.main.serialization_manager", SRC / "main" / "serialization_manager.py")
  SM = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = SM
  spec.loader.exec_module(SM)
  if FSYNC_DELAY > 0:
    SM._WORKER_SOURCE = "import os, time\n_f = os.fsync\nos.fsync = lambda fd: (time.sleep({}), _f(fd))[1]\n".format(FSYNC_DELAY) + SM._WORKER_SOURCE

NPMixin = load_source(SRC / "business" / "mixins_libs" / "network_processor_mixin.py", "_NetworkProcessorMixin")
PersistMixin = load_source(SRC / "utils" / "plugins_base" / "persistence_serialization_mixin.py", "_PersistenceSerializationMixin")

from ratio1 import Logger  # noqa: E402
from ratio1.bc import DefaultBlockEngine  # noqa: E402

os.chdir(DATA)
ENGINE = DefaultBlockEngine(
  log=Logger("SIM", base_folder=".", app_folder="_bc", silent=True), name=CFG["name"],
  config={"PEM_FILE": "node.pem", "PASSWORD": None, "PEM_LOCATION": "data"},
)
ROSTER = {}  # address -> {"eth":..., "dauth": bool, "name":...}
DOWN = set()


_NOW, _SLEEP = time.time, time.sleep


class PD:
  EE_SENDER, EE_ID, EE_DESTINATION = "EE_SENDER", "EE_ID", "EE_DESTINATION"
  EE_IS_ENCRYPTED, EE_ENCRYPTED_DATA, EE_PAYLOAD_PATH = "EE_IS_ENCRYPTED", "EE_ENCRYPTED_DATA", "EE_PAYLOAD_PATH"


class SimBC:
  """Plugin-facing block-engine wrapper: real crypto, simulated registry."""
  address = ENGINE.address

  def verify(self, dct_data, str_signature=None, sender_address=None, return_full_info=True, log_hash_sign_fails=True):
    return ENGINE.verify(dct_data, signature=str_signature, sender_address=sender_address, return_full_info=return_full_info)

  def decrypt_str(self, str_b64data, str_sender):
    return ENGINE.decrypt(str_b64data, str_sender)

  def get_whitelist(self, with_prefix=True):
    return [addr for addr in ROSTER if addr != ENGINE.address]

  def node_address_to_eth_address(self, address):
    return ROSTER[address]["eth"]

  def get_eth_oracles(self):
    return [node["eth"] for node in ROSTER.values()]

  def get_eth_dauth_oracles(self):
    return [node["eth"] for node in ROSTER.values() if node.get("dauth")]


class SimBase(NPMixin, PersistMixin):
  """Stands in for BasePluginExecutor: queue, loop services and helpers used by the plugin."""
  CONFIG = {"VALIDATION_RULES": {}, "MAX_INPUTS_QUEUE_SIZE": 1024, "PROCESS_DELAY": 0}
  payload_handler = staticmethod(NPMixin.payload_handler)

  def __init__(self):
    for key, value in type(self).CONFIG.items():
      setattr(self, "cfg_" + key.lower(), value)
    for key, value in CFG.get("plugin_config", {}).items():
      setattr(self, "cfg_" + key.lower(), value)
    self.cfg_skip_message_verify = False
    self.cfg_accept_self = False
    self.cfg_full_debug_payloads = False
    self.cfg_log_stats_period = None
    self.cfg_serialization_signature = None
    self.cfg_instance_id = "CHAIN_STORE_BASE_INST"
    self._stream_id, self._signature = "admin_pipeline", "CHAIN_STORE_BASE"
    self.ee_addr, self.ee_id = ENGINE.address, CFG["name"]
    self.bc, self.const = SimBC(), types.SimpleNamespace(PAYLOAD_DATA=PD)
    self.netmon = types.SimpleNamespace(network_node_is_online=lambda addr: addr not in DOWN)
    self.log = types.SimpleNamespace(get_data_folder=lambda: str(DATA), P=lambda msg, color=None: log(msg))
    self.plugins_shmem, self.inputs = {}, None
    self.upstream_inputs_deque = deque(maxlen=self.cfg_max_inputs_queue_size)
    self.lost_inputs_count, self.actual_plugin_resolution = 0, 0.0
    self.outbox = deque()
    self.save_seconds, self.save_calls, self.save_max = 0.0, 0, 0.0
    self.deque, self.deepcopy, self.defaultdict = deque, deepcopy, defaultdict

  # services
  def P(self, msg, color=None, **kwargs):
    log(str(msg)[:600])

  time = staticmethod(_NOW)
  sleep = staticmethod(_SLEEP)
  json_loads = staticmethod(json.loads)

  def json_dumps(self, value, **kwargs):
    return json.dumps(value, **kwargs)

  def uuid(self, size=13):
    return ("%032x" % random.getrandbits(128))[:size]

  def get_hash(self, text, algorithm="sha256", length=10):
    return hashlib.new(algorithm, text.encode()).hexdigest()[:length]

  def str_to_base64(self, text, url_safe=False, **kwargs):
    return (base64.urlsafe_b64encode if url_safe else base64.b64encode)(text.encode()).decode()

  def base64_to_str(self, text, url_safe=False, **kwargs):
    text = text + "=" * ((4 - len(text) % 4) % 4)
    return (base64.urlsafe_b64decode if url_safe else base64.b64decode)(text.encode()).decode()

  def trace_info(self):
    import traceback
    return traceback.format_exc()

  def on_init(self):
    return

  def on_close(self):
    return

  # persistence: the real mixin, with the folder fixed and the save timed on the calling thread
  _persistence_fld = "cache_plugins/cstore"
  plugin_id = "cstore"

  def _maybe_setup_persistence_fld(self):
    return

  def cacheapi_save_pickle(self, obj, **kwargs):
    start = time.time()
    try:
      return super().cacheapi_save_pickle(obj, **kwargs)
    finally:
      elapsed = time.time() - start
      self.save_seconds += elapsed
      self.save_calls += 1
      self.save_max = max(self.save_max, elapsed)

  def cacheapi_load_pickle(self, default=None, verbose=True):
    path = DATA / self._persistence_fld / "object.pickle"
    if path.exists():
      with open(path, "rb") as stream:
        return pickle.load(stream)
    return default

  # input queue, as BasePluginExecutor
  @property
  def input_queue_size(self):
    return len(self.upstream_inputs_deque)

  def add_inputs(self, inp):
    if len(self.upstream_inputs_deque) >= self.cfg_max_inputs_queue_size:
      self.lost_inputs_count += 1
    self.upstream_inputs_deque.append(inp)

  def get_next_avail_input(self):
    return deepcopy(self.upstream_inputs_deque.popleft()) if self.input_queue_size > 0 else None

  def dataapi_struct_datas(self, full=False, as_list=False):
    return [item["STRUCT_DATA"] for item in (self.inputs or {}).get("INPUTS", [])]

  # transport
  def send_encrypted_payload(self, node_addr, **kwargs):
    self.outbox.append((node_addr if isinstance(node_addr, list) else [node_addr], kwargs))

  def receive_and_decrypt_payload(self, data, verbose=0):
    result = {key: value for key, value in data.items() if key != PD.EE_ENCRYPTED_DATA}
    if ENGINE.address not in data.get(PD.EE_DESTINATION, []):
      return {}
    try:
      return {**result, **json.loads(self.bc.decrypt_str(data[PD.EE_ENCRYPTED_DATA], data[PD.EE_SENDER]))}
    except Exception as exc:
      log("decrypt failed: {}".format(exc))
      return result


ChainStore = load_source(
  SRC / "business" / "default" / "admin" / "chain_store_base.py", "ChainStoreBasePlugin",
  extra={"NetworkProcessorPlugin": SimBase},
  strip=("from naeural_core.business.base.network_processor import NetworkProcessorPlugin\n",),
)
if hasattr(ChainStore, "CS_STATS_INTERVAL"):
  ChainStore.CS_STATS_INTERVAL = CFG.get("stats_interval", 10 ** 9)  # keep the counters for the final report
STORE_BLOCK = CFG.get("store_block_ms", 0) / 1000.0
if STORE_BLOCK > 0:
  # A blocking call on every remote store (for example a slow registry lookup).
  _member = ChainStore._ChainStoreBasePlugin__versioned_record
  ChainStore._ChainStoreBasePlugin__versioned_record = lambda self, key, value: (time.sleep(STORE_BLOCK), _member(self, key, value))[1]

STOP = threading.Event()
GO = threading.Event()
QUIET = threading.Event()  # workloads stop writing; the fleet settles before the final report
CPU_AT_GO = [0.0]
T_GO = [0.0]


def running():
  return not STOP.is_set() and not QUIET.is_set()
STATS = defaultdict(int)
RESULTS = defaultdict(list)
PLUGIN = None


def hset_key(hkey, field):
  return "hs:{}:{}".format(hashlib.sha256(hkey.encode()).hexdigest()[:10], base64.urlsafe_b64encode(field.encode()).decode())


def storage():
  return PLUGIN._ChainStoreBasePlugin__chain_storage


def blob(size, seed):
  """Text of about ``size`` bytes that compresses about as well as a job configuration."""
  rnd = random.Random(seed)
  rows, total = [], 0
  while total < size:
    row = {"node": "0xai_%040x" % rnd.getrandbits(160), "image": "repo/app:1.%d" % rnd.randrange(99),
           "env": {"KEY_%d" % idx: "value-%d" % rnd.randrange(9999) for idx in range(6)}, "cid": "Qm%044x" % rnd.getrandbits(176)}
    rows.append(row)
    total += len(json.dumps(row))
  return rows


def plugin_loop():
  """plugin_loop + execute of the base plugin: one input per iteration, 20 ms floor."""
  resolution, yields = CFG.get("loop_resolution", 20), CFG.get("loop_yields", 0)
  window = deque(maxlen=20)
  while not STOP.is_set():
    start = time.time()
    for _ in range(yields):
      time.sleep(0)  # give up the interpreter lock, as every sleep, lock and file call of the real loop does
    try:
      PLUGIN.inputs = PLUGIN.get_next_avail_input() if PLUGIN.input_queue_size > 0 else None
      PLUGIN.network_processor_loop()
      PLUGIN.process()
    except Exception as exc:
      STATS["loop_errors"] += 1
      log("loop error: {!r}".format(exc))
    PLUGIN.inputs = None
    STATS["iterations"] += 1
    STATS["max_queue"] = max(STATS["max_queue"], PLUGIN.input_queue_size)
    elapsed = time.time() - start
    time.sleep(max(0.020, 1.0 / resolution - elapsed))
    window.append(time.time())
    if len(window) > 1:
      PLUGIN.actual_plugin_resolution = (len(window) - 1) / (window[-1] - window[0])
    RESULTS["loop_ts"].append(round(time.time() - T0, 3))


def comm_loop():
  """Communication thread: encrypt for the recipients, sign, hand to the transport."""
  while not STOP.is_set() or PLUGIN.outbox:
    if not PLUGIN.outbox:
      time.sleep(0.002)
      continue
    dest, fields = PLUGIN.outbox.popleft()
    try:
      text = json.dumps(fields)
      payload = {
        PD.EE_PAYLOAD_PATH: PLUGIN.get_instance_path(), PD.EE_ID: CFG["name"], PD.EE_IS_ENCRYPTED: True,
        PD.EE_DESTINATION: dest, PD.EE_ENCRYPTED_DATA: ENGINE.encrypt(plaintext=text, receiver_address=dest),
      }
      ENGINE.sign(payload, add_data=True)
      body = json.dumps(payload).encode()
      op = (fields.get("CHAIN_STORE_DATA") or {}).get("op", "?")
      STATS["sent_" + op] += 1
      STATS["sent_bytes"] += len(body) * len(dest)
      STATS["sent_copies"] += len(dest)
      STATS["plain_bytes_" + op] += len(text)
      send_frame({"t": "msg", "dst": dest}, body)
    except Exception as exc:
      STATS["comm_errors"] += 1
      log("comm error: {!r}".format(exc))


def hog(duty):
  """Pure Python work that holds the interpreter lock (another busy plugin)."""
  GO.wait()
  while not STOP.is_set():
    until = time.time() + 0.05 * duty
    while time.time() < until:
      sum(i * i for i in range(500))
    if duty < 1:
      time.sleep(0.05 * (1 - duty))


def timed_set(tag, key, value, **kwargs):
  start = time.time()
  try:
    done = bool(PLUGIN._set_value(key, value, **kwargs))
  except Exception as exc:
    done = False
    log("{} set error: {}".format(tag, str(exc)[:200]))
  RESULTS[tag].append((round(start - T0, 3), round(time.time() - start, 4), done))
  return done


def wl_operation(spec):
  """The stuck Deeploy operation: a new signed snapshot every ``period`` s, re-published every ``republish`` s."""
  GO.wait()
  time.sleep(spec.get("start", 0))
  op_ids = ["{:016x}-{:032x}".format(random.getrandbits(64), random.getrandbits(128)) for _ in range(spec.get("ops", 1))]
  body = blob(spec.get("bytes", 20000), 7)
  sequence, last_write, last_sent, record, key = 0, 0, 0, None, None
  end = time.time() + spec.get("duration", 1e9)
  while running() and time.time() < end:
    now = time.time()
    if now - last_write >= spec["period"]:
      sequence += 1
      op_id = op_ids[sequence % len(op_ids)]
      record = {"operation_id": op_id, "sequence": sequence, "coordinator": ENGINE.address, "kind": "node_relink",
                "phase": "restoring_previous", "recovery": "blocked", "sent_at": now, "nodes": body}
      ENGINE.sign(record, add_data=True)
      record = json.loads(json.dumps(record))
      key = hset_key("DEEPLOY_OPERATIONS", "{}|{}".format(op_id, sequence % 8))
      last_write = last_sent = now
      timed_set("op_write", key, record, readonly=True)
    elif record is not None and now - last_sent >= spec.get("republish", 2.0):
      last_sent = now
      timed_set("op_republish", key, record, readonly=True)
    time.sleep(0.02)


def wl_local(spec):
  """A local service writing small records and waiting for their confirmation (custody-style writes)."""
  GO.wait()
  time.sleep(spec.get("start", 0))
  count = 0
  while running():
    count += 1
    key = hset_key(spec.get("hkey", "APP_STATE"), "{}-{}".format(CFG["name"], count % spec.get("keys", 50)))
    timed_set("local_write", key, {"n": count, "t": time.time(), "pad": "x" * spec.get("bytes", 300)},
              timeout=spec.get("timeout", 10), max_retries=spec.get("max_retries", 2))
    time.sleep(spec["period"])


def wl_hsync(spec):
  """Periodic snapshot refresh of one namespace, as dAuth and Deeploy do."""
  GO.wait()
  time.sleep(spec.get("start", 0))
  kwargs = {"all_peers": True} if spec.get("all_peers") and CFG.get("variant") == "new" else {}
  while running():
    start = time.time()
    try:
      result = PLUGIN._hsync(spec["hkey"], timeout=spec.get("timeout", 5), **kwargs)
      RESULTS["hsync"].append((round(start - T_GO[0], 3), round(time.time() - start, 4), True, result.get("merged_fields"), result.get("responses", 1), spec["hkey"]))
    except Exception as exc:
      RESULTS["hsync"].append((round(start - T_GO[0], 3), round(time.time() - start, 4), False, 0, 0, spec["hkey"]))
    time.sleep(spec["period"])


REVISIONS = "DAUTH_JOB_SECRET_REVISIONS"


def wl_secrets(spec):
  """dAuth custody: write secret revisions, later revoke some (marker, then tombstone) and add newer ones."""
  GO.wait()
  field = lambda idx: "{}|op{:04d}:deploy".format(100 + idx, idx)
  full = lambda idx: {"job_id": str(100 + idx), "revision": "op{:04d}:deploy".format(idx), "bundle": {"ENV": blob(spec.get("bytes", 4000), idx)}}
  for idx in range(spec["count"]):
    timed_set("secret_write", hset_key(REVISIONS, field(idx)), full(idx))
  while running() and time.time() - T_GO[0] < spec["revoke_at"]:
    time.sleep(0.2)
  for idx in range(spec["revoke"]):
    ident = {"job_id": str(100 + idx), "revision": "op{:04d}:deploy".format(idx)}
    timed_set("secret_write", hset_key(REVISIONS, field(idx) + "|revoked"), dict(ident))
    timed_set("secret_write", hset_key(REVISIONS, field(idx)), dict(ident, revoked=True))
  for idx in range(spec["count"], spec["count"] + spec["add"]):
    timed_set("secret_write", hset_key(REVISIONS, field(idx)), full(idx))


def secret_state():
  """Count secret revisions stored in full, tombstones and markers (no values)."""
  prefix, counts = hset_key(REVISIONS, "")[:14], {"full": 0, "tombstones": 0, "markers": 0}
  for key, record in list(storage().items()):
    if not key.startswith(prefix):
      continue
    value = record.get("value")
    if base64.urlsafe_b64decode(key[len(prefix):] + "==").decode().endswith("|revoked"):
      counts["markers"] += 1
    elif isinstance(value, dict) and value.get("revoked") is True:
      counts["tombstones"] += 1
    else:
      counts["full"] += 1
  return counts


def watch_secrets():
  """Record when this node's view of the secret revisions changes."""
  GO.wait()
  last = None
  while not STOP.is_set():
    now = secret_state()
    if now != last:
      RESULTS["secret_state"].append((round(time.time() - T_GO[0], 2), now["full"], now["tombstones"], now["markers"]))
      last = now
    time.sleep(0.25)


def wl_big_save(spec):
  """Another manager saving a large snapshot (network monitor database, epochs status)."""
  GO.wait()
  data = {"node-%d" % idx: [{"hb": blob(600, idx * 1000 + jdx)} for jdx in range(40)] for idx in range(spec.get("nodes", 400))}
  path = str(DATA / "network_monitor" / "db.pkl")
  os.makedirs(os.path.dirname(path), exist_ok=True)
  manager = SM.get_serialization_manager() if SM is not None else None
  while not STOP.is_set():
    time.sleep(spec["period"])
    start = time.time()
    if manager is not None:
      manager.submit(path, path, data)
    else:
      with open(path + ".tmp", "wb") as stream:
        pickle.dump(data, stream, protocol=pickle.HIGHEST_PROTOCOL)
        stream.flush()
        os.fsync(stream.fileno())
      os.replace(path + ".tmp", path)
    RESULTS["big_save"].append(round(time.time() - start, 4))


WORKLOADS = {"operation": wl_operation, "local": wl_local, "hsync": wl_hsync, "big_save": wl_big_save, "secrets": wl_secrets}


def preload():
  """Give the store its production size before the plugin loads it."""
  spec = CFG.get("preload")
  path = DATA / SimBase._persistence_fld / "object.pickle"
  if not spec or path.exists():
    return
  rnd, store = random.Random(11), {}
  for idx in range(spec.get("keys", 3657)):
    key = hset_key("PRELOAD_%d" % (idx % 40), "field-%d" % idx)
    store[key] = {"key": key, "value": {"data": blob(spec.get("value_bytes", 1400), rnd.random())}, "owner": "preload",
                  "readonly": False, "token": None, "confirms": -1, "min_confirms": 0}
  for key, record in (CFG.get("seed_records") or {}).items():
    store[key] = record
  os.makedirs(path.parent, exist_ok=True)
  with open(path, "wb") as stream:
    pickle.dump(store, stream, protocol=pickle.HIGHEST_PROTOCOL)


MANAGER = None


def report():
  usage = os.times()
  manager = MANAGER
  worker = None
  pickle_path = DATA / SimBase._persistence_fld / "object.pickle"
  plain = PLUGIN._ChainStoreBasePlugin__stats if hasattr(PLUGIN, "_ChainStoreBasePlugin__stats") else None
  return {
    "name": CFG["name"], "addr": ENGINE.address, "stats": dict(STATS), "results": {key: value for key, value in RESULTS.items()},
    "lost_inputs": PLUGIN.lost_inputs_count, "queue_now": PLUGIN.input_queue_size, "keys": len(storage()),
    "save_calls": PLUGIN.save_calls, "save_thread_seconds": round(PLUGIN.save_seconds, 4), "save_thread_max": round(PLUGIN.save_max, 4),
    "cpu_seconds": round(usage.user + usage.system - CPU_AT_GO[0], 2), "child_cpu_seconds": round(usage.children_user + usage.children_system, 2),
    "rss_mb": int(open("/proc/self/status").read().split("VmRSS:")[1].split()[0]) // 1024,
    "manager": manager.get_stats() if manager is not None else None, "worker": worker,
    "pickle_bytes": pickle_path.stat().st_size if pickle_path.exists() else 0,
    "digest": {key: hashlib.sha256(json.dumps(record.get("value"), sort_keys=True, default=str).encode()).hexdigest()[:12]
               for key, record in storage().items() if not record.get("owner") == "preload"},
    "plugin_stats": dict(plain) if isinstance(plain, dict) else None, "secrets": secret_state(),
  }


def main():
  global PLUGIN
  if CFG.get("cpus"):
    os.sched_setaffinity(0, set(CFG["cpus"]))
  preload()
  send_frame({"t": "hello", "addr": ENGINE.address, "eth": ENGINE.eth_address, "name": CFG["name"], "dauth": CFG.get("dauth", True)})
  header, _ = read_frame()
  while header is not None and header.get("t") != "roster":
    header, _ = read_frame()  # anything queued for a previous life of this node
  ROSTER.update(header["nodes"])
  if SM is not None and CFG.get("serialization_manager", "process") != "off":
    os.environ[SM.ENV_SWITCH] = "thread" if CFG["serialization_manager"] == "thread" else "1"
    globals()["MANAGER"] = SM.start_global(log=types.SimpleNamespace(P=lambda msg, color=None: log(msg)))
  PLUGIN = ChainStore()
  PLUGIN.network_processor_init()
  PLUGIN.on_init()
  STATS["loaded_keys"] = len([key for key, record in storage().items() if record.get("owner") != "preload"])
  threads = [threading.Thread(target=plugin_loop, name="plugin_loop", daemon=True), threading.Thread(target=comm_loop, name="comm", daemon=True)]
  threads += [threading.Thread(target=hog, args=(CFG.get("hog_duty", 1.0),), name="hog-%d" % idx, daemon=True) for idx in range(CFG.get("hogs", 0))]
  threads += [threading.Thread(target=WORKLOADS[spec["type"]], args=(spec,), name="wl-" + spec["type"], daemon=True) for spec in CFG.get("workloads", [])]
  if CFG.get("watch_secrets"):
    threads.append(threading.Thread(target=watch_secrets, name="watch", daemon=True))
  for thread in threads:
    thread.start()
  send_frame({"t": "ready"})
  while True:
    header, body = read_frame()
    if header is None or header["t"] == "stop":
      break
    if header["t"] == "go":
      CPU_AT_GO[0] = os.times().user + os.times().system
      T_GO[0] = time.time()
      GO.set()
    elif header["t"] == "quiet":
      QUIET.set()
    elif header["t"] == "msg":
      STATS["received"] += 1
      PLUGIN.add_inputs({"STREAM_NAME": "admin_pipeline", "INPUTS": [{"TYPE": "STRUCT_DATA", "STRUCT_DATA": json.loads(body)}]})
    elif header["t"] == "down":
      DOWN.clear()
      DOWN.update(header["nodes"])
    elif header["t"] == "kill_worker":
      manager = SM.get_serialization_manager() if SM is not None else None
      if manager is not None and manager._proc is not None and manager._proc.poll() is None:
        try:
          os.kill(manager._proc.pid, 9)
          STATS["worker_kills"] += 1
        except ProcessLookupError:
          pass
  STOP.set()
  GO.set()
  threads[0].join(timeout=20)
  try:
    PLUGIN.on_close()
  except Exception as exc:
    log("close error: {!r}".format(exc))
  worker = None
  if MANAGER is not None and MANAGER._proc is not None and MANAGER._proc.poll() is None:
    worker = {"rss_kb": int(open("/proc/{}/status".format(MANAGER._proc.pid)).read().split("VmRSS:")[1].split()[0])}
  if SM is not None:
    SM.stop_global(timeout=30)
  threads[1].join(timeout=5)
  result = report()
  result["worker"] = worker
  send_frame({"t": "report"}, json.dumps(result).encode())


if __name__ == "__main__":
  try:
    main()
  except Exception:
    import traceback
    log(traceback.format_exc())
    raise

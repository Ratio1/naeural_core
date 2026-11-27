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
from datetime import datetime, timezone
from inspect import signature
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
  """Append a timestamped message to the node's diagnostic log.

  Flush each line for inspection while the simulator is running.

  Parameters
  ----------
  msg : object
    Message formatted with str.

  Returns
  -------
  None

  Raises
  ------
  OSError
    Writing or flushing the log failed.

  Examples
  --------
  >>> log("oracle ready")  # doctest: +SKIP

  Notes
  -----
  The elapsed timestamp is relative to subprocess initialization.
  """
  LOG.write("{:8.3f} {}\n".format(time.time() - T0, msg))
  LOG.flush()


def send_frame(header, body=b""):
  """Write a complete length-prefixed frame to the driver pipe.

  Serialize the header to JSON and keep the payload bytes intact.

  Parameters
  ----------
  header : dict
    JSON-serializable metadata describing the frame.
  body : bytes, optional
    Payload, empty for control frames.

  Returns
  -------
  None
    The frame has been flushed to the pipe.

  Raises
  ------
  OSError
    Writing or flushing the pipe failed.
  TypeError or ValueError
    Header data cannot be encoded as JSON.
  struct.error
    A field is too long for an unsigned four-byte count.

  Examples
  --------
  >>> send_frame({"t": "ready"})  # doctest: +SKIP

  Notes
  -----
  The lock keeps the byte stream ordered when workload and control threads
  both send frames.
  """
  # Encode control metadata separately from the opaque payload.
  head = json.dumps(header).encode()
  with OUT_LOCK:
    # Keep both lengths and their contents together under the write lock.
    OUT.write(struct.pack(">I", len(head)) + head + struct.pack(">I", len(body)) + body)
    # Flush so the driver can consume frames before the buffer fills.
    OUT.flush()


def read_frame():
  """Read the next frame sent by the driver.

  Decode the header and read the body using their separate size prefixes.

  Parameters
  ----------
  None

  Returns
  -------
  tuple of (dict, bytes) or tuple of (None, None)
    Decoded header and opaque body. A short first size read ends the stream.

  Raises
  ------
  OSError
    The pipe read failed.
  ValueError
    The header bytes could not be decoded as JSON.
  struct.error
    The body length prefix is incomplete.

  Examples
  --------
  >>> header, body = read_frame()  # doctest: +SKIP

  Notes
  -----
  The frame protocol is private to the simulator. The body is read once
  using the advertised length.
  """
  # Read the fixed-size header length before attempting JSON decoding.
  raw = INP.read(4)
  if len(raw) < 4:
    return None, None
  # Decode just the JSON metadata so the payload remains opaque.
  header = json.loads(INP.read(struct.unpack(">I", raw)[0]))
  # The separate length allows payloads containing any byte sequence.
  body = INP.read(struct.unpack(">I", INP.read(4))[0])
  return header, body


if FSYNC_DELAY > 0:
  _fsync = os.fsync
  os.fsync = lambda fd: (time.sleep(FSYNC_DELAY), _fsync(fd))[1]


def load_source(path, name, extra=None, strip=()):
  """Load a simulator-selected definition from a source checkout.

  Execute the file in a separate namespace with the supplied stand-ins.

  Parameters
  ----------
  path : str or os.PathLike
    Trusted Python source file from the checkout under test.
  name : str
    Name of the definition to retrieve.
  extra : dict or None, optional
    Additional globals made available to the file.
  strip : iterable of str, optional
    Literal substrings to remove before compilation.

  Returns
  -------
  object
    The definition bound to the requested name.

  Raises
  ------
  OSError
    Reading the source failed.
  SyntaxError
    The modified text could not be compiled.
  KeyError
    The requested definition was not provided by the file.

  Examples
  --------
  >>> load_source(SRC / "main" / "example.py", "Example")  # doctest: +SKIP

  Notes
  -----
  Top-level code is executed and can propagate errors of its own. Choose
  only checkouts intended for this simulator.
  """
  # Read the exact source instead of importing all of Core.
  text = Path(path).read_text()
  for needle in strip:
    # Apply each caller-requested literal substitution before compiling.
    text = text.replace(needle, "")
  # Give the selected source its own name and minimal global environment.
  space = {"__name__": "sim_" + name}
  # Supply only the additional dependencies provided by the caller.
  space.update(extra or {})
  # Preserve the input path in tracebacks from the executed source.
  exec(compile(text, str(path), "exec"), space)
  return space[name]


# Light stand-ins for the package, so loading three source files does not import the whole node.
for mod in ("naeural_core", "naeural_core.main", "naeural_core.business", "naeural_core.business.base"):
  sys.modules.setdefault(mod, types.ModuleType(mod)).__path__ = []
# The SDK reads Core's version on startup, even though the full node package is stubbed.
sys.modules["naeural_core.main"].__path__ = [str(SRC / "main")]
sys.modules["naeural_core"].constants = types.SimpleNamespace(CACHE_SERVING="serving")
SM = None
if (SRC / "main" / "serialization_manager.py").exists():
  import importlib.util
  spec = importlib.util.spec_from_file_location("naeural_core.main.serialization_manager", SRC / "main" / "serialization_manager.py")
  SM = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = SM
  spec.loader.exec_module(SM)
  if FSYNC_DELAY > 0 and hasattr(SM, "_WORKER_SOURCE"):
    # Only older snapshots with a separate writer process need their own fsync delay.
    SM._WORKER_SOURCE = "import os, time\n_f = os.fsync\nos.fsync = lambda fd: (time.sleep({}), _f(fd))[1]\n".format(FSYNC_DELAY) + SM._WORKER_SOURCE
# Comparisons can load either the older writer-process API or the per-save thread API.
LEGACY_MANAGER = SM is not None and hasattr(SM, "start_global")

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
  """Name the payload fields used by the simulated transport.

  Expose the names expected by the actual network mixin.

  Parameters
  ----------
  None

  Returns
  -------
  PD
    Object exposing the frame field constants.

  Examples
  --------
  >>> fields = PD()

  Notes
  -----
  The simulator forwards these names unchanged to the Core network mixin.
  """
  EE_SENDER, EE_ID, EE_DESTINATION = "EE_SENDER", "EE_ID", "EE_DESTINATION"
  EE_IS_ENCRYPTED, EE_ENCRYPTED_DATA, EE_PAYLOAD_PATH = "EE_IS_ENCRYPTED", "EE_ENCRYPTED_DATA", "EE_PAYLOAD_PATH"


class SimBC:
  """Provide the chain store with its blockchain and roster interface.

  Use the SDK engine for cryptography and the simulated roster for peer queries.

  Parameters
  ----------
  None

  Returns
  -------
  SimBC
    Plugin-facing blockchain interface.

  Examples
  --------
  >>> blockchain = SimBC()

  Notes
  -----
  The oracle address is derived from the subprocess's own signing engine.
  """
  address = ENGINE.address

  def verify(self, dct_data, str_signature=None, sender_address=None, return_full_info=True, log_hash_sign_fails=True):
    """Verify a signed payload using the oracle's SDK engine.

    Pass the explicit signature, sender and return mode through to the verifier.

    Parameters
    ----------
    dct_data : dict
      Signed payload.
    str_signature : str or None, optional
      Explicit signature, if not read from the payload.
    sender_address : str or None, optional
      Explicit sender, if not read from the payload.
    return_full_info : bool, optional
      Choose the detailed SDK result.
    log_hash_sign_fails : bool, optional
      Accepted for compatibility with the caller.

    Returns
    -------
    bool or object
      Verification result returned by the SDK.

    Examples
    --------
    >>> blockchain.verify(payload)  # doctest: +SKIP

    Notes
    -----
    The compatibility logging argument is not forwarded.
    """
    return ENGINE.verify(dct_data, signature=str_signature, sender_address=sender_address, return_full_info=return_full_info)

  def decrypt_str(self, str_b64data, str_sender):
    """Decrypt a payload received from another simulated oracle.

    Delegate ciphertext handling to the same engine that signs node messages.

    Parameters
    ----------
    str_b64data : str
      Encoded encrypted payload.
    str_sender : str
      Peer blockchain address.

    Returns
    -------
    str or None
      Plaintext when decryption succeeds.

    Examples
    --------
    >>> blockchain.decrypt_str(ciphertext, peer)  # doctest: +SKIP

    Notes
    -----
    A simulated peer still uses its own real local signing identity.
    """
    return ENGINE.decrypt(str_b64data, str_sender)

  def get_whitelist(self, with_prefix=True):
    """Return the other oracle addresses in this simulation.

    Look up membership in the roster installed by the driver.

    Parameters
    ----------
    with_prefix : bool, optional
      Accepted for compatibility with the caller.

    Returns
    -------
    list of str
      Roster keys other than the current oracle's address.

    Examples
    --------
    >>> blockchain.get_whitelist()  # doctest: +SKIP

    Notes
    -----
    Addresses are returned as stored in the roster.
    """
    return [addr for addr in ROSTER if addr != ENGINE.address]

  def node_address_to_eth_address(self, address):
    """Look up a peer's Ethereum address.

    Use the address mapping supplied in the driver's roster.

    Parameters
    ----------
    address : str
      Simulated peer's blockchain address.

    Returns
    -------
    str
      Peer's Ethereum address.

    Raises
    ------
    KeyError
      The peer or its Ethereum address is absent.

    Examples
    --------
    >>> blockchain.node_address_to_eth_address(peer)  # doctest: +SKIP

    Notes
    -----
    A missing peer remains observable as a lookup failure.
    """
    return ROSTER[address]["eth"]

  def get_eth_oracles(self):
    """List the protocol oracles in the simulated roster.

    Project their Ethereum addresses for Core's membership checks.

    Parameters
    ----------
    None

    Returns
    -------
    list of str
      Ethereum addresses of the simulated oracles.

    Examples
    --------
    >>> blockchain.get_eth_oracles()  # doctest: +SKIP

    Notes
    -----
    Includes the current oracle if present in the roster.
    """
    return [node["eth"] for node in ROSTER.values()]

  def get_eth_dauth_oracles(self):
    """List the simulated dAuth oracle Ethereum addresses.

    Select peers marked for dAuth by the driver.

    Parameters
    ----------
    None

    Returns
    -------
    list of str
      Ethereum addresses of the selected peers.

    Examples
    --------
    >>> blockchain.get_eth_dauth_oracles()  # doctest: +SKIP

    Notes
    -----
    Peers without the dAuth flag are excluded.
    """
    return [node["eth"] for node in ROSTER.values() if node.get("dauth")]


class SimBase(NPMixin, PersistMixin):
  """Provide the plugin services required by the loaded chain store.

  Combine the real network and persistence mixins with local queue and runtime stand-ins.

  Parameters
  ----------
  None

  Returns
  -------
  SimBase
    Simulation service state for the plugin.

  Examples
  --------
  >>> SimBase()  # doctest: +SKIP

  Notes
  -----
  The roster, timing and scenario configuration belong to the oracle subprocess.
  """
  CONFIG = {"VALIDATION_RULES": {}, "MAX_INPUTS_QUEUE_SIZE": 1024, "PROCESS_DELAY": 0}
  payload_handler = staticmethod(NPMixin.payload_handler)

  def __init__(self):
    """Initialize the services and queues used by the real plugin.

    Configuration is read from the oracle's simulation settings.

    Parameters
    ----------
    None

    Returns
    -------
    None

    Examples
    --------
    >>> base = SimBase()  # doctest: +SKIP

    Notes
    -----
    Per-oracle overrides replace plugin defaults before queues and persistence
    measurements are initialized.
    """
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
    # Older snapshots query a boolean, while the current store consumes NetMon status.
    self.netmon = types.SimpleNamespace(
      network_node_is_online=lambda addr: addr not in DOWN,
      network_node_simple_status=lambda addr, **kwargs: "LOST STATUS" if addr in DOWN else "ONLINE",
    )
    self.log = types.SimpleNamespace(get_data_folder=lambda: str(DATA), P=lambda msg, color=None: log(msg))
    self.plugins_shmem, self.inputs = {}, None
    self.upstream_inputs_deque = deque(maxlen=self.cfg_max_inputs_queue_size)
    self.lost_inputs_count, self.actual_plugin_resolution = 0, 0.0
    self.outbox = deque()
    self.save_seconds, self.save_calls, self.save_max = 0.0, 0, 0.0
    # Count saves completed away from the simulation's main and plugin threads.
    self.periodic_saves = 0
    self.save_errors = 0
    self.deque, self.deepcopy, self.defaultdict = deque, deepcopy, defaultdict

  # services
  def P(self, msg, color=None, **kwargs):
    """Write a plugin diagnostic to the simulator log.

    Convert the message to text before applying the local length limit.

    Parameters
    ----------
    msg : object
      Diagnostic message.
    color : str or None, optional
      Supported for compatibility.
    **kwargs
      Extra logging options accepted for compatibility.

    Returns
    -------
    None

    Raises
    ------
    OSError
      Writing or flushing the diagnostic failed.

    Examples
    --------
    >>> base.P("ready")  # doctest: +SKIP

    Notes
    -----
    Keep at most 600 characters in the diagnostics.
    """
    log(str(msg)[:600])

  time = staticmethod(_NOW)
  sleep = staticmethod(_SLEEP)
  json_loads = staticmethod(json.loads)
  datetime, timezone = datetime, timezone

  def json_dumps(self, value, **kwargs):
    """Serialize a plugin value to JSON text.

    Forward serialization options to Python's JSON encoder.

    Parameters
    ----------
    value : object
      Value to encode.
    **kwargs
      JSON encoder options.

    Returns
    -------
    str
      Encoded JSON document.

    Raises
    ------
    TypeError or ValueError
      The value cannot be encoded using the supplied options.

    Examples
    --------
    >>> base.json_dumps({"ready": True})  # doctest: +SKIP

    Notes
    -----
    The simulator uses the same standard encoder for transport frames.
    """
    return json.dumps(value, **kwargs)

  def uuid(self, size=13):
    """Generate a random hexadecimal identifier.

    Truncate a 128-bit random value to the requested length.

    Parameters
    ----------
    size : int, optional
      Requested prefix length in hexadecimal characters.

    Returns
    -------
    str
      Hexadecimal identifier prefix.

    Examples
    --------
    >>> base.uuid(8)  # doctest: +SKIP

    Notes
    -----
    The source produces at most 32 hexadecimal characters.
    """
    return ("%032x" % random.getrandbits(128))[:size]

  def get_hash(self, text, algorithm="sha256", length=10):
    """Compute a hexadecimal digest prefix for text.

    Encode the supplied text as UTF-8 before hashing.

    Parameters
    ----------
    text : str
      Text to hash.
    algorithm : str, optional
      Hash name understood by hashlib.
    length : int, optional
      Number of hexadecimal characters to return.

    Returns
    -------
    str
      Digest prefix.

    Raises
    ------
    ValueError
      The digest algorithm is unavailable.

    Examples
    --------
    >>> base.get_hash("store")  # doctest: +SKIP

    Notes
    -----
    SHA-256 is used when no algorithm is supplied.
    """
    return hashlib.new(algorithm, text.encode()).hexdigest()[:length]

  def str_to_base64(self, text, url_safe=False, **kwargs):
    """Encode text as a Base64 string.

    Encode UTF-8 bytes using the alphabet requested by the plugin.

    Parameters
    ----------
    text : str
      Text to encode.
    url_safe : bool, optional
      Choose the URL-safe alphabet.
    **kwargs
      Compatibility options.

    Returns
    -------
    str
      Padded Base64 string.

    Examples
    --------
    >>> base.str_to_base64("ok")  # doctest: +SKIP

    Notes
    -----
    The result retains the encoder's padding.
    """
    return (base64.urlsafe_b64encode if url_safe else base64.b64encode)(text.encode()).decode()

  def base64_to_str(self, text, url_safe=False, **kwargs):
    """Decode a Base64 string into UTF-8 text.

    Restore any missing padding before applying the selected alphabet.

    Parameters
    ----------
    text : str
      Encoded text.
    url_safe : bool, optional
      Choose the URL-safe alphabet.
    **kwargs
      Compatibility options.

    Returns
    -------
    str
      Decoded text.

    Raises
    ------
    ValueError
      The encoded data or UTF-8 plaintext is invalid.

    Examples
    --------
    >>> base.base64_to_str("b2s=")  # doctest: +SKIP

    Notes
    -----
    Unpadded strings from storage keys can be decoded directly.
    """
    text = text + "=" * ((4 - len(text) % 4) % 4)
    return (base64.urlsafe_b64decode if url_safe else base64.b64decode)(text.encode()).decode()

  def trace_info(self):
    """Format the currently handled exception.

    Use the standard traceback formatter for plugin diagnostics.

    Parameters
    ----------
    None

    Returns
    -------
    str
      Formatted exception text.

    Examples
    --------
    >>> base.trace_info()  # doctest: +SKIP

    Notes
    -----
    Call while handling an exception to obtain its stack.
    """
    import traceback
    return traceback.format_exc()

  def on_init(self):
    """Provide an empty base initialization hook.

    Allow the loaded plugin to call its inherited startup hook.

    Parameters
    ----------
    None

    Returns
    -------
    None

    Examples
    --------
    >>> base.on_init()  # doctest: +SKIP

    Notes
    -----
    The simulator initializes its shared services in the constructor.
    """
    return

  def on_close(self):
    """Provide an empty base shutdown hook.

    Allow the loaded plugin to invoke inherited cleanup.

    Parameters
    ----------
    None

    Returns
    -------
    None

    Examples
    --------
    >>> base.on_close()  # doctest: +SKIP

    Notes
    -----
    The simulator driver coordinates process shutdown separately.
    """
    return

  # persistence: the real mixin, with the folder fixed and the save timed on the calling thread
  _persistence_fld = "cache_plugins/cstore"
  plugin_id = "cstore"

  def _maybe_setup_persistence_fld(self):
    """Keep the simulator's fixed persistence directory.

    Satisfy the persistence mixin's setup hook.

    Parameters
    ----------
    None

    Returns
    -------
    None

    Examples
    --------
    >>> base._maybe_setup_persistence_fld()  # doctest: +SKIP

    Notes
    -----
    The save path uses the configured node data directory and fixed plugin subfolder.
    """
    return

  def cacheapi_save_pickle(self, obj, **kwargs):
    """Write the real replica and measure the calling thread's save.

    Distinguishes periodic manager writes from synchronous close writes.

    Parameters
    ----------
    obj : object
      Replica data supplied by the chain store.
    **kwargs
      Save options forwarded to the runtime persistence mixin.

    Returns
    -------
    object
      The underlying persistence method's return value.

    Raises
    ------
    Exception
      The persistence method raised. The simulator records that failure and
      lets the caller handle it.

    Examples
    --------
    >>> base.cacheapi_save_pickle(replica, strict=True)  # doctest: +SKIP

    Notes
    -----
    Caller time is recorded even for failed writes. A successful save on
    a thread other than the main and plugin threads counts as periodic.
    """
    start = time.time()
    try:
      # The current plugin performs its periodic writes on a dedicated manager thread.
      result = super().cacheapi_save_pickle(obj, **kwargs)
      if threading.current_thread().name not in ("MainThread", "plugin_loop"):
        self.periodic_saves += 1
      return result
    except Exception:
      # The plugin can catch and retry a save, so record the original failure here.
      self.save_errors += 1
      raise
    finally:
      elapsed = time.time() - start
      self.save_seconds += elapsed
      self.save_calls += 1
      self.save_max = max(self.save_max, elapsed)

  def cacheapi_load_pickle(self, default=None, verbose=True):
    """Load the oracle replica from its fixed path.

    Return the supplied default when the replica has not been written.

    Parameters
    ----------
    default : object, optional
      Fallback for an absent replica.
    verbose : bool, optional
      Compatibility option accepted from the plugin.

    Returns
    -------
    object
      Unpickled value or fallback.

    Raises
    ------
    OSError
      Opening or reading the replica failed.
    pickle.UnpicklingError
      The replica cannot be decoded.

    Examples
    --------
    >>> base.cacheapi_load_pickle({})  # doctest: +SKIP

    Notes
    -----
    Read failures propagate so the fixture exposes persistence errors.
    """
    path = DATA / self._persistence_fld / "object.pickle"
    if path.exists():
      with open(path, "rb") as stream:
        return pickle.load(stream)
    return default

  # input queue, as BasePluginExecutor
  @property
  def input_queue_size(self):
    """Measure the pending simulated input queue.

    Use the current length of the plugin's bounded deque.

    Parameters
    ----------
    None

    Returns
    -------
    int
      Number of pending inputs.

    Examples
    --------
    >>> base.input_queue_size  # doctest: +SKIP

    Notes
    -----
    This count excludes inputs already handed to the plugin.
    """
    return len(self.upstream_inputs_deque)

  def add_inputs(self, inp):
    """Deliver a frame to the plugin's bounded input queue.

    Record each eviction when a full deque accepts a new input.

    Parameters
    ----------
    inp : dict
      Simulated input frame for the network mixin.

    Returns
    -------
    None

    Examples
    --------
    >>> base.add_inputs(frame)  # doctest: +SKIP

    Notes
    -----
    The deque itself removes the oldest input when full.
    """
    if len(self.upstream_inputs_deque) >= self.cfg_max_inputs_queue_size:
      self.lost_inputs_count += 1
    self.upstream_inputs_deque.append(inp)

  def get_next_avail_input(self):
    """Take a copy of the oldest queued input.

    Remove it from the deque before passing work to the plugin.

    Parameters
    ----------
    None

    Returns
    -------
    dict or None
      Next input or None when the deque is empty.

    Examples
    --------
    >>> base.get_next_avail_input()  # doctest: +SKIP

    Notes
    -----
    Copying keeps subsequent plugin mutations local to this observation.
    """
    return deepcopy(self.upstream_inputs_deque.popleft()) if self.input_queue_size > 0 else None

  def dataapi_struct_datas(self, full=False, as_list=False):
    """Return structured input payloads for the current plugin iteration.

    Project the structured payload from each provided input.

    Parameters
    ----------
    full : bool, optional
      Accepted for compatibility with the plugin interface.
    as_list : bool, optional
      Accepted for compatibility. The result is always a list.

    Returns
    -------
    list
      Structured payloads or an empty list when there is no input.

    Raises
    ------
    KeyError
      An input item lacks its structured payload.

    Examples
    --------
    >>> base.dataapi_struct_datas()  # doctest: +SKIP

    Notes
    -----
    The plugin loop chooses which input is current.
    """
    return [item["STRUCT_DATA"] for item in (self.inputs or {}).get("INPUTS", [])]

  # transport
  def send_encrypted_payload(self, node_addr, **kwargs):
    """Queue a payload for the oracle's communication thread.

    Normalize a single destination to the same list form as a multicast.

    Parameters
    ----------
    node_addr : str or list of str
      Addresses to receive the payload.
    **kwargs
      Payload fields to encrypt and send.

    Returns
    -------
    None

    Examples
    --------
    >>> base.send_encrypted_payload(peer, CHAIN_STORE_DATA=data)  # doctest: +SKIP

    Notes
    -----
    This method queues data only. The communication thread performs the
    encryption, signing and transport.
    """
    # Preserve a uniform recipient representation for the communication thread.
    self.outbox.append((node_addr if isinstance(node_addr, list) else [node_addr], kwargs))

  def receive_and_decrypt_payload(self, data, verbose=0):
    """Recover transport fields and decrypt a payload addressed to this node.

    Keep the visible envelope fields if decryption or JSON decoding fails.

    Parameters
    ----------
    data : dict
      Received transport envelope.
    verbose : int, optional
      Compatibility option accepted from the network mixin.

    Returns
    -------
    dict
      Envelope merged with decrypted fields, the public envelope on failure,
      or an empty mapping when the node is not a recipient.

    Examples
    --------
    >>> base.receive_and_decrypt_payload(envelope)  # doctest: +SKIP

    Notes
    -----
    The incoming ciphertext field is removed from the returned envelope.
    """
    # Keep the transport envelope available without retaining the ciphertext field.
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
# Select the namespace refresh interface once for the arbitrary Core snapshot under test.
ALL_PEER_HSYNC = "all_peers" in signature(ChainStore._hsync).parameters
STORE_BLOCK = CFG.get("store_block_ms", 0) / 1000.0
if STORE_BLOCK > 0:
  # A blocking call on every remote store (for example a slow registry lookup).
  _member = ChainStore._ChainStoreBasePlugin__versioned_record
  ChainStore._ChainStoreBasePlugin__versioned_record = (
    lambda self, *args, **kwargs: (time.sleep(STORE_BLOCK), _member(self, *args, **kwargs))[1]
  )

STOP = threading.Event()
GO = threading.Event()
QUIET = threading.Event()  # workloads stop writing; the fleet settles before the final report
CPU_AT_GO = [0.0]
T_GO = [0.0]


def running():
  """Check whether workload writers should continue.

  Consult both the process stop event and the settle phase event.

  Parameters
  ----------
  None

  Returns
  -------
  bool
    Whether writers can continue submitting workload traffic.

  Examples
  --------
  >>> running()  # doctest: +SKIP

  Notes
  -----
  The settle phase stops writes while the fleet can still drain input.
  """
  return not STOP.is_set() and not QUIET.is_set()
STATS = defaultdict(int)
RESULTS = defaultdict(list)
PLUGIN = None


def hset_key(hkey, field):
  """Encode a namespace and field as a simulated storage key.

  Combine a short namespace digest with a URL-safe encoded field.

  Parameters
  ----------
  hkey : str
    Namespace name.
  field : str
    Field within the namespace.

  Returns
  -------
  str
    Storage key matching the chain store namespace format.

  Examples
  --------
  >>> hset_key("APP_STATE", "node")  # doctest: +SKIP

  Notes
  -----
  The input text is encoded as UTF-8 before hashing or Base64 encoding.
  """
  return "hs:{}:{}".format(hashlib.sha256(hkey.encode()).hexdigest()[:10], base64.urlsafe_b64encode(field.encode()).decode())


def storage():
  """Access the current oracle's in-memory store.

  Read the selected chain-store implementation's private storage mapping.

  Parameters
  ----------
  None

  Returns
  -------
  dict
    Live mapping of storage keys to records.

  Examples
  --------
  >>> storage()  # doctest: +SKIP

  Notes
  -----
  Call only after the simulator has initialized its plugin.
  """
  return PLUGIN._ChainStoreBasePlugin__chain_storage


def workload_records():
  """Select persisted values that originated in a simulated workload.

  Replica equality and restart load counts use the same projection.

  Parameters
  ----------
  None

  Returns
  -------
  dict
    Chain-store records excluding preloaded values and local catch-up state.

  Examples
  --------
  >>> len(workload_records())  # doctest: +SKIP

  Notes
  -----
  The result shares record values with the live store. Only the mapping of
  keys is filtered.
  """
  # Preload initializes every node with identical values, and catch-up state belongs to one node.
  return {key: record for key, record in storage().items()
          if record.get("owner") != "preload" and key != "hs:cstore_pull"}


def blob(size, seed):
  """Generate deterministic synthetic workload rows.

  Accumulate JSON row sizes until they reach the requested size.

  Parameters
  ----------
  size : int
    Target sum of encoded row sizes in bytes.
  seed : int or float
    Random generator seed.

  Returns
  -------
  list of dict
    Synthetic job configuration rows.

  Examples
  --------
  >>> len(blob(1, 7))
  1

  Notes
  -----
  Row boundaries can exceed the target. The estimate excludes outer list separators.
  """
  rnd = random.Random(seed)
  rows, total = [], 0
  while total < size:
    row = {"node": "0xai_%040x" % rnd.getrandbits(160), "image": "repo/app:1.%d" % rnd.randrange(99),
           "env": {"KEY_%d" % idx: "value-%d" % rnd.randrange(9999) for idx in range(6)}, "cid": "Qm%044x" % rnd.getrandbits(176)}
    rows.append(row)
    total += len(json.dumps(row))
  return rows


def plugin_loop():
  """Run the loaded plugin's input and processing loop.

  Observe one queued input per iteration and measure loop rate and queue depth.

  Parameters
  ----------
  None

  Returns
  -------
  None

  Examples
  --------
  >>> plugin_loop()  # doctest: +SKIP

  Notes
  -----
  Each iteration sleeps at least 20 milliseconds. Configured yields add scheduling contention.
  """
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
  """Encrypt and sign simulated network messages.

  Drain the plugin's outbox into the transport pipe until shutdown.

  Parameters
  ----------
  None

  Returns
  -------
  None

  Examples
  --------
  >>> comm_loop()  # doctest: +SKIP

  Notes
  -----
  Traffic counters account for recipient copies. Failures increment the communication error counter.
  """
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
  """Create controlled interpreter contention in an oracle.

  Alternate Python computation with idle time after the workload begins.

  Parameters
  ----------
  duty : float
    Fraction of a nominal 50 millisecond cycle spent computing.

  Returns
  -------
  None

  Examples
  --------
  >>> hog(0.6)  # doctest: +SKIP

  Notes
  -----
  The loop measures time inside this process. Other threads still compete for the interpreter.
  """
  GO.wait()
  while not STOP.is_set():
    until = time.time() + 0.05 * duty
    while time.time() < until:
      sum(i * i for i in range(500))
    if duty < 1:
      time.sleep(0.05 * (1 - duty))


def timed_set(tag, key, value, **kwargs):
  """Write one record and record its elapsed time and outcome.

  Use the real simulated store API and convert its result to a boolean.

  Parameters
  ----------
  tag : str
    Results bucket for this workload.
  key : str
    Target storage key.
  value : object
    Record to write.
  **kwargs
    Options forwarded to the store's write call.

  Returns
  -------
  bool
    Whether the write was confirmed.

  Examples
  --------
  >>> timed_set("local_write", key, {"value": 1})  # doctest: +SKIP

  Notes
  -----
  Exceptions from the store count as failed samples and are logged.
  """
  start = time.time()
  try:
    done = bool(PLUGIN._set_value(key, value, **kwargs))
  except Exception as exc:
    done = False
    log("{} set error: {}".format(tag, str(exc)[:200]))
  RESULTS[tag].append((round(start - T0, 3), round(time.time() - start, 4), done))
  return done


def wl_operation(spec):
  """Publish and periodically resend signed operation snapshots.

  Advance the sequence for new snapshots and resend the same record between new writes.

  Parameters
  ----------
  spec : dict
    Operation cadence, resend cadence, size and optional start and stop times.

  Returns
  -------
  None

  Examples
  --------
  >>> wl_operation({"period": 1, "republish": 0.5})  # doctest: +SKIP

  Notes
  -----
  Operation IDs rotate when more than one synthetic operation is configured.
  """
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
  """Write and confirm small namespace records periodically.

  Use the local oracle and a bounded set of rotating field names.

  Parameters
  ----------
  spec : dict
    Write interval and optional start, namespace, size and retry settings.

  Returns
  -------
  None

  Examples
  --------
  >>> wl_local({"period": 1})  # doctest: +SKIP

  Notes
  -----
  Reused field names allow the workload to continue without unbounded growth.
  """
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
  """Refresh a namespace periodically and record response and merge counts.

  Older implementations can collect all replies in a single call. For a
  requested multi-peer refresh on other snapshots, contact one peer per call
  and advance to the next peer at the next interval.

  Parameters
  ----------
  spec : dict
    Namespace, start time, interval, timeout, and desired peer selection.

  Returns
  -------
  None
    Writes one result row for each attempted refresh.

  Examples
  --------
  >>> wl_hsync({"hkey": "APP_STATE", "period": 10, "all_peers": True})  # doctest: +SKIP

  Notes
  -----
  During fallback rotation, an unsuccessful peer still consumes its turn.
  A later interval can try a different online peer.
  """
  GO.wait()
  time.sleep(spec.get("start", 0))
  # Rotate fallback requests through online peers so a stale peer cannot answer every refresh.
  next_peer = 0
  # Keep the older implementation's native all-peer call for comparisons.
  kwargs = {"all_peers": True} if spec.get("all_peers") and ALL_PEER_HSYNC else {}
  while running():
    start = time.time()
    if spec.get("all_peers") and not ALL_PEER_HSYNC:
      # Only one peer can answer each call on these Core snapshots.
      peers = [peer for peer in PLUGIN.bc.get_whitelist() if peer not in DOWN]
      if peers:
        # A failing peer still consumes its turn.
        kwargs = {"peers": [peers[next_peer % len(peers)]], "include_default_peers": False}
        # Try the next peer at the next scheduled refresh.
        next_peer += 1
    try:
      result = PLUGIN._hsync(spec["hkey"], timeout=spec.get("timeout", 5), **kwargs)
      RESULTS["hsync"].append((round(start - T_GO[0], 3), round(time.time() - start, 4), True, result.get("merged_fields"), result.get("responses", 1), spec["hkey"]))
    except Exception as exc:
      RESULTS["hsync"].append((round(start - T_GO[0], 3), round(time.time() - start, 4), False, 0, 0, spec["hkey"]))
    time.sleep(spec["period"])


REVISIONS = "DAUTH_JOB_SECRET_REVISIONS"


def wl_secrets(spec):
  """Write, revoke and replace synthetic secret revisions.

  Store a revocation marker and then a tombstone for each selected revision.

  Parameters
  ----------
  spec : dict
    Initial revision count, revocation time and counts to revoke and append.

  Returns
  -------
  None

  Examples
  --------
  >>> wl_secrets({"count": 3, "revoke_at": 5, "revoke": 1, "add": 1})  # doctest: +SKIP

  Notes
  -----
  All data originates in the simulator. These revisions contain synthetic payloads.
  """
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
  """Count the forms of secret revision records.

  Classify entries as complete revisions, tombstones or revocation markers.

  Parameters
  ----------
  None

  Returns
  -------
  dict of str to int
    Counts by record form.

  Examples
  --------
  >>> secret_state()  # doctest: +SKIP

  Notes
  -----
  The summary contains counts without record values.
  """
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
  """Sample the oracle's secret revision state until shutdown.

  Append a time and count tuple when the sampled state changes.

  Parameters
  ----------
  None

  Returns
  -------
  None

  Examples
  --------
  >>> watch_secrets()  # doctest: +SKIP

  Notes
  -----
  Sampling occurs every 250 milliseconds. Intermediate changes can occur between samples.
  """
  GO.wait()
  last = None
  while not STOP.is_set():
    now = secret_state()
    if now != last:
      RESULTS["secret_state"].append((round(time.time() - T_GO[0], 2), now["full"], now["tombstones"], now["markers"]))
      last = now
    time.sleep(0.25)


def wl_big_save(spec):
  """Exercise periodic serialization of a large synthetic snapshot.

  Time the caller while submitting through an available manager or writing directly.

  Parameters
  ----------
  spec : dict
    Save period and optional synthetic node count.

  Returns
  -------
  None

  Raises
  ------
  OSError
    Preparing or writing the direct snapshot failed.

  Examples
  --------
  >>> wl_big_save({"period": 5, "nodes": 100})  # doctest: +SKIP

  Notes
  -----
  With a manager, the sample covers submission time. A direct write also includes disk synchronization.
  """
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
  """Prepare a seeded replica before starting the chain store.

  Write the configured synthetic baseline and any explicitly supplied records.

  Parameters
  ----------
  None

  Returns
  -------
  None

  Raises
  ------
  OSError
    Creating or writing the initial replica failed.

  Examples
  --------
  >>> preload()  # doctest: +SKIP

  Notes
  -----
  A replica already persisted by a previous process is left available for restart.
  """
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
  """Summarize one oracle after input processing stops.

  Record workload digests separately from local persistence and runtime metrics.

  Parameters
  ----------
  None

  Returns
  -------
  dict
    Oracle counters, workload results and replicated value digests.

  Examples
  --------
  >>> report()["keys"]  # doctest: +SKIP

  Notes
  -----
  Only workload record digests participate in fleet comparison. Local
  restart state and the seeded preload are excluded from that projection.
  """
  usage = os.times()
  manager = MANAGER
  worker = None
  pickle_path = DATA / SimBase._persistence_fld / "object.pickle"
  # Read whichever counter table the selected snapshot exposes.
  plain = getattr(PLUGIN, "_ChainStoreBasePlugin__cs_stats", getattr(PLUGIN, "_ChainStoreBasePlugin__stats", None))
  # The per-save thread manager has no worker-process counters.
  manager_stats = None
  if manager is not None:
    # Legacy backends retain their own counters for comparative runs.
    manager_stats = manager.get_stats() if LEGACY_MANAGER else {
      "backend": "thread", "errors": PLUGIN.save_errors, "periodic_saves": PLUGIN.periodic_saves,
    }
  return {
    "name": CFG["name"], "addr": ENGINE.address, "stats": dict(STATS), "results": {key: value for key, value in RESULTS.items()},
    "lost_inputs": PLUGIN.lost_inputs_count, "queue_now": PLUGIN.input_queue_size, "keys": len(storage()),
    "save_calls": PLUGIN.save_calls, "save_thread_seconds": round(PLUGIN.save_seconds, 4), "save_thread_max": round(PLUGIN.save_max, 4),
    "cpu_seconds": round(usage.user + usage.system - CPU_AT_GO[0], 2), "child_cpu_seconds": round(usage.children_user + usage.children_system, 2),
    "rss_mb": int(open("/proc/self/status").read().split("VmRSS:")[1].split()[0]) // 1024,
    "manager": manager_stats, "worker": worker,
    "pickle_bytes": pickle_path.stat().st_size if pickle_path.exists() else 0,
    "digest": {key: hashlib.sha256(json.dumps(record.get("value"), sort_keys=True, default=str).encode()).hexdigest()[:12]
               for key, record in workload_records().items()},
    "plugin_stats": dict(plain) if isinstance(plain, dict) else None, "secrets": secret_state(),
  }


def main():
  """Run one oracle until the driver sends its stop frame.

  A roster frame supplies the recipients before the real CStore starts. The
  final frame reports the oracle's outcomes after shutdown.

  Parameters
  ----------
  None

  Returns
  -------
  None

  Examples
  --------
  >>> main()  # doctest: +SKIP

  Notes
  -----
  The node reuses its persisted data on restart. It only accepts writer
  process kill commands for source trees with the legacy writer process.
  """
  global PLUGIN
  if CFG.get("cpus"):
    os.sched_setaffinity(0, set(CFG["cpus"]))
  preload()
  send_frame({"t": "hello", "addr": ENGINE.address, "eth": ENGINE.eth_address, "name": CFG["name"], "dauth": CFG.get("dauth", True)})
  header, _ = read_frame()
  while header is not None and header.get("t") != "roster":
    header, _ = read_frame()  # anything queued for a previous life of this node
  ROSTER.update(header["nodes"])
  if LEGACY_MANAGER and CFG.get("serialization_manager", "process") != "off":
    os.environ[SM.ENV_SWITCH] = "thread" if CFG["serialization_manager"] == "thread" else "1"
    globals()["MANAGER"] = SM.start_global(log=types.SimpleNamespace(P=lambda msg, color=None: log(msg)))
  elif SM is not None and not LEGACY_MANAGER:
    # This Core requires its real manager even when the legacy process override is set.
    globals()["MANAGER"] = SM.SerializationManager.start(log=types.SimpleNamespace(P=lambda msg, color=None: log(msg)))
  PLUGIN = ChainStore()
  PLUGIN.network_processor_init()
  PLUGIN.on_init()
  # Count actual workload records before the oracle handles any incoming frames.
  STATS["loaded_keys"] = len(workload_records())
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
    elif header["t"] == "kill_worker" and LEGACY_MANAGER:
      manager = MANAGER
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
  if LEGACY_MANAGER and MANAGER is not None and MANAGER._proc is not None and MANAGER._proc.poll() is None:
    worker = {"rss_kb": int(open("/proc/{}/status".format(MANAGER._proc.pid)).read().split("VmRSS:")[1].split()[0])}
  if LEGACY_MANAGER:
    SM.stop_global(timeout=30)
  elif MANAGER is not None:
    # Join periodic writers before reporting the final persistence counters.
    MANAGER.stop(timeout=30)
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

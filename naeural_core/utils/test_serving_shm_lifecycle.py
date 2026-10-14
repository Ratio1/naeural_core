"""Exercise serving ownership/failure paths without importing the Edge runtime.

Selected manager and worker methods retain their exact production AST. Runtime
startup, model discovery and process objects are replaced at their boundaries;
the transport case uses real tiny OS shared-memory segments and a spawned child.
These tests make no GPU, broker, model-download or live-node calls.
"""

import ast
from collections import OrderedDict, defaultdict, deque
import importlib.util
import itertools
import multiprocessing as mp
from multiprocessing import shared_memory
import os
import pathlib
import re
import signal
import sys
import threading
import time
import traceback
import types
import unittest
from unittest import mock
import uuid

import numpy as np


_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load(name, path):
  """Load a complete utility module without starting its package imports."""
  spec = importlib.util.spec_from_file_location(name, path)
  module = importlib.util.module_from_spec(spec)
  sys.modules[name] = module
  spec.loader.exec_module(module)
  return module


class _RuntimeBase:
  """Provide only logging and automatic utility startup at the runtime boundary."""

  def __init__(self, log=None, **kwargs):
    self.log = log or _Log()
    if hasattr(self, "_init_mem"):
      self.startup()

  def startup(self):
    """Leave actual SHM initialization to the production subclass."""

  def P(self, text, **kwargs):
    self.log.P(text, **kwargs)

  def _create_notification(self, **kwargs):
    """Retain no externally delivered notifications in this isolated harness."""


class _Log:
  """Collect messages and provide deterministic harmless logging dependencies."""

  python_minor = sys.version_info.minor

  def __init__(self):
    self.lines = []

  def P(self, text, **kwargs):
    self.lines.append(str(text))

  def start_timer(self, name):
    pass

  def stop_timer(self, name):
    pass

  def now_str(self, **kwargs):
    return "test-time"

  def time_to_str(self, value):
    return "test-time"

  def dict_pretty_format(self, value):
    return str(value)

  def show_timers(self, **kwargs):
    pass

  def gpu_info(self):
    """Keep stop-all's optional GPU observation outside this CPU harness."""
    return []


_DIAG = _load("serving_lifecycle_diag", _ROOT / "utils/shm_diagnostics.py")
_package = types.ModuleType("naeural_core")
_package.DecentrAIObject = _RuntimeBase
with mock.patch.dict(sys.modules, {
  "naeural_core": _package,
  "naeural_core.utils.shm_diagnostics": _DIAG,
}):
  _SHM = _load("serving_lifecycle_shm", _ROOT / "utils/system_shared_memory.py")
# Pipe placeholders must resolve this same dynamically loaded class by module
# name in both interpreters; the temporary package patch restores its registry.
sys.modules["serving_lifecycle_shm"] = _SHM


def _source_class(path, name, methods, namespace, base=None):
  """Compile unchanged method bodies, preserving private-name mangling.

  Parameters
  ----------
  path : str
    Source path relative to the production package.
  name : str
    Class whose exact methods are exercised.
  methods : set[str]
    Explicit bounded production surface used by these tests.
  namespace : dict
    Harmless substitutes for external runtime dependencies.
  base : type or None
    Constructor boundary; inheritance does not supply production behavior.
  """
  tree = ast.parse((_ROOT / path).read_text())
  original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
  selected = [node for node in original.body if isinstance(node, ast.FunctionDef) and node.name in methods]
  missing = set(methods) - {node.name for node in selected}
  if missing:
    raise AssertionError(f"Production methods missing: {sorted(missing)}")
  original.body, original.keywords, original.decorator_list = selected, [], []
  original.bases = [] if base is None else [ast.Name(id="_BoundaryBase", ctx=ast.Load())]
  namespace["_BoundaryBase"] = base
  exec(compile(ast.fix_missing_locations(ast.Module(body=[original], type_ignores=[])), path, "exec"), namespace)
  return namespace[name]


_manager_tree = ast.parse((_ROOT / "serving/serving_manager.py").read_text())
_sm_class = next(node for node in _manager_tree.body if isinstance(node, ast.ClassDef) and node.name == "SMConst")
_SM_ENV = {}
exec(compile(ast.Module(body=[_sm_class], type_ignores=[]), "SMConst", "exec"), _SM_ENV)
_SM = _SM_ENV["SMConst"]
_constant_tree = ast.parse((_ROOT / "constants.py").read_text())
_serving_constants = next(node for node in _constant_tree.body
  if isinstance(node, ast.ClassDef) and node.name == "SERVING")
_CONSTANT_ENV = {}
exec(compile(ast.Module(body=[_serving_constants], type_ignores=[]), "SERVING", "exec"), _CONSTANT_ENV)
_CT = types.SimpleNamespace(
  SERVING=_CONSTANT_ENV["SERVING"],
  PLUGIN_SEARCH=types.SimpleNamespace(LOC_SERVING_PLUGINS=[], SUFFIX_SERVING_PLUGINS="",
    SAFE_LOC_SERVING_PLUGINS=[], SERVING_SAFE_IMPORTS=[]),
  CUSTOM_DOWNLOADABLE_MODEL="CUSTOM_DOWNLOADABLE_MODEL", COVERED_SERVERS="COVERED_SERVERS",
)
_ENV = dict(os=os, re=re, uuid=uuid, uuid4=uuid.uuid4, monotonic=time.monotonic, np=np, time=time.time,
  strftime=time.strftime, gmtime=time.gmtime, sleep=lambda _: None,
  threading=threading, traceback=traceback, OrderedDict=OrderedDict,
  defaultdict=defaultdict, deque=deque, itertools=itertools, ct=_CT,
  SMConst=_SM, NumpySharedMemory=_SHM.NumpySharedMemory, shm_diag=_DIAG,
  comm_generator=lambda **kwargs: (_Comm(), _Comm()),
  replace_ndarray_with_shm=_SHM.replace_ndarray_with_shm,
  replace_shm_with_ndarray=_SHM.replace_shm_with_ndarray,
  parent_process=mp.parent_process,
)
_Manager = _source_class("serving/serving_manager.py", "ServingManager", {
  "__init__", "startup", "get_server_name", "get_comm_method", "_create_server",
  "_create_serving_shm", "_release_server_resources", "_record_shm_failure",
  "_retry_pending_cleanup",
  "_shm_summary_lines", "_shm_inventory_lines", "_cleanup_server", "stop_server",
  "stop_all_servers",
  "_get_server", "is_covered_by", "get_active_servers", "get_diagnostic_server_name",
  "_send_message", "_assign_server", "_check_equal_servers", "_maybe_complete_servers",
}, _ENV, base=_RuntimeBase)
_WORKER_ENV = dict(_ENV)
_Worker = _source_class("serving/base/base_serving_process.py", "ModelServingProcess", {
  "create_numpy_shared_memory", "run", "_get_message",
}, _WORKER_ENV)


class _Comm:
  """Record close/send ordering and inject one concrete IPC failure."""

  def __init__(self, close_error=None):
    self.calls, self.close_error = [], close_error

  def close(self):
    self.calls.append("close")
    if self.close_error is not None:
      raise self.close_error

  def send(self, payload):
    self.calls.append(("send", payload))


class _Process:
  """Expose a model-process boundary with independently failing operations."""

  def __init__(self, **kwargs):
    self.kwargs = kwargs
    self.inprocess = kwargs.get("inprocess", False)
    self.comm_method = kwargs.get("comm_method", "shm")
    self.server_name = kwargs.get("server_name", "DETECTOR")
    self.pid, self.exitcode = 123, None
    self.calls = []
    self.start_error = self.terminate_error = None

  def start(self):
    self.calls.append("start")
    if self.start_error is not None:
      raise self.start_error

  def is_alive(self):
    return self.exitcode is None

  def terminate(self):
    self.calls.append("terminate")
    if self.terminate_error is not None:
      raise self.terminate_error
    self.exitcode = -15

  def join(self, timeout):
    self.calls.append("join")
    self.exitcode = 0

  def kill(self):
    self.calls.append("kill")
    self.exitcode = -9

  def shutdown(self):
    self.calls.append("shutdown")

  def get_server_parents(self):
    return []


def _manager(config=None):
  """Use the actual manager constructor, then isolate model-discovery edges."""
  manager = _Manager(shmem={}, owner=types.SimpleNamespace(set_loop_stage=lambda *a, **kw: None), log=_Log())
  manager.cfg_serving_environment = dict(SHM_MAX_IMAGE_SHAPE=(2, 2, 3), **(config or {}))
  manager._server_collector_timedelta = 3600
  manager._get_module_name_and_class = lambda **kwargs: ("test", "Process", _Process, {})
  manager._wait_for_result = lambda **kwargs: ("READY", "synthetic")
  manager.get_server_config = lambda name: {}
  manager._maybe_complete_servers = lambda *args: None
  manager._reset_usage = lambda *args: None
  manager.show_servers = lambda *args: None
  manager._show_serving_error = lambda *args: None
  return manager


def _assert_absent(test, names):
  """Check explicit names without treating a successful attachment as discovery."""
  for name in names:
    with test.subTest(name=name):
      try:
        handle = shared_memory.SharedMemory(name=name)
      except FileNotFoundError:
        continue
      handle.close()
      test.fail(f"Owned shared memory remains: {name}")


def _transport_child(pipe, kwargs):
  """Attach the exact parent pair and restore a full request in a spawned child."""
  shm = _SHM.NumpySharedMemory(**kwargs, create=False, log=_Log())
  worker = _Worker()
  worker.npy_shm, worker.comm_eng = shm, pipe
  worker.server_name, worker._full_debug = "DETECTOR", False
  worker.log, worker.time = _Log(), time.time
  worker.P = worker.log.P
  try:
    pipe.send({"initialized": shm.initialized, "name": shm._mem_name,
      "data_name": shm._shm.name, "control_name": shm._shm_additional.name,
      "role": shm.instance_type})
    command, data, requested, cmd_id, _ = worker._get_message(wait=True)
    values = [None if image is None else image.tolist() for image in data]
    pipe.send(dict(command=command, requested=requested, cmd_id=cmd_id, values=values,
      occupied=int(np.count_nonzero(shm._np_mem_additional[:, 0]))))
  finally:
    shm.shutdown()
    pipe.close()


def _blocked_cleanup_child(ready, release):
  """Wait in a real spawned worker until explicitly released or terminated.

  Parameters
  ----------
  ready, release : multiprocessing.Event
    Isolated parent handshakes; no runtime service or shared image is accessed.
  """
  ready.set()
  release.wait(30)


class ServingShmLifecycleTests(unittest.TestCase):
  """Pin creation ownership, minimum capacity and cleanup under failed startup."""

  def allocate(self, manager):
    """Register cleanup before returning a production allocation to a test."""
    shm, kwargs, method = manager._create_serving_shm("DETECTOR", False, "shm")
    if shm is not None:
      self.addCleanup(shm.shutdown)
    return shm, kwargs, method

  def test_new_sessions_and_worker_generations_get_exact_fresh_names(self):
    names = []
    for manager in (_manager(), _manager()):
      for _ in range(2):
        shm, kwargs, method = self.allocate(manager)
        self.assertEqual(method, "shm")
        self.assertTrue(shm.initialized)
        self.assertEqual(shm._mem_name, kwargs["mem_name"])
        self.assertEqual(shm.instance_type, "Creator")
        self.assertEqual(int(np.count_nonzero(shm._np_mem_additional[:, 0])), 0)
        names.append(kwargs["mem_name"])
    self.assertEqual(len(set(names)), 4)
    self.assertTrue(all("DETECTOR" in name for name in names))

  def test_effective_capacity_has_100_minimum_and_preserves_larger_config(self):
    for configured, expected in ((None, 100), (50, 100), (100, 100), (200, 200)):
      with self.subTest(configured=configured):
        manager = _manager({} if configured is None else {"SHM_MAX_LEN": configured})
        shm, kwargs, _ = self.allocate(manager)
        self.assertEqual(kwargs["maxlen"], expected)
        self.assertEqual(shm.maxlen, expected)
        self.assertEqual(shm._shm.size, expected * 12)
        self.assertEqual(shm._shm_additional.size, expected * 11 * 4)

  def test_pipe_and_inprocess_startup_never_construct_shared_memory(self):
    for inprocess, method in ((False, "pipe"), (True, "shm"), (True, "pipe")):
      with self.subTest(inprocess=inprocess, method=method):
        manager = _manager({"COMM_METHOD": method})
        with mock.patch.dict(_ENV, NumpySharedMemory=mock.Mock(side_effect=AssertionError("unexpected allocation"))):
          self.assertEqual(manager._create_server("DETECTOR", inprocess=inprocess), "DETECTOR")
        self.assertIsNone(manager._servers["DETECTOR"].get("SHM"))

  def test_model_receives_exact_parent_allocation_before_start(self):
    manager = _manager()
    self.addCleanup(manager._cleanup_server, "DETECTOR", wait=True)
    self.assertEqual(manager._create_server("DETECTOR"), "DETECTOR")
    entry = manager._servers["DETECTOR"]
    self.assertEqual(entry["SERVER"].kwargs["npy_shm_kwargs"]["mem_name"], entry["SHM"]._mem_name)
    self.assertEqual(entry["SERVER"].calls[0], "start")

  def test_nonzero_startup_flags_and_wrong_actual_sizes_fail_before_child(self):
    for defect in ("occupied", "data_size", "control_size"):
      with self.subTest(defect=defect):
        manager, closed = _manager(), []
        def malformed(**kwargs):
          flags = np.zeros((100, 11), dtype=np.int32)
          if defect == "occupied":
            flags[0, 0] = 1
          shm = types.SimpleNamespace(initialized=True, instance_type="Creator",
            maxlen=100, shape_offset=10, _np_bytes=1200, is_buffer=True,
            _np_mem_additional=flags, _mem_name=kwargs["mem_name"],
            _shm=types.SimpleNamespace(size=1201 if defect == "data_size" else 1200),
            _shm_additional=types.SimpleNamespace(size=4401 if defect == "control_size" else 4400),
            _mmap_obj=None, _mmap_obj_additional=None,
            cleanup_errors=[], cleanup_errors_dropped=0)
          def shutdown():
            closed.append(True)
            shm._shm = shm._shm_additional = None
          shm.shutdown = shutdown
          return shm
        with mock.patch.dict(_ENV, NumpySharedMemory=malformed):
          shm, kwargs, method = manager._create_serving_shm("DETECTOR", False, "shm")
        self.assertIsNone(shm)
        self.assertEqual(method, "pipe")
        self.assertEqual(closed, [True])

  def test_creation_collision_retries_only_fresh_names_then_falls_back(self):
    for failures, expected_attempts in ((["FileExistsError"] * 3, 3), (["PermissionError"], 1)):
      with self.subTest(failures=failures):
        manager, attempts, closed = _manager(), [], []
        def failed_allocation(**kwargs):
          attempts.append(kwargs)
          return types.SimpleNamespace(initialized=False, error_type=failures[len(attempts)-1],
            _mem_name=kwargs["mem_name"], cleanup_errors=[], cleanup_errors_dropped=0,
            _shm=None, _shm_additional=None, _mmap_obj=None, _mmap_obj_additional=None,
            shutdown=lambda: closed.append(kwargs["mem_name"]))
        def fallback_model(**kwargs):
          # All unsuccessful owned attempts must be retired before the child
          # constructor receives its final transport; no later pipe switch exists.
          self.assertEqual(len(closed), expected_attempts)
          self.assertEqual(kwargs["comm_method"], "pipe")
          self.assertIsNone(kwargs["npy_shm_kwargs"])
          return _Process(**kwargs)
        manager._get_module_name_and_class = lambda **kwargs: ("test", "Process", fallback_model, {})
        with mock.patch.dict(_ENV, NumpySharedMemory=failed_allocation):
          self.assertEqual(manager._create_server("DETECTOR"), "DETECTOR")
        self.assertIsNone(manager._servers["DETECTOR"]["SHM"])
        self.assertEqual(len(attempts), expected_attempts)
        self.assertEqual(len({attempt["mem_name"] for attempt in attempts}), expected_attempts)
        self.assertTrue(all(attempt["create"] for attempt in attempts))
        self.assertEqual(closed, [attempt["mem_name"] for attempt in attempts])
        self.assertIn(failures[0], "\n".join(manager.log.lines))

  def test_constructor_start_and_ready_failures_release_owned_allocation(self):
    for phase in ("constructor", "start", "ready", "ready_exception", "early_exit", "config"):
      with self.subTest(phase=phase):
        manager, allocations = _manager(), []
        original = _SHM.NumpySharedMemory
        def allocation(**kwargs):
          shm = original(**kwargs)
          allocations.append(shm)
          self.addCleanup(shm.shutdown)
          return shm
        failure = RuntimeError(f"{phase} original")
        def model(**kwargs):
          if phase == "constructor":
            raise failure
          process = _Process(**kwargs)
          if phase == "start":
            process.start_error = failure
          if phase == "early_exit":
            process.exitcode = 7
          return process
        manager._get_module_name_and_class = lambda **kwargs: ("test", "Process", model, {})
        if phase == "ready":
          manager._wait_for_result = lambda **kwargs: ("STARTUP_FAILURE", "failure")
        if phase == "ready_exception":
          manager._wait_for_result = mock.Mock(side_effect=failure)
        if phase == "config":
          manager.get_server_config = mock.Mock(side_effect=failure)
        with mock.patch.dict(_ENV, NumpySharedMemory=allocation):
          if phase in ("constructor", "start", "ready_exception", "config"):
            with self.assertRaises(RuntimeError) as caught:
              manager._create_server("DETECTOR")
            self.assertIs(caught.exception, failure)
          else:
            self.assertIsNone(manager._create_server("DETECTOR"))
        self.assertNotIn("DETECTOR", manager._servers)
        self.assertEqual(len(allocations), 1)
        _assert_absent(self, [allocations[0]._mem_name, allocations[0]._mem_name + "_additional"])

  def test_teardown_attempts_shm_despite_stop_close_and_terminate_errors(self):
    for operation in ("send", "close", "terminate"):
      with self.subTest(operation=operation):
        manager = _manager()
        shm, _, _ = self.allocate(manager)
        name = shm._mem_name
        original = RuntimeError(f"{operation} original")
        worker, comm = _Process(), _Comm(original if operation == "close" else None)
        if operation == "terminate":
          worker.terminate_error = original
        manager._servers["DETECTOR"] = {"SERVER": worker, "COMM": comm, "SHM": shm, "PID": worker.pid}
        manager._servers["ALIAS"] = {"SERVER": worker, "COMM": comm, "COVERED_BY": "DETECTOR", "PID": worker.pid}
        if operation == "send":
          manager._send_message = mock.Mock(side_effect=original)
        with self.assertRaises(RuntimeError) as caught:
          if operation == "terminate":
            manager._cleanup_server("DETECTOR", terminate=True)
          else:
            manager.stop_server("DETECTOR")
        self.assertIs(caught.exception, original)
        self.assertIn("close", comm.calls)
        self.assertEqual(manager._servers, {})
        _assert_absent(self, [name, name + "_additional"])
        self.assertNotIn("gracefully stopped", "\n".join(manager.log.lines))

  def test_original_startup_and_stop_failures_survive_secondary_cleanup_errors(self):
    for phase in ("constructor", "stop"):
      with self.subTest(phase=phase):
        manager = _manager()
        original, secondary = LookupError("original"), OSError("secondary private-canary")
        comm, other_comm = _Comm(secondary), _Comm(secondary)
        allocations = []
        def allocation(**kwargs):
          shm = _SHM.NumpySharedMemory(**kwargs)
          allocations.append(shm)
          self.addCleanup(shm.shutdown)
          return shm
        with mock.patch.dict(_ENV, NumpySharedMemory=allocation,
            comm_generator=lambda **kwargs: (comm, other_comm)):
          if phase == "constructor":
            def failing_model(**kwargs):
              raise original
            manager._get_module_name_and_class = lambda **kwargs: ("test", "Process", failing_model, {})
            action = lambda: manager._create_server("DETECTOR")
          else:
            manager._create_server("DETECTOR")
            manager._send_message = mock.Mock(side_effect=original)
            action = lambda: manager.stop_server("DETECTOR")
          with self.assertRaises(LookupError) as caught:
            action()
        self.assertIs(caught.exception, original)
        self.assertIn("close", comm.calls)
        self.assertEqual(manager._servers, {})
        self.assertTrue(any(record["phase"] == "comm_close" and record["error_type"] == "OSError"
          for record in manager._shm_failures))
        self.assertNotIn("private-canary", "\n".join(manager._shm_inventory_lines()))
        _assert_absent(self, [allocations[0]._mem_name, allocations[0]._mem_name + "_additional"])
        expected_comms = (comm, other_comm) if phase == "constructor" else (comm,)
        self.assertEqual(manager._pending_cleanup["DETECTOR"]["comms"], expected_comms)
        comm.close_error = other_comm.close_error = None
        manager.stop_all_servers()
        self.assertEqual(manager._pending_cleanup, {})

  def test_successful_cleanup_retry_preserves_history_without_fresh_failure(self):
    manager = _manager()
    shm, _, _ = self.allocate(manager)
    name, data_handle = shm._mem_name, shm._shm
    # The actual kernel pair exists; only the first data-handle close is faulty.
    # Owned unlink must still remove both names, and the failed handle survives
    # locally so its next ordinary close can succeed without attaching anything.
    with mock.patch.object(data_handle, "close", side_effect=BufferError("close failure")):
      first_error = manager._release_server_resources("DETECTOR", None, (), shm)
    self.assertIsInstance(first_error, RuntimeError)
    self.assertIs(shm._shm, data_handle)
    self.assertIsNone(shm._shm_additional)
    _assert_absent(self, [name, name + "_additional"])
    history = list(shm.cleanup_errors)
    self.assertEqual(history, [{"segment": name, "operation": "close", "exception_type": "BufferError"}])
    self.assertIsNone(manager._release_server_resources("DETECTOR", None, (), shm))
    self.assertIsNone(shm._shm)
    self.assertEqual(shm.cleanup_errors, history)
    _assert_absent(self, [name, name + "_additional"])

  def test_real_sigint_during_join_retains_worker_for_manager_shutdown(self):
    """Prove manager shutdown recovers a real SIGINT-interrupted worker join."""
    manager = _manager()
    shm, _, _ = self.allocate(manager)
    names = (shm._mem_name, shm._mem_name + "_additional")
    context = mp.get_context("spawn")
    ready, release = context.Event(), context.Event()
    worker = context.Process(target=_blocked_cleanup_child, args=(ready, release), daemon=True)
    worker.inprocess, worker.server_name = False, "DETECTOR"
    comm, peer = context.Pipe()
    interrupt = threading.Timer(0.2, os.kill, args=(os.getpid(), signal.SIGINT))
    try:
      worker.start()
      self.assertTrue(ready.wait(10), "real worker did not reach its blocking wait")
      manager._servers = {
        "DETECTOR": {"SERVER": worker, "COMM": comm, "SHM": shm, "PID": worker.pid},
        "ALIAS": {"SERVER": worker, "COMM": comm, "COVERED_BY": "DETECTOR"},
      }
      interrupt.start()
      with self.assertRaises(KeyboardInterrupt):
        manager._cleanup_server("DETECTOR", wait=True)
      # Incomplete cleanup is owned but cannot be selected for prediction or
      # included in the heartbeat. Recovery must come from manager shutdown.
      self.assertIs(manager._pending_cleanup["DETECTOR"]["server"], worker)
      self.assertEqual(manager._servers, {})
      self.assertIsNone(manager._get_server("DETECTOR"))
      self.assertIsNone(manager._get_server("ALIAS"))
      self.assertEqual(manager.get_active_servers(show=False), [])
      manager.stop_all_servers()
      self.assertFalse(worker.is_alive())
      self.assertEqual(manager._pending_cleanup, {})
      _assert_absent(self, names)
    finally:
      interrupt.cancel()
      if interrupt.ident is not None:
        interrupt.join(2)
      # Also reclaim this test's own resources if an assertion or implementation
      # failure prevents the manager from reaching its ordinary retry path.
      if worker.pid is not None:
        if worker.is_alive():
          worker.terminate()
        worker.join(5)
        if worker.is_alive():
          worker.kill()
          worker.join(5)
      comm.close()
      peer.close()
      shm.shutdown()

  def test_unlink_failure_is_retained_and_retried_by_manager_shutdown(self):
    """Recover an owned real segment through the manager after one bad unlink."""
    manager = _manager()
    shm, _, _ = self.allocate(manager)
    name, data_handle = shm._mem_name, shm._shm
    comm, worker = _Comm(), _Process()
    manager._servers["DETECTOR"] = {"SERVER": worker, "COMM": comm, "SHM": shm}
    with mock.patch.object(data_handle, "unlink", side_effect=PermissionError("one-time unlink")):
      with self.assertRaises(RuntimeError):
        manager._cleanup_server("DETECTOR", wait=True)
    self.assertEqual(manager._servers, {})
    self.assertEqual(manager._pending_cleanup["DETECTOR"], {"server": None, "comms": (), "shm": shm})
    # Inspect only the exact name created above; this attachment never performs
    # cleanup and therefore cannot hide a missing manager retry.
    handle = shared_memory.SharedMemory(name=name)
    handle.close()
    manager.stop_all_servers()
    self.assertEqual(manager._pending_cleanup, {})
    self.assertEqual(comm.calls.count("close"), 1)
    _assert_absent(self, [name, name + "_additional"])

  def test_persistent_pending_cleanup_blocks_both_new_generation_entries(self):
    """Fail closed before discovery or generation allocation when unlink fails."""
    manager = _manager()
    shm, _, _ = self.allocate(manager)
    manager._servers["DETECTOR"] = {"SERVER": _Process(), "COMM": _Comm(), "SHM": shm}
    generation = manager._shm_generation
    with mock.patch.object(shm._shm, "unlink", side_effect=PermissionError("persistent unlink")):
      with self.assertRaises(RuntimeError):
        manager._cleanup_server("DETECTOR", wait=True)
      discovery = mock.Mock(side_effect=AssertionError("model discovery before old cleanup"))
      manager._get_module_name_and_class = discovery
      allocator = mock.Mock(side_effect=AssertionError("new allocation before old cleanup"))
      with mock.patch.dict(_ENV, NumpySharedMemory=allocator):
        for action in (lambda: manager._create_server("DETECTOR"),
            lambda: manager._create_serving_shm("DETECTOR", False, "shm")):
          with self.subTest(action=action):
            with self.assertRaises(RuntimeError):
              action()
      discovery.assert_not_called()
      allocator.assert_not_called()
      self.assertEqual(manager._shm_generation, generation)
      self.assertIs(manager._pending_cleanup["DETECTOR"]["shm"], shm)
      self.assertEqual(manager._servers, {})
    manager.stop_all_servers()
    self.assertEqual(manager._pending_cleanup, {})
    _assert_absent(self, [shm._mem_name, shm._mem_name + "_additional"])

  def test_constructor_failure_keeps_original_and_retains_failed_unlink(self):
    """Keep failed startup ownership even when the constructor never returns."""
    manager, allocations = _manager(), []
    original = LookupError("original constructor failure")
    def allocation(**kwargs):
      shm = _SHM.NumpySharedMemory(**kwargs)
      allocations.append(shm)
      self.addCleanup(shm.shutdown)
      return shm
    def failing_model(**kwargs):
      raise original
    real_unlink = shared_memory.SharedMemory.unlink
    def failed_owned_unlink(handle):
      if allocations and handle is allocations[0]._shm:
        raise PermissionError("startup unlink")
      return real_unlink(handle)
    manager._get_module_name_and_class = lambda **kwargs: ("test", "Process", failing_model, {})
    with mock.patch.dict(_ENV, NumpySharedMemory=allocation), \
        mock.patch.object(shared_memory.SharedMemory, "unlink", failed_owned_unlink):
      with self.assertRaises(LookupError) as caught:
        manager._create_server("DETECTOR")
    self.assertIs(caught.exception, original)
    self.assertEqual(len(allocations), 1)
    self.assertEqual(manager._servers, {})
    self.assertIs(manager._pending_cleanup["DETECTOR"]["shm"], allocations[0])
    manager.stop_all_servers()
    self.assertEqual(manager._pending_cleanup, {})
    _assert_absent(self, [allocations[0]._mem_name, allocations[0]._mem_name + "_additional"])

  def test_failed_candidate_cleanup_prevents_retry_allocation_or_pipe_start(self):
    """Retain an incomplete failed candidate before collision retry or fallback."""
    manager, allocations = _manager(), []
    def allocation(**kwargs):
      shm = _SHM.NumpySharedMemory(**kwargs)
      allocations.append(shm)
      self.addCleanup(shm.shutdown)
      # The production utility can return an incompletely cleaned candidate
      # after startup failure. Use real owned handles to exercise that boundary.
      shm.initialized, shm.error_type = False, "FileExistsError"
      return shm
    model = mock.Mock(side_effect=AssertionError("worker constructed after failed candidate cleanup"))
    manager._get_module_name_and_class = lambda **kwargs: ("test", "Process", model, {})
    real_unlink = shared_memory.SharedMemory.unlink
    def failed_owned_unlink(handle):
      if allocations and handle is allocations[0]._shm:
        raise PermissionError("candidate unlink")
      return real_unlink(handle)
    with mock.patch.dict(_ENV, NumpySharedMemory=allocation), \
        mock.patch.object(shared_memory.SharedMemory, "unlink", failed_owned_unlink):
      with self.assertRaises(RuntimeError):
        manager._create_server("DETECTOR")
      self.assertEqual(len(allocations), 1)
      self.assertIs(manager._pending_cleanup["DETECTOR"]["shm"], allocations[0])
      self.assertEqual(manager._servers, {})
      model.assert_not_called()
    manager.stop_all_servers()
    self.assertEqual(manager._pending_cleanup, {})
    _assert_absent(self, [allocations[0]._mem_name, allocations[0]._mem_name + "_additional"])

  def test_partial_release_preserves_all_independently_failed_endpoints(self):
    """Never overwrite pending references when a later release omits them."""
    manager = _manager()
    first_error, second_error = OSError("first close"), PermissionError("second close")
    first, second = _Comm(first_error), _Comm(second_error)
    self.assertIs(manager._release_server_resources("DETECTOR", None, (first, second), None), first_error)
    self.assertEqual(manager._pending_cleanup["DETECTOR"]["comms"], (first, second))
    # A startup finally block can revisit the same owner with only the resources
    # that reached its own scope. It must not erase an earlier failed candidate.
    self.assertIsNone(manager._release_server_resources("DETECTOR", None, (), None))
    self.assertEqual(manager._pending_cleanup["DETECTOR"]["comms"], (first, second))
    first.close_error = None
    self.assertIsNone(manager._release_server_resources("DETECTOR", None, (first,), None))
    self.assertEqual(manager._pending_cleanup["DETECTOR"]["comms"], (second,))
    second.close_error = None
    manager.stop_server("DETECTOR")
    self.assertEqual(manager._pending_cleanup, {})
    self.assertEqual((first.calls.count("close"), second.calls.count("close")), (2, 2))

  def test_live_worker_without_wait_remains_owned_until_stop_retry(self):
    """Retire routing immediately while preserving an unstopped worker handle."""
    manager = _manager()
    worker, comm = _Process(), _Comm()
    manager._servers["DETECTOR"] = {"SERVER": worker, "COMM": comm}
    with self.assertRaises(RuntimeError):
      manager._cleanup_server("DETECTOR")
    self.assertEqual(manager._servers, {})
    self.assertEqual(manager._pending_cleanup["DETECTOR"], {"server": worker, "comms": (), "shm": None})
    manager.stop_server("DETECTOR")
    self.assertFalse(worker.is_alive())
    self.assertEqual(manager._pending_cleanup, {})
    self.assertEqual(comm.calls.count("close"), 1)

  def test_stop_all_retries_new_failure_once_and_preserves_first_error(self):
    """Bound the same-call retry while preserving its original exception."""
    manager = _manager()
    original = PermissionError("first close")
    comm = _Comm()
    close = mock.Mock(side_effect=[original, None])
    comm.close = close
    manager._servers["DETECTOR"] = {"SERVER": _Process(), "COMM": comm}
    with self.assertRaises(PermissionError) as caught:
      manager.stop_all_servers()
    self.assertIs(caught.exception, original)
    self.assertEqual(close.call_count, 2)
    self.assertEqual(manager._servers, {})
    self.assertEqual(manager._pending_cleanup, {})

  def test_pending_summary_excludes_owned_pair_from_unknown_inventory(self):
    """Show non-routable cleanup ownership without misclassifying its own pair."""
    manager = _manager()
    shm, _, _ = self.allocate(manager)
    manager._servers["DETECTOR"] = {"SERVER": _Process(), "COMM": _Comm(), "SHM": shm}
    with mock.patch.object(shm._shm, "unlink", side_effect=PermissionError("pending unlink")):
      with self.assertRaises(RuntimeError):
        manager._cleanup_server("DETECTOR", wait=True)
    stats = []
    entries = [types.SimpleNamespace(name=name,
      stat=lambda follow_symlinks, name=name: stats.append(name) or types.SimpleNamespace(st_size=10))
      for name in (shm._mem_name, shm._mem_name + "_additional", "r1s_other_manager")]
    scanner = mock.MagicMock()
    scanner.__enter__.return_value = iter(entries)
    with mock.patch.object(os, "scandir", return_value=scanner), \
        mock.patch.object(_DIAG, "occupancy_observation", side_effect=AssertionError("pending slot scan")):
      self.assertEqual(manager.get_active_servers(show=True), [])
    text = "\n".join(manager.log.lines)
    self.assertEqual(stats, ["r1s_other_manager"])
    self.assertIn("pending", text.lower())
    self.assertIn("DETECTOR", text)
    self.assertIn("possible_leftovers=1", text)
    self.assertIsNone(manager._get_server("DETECTOR"))
    manager.stop_all_servers()
    self.assertEqual(manager._pending_cleanup, {})

  def test_coverage_assignment_waits_for_old_cleanup_and_retires_failed_coverer(self):
    """Prevent a new alias from hiding an old worker's failed owned unlink."""
    manager = _manager()
    old_shm, _, _ = self.allocate(manager)
    manager._servers["DETECTOR"] = {"SERVER": _Process(), "COMM": _Comm(), "SHM": old_shm}
    manager._maybe_complete_servers = types.MethodType(_Manager._maybe_complete_servers, manager)
    processes = []
    def model(**kwargs):
      """Retain each constructed worker boundary to prove failed startup exits."""
      process = _Process(**kwargs)
      processes.append(process)
      return process
    manager._get_module_name_and_class = lambda **kwargs: (
      "test", "Process", model, {"COVERED_SERVERS": ["DETECTOR"]})
    allocations, endpoints = [], []
    def allocation(**kwargs):
      """Track only this test's new coverer allocations for cleanup evidence."""
      shm = _SHM.NumpySharedMemory(**kwargs)
      allocations.append(shm)
      self.addCleanup(shm.shutdown)
      return shm
    def communications(**kwargs):
      """Track both independently owned endpoints of each startup attempt."""
      pair = (_Comm(), _Comm())
      endpoints.append(pair)
      return pair
    with mock.patch.dict(_ENV, NumpySharedMemory=allocation, comm_generator=communications):
      with mock.patch.object(old_shm._shm, "unlink", side_effect=PermissionError("old alias unlink")):
        with self.assertRaises(RuntimeError):
          manager._cleanup_server("DETECTOR", wait=True)
        with self.assertRaises(RuntimeError):
          manager._create_server("SECOND")
        self.assertEqual(manager._servers, {})
        self.assertIs(manager._pending_cleanup["DETECTOR"]["shm"], old_shm)
        self.assertNotIn("SECOND", manager._pending_cleanup)
        self.assertEqual(len(allocations), 1)
        self.assertFalse(processes[0].is_alive())
        self.assertEqual([comm.calls.count("close") for comm in endpoints[0]], [1, 1])
        _assert_absent(self, [allocations[0]._mem_name, allocations[0]._mem_name + "_additional"])
      # Retrying actual startup now retires the old owner before assigning the
      # same logical name as an alias. The failed coverer's generation stays gone.
      self.assertEqual(manager._create_server("SECOND"), "SECOND")
    self.assertEqual(manager._pending_cleanup, {})
    self.assertEqual(manager._servers["DETECTOR"]["COVERED_BY"], "SECOND")
    self.assertIs(manager._servers["DETECTOR"]["SERVER"], manager._servers["SECOND"]["SERVER"])
    _assert_absent(self, [old_shm._mem_name, old_shm._mem_name + "_additional"])
    manager.stop_all_servers()
    self.assertEqual(manager._servers, {})
    _assert_absent(self, [allocations[1]._mem_name, allocations[1]._mem_name + "_additional"])

  def test_stop_all_retries_pending_owner_despite_same_name_covered_alias(self):
    """Defensively recover prior alias-shadowed ownership during final stop."""
    manager = _manager()
    old_shm, _, _ = self.allocate(manager)
    manager._servers["DETECTOR"] = {"SERVER": _Process(), "COMM": _Comm(), "SHM": old_shm}
    with mock.patch.object(old_shm._shm, "unlink", side_effect=PermissionError("old alias unlink")):
      with self.assertRaises(RuntimeError):
        manager._cleanup_server("DETECTOR", wait=True)
    self.assertEqual(manager._create_server("SECOND"), "SECOND")
    second_entry = manager._servers["SECOND"]
    second_shm = second_entry["SHM"]
    self.addCleanup(second_shm.shutdown)
    # Simulate state created before the assignment gate existed. This bypass is
    # deliberate: ordinary production alias assignment now refuses this state.
    manager._servers["DETECTOR"] = {
      "SERVER": second_entry["SERVER"], "COMM": second_entry["COMM"],
      "PID": second_entry["PID"], "COVERED_BY": "SECOND",
    }
    self.assertIs(manager._pending_cleanup["DETECTOR"]["shm"], old_shm)
    manager.stop_all_servers()
    self.assertEqual(manager._servers, {})
    self.assertEqual(manager._pending_cleanup, {})
    self.assertIn("All servers stopped.", manager.log.lines)
    _assert_absent(self, [old_shm._mem_name, old_shm._mem_name + "_additional",
      second_shm._mem_name, second_shm._mem_name + "_additional"])

  def test_stop_all_retires_later_workers_after_first_worker_failure(self):
    for operation, exception_class in (("stop", LookupError), ("comm_close", OSError),
        ("terminate", LookupError), ("stop_control", KeyboardInterrupt)):
      with self.subTest(operation=operation):
        manager = _manager()
        first_shm, _, _ = self.allocate(manager)
        second_shm, _, method = manager._create_serving_shm("SECOND", False, "shm")
        self.addCleanup(second_shm.shutdown)
        self.assertEqual(method, "shm")
        original = exception_class("first worker original")
        secondary = OSError("first worker terminate failure")
        first_worker, second_worker = _Process(), _Process(server_name="SECOND")
        first_comm = _Comm(original if operation == "comm_close" else None)
        second_comm = _Comm()
        if operation == "terminate":
          first_worker.terminate_error = secondary
        manager._servers = {
          "DETECTOR": {"SERVER": first_worker, "COMM": first_comm, "SHM": first_shm},
          "ALIAS": {"SERVER": first_worker, "COMM": first_comm, "COVERED_BY": "DETECTOR"},
          "SECOND": {"SERVER": second_worker, "COMM": second_comm, "SHM": second_shm},
        }
        actual_send = manager._send_message
        def send(server_name, command, inputs):
          if server_name == "DETECTOR" and operation != "comm_close":
            raise original
          return actual_send(server_name, command, inputs)
        manager._send_message = send
        with self.assertRaises(exception_class) as caught:
          manager.stop_all_servers()
        self.assertIs(caught.exception, original)
        self.assertEqual(manager._servers, {})
        self.assertEqual(first_comm.calls.count("close"), 2 if operation == "comm_close" else 1)
        self.assertEqual(second_comm.calls.count("close"), 1)
        self.assertEqual(sum(isinstance(call, tuple) and call[0] == "send" for call in second_comm.calls), 1)
        self.assertIn("join", second_worker.calls)
        if operation == "terminate":
          self.assertIn("terminate", first_worker.calls)
          self.assertTrue(any(record["phase"] == "process_cleanup" and record["error_type"] == "OSError"
            for record in manager._shm_failures))
        _assert_absent(self, [first_shm._mem_name, first_shm._mem_name + "_additional",
          second_shm._mem_name, second_shm._mem_name + "_additional"])

  def test_child_attachment_failure_preserves_abnormal_exit_before_ready(self):
    worker = _Worker()
    worker.inprocess, worker.comm_method = False, "shm"
    worker.npy_shm_kwargs = dict(mem_name="r1_serving_missing_" + uuid.uuid4().hex,
      np_shape=(2, 2, 3), np_type="uint8", is_buffer=True, maxlen=100)
    # Match the real constructor: failed attachment precedes the point where
    # run() marks model startup, so the existing error classification is retained.
    worker.npy_shm, worker._startup_failure = None, False
    worker.server_name, worker.log = "DETECTOR", _Log()
    worker.P = worker.log.P
    worker._create_logger = lambda: None
    worker._setup_config = mock.Mock(side_effect=AssertionError("model configuration reached after attach failure"))
    worker.shutdown = mock.Mock()
    sent = []
    worker._send_message = lambda command, inputs: sent.append(command)
    worker.run()
    self.assertEqual(sent, ["ABNORMAL_EXIT"])
    worker._setup_config.assert_not_called()

  def test_summary_keeps_heartbeat_schema_and_scans_physical_worker_once(self):
    manager = _manager()
    shm, _, _ = self.allocate(manager)
    worker = _Process()
    manager._servers = {
      "DETECTOR": {"SERVER": worker, "SHM": shm},
      "ALIAS": {"SERVER": worker, "COVERED_BY": "DETECTOR"},
      "PIPE": {"SERVER": _Process(comm_method="pipe")},
      "LOCAL": {"SERVER": _Process(inprocess=True)},
    }
    expected_keys = {"Name (ID)", "Server", "Created", "Last run", "Idle (s)", "Run count", "Inprocess"}
    with mock.patch.object(_DIAG, "occupancy_observation", side_effect=AssertionError("heartbeat scan")), \
         mock.patch.object(os, "scandir", side_effect=AssertionError("heartbeat inventory")):
      heartbeat = manager.get_active_servers(show=False)
    self.assertEqual([row["Name (ID)"] for row in heartbeat], ["DETECTOR", "PIPE", "LOCAL"])
    self.assertTrue(all(set(row) == expected_keys for row in heartbeat))
    manager.log.lines.clear()
    with mock.patch.object(_DIAG, "occupancy_observation", wraps=_DIAG.occupancy_observation) as scans:
      shown = manager.get_active_servers(show=True)
    self.assertEqual(shown, heartbeat)
    self.assertEqual(scans.call_count, 1)
    text = "\n".join(manager.log.lines)
    self.assertEqual(text.count("capacity=100"), 1)
    self.assertIn("context=snapshot", text)
    self.assertIn("ALIAS", text)
    self.assertIn("pipe", text.lower())
    self.assertIn("inprocess", text.lower())

  def test_residual_snapshot_uses_only_metadata_and_never_clears_flags(self):
    manager = _manager()
    shm, _, _ = self.allocate(manager)
    shm._np_mem_additional[3, 0] = 1
    manager._servers = {"DETECTOR": {"SERVER": _Process(), "SHM": shm}}
    class ForbiddenPixels:
      """Reject any request for image storage made by a diagnostic reader."""
      def __getitem__(self, index):
        raise AssertionError("summary accessed image pixels")
      def __array__(self, *args, **kwargs):
        raise AssertionError("summary accessed image pixels")
    shm._np_mem = ForbiddenPixels()
    for context in ("snapshot", "after_collection"):
      manager.log.lines.clear()
      manager.get_active_servers(show=True, shm_context=context)
      text = "\n".join(manager.log.lines)
      self.assertIn("occupied=1 free=99", text)
      self.assertIn("context=" + context, text)
      self.assertIn("suspected_residual_slots=" + ("1" if context == "after_collection" else "unchecked"), text)
      self.assertNotIn("proven leak", text.lower())
      self.assertEqual(shm._np_mem_additional[3, 0], 1)

  def test_namespace_inventory_is_bounded_metadata_only_and_explicitly_incomplete(self):
    manager = _manager()
    stats = []
    entries = [types.SimpleNamespace(name=f"r1s_foreign_{index}",
      stat=lambda follow_symlinks, index=index: stats.append(index) or types.SimpleNamespace(st_size=10))
      for index in range(300)]
    scanner = mock.MagicMock()
    scanner.__enter__.return_value = iter(entries)
    with mock.patch.object(os, "scandir", return_value=scanner), \
         mock.patch.object(shared_memory, "SharedMemory", side_effect=AssertionError("inventory attached")):
      text = "\n".join(manager._shm_inventory_lines())
    self.assertEqual(len(stats), 256)
    self.assertIn("status=incomplete", text)
    self.assertIn("truncated=True", text)
    self.assertIn("possible_leftovers=256", text)
    self.assertIn("shown=16", text)
    self.assertNotIn("r1s_foreign_16:", text)
    self.assertIn("ownership unproven", text)
    with mock.patch.object(os, "scandir", side_effect=PermissionError("private-canary")):
      text = "\n".join(manager._shm_inventory_lines())
    self.assertIn("unavailable:PermissionError", text)
    self.assertIn("possible_leftovers=unknown", text)
    self.assertIn("sampled_bytes=unknown", text)
    self.assertNotIn("private-canary", text)

  def test_directory_iteration_failure_retains_unknown_visibility_after_stat_error(self):
    manager = _manager()
    def failed_directory():
      yield types.SimpleNamespace(name="r1s_unreadable",
        stat=mock.Mock(side_effect=PermissionError("stat private-canary")))
      raise OSError("directory private-canary")
    scanner = mock.MagicMock()
    scanner.__enter__.return_value = failed_directory()
    with mock.patch.object(os, "scandir", return_value=scanner):
      text = "\n".join(manager._shm_inventory_lines())
    self.assertIn("status=unavailable:OSError", text)
    self.assertIn("stat_errors=1", text)
    self.assertIn("possible_leftovers=unknown", text)
    self.assertIn("sampled_bytes=unknown", text)
    self.assertNotIn("private-canary", text)

  def test_inventory_reports_legacy_leftovers_and_failed_cleanup_with_zero_workers(self):
    manager = _manager()
    manager._shm_legacy_names["DETECTOR"] = None
    entries = [types.SimpleNamespace(name=name, stat=lambda **kw: types.SimpleNamespace(st_size=10))
      for name in ("DETECTOR", "DETECTOR_additional", "UNRELATED", "r1s_old_owned")]
    scanner = mock.MagicMock()
    scanner.__enter__.return_value = iter(entries)
    for index in range(80):
      manager._record_shm_failure("DETECTOR", "unlink", "PermissionError", "r1s_old_owned")
    with mock.patch.object(os, "scandir", return_value=scanner):
      self.assertEqual(manager.get_active_servers(show=True), [])
    text = "\n".join(manager.log.lines)
    self.assertIn("possible_leftovers=3", text)
    self.assertIn("DETECTOR_additional", text)
    self.assertIn("r1s_old_owned", text)
    self.assertNotIn("UNRELATED", text)
    self.assertIn("dropped=", text)
    self.assertLess(len(manager._shm_failures), 80)

  def test_inventory_excludes_both_exact_owned_names_and_keeps_unmatched_names(self):
    manager = _manager()
    shm, _, _ = self.allocate(manager)
    manager._servers = {"DETECTOR": {"SERVER": _Process(), "SHM": shm}}
    stats = []
    entries = [types.SimpleNamespace(name=name,
      stat=lambda follow_symlinks, name=name: stats.append(name) or types.SimpleNamespace(st_size=10))
      for name in (shm._mem_name, shm._mem_name + "_additional", "r1s_other_manager")]
    scanner = mock.MagicMock()
    scanner.__enter__.return_value = iter(entries)
    with mock.patch.object(os, "scandir", return_value=scanner):
      text = "\n".join(manager._shm_inventory_lines())
    self.assertEqual(stats, ["r1s_other_manager"])
    self.assertIn("possible_leftovers=1", text)
    self.assertIn("r1s_other_manager", text)
    self.assertNotIn(shm._mem_name, text)

  def test_startup_inventory_runs_even_without_requested_workers(self):
    manager = _manager()
    manager._create_servers = mock.Mock()
    entries = [types.SimpleNamespace(name="r1s_old_manager", stat=lambda **kw: types.SimpleNamespace(st_size=10))]
    scanner = mock.MagicMock()
    scanner.__enter__.return_value = iter(entries)
    with mock.patch.object(os, "scandir", return_value=scanner) as scan, mock.patch.object(_DIAG, "start"):
      manager.startup()
    scan.assert_called_once_with("/dev/shm")
    self.assertIn("r1s_old_manager", "\n".join(manager.log.lines))
    manager._create_servers.assert_called_once_with()

  def test_only_orchestrator_periodic_summary_claims_after_collection(self):
    callers = []
    tree = ast.parse((_ROOT / "main/orchestrator.py").read_text())
    for node in ast.walk(tree):
      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get_active_servers":
        contexts = [keyword.value for keyword in node.keywords if keyword.arg == "shm_context"]
        callers.extend(context.value for context in contexts if isinstance(context, ast.Constant))
    self.assertEqual(callers, ["after_collection"])
    self.assertEqual(_Manager.get_active_servers.__defaults__, (True, "b", "snapshot"))

  def test_spawned_transport_admits_100_arrays_rejects_101_and_returns_slots(self):
    for count in (100, 101):
      with self.subTest(count=count):
        manager = _manager()
        shm, kwargs, _ = self.allocate(manager)
        context = mp.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_transport_child, args=(child, kwargs))
        try:
          process.start()
          child.close()
          self.assertTrue(parent.poll(15), "child attachment timed out")
          self.assertEqual(parent.recv(), {"initialized": True, "name": kwargs["mem_name"],
            "data_name": kwargs["mem_name"], "control_name": kwargs["mem_name"] + "_additional", "role": "User"})
          manager._servers["DETECTOR"] = {"SHM": shm, "COMM": parent}
          inputs = [np.full((2, 2, 3), index, dtype=np.uint8) for index in range(count)]
          manager._send_message("DETECTOR", "PREDICT", inputs)
          self.assertTrue(parent.poll(15), "child receive timed out")
          evidence = parent.recv()
          process.join(15)
          self.assertEqual(process.exitcode, 0)
          self.assertEqual(evidence["values"], [image.tolist() if index < 100 else None
            for index, image in enumerate(inputs)])
          self.assertEqual((evidence["command"], evidence["requested"], evidence["cmd_id"]), ("PREDICT", "DETECTOR", 1))
          self.assertEqual(evidence["occupied"], 0)
          self.assertEqual(int(np.count_nonzero(shm._np_mem_additional[:, 0])), 0)
        finally:
          if process.is_alive():
            process.terminate()
            process.join(5)
          parent.close()
          child.close()


if __name__ == "__main__":
  unittest.main()

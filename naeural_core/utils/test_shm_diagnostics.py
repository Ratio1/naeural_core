"""Differential real-method diagnostics tests without runtime/GPU startup.

The SHM implementation and diagnostic utility are loaded from their complete
source. Selected manager/worker/CV methods retain their exact bodies; only
unrelated imports, inheritance and startup are excluded. NumPy storage models
slot flags/data; no operating-system shared-memory segments are opened.
"""

import ast
import importlib.util
import json
import pathlib
import os
import sys
import time
import types
import unittest
from unittest import mock

import numpy as np


_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_file(name, path):
  """Load a complete module without importing the runtime package."""
  spec = importlib.util.spec_from_file_location(name, path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


_DIAG = _load_file("shm_diag_test_subject", _ROOT / "utils/shm_diagnostics.py")
_package = types.ModuleType("naeural_core")
_package.DecentrAIObject = object
with mock.patch.dict(sys.modules, {
  "naeural_core": _package,
  "naeural_core.utils.shm_diagnostics": _DIAG,
}):
  _SHM = _load_file("shm_test_subject", _ROOT / "utils/system_shared_memory.py")


def _methods(path, class_name, names, namespace):
  """Retain selected source methods, including private-name mangling."""
  tree = ast.parse((_ROOT / path).read_text())
  original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
  methods = [n for n in ast.walk(original) if isinstance(n, ast.FunctionDef) and n.name in names]
  assert {n.name for n in methods} == set(names)
  original.body, original.bases, original.keywords, original.decorator_list = methods, [], [], []
  exec(compile(ast.Module(body=[original], type_ignores=[]), str(path), "exec"), namespace)
  return namespace[class_name]


_CT = types.SimpleNamespace(INFERENCES="INFERENCES", COORDS_NONE="none",
                           NOTIFICATION_TYPE=types.SimpleNamespace(STATUS_EXCEPTION="exception"))
_SM = types.SimpleNamespace(SHM="SHM", COMM="COMM", TYPE="TYPE", DATA="DATA",
                           SERVER_NAME="SERVER_NAME", CMD_ID="CMD_ID", TIMESTAMP="TIMESTAMP",
                           PREDICT="PREDICT", RESULT="RESULT", TIMERS="TIMERS", ABNORMAL_EXIT="ABNORMAL_EXIT")
_SM.SERVER, _SM.PID, _SM.COVERED_BY = "SERVER", "PID", "COVERED_BY"
_ENV = {"shm_diag": _DIAG, "ct": _CT, "SMConst": _SM, "time": time.time,
        "sleep": lambda _: None, "traceback": __import__("traceback"),
        "replace_ndarray_with_shm": _SHM.replace_ndarray_with_shm,
        "replace_shm_with_ndarray": _SHM.replace_shm_with_ndarray,
        "get_serving_process_given_ai_engine": lambda _: "DETECTOR"}
_Manager = _methods("serving/serving_manager.py", "ServingManager",
                    ["_send_message", "_wait_for_comm_message", "_wait_for_result", "_close_server_on_null",
                     "get_diagnostic_server_name"], dict(_ENV))
_Worker = _methods("serving/base/base_serving_process.py", "ModelServingProcess",
                   ["_get_message", "_send_message"], dict(_ENV))
_CV = _methods("business/base/cv_plugin_executor.py", "CVPluginExecutor",
               ["_pre_process"], dict(_ENV))
_DataAPI = _methods("business/mixins_base/dataapi.py", "_DataAPIMixin",
                    ["dataapi_specific_image_inferences"], {})
_BusinessManager = _methods("business/business_manager.py", "BusinessManager",
                            ["fetch_ai_engines"], {"json": json})
with mock.patch.dict(sys.modules, {
  "extensions.serving.ai_engines.stable": types.SimpleNamespace(AI_ENGINES={}),
}):
  _stable_engines = _load_file("stable_engines_subject", _ROOT / "serving/ai_engines/stable.py")
with mock.patch.dict(sys.modules, {"naeural_core.serving.ai_engines": _stable_engines}):
  _engine_utils = _load_file("engine_utils_subject", _ROOT / "serving/ai_engines/utils.py")
_DataHandler = _methods("main/main_loop_data_handler.py", "MainLoopDataHandler",
                        ["append_inferences"], {"json": json,
                          "get_ai_engine_given_serving_process": _engine_utils.get_ai_engine_given_serving_process})


class _Log:
  """Capture new records while retaining timer calls as observable ordering."""

  def __init__(self):
    self.lines = []
    self.timers = []

  def P(self, message, **kwargs):
    self.lines.append(message)

  def start_timer(self, name):
    self.timers.append(("start", name))

  def stop_timer(self, name):
    self.timers.append(("stop", name))

  def records(self, include_configuration=False):
    records = [json.loads(line[len("[SHM_DIAG] "):]) for line in self.lines
               if line.startswith("[SHM_DIAG] ")]
    return records if include_configuration else [r for r in records if r["event"] != "configuration"]


class _Comm:
  """Record the actual IPC call order without opening a pipe or socket."""

  def __init__(self, payload=None, failure=None):
    self.payload, self.failure, self.calls = payload, failure, []

  def poll(self, wait):
    self.calls.append("poll")
    return self.payload is not None

  def recv(self):
    self.calls.append("recv")
    if self.failure:
      raise self.failure
    return self.payload

  def send(self, payload):
    self.calls.append("send")
    if self.failure:
      raise self.failure
    self.payload = payload


def _buffer(occupied=0, initialized=True):
  """Use actual buffer methods with small ordinary NumPy backing storage."""
  shm = object.__new__(_SHM.NumpySharedMemory)
  shm.initialized, shm.is_buffer = initialized, True
  shm.maxlen, shm.shape_offset, shm._max_element_size = 50, 10, 48
  shm._np_mem = np.zeros((50, 48), dtype=np.uint8)
  shm._np_mem_additional = np.zeros((50, 11), dtype=np.int32)
  shm._np_mem_additional[:occupied, 0] = 1
  shm._init_np_shape = (4, 4, 3)
  shm.log = _Log()
  shm._add_warning = lambda _: None
  return shm


def _request(count, doubled=False, auxiliary=False):
  """Build only synthetic frames; include duplicate references on demand."""
  entries = []
  for i in range(count):
    image = np.full((4, 4, 3), i % 256, dtype=np.uint8)
    inputs = [{"TYPE": "IMG", "IMG": image}]
    if doubled:
      inputs.append({"TYPE": "IMG", "IMG": image})
    entry = {"STREAM_NAME": str(i), "INPUTS": inputs}
    if auxiliary:
      entry["METADATA"] = {"secret_canary": image}
    entries.append(entry)
  return entries


class ShmDiagnosticsTests(unittest.TestCase):
  """Pin transport/consumer invariance and diagnostic evidence/privacy."""

  def setUp(self):
    self.patch = mock.patch.multiple(_DIAG, _TARGETS=("DETECTOR",),
                                    _UNTIL=time.time() + 600,
                                    _DEADLINE=time.monotonic() + 600,
                                    _STATE={}, _BYTES=0, _LIMIT=1024 * 1024,
                                    _PENDING={}, _EVICTIONS={}, _CONFIGURED={})
    self.patch.start()
    self.addCleanup(self.patch.stop)

  def manager(self, shm, comm):
    manager = _Manager()
    manager.log, manager.full_debug = _Log(), False
    manager.get_server_name = lambda name: name.upper()
    manager._servers = {"DETECTOR": {"SHM": shm, "COMM": comm}}
    manager._ServingManager__cmd_count = 0
    manager.P = manager.log.P
    manager.show_servers = lambda: None
    return manager

  def worker(self, shm, comm):
    worker = _Worker()
    worker.log, worker._full_debug = _Log(), False
    worker.server_name, worker.npy_shm, worker.comm_eng = "DETECTOR", shm, comm
    worker.time, worker.P = time.time, worker.log.P
    return worker

  def consumer(self, engines, startup_params, raw, calls):
    """Isolate downstream CV work while observing its real preprocessing hook."""
    cv = _CV()
    cv.log, cv.instance_hash, cv._coords_type = _Log(), "bounded-instance", "set"
    cv.cfg_ai_engine, cv.cfg_startup_ai_engine_params = engines, startup_params
    cv.dataapi_images = lambda: {0: np.zeros((1, 1, 3))}
    cv.dataapi_inferences = lambda: raw
    cv.dataapi_images_inferences = lambda: raw
    cv._get_detector_ai_engines = lambda: engines if isinstance(engines, list) else [engines]
    cv._get_detector_ai_engine = lambda: cv._get_detector_ai_engines()[0]
    cv.cfg_meta_type_mapping, cv.cfg_meta_nms_iou_thr = {}, 0.5
    def filtering(data):
      calls.append(("filter", len(cv.log.records())))
      return data
    cv._filter_confidence_threshold = filtering
    cv._filter_object_types = lambda data: data
    cv._maybe_process_meta_types = lambda data, **kwargs: data
    cv._track_objects = lambda data: data
    cv._get_generic_positional_objects = lambda data: (data, data)
    cv._keep_only_intersected_objects = lambda data: data
    cv._track_in_zone_objects = lambda **kwargs: kwargs["dct_inference"]
    cv._CVPluginExecutor__maybe_complete_color_tag = lambda *args: None
    return cv

  def test_capacity_and_receiver_loss_preserve_data_flags_and_wire_shape(self):
    cases = [(49, 0, False, False, 0), (50, 0, False, False, 0),
             (51, 0, False, False, 1), (40, 15, False, False, 5),
             (30, 0, True, False, 10), (35, 0, False, True, 20)]
    for count, occupied, doubled, auxiliary, failures in cases:
      with self.subTest(count=count, occupied=occupied, doubled=doubled, auxiliary=auxiliary):
        results = []
        for enabled in (False, True):
          _DIAG._TARGETS = ("DETECTOR",) if enabled else ()
          _DIAG._STATE.clear()
          shm, comm = _buffer(occupied), _Comm()
          request = _request(count, doubled, auxiliary)
          original = request[0]["INPUTS"][0]["IMG"]
          manager = self.manager(shm, comm)
          manager._send_message("DETECTOR", "PREDICT", request)
          self.assertIs(original, request[0]["INPUTS"][0]["IMG"])
          self.assertEqual(set(comm.payload), {"TYPE", "DATA", "SERVER_NAME", "CMD_ID", "TIMESTAMP"})
          worker = self.worker(shm, comm)
          restored = worker._get_message()[1]
          values = [None if item["IMG"] is None else item["IMG"].tolist()
                    for entry in restored for item in entry["INPUTS"]]
          results.append((values, shm._np_mem_additional.tolist(), comm.calls, manager.log.timers))
          if enabled:
            sent, received = manager.log.records()[0], worker.log.records()[0]
            self.assertEqual(sent["missing"], failures)
            self.assertEqual(received["missing"], failures)
            self.assertEqual(sent["cmd_id"], received["cmd_id"])
            self.assertEqual(sent["occupied_before"], occupied)
            self.assertEqual(sent["occupied_after"], min(50, occupied + count * (2 if doubled or auxiliary else 1)))
            self.assertEqual(received.get("read_ok", 0), sent["admitted"])
            self.assertNotIn("secret_canary", "\n".join(manager.log.lines + worker.log.lines))
          else:
            self.assertEqual(manager.log.records() + worker.log.records(), [])
        self.assertEqual(results[0], results[1])

  def test_invalidated_and_unadmitted_receives_are_distinct(self):
    shm = _buffer()
    converted = _SHM.replace_ndarray_with_shm(_request(1), shm)
    shm._np_mem_additional[0, 0] = 0
    trace = {}
    restored = _SHM.replace_shm_with_ndarray(converted, shm, trace=trace)
    self.assertIsNone(restored[0]["INPUTS"][0]["IMG"])
    self.assertEqual(trace["reason"], "slot_unavailable")
    trace = {}
    self.assertIsNone(_SHM.shm_to_ndarray(_SHM.NumpySharedMemoryPlaceholder(None), shm, trace=trace))
    self.assertEqual(trace["reason"], "unadmitted_placeholder")

  def test_rejection_categories_and_healthy_slot_reuse(self):
    for shm, image, reason in [(_buffer(initialized=False), np.zeros((1,), dtype=np.uint8), "uninitialized"),
                               (_buffer(), np.zeros((49,), dtype=np.uint8), "array_too_large"),
                               (_buffer(), np.zeros((1,) * 11, dtype=np.uint8), "rank_too_large")]:
      trace = {}
      self.assertIsNone(_SHM.ndarray_to_shm(image, shm, trace=trace).get_shm_idx())
      self.assertEqual(trace["reason"], reason)
    shm = _buffer()
    for _ in range(10):
      trace = {}
      converted = _SHM.replace_ndarray_with_shm(_request(40), shm, trace=trace)
      _SHM.replace_shm_with_ndarray(converted, shm)
      self.assertEqual(trace.get("missing", 0), 0)
      self.assertEqual(np.count_nonzero(shm._np_mem_additional[:, 0]), 0)

  def test_ipc_errors_retain_exception_and_idle_is_silent(self):
    for enabled in (False, True):
      _DIAG._TARGETS = ("DETECTOR",) if enabled else ()
      _DIAG._STATE.clear()
      manager = self.manager(_buffer(), _Comm(failure=OSError("secret_canary")))
      with self.assertRaisesRegex(ValueError, "Comm pipe exception"):
        manager._send_message("DETECTOR", "PREDICT", _request(1))
      if enabled:
        self.assertEqual(manager.log.records()[0]["exception_type"], "OSError")
        self.assertFalse(manager.log.records()[0]["completed"])
        self.assertNotIn("secret_canary", json.dumps(manager.log.records()))
      failure = EOFError("secret_canary")
      comm = _Comm(payload={"present": True}, failure=failure)
      manager = self.manager(None, comm)
      with self.assertRaises(EOFError) as caught:
        manager._wait_for_comm_message("DETECTOR", max_wait_time=1)
      self.assertIs(caught.exception, failure)
      worker = self.worker(None, comm)
      with self.assertRaises(EOFError) as caught:
        worker._get_message()
      self.assertIs(caught.exception, failure)
      worker = self.worker(None, _Comm())
      self.assertEqual(worker._get_message(), (None,) * 5)
      self.assertEqual(worker.log.records(), [])

  def test_suppression_recovery_expiry_caps_and_log_failure(self):
    log = _Log()
    with mock.patch.object(_DIAG.time, "monotonic", return_value=10):
      _DIAG.observe(log, "DETECTOR", "send", 1, missing=1, reason="no_free_slot")
      for _ in range(100):
        _DIAG.observe(log, "DETECTOR", "send", 2, missing=1, reason="no_free_slot")
    with mock.patch.object(_DIAG.time, "monotonic", return_value=20):
      _DIAG.observe(log, "DETECTOR", "send", 3, missing=1, reason="no_free_slot")
    self.assertEqual(log.records()[1]["suppressed"], 100)
    with mock.patch.object(_DIAG.time, "monotonic", return_value=21):
      _DIAG.observe(log, "DETECTOR", "send", 4, missing=0)
    self.assertTrue(log.records()[-1]["recovery"])
    _DIAG._DEADLINE = 0
    _DIAG.observe(log, "DETECTOR", "receive", 5, reason="expired")
    self.assertEqual(len(log.records()), 3)
    _DIAG._DEADLINE = time.monotonic() + 60
    _DIAG._TARGETS = ("DETECTOR",)
    _DIAG._BYTES, _DIAG._LIMIT = 0, 128
    _DIAG.observe(log, "DETECTOR", "receive", 6)
    self.assertEqual(log.records()[-1]["event"], "output_cap")
    self.assertEqual(_DIAG._BYTES, _DIAG._LIMIT)
    _DIAG._BYTES, _DIAG._LIMIT = 0, 1024 * 1024
    log.P = mock.Mock(side_effect=RuntimeError("logger unavailable"))
    _DIAG.observe(log, "DETECTOR", "consumer", 7, secret="secret_canary")

  def test_consumer_observes_before_filters_without_changing_outputs(self):
    for rows, reason in [([[]], None), ([], "cardinality"), ([7], "wrong_type"), (None, "wrong_type"),
                         ("absent", "absent_model")]:
      outcomes = []
      for enabled in (False, True):
        _DIAG._TARGETS = ("DETECTOR",) if enabled else ()
        _DIAG._STATE.clear()
        calls = []
        raw = {} if rows == "absent" else {"detector": rows}
        cv = self.consumer("detector", {}, raw, calls)
        outcomes.append(cv._pre_process())
        if rows == [] or rows == [[]]:
          api = _DataAPI()
          api.dataapi_specific_image = lambda **kwargs: original_image
          api.dataapi_images_instance_inferences = lambda: outcomes[-1]['DCT_INSTANCE_INFERENCES']
          original_image = np.zeros((1, 1, 3))
          if rows == []:
            with self.assertRaises(IndexError):
              api.dataapi_specific_image_inferences(idx=0, how='dict')
          else:
            self.assertEqual(api.dataapi_specific_image_inferences(idx=0, how='dict'), {'detector': []})
        self.assertEqual(calls, [("filter", int(enabled))])
        if enabled:
          record = cv.log.records()[0]
          self.assertEqual(record["reason"], reason)
          self.assertFalse(record["correlated"])
          self.assertIsNone(record["cmd_id"])
      self.assertEqual(outcomes[0], outcomes[1])

  def test_consumer_target_matches_business_manager_model_instances(self):
    """Select the real workers produced by flat/keyed per-engine startup config."""
    custom, general, nano = "custom_second_stage_detector", "general_detector", "nano_general_detector"
    cases = [
      (custom, {"MODEL_INSTANCE_ID": "helmet"}),
      (custom, {custom: {"MODEL_INSTANCE_ID": "helmet"}}),
      ([custom, general], {custom: {"MODEL_INSTANCE_ID": "helmet"},
                            general: {"MODEL_INSTANCE_ID": "road"}}),
      (general, {}),
      (custom, {"MODEL_INSTANCE_ID": "ignored", custom: {"MODEL_INSTANCE_ID": "keyed"}}),
      ([general, nano], {"MODEL_INSTANCE_ID": "ignored"}),
    ]
    resolve = _engine_utils.get_serving_process_given_ai_engine
    with mock.patch.dict(_CV._pre_process.__globals__, {"get_serving_process_given_ai_engine": resolve}):
      for engines, startup_params in cases:
        business = _BusinessManager()
        business._dct_current_instances = {"test": types.SimpleNamespace(cfg_ai_engine=engines)}
        business.dct_instances_details = {"test": ("stream", "plugin", {
          "STARTUP_AI_ENGINE_PARAMS": startup_params,
        })}
        targets, outputs = set(), {}
        handler = _DataHandler()
        handler._dct_models_stream_idx = {}
        handler.dct_business_inputs = {"test": {}}
        for handle in business.fetch_ai_engines():
          target = resolve(handle)
          targets.add(("_".join(target) if isinstance(target, tuple) else target).upper())
          handler._dct_models_stream_idx[target] = [("stream", "{}", {})]
          outputs[target] = {"INFERENCES": [[[]]], "INFERENCES_META": {}}
        handler._dct_serving_processes_details = business.dct_serving_processes_details
        handler.append_inferences(outputs)
        raw = handler.dct_business_inputs["test"]["INFERENCES"]
        before = json.dumps(startup_params, sort_keys=True)
        outcomes = []
        for enabled in (False, True):
          with self.subTest(engines=engines, params=startup_params, enabled=enabled):
            _DIAG._TARGETS = tuple(targets) if enabled else ()
            _DIAG._STATE.clear()
            calls = []
            cv = self.consumer(engines, startup_params, raw, calls)
            outcomes.append(cv._pre_process())
            records = cv.log.records()
            self.assertEqual({r["target"] for r in records}, targets if enabled else set())
            self.assertTrue(all(r["reason"] is None and not r["correlated"] for r in records))
            self.assertEqual(calls, [("filter", len(targets) if enabled else 0)])
            self.assertEqual(json.dumps(startup_params, sort_keys=True), before)
        self.assertEqual(outcomes[0], outcomes[1])

        # A bare-key result from another identity must not hide a missing
        # configured instance, even when its cardinality looks healthy.
        if custom in (engines if isinstance(engines, list) else [engines]):
          raw = {custom: [[]]}
          _DIAG._STATE.clear()
          cv = self.consumer(engines, startup_params, raw, [])
          cv._pre_process()
          record = next(r for r in cv.log.records() if r["target"].startswith("TH_YOLO_SECOND_STAGE_"))
          self.assertEqual(record["reason"], "absent_model")

  def test_result_timeout_and_result_envelope_are_preserved(self):
    for payload in (None, {"TYPE": "RESULT", "DATA": {"INFERENCES": [[], [[]]]}, "CMD_ID": 71}):
      outcomes = []
      for enabled in (False, True):
        _DIAG._TARGETS = ("DETECTOR",) if enabled else ()
        _DIAG._STATE.clear()
        manager, events = self.manager(None, _Comm(payload)), []
        manager._server_wait_time = lambda _: 5
        manager._create_notification = lambda **kwargs: events.append(("notification", kwargs))
        manager._cleanup_server = lambda *args, **kwargs: events.append(("cleanup", args, kwargs))
        manager.increment_timeouts = lambda **kwargs: events.append(("timeout", kwargs))
        result = manager._wait_for_result("DETECTOR", max_wait_time=5)
        outcomes.append((result, events, manager._servers["DETECTOR"]["COMM"].calls))
        if enabled:
          record = manager.log.records()[0]
          if payload is None:
            self.assertEqual(record["reason"], "response_timeout")
            self.assertFalse(record["correlated"])
          else:
            self.assertEqual(record["cmd_id"], 71)
            self.assertEqual(record["missing"], 1)
      self.assertEqual(outcomes[0], outcomes[1])

  def test_configuration_is_opt_in_targeted_finite_and_off_has_no_clock(self):
    cases = [("", False), ("invalid", False), ("[]", False),
             (json.dumps({"targets": ["DETECTOR"], "until": time.time() - 1}), False),
             (json.dumps({"targets": ["DETECTOR"], "until": time.time() + 700}), False),
             (json.dumps({"targets": ["A", "B", "C"], "until": time.time() + 50}), False),
             (json.dumps({"targets": ["DETECTOR"], "until": time.time() + 50}), True)]
    for configuration, expected in cases:
      with self.subTest(configuration=configuration), mock.patch.dict(os.environ, {"EE_SHM_DIAGNOSTICS": configuration}):
        module = _load_file("config_subject", _ROOT / "utils/shm_diagnostics.py")
        self.assertEqual(module.enabled("DETECTOR"), expected)
        self.assertFalse(module.enabled("UNSELECTED"))
        if not expected:
          with mock.patch.object(module.time, "monotonic", side_effect=AssertionError("off clock")):
            self.assertFalse(module.enabled())

  def test_result_type_inspection_preserves_envelope_and_result_consumer(self):
    """A failing diagnostic predicate must leave the original consumer in charge."""
    class ResultType(str):
      def __eq__(self, other):
        raise RuntimeError("secret_canary")

      def __ne__(self, other):
        raise RuntimeError("secret_canary")

    for kind in (np.array(["RESULT", "RESULT"]), ResultType("RESULT")):
      for enabled in (False, True):
        with self.subTest(kind=type(kind).__name__, enabled=enabled):
          _DIAG._TARGETS = ("DETECTOR",) if enabled else ()
          _DIAG._STATE.clear()
          payload = {"TYPE": kind, "DATA": {"INFERENCES": [[]]}, "CMD_ID": 72}
          comm = _Comm(payload)
          manager = self.manager(None, comm)
          self.assertIs(manager._wait_for_comm_message("DETECTOR", max_wait_time=1), payload)
          self.assertEqual(comm.calls, ["poll", "recv"])
          if enabled:
            record = manager.log.records()[0]
            self.assertTrue(record["observation_error"])
            self.assertNotIn("secret_canary", json.dumps(record))
          else:
            self.assertEqual(manager.log.records(), [])

          # The real result consumer tolerates this string subtype through
          # .upper(); keep that success and the ndarray's original exception.
          _DIAG._STATE.clear()
          comm.calls.clear()
          if isinstance(kind, str):
            result = manager._wait_for_result("DETECTOR", max_wait_time=1)
            self.assertEqual(result[0], "RESULT")
            self.assertIs(result[1], payload["DATA"])
          else:
            with self.assertRaises(AttributeError):
              manager._wait_for_result("DETECTOR", max_wait_time=1)
          self.assertEqual(comm.calls, ["poll", "recv"])

  def test_fork_reset_preserves_expiry_and_does_not_inherit_a_locked_gate(self):
    deadline, until = _DIAG._DEADLINE, _DIAG._UNTIL
    original = _DIAG._LOCK
    original.acquire()
    try:
      _DIAG._BYTES = 100
      _DIAG._after_fork()
      self.assertIsNot(original, _DIAG._LOCK)
      self.assertTrue(_DIAG._LOCK.acquire(blocking=False))
      _DIAG._LOCK.release()
      self.assertEqual((_DIAG._DEADLINE, _DIAG._UNTIL), (deadline, until))
      self.assertEqual((_DIAG._BYTES, _DIAG._STATE), (0, {}))
    finally:
      original.release()

  def test_conversion_and_worker_send_exceptions_are_not_swallowed(self):
    states = []
    for enabled in (False, True):
      _DIAG._TARGETS = ("DETECTOR",) if enabled else ()
      _DIAG._STATE.clear()
      shm, comm = _buffer(), _Comm()
      manager = self.manager(shm, comm)
      with self.assertRaises(ValueError):
        manager._send_message("DETECTOR", "PREDICT", [np.array(["secret_canary"], dtype=object)])
      states.append((shm._np_mem_additional.tolist(), comm.calls))
      if enabled:
        record = manager.log.records()[0]
        self.assertFalse(record["attempted"])
        self.assertEqual(record["exception_type"], "ValueError")
        self.assertNotIn("secret_canary", json.dumps(record))
      failure = BrokenPipeError("secret_canary")
      worker = self.worker(None, _Comm(failure=failure))
      with self.assertRaises(BrokenPipeError) as caught:
        worker._send_message("RESULT", {}, cmd_id=8)
      self.assertIs(caught.exception, failure)
    self.assertEqual(states[0], states[1])

  def test_malformed_metadata_and_coercible_slots_preserve_transport(self):
    """Probes must not validate values that the original transport tolerates."""
    for use_shm in (False, True):
      results = []
      for enabled in (False, True):
        _DIAG._TARGETS = ("DETECTOR",) if enabled else ()
        _DIAG._STATE.clear()
        shm, comm = _buffer() if use_shm else None, _Comm()
        request = [{"INPUTS": [{"TYPE": np.array([1, 2]), "IMG": np.zeros((4, 4, 3), dtype=np.uint8)}]}]
        manager = self.manager(shm, comm)
        manager._send_message("DETECTOR", "PREDICT", request)
        restored = self.worker(shm, comm)._get_message()[1]
        results.append((restored[0]['INPUTS'][0]['TYPE'].tolist(),
                        restored[0]['INPUTS'][0]['IMG'].tolist(), comm.calls,
                        None if shm is None else shm._np_mem_additional.tolist()))
      self.assertEqual(results[0], results[1])
    for slot in ("100", 100.0, "0", 0.0):
      traces = []
      for enabled in (False, True):
        shm, trace = _buffer(), {} if enabled else None
        result = _SHM.shm_to_ndarray(_SHM.NumpySharedMemoryPlaceholder(slot), shm, trace=trace)
        self.assertIsNone(result)
        traces.append(shm._np_mem_additional.tolist())
        if enabled:
          self.assertEqual(trace['reason'], 'invalid_slot' if int(slot) == 100 else 'slot_unavailable')
      self.assertEqual(traces[0], traces[1])

  def test_diagnostic_inspection_failure_does_not_change_original_result(self):
    """Only the observation may fail when inspecting malformed custom values."""
    class Uncountable(list):
      def __len__(self):
        raise RuntimeError("secret_canary")
    trace = _DIAG.request_observation(Uncountable())
    self.assertTrue(trace['observation_error'])
    payload = {"TYPE": "RESULT", "DATA": {"INFERENCES": Uncountable()}, "CMD_ID": 10}
    manager = self.manager(None, _Comm(payload))
    self.assertIs(manager._wait_for_comm_message("DETECTOR", max_wait_time=1), payload)
    record = manager.log.records()[0]
    self.assertTrue(record['observation_error'])
    self.assertNotIn('secret_canary', json.dumps(record))


if __name__ == "__main__":
  unittest.main()

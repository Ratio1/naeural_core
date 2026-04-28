import unittest

from collections import defaultdict, deque
from contextlib import nullcontext
from copy import deepcopy
from threading import Event, Lock
from types import SimpleNamespace
from queue import Queue
from time import time

from naeural_core import constants as ct
from naeural_core.business.business_manager import BusinessManager
from naeural_core.main.orchestrator import Orchestrator


class _FakeLog:
  def __init__(self):
    self.config_data = {}

  def start_timer(self, _name):
    return

  def stop_timer(self, _name, **_kwargs):
    return

  def managed_lock_resource(self, _name):
    return nullcontext()


class _FakePlugin:
  def __init__(self, network_route_by_handler=False, handlers=None):
    self.cfg_network_route_by_handler = network_route_by_handler
    self._handlers = {handler.upper() for handler in (handlers or [])}
    self.added_inputs = []
    self.added_event = Event()
    self.done_loop = False
    self.upstream_inputs_deque = deque()

  def add_inputs(self, inputs):
    self.added_inputs.append(deepcopy(inputs))
    self.added_event.set()
    return

  def get_registered_payload_signatures(self):
    return self._handlers

  def stop_thread(self):
    self.done_loop = True
    return


class _FakeConfigManager:
  def __init__(self, streams):
    self.dct_config_streams = streams


class _FakeCaptureManager:
  def __init__(self, captures):
    self._captures = captures
    self.updated_streams = []
    self.ensure_calls = []
    self.get_stream_calls = []
    self.get_all_calls = []

  def update_streams(self, dct_streams):
    self.updated_streams.append(deepcopy(dct_streams))
    return

  def ensure_stream_capture(self, config_stream, verbose=True):
    self.ensure_calls.append(deepcopy(config_stream))
    return True

  def get_all_captured_data(self, timer_section=None, display_status=True):
    self.get_all_calls.append((timer_section, display_status))
    return deepcopy(self._captures)

  def get_single_stream_captured_data(self, stream_name):
    self.get_stream_calls.append(stream_name)
    if stream_name not in self._captures:
      return {}
    return {stream_name: deepcopy(self._captures[stream_name])}


class _FakeAdminBusinessManager:
  def __init__(self):
    self.built_from = None
    self.dispatched_inputs = None
    self.bootstrapped_streams = None

  def bootstrap_admin_pipeline_instances(self, dct_config_streams):
    self.bootstrapped_streams = deepcopy(dct_config_streams)
    return ["admin_hash"]

  def build_admin_capture_inputs(self, dct_captures):
    self.built_from = deepcopy(dct_captures)
    return {
      "admin_hash": {
        "STREAM_NAME": dct_captures["admin_pipeline"]["STREAM_NAME"],
        "STREAM_METADATA": dct_captures["admin_pipeline"]["STREAM_METADATA"],
        "INPUTS": dct_captures["admin_pipeline"]["INPUTS"],
      },
    }

  def dispatch_admin_pipeline_inputs(self, dct_business_inputs):
    self.dispatched_inputs = deepcopy(dct_business_inputs)
    return len(dct_business_inputs)


class TestAdminPipelineAsyncDispatch(unittest.TestCase):
  ADMIN_HASH = "admin_hash"
  NON_ADMIN_HASH = "non_admin_hash"

  def setUp(self):
    self._managers = []

  def tearDown(self):
    for manager in self._managers:
      manager._stop_admin_dispatch_thread()

  def _make_manager(self, async_enabled, run_on_threads=True):
    manager = BusinessManager.__new__(BusinessManager)
    manager.log = _FakeLog()
    manager.owner = SimpleNamespace(set_loop_stage=lambda *args, **kwargs: None)
    manager.P = lambda *args, **kwargs: None
    manager.config_data = {
      "ADMIN_PIPELINE_ASYNC_DISPATCH": async_enabled,
      "ADMIN_PIPELINE_DISPATCH_POLL_SECONDS": 0.01,
      "ADMIN_PIPELINE_QUEUE_MAXLEN": 8,
    }
    manager._run_on_threads = run_on_threads
    manager._dct_subalterns = {}
    manager._dct_current_instances = manager._dct_subalterns
    manager._dct_hash_mappings = {
      self.ADMIN_HASH: (ct.CONST_ADMIN_PIPELINE_NAME, "ADMIN_SIG", "ADMIN_01"),
      self.NON_ADMIN_HASH: ("test_stream", "TEST_SIG", "TEST_01"),
    }
    manager._dct_stop_timings = {}
    manager._dct_instance_hash_log = {}
    manager._graceful_stop_instances = defaultdict(lambda: 0)
    manager._BusinessManager__dauth_hash = None
    manager._admin_dispatch_queue = None
    manager._admin_dispatch_thread = None
    manager._admin_dispatch_stop = Event()
    manager._admin_dispatch_lock = Lock()
    manager._admin_instance_hashes = {self.ADMIN_HASH}
    manager._admin_dispatch_counters = {
      "enqueued": 0,
      "dispatched": 0,
      "dropped_missing_plugin": 0,
      "dropped_queue_full": 0,
    }
    manager._admin_dispatch_last_loop_ts = None
    manager._admin_dispatch_last_progress_ts = None
    manager._admin_dispatch_last_warning_ts = 0
    manager._admin_dispatch_consecutive_failures = 0
    self._managers.append(manager)
    return manager

  def test_disabled_mode_keeps_inline_delivery(self):
    manager = self._make_manager(async_enabled=False)
    admin_plugin = _FakePlugin()
    non_admin_plugin = _FakePlugin()
    manager._dct_subalterns[self.ADMIN_HASH] = admin_plugin
    manager._dct_subalterns[self.NON_ADMIN_HASH] = non_admin_plugin

    manager.execute_all_plugins({
      self.ADMIN_HASH: {"INPUTS": [{"TYPE": "STRUCT_DATA", "STRUCT_DATA": {"value": "admin"}}]},
      self.NON_ADMIN_HASH: {"INPUTS": [{"TYPE": "STRUCT_DATA", "STRUCT_DATA": {"value": "normal"}}]},
    })

    self.assertEqual(len(admin_plugin.added_inputs), 1)
    self.assertEqual(len(non_admin_plugin.added_inputs), 1)

  def test_async_dispatch_is_opt_out_by_default(self):
    manager = self._make_manager(async_enabled=False)
    manager.config_data.pop("ADMIN_PIPELINE_ASYNC_DISPATCH", None)

    self.assertTrue(manager.cfg_admin_pipeline_async_dispatch)

  def test_async_admin_delivery_happens_without_inline_execution(self):
    manager = self._make_manager(async_enabled=True)
    manager._initialize_admin_async_dispatch()
    admin_plugin = _FakePlugin()
    non_admin_plugin = _FakePlugin()
    manager._dct_subalterns[self.ADMIN_HASH] = admin_plugin
    manager._dct_subalterns[self.NON_ADMIN_HASH] = non_admin_plugin

    dct_business_inputs = {
      self.ADMIN_HASH: {
        "INPUTS": [{"TYPE": "STRUCT_DATA", "STRUCT_DATA": {"value": {"email": "admin snapshot"}}}],
      },
      self.NON_ADMIN_HASH: {
        "INPUTS": [{"TYPE": "STRUCT_DATA", "STRUCT_DATA": {"value": "normal inline"}}],
      },
    }

    enqueued = manager.dispatch_admin_pipeline_inputs(dct_business_inputs)
    self.assertEqual(enqueued, 1)
    self.assertTrue(admin_plugin.added_event.wait(1.0))
    self.assertEqual(len(admin_plugin.added_inputs), 1)
    self.assertEqual(len(non_admin_plugin.added_inputs), 0)

  def test_enabled_mode_skips_duplicate_inline_admin_delivery(self):
    manager = self._make_manager(async_enabled=True)
    manager._initialize_admin_async_dispatch()
    admin_plugin = _FakePlugin()
    non_admin_plugin = _FakePlugin()
    manager._dct_subalterns[self.ADMIN_HASH] = admin_plugin
    manager._dct_subalterns[self.NON_ADMIN_HASH] = non_admin_plugin

    dct_business_inputs = {
      self.ADMIN_HASH: {"INPUTS": [{"TYPE": "STRUCT_DATA", "STRUCT_DATA": {"value": "admin"}}]},
      self.NON_ADMIN_HASH: {"INPUTS": [{"TYPE": "STRUCT_DATA", "STRUCT_DATA": {"value": "normal"}}]},
    }

    manager.dispatch_admin_pipeline_inputs(dct_business_inputs)
    self.assertTrue(admin_plugin.added_event.wait(1.0))

    manager.execute_all_plugins(dct_business_inputs)

    self.assertEqual(len(admin_plugin.added_inputs), 1)
    self.assertEqual(len(non_admin_plugin.added_inputs), 1)

  def test_async_dispatch_uses_snapshot_owned_inputs(self):
    manager = self._make_manager(async_enabled=True)
    manager._initialize_admin_async_dispatch()
    admin_plugin = _FakePlugin()
    manager._dct_subalterns[self.ADMIN_HASH] = admin_plugin

    dct_business_inputs = {
      self.ADMIN_HASH: {
        "INPUTS": [{
          "TYPE": "STRUCT_DATA",
          "STRUCT_DATA": {
            "payload": {
              "subject": "Original subject",
              "body": ["line-1", "line-2"],
            },
          },
        }],
      },
    }

    manager.dispatch_admin_pipeline_inputs(dct_business_inputs)
    dct_business_inputs[self.ADMIN_HASH]["INPUTS"][0]["STRUCT_DATA"]["payload"]["subject"] = "Mutated subject"
    dct_business_inputs[self.ADMIN_HASH]["INPUTS"][0]["STRUCT_DATA"]["payload"]["body"].append("line-3")

    self.assertTrue(admin_plugin.added_event.wait(1.0))
    delivered_payload = admin_plugin.added_inputs[0]["INPUTS"][0]["STRUCT_DATA"]["payload"]
    self.assertEqual(delivered_payload["subject"], "Original subject")
    self.assertEqual(delivered_payload["body"], ["line-1", "line-2"])

  def test_async_dispatch_requires_threaded_plugins(self):
    manager = self._make_manager(async_enabled=True, run_on_threads=False)

    with self.assertRaisesRegex(ValueError, "PLUGINS_ON_THREADS"):
      manager._initialize_admin_async_dispatch()

  def test_async_dispatch_reuses_network_input_filtering(self):
    manager = self._make_manager(async_enabled=True)
    manager._initialize_admin_async_dispatch()
    admin_plugin = _FakePlugin(network_route_by_handler=True, handlers={"KEEP_ME"})
    manager._dct_subalterns[self.ADMIN_HASH] = admin_plugin

    manager.dispatch_admin_pipeline_inputs({
      self.ADMIN_HASH: {
        "INPUTS": [
          {
            "TYPE": "STRUCT_DATA",
            "STRUCT_DATA": {
              ct.PAYLOAD_DATA.EE_PAYLOAD_PATH: ["sender", ct.CONST_ADMIN_PIPELINE_NAME, "KEEP_ME", "inst"],
            },
          },
          {
            "TYPE": "STRUCT_DATA",
            "STRUCT_DATA": {
              ct.PAYLOAD_DATA.EE_PAYLOAD_PATH: ["sender", ct.CONST_ADMIN_PIPELINE_NAME, "DROP_ME", "inst"],
            },
          },
        ],
      },
    })

    self.assertTrue(admin_plugin.added_event.wait(1.0))
    delivered_inputs = admin_plugin.added_inputs[0]["INPUTS"]
    self.assertEqual(len(delivered_inputs), 1)
    self.assertEqual(delivered_inputs[0]["STRUCT_DATA"][ct.PAYLOAD_DATA.EE_PAYLOAD_PATH][2], "KEEP_ME")

  def test_dead_dispatch_thread_is_restarted_by_health_check(self):
    manager = self._make_manager(async_enabled=True)
    restarted = []
    manager._admin_dispatch_thread = SimpleNamespace(is_alive=lambda: False, join=lambda timeout=None: None)
    manager._start_admin_dispatch_thread = lambda: restarted.append("restart")

    manager._ensure_admin_async_dispatch_health()

    self.assertEqual(restarted, ["restart"])
    manager._admin_dispatch_thread = None

  def test_dispatch_health_warns_when_progress_is_stale(self):
    manager = self._make_manager(async_enabled=True)
    messages = []
    manager.P = lambda message, **kwargs: messages.append(message)
    manager._admin_dispatch_queue = Queue(maxsize=8)
    manager._admin_dispatch_queue.put_nowait(("admin_hash", {"INPUTS": []}))
    manager._admin_dispatch_thread = SimpleNamespace(is_alive=lambda: True, join=lambda timeout=None: None)
    manager._admin_dispatch_last_progress_ts = time() - 2.0
    manager._admin_dispatch_last_warning_ts = 0
    manager.config_data["ADMIN_PIPELINE_STALL_WARNING_SECONDS"] = 0.5

    manager._ensure_admin_async_dispatch_health()

    self.assertTrue(any("appears stalled" in message for message in messages))
    manager._admin_dispatch_thread = None

  def test_dispatch_health_does_not_warn_when_queue_is_empty(self):
    manager = self._make_manager(async_enabled=True)
    messages = []
    manager.P = lambda message, **kwargs: messages.append(message)
    manager._admin_dispatch_queue = Queue(maxsize=8)
    manager._admin_dispatch_thread = SimpleNamespace(is_alive=lambda: True, join=lambda timeout=None: None)
    manager._admin_dispatch_last_progress_ts = time() - 2.0
    manager._admin_dispatch_last_warning_ts = 0
    manager.config_data["ADMIN_PIPELINE_STALL_WARNING_SECONDS"] = 0.5

    manager._ensure_admin_async_dispatch_health()

    self.assertFalse(any("appears stalled" in message for message in messages))
    manager._admin_dispatch_thread = None

  def test_build_admin_capture_inputs_skips_malformed_capture_envelopes(self):
    manager = self._make_manager(async_enabled=True)

    dct_business_inputs = manager.build_admin_capture_inputs({
      "admin_pipeline": {
        "STREAM_NAME": "admin_pipeline",
        "STREAM_METADATA": {"source": "admin"},
        "INPUTS": {"bad": "shape"},
      }
    })

    self.assertEqual(dct_business_inputs, {})


class TestAdminPipelineCollectionLane(unittest.TestCase):
  def _make_orchestrator(self, sequential=False):
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.log = _FakeLog()
    orchestrator.P = lambda *args, **kwargs: None
    orchestrator._create_notification = lambda **kwargs: None
    orchestrator.config_data = {
      "ADMIN_PIPELINE_ASYNC_DISPATCH": True,
      "ADMIN_PIPELINE_DISPATCH_POLL_SECONDS": 0.01,
      "SEQUENTIAL_STREAMS": sequential,
    }
    orchestrator._config_manager = _FakeConfigManager({
      "admin_pipeline": {"NAME": "admin_pipeline", "TYPE": "NetworkListener"},
      "regular_pipeline": {"NAME": "regular_pipeline", "TYPE": "Void"},
    })
    orchestrator._capture_manager = _FakeCaptureManager({
      "admin_pipeline": {
        "STREAM_NAME": "admin_pipeline",
        "STREAM_METADATA": {"source": "admin"},
        "INPUTS": [{"TYPE": "STRUCT_DATA", "STRUCT_DATA": {"payload": "admin"}}],
      },
    })
    orchestrator._business_manager = _FakeAdminBusinessManager()
    orchestrator._Orchestrator__capture_manager_lock = Lock()
    setattr(orchestrator, "_Orchestrator__done", False)
    orchestrator._current_dct_config_streams = {}
    orchestrator._thread_admin_pipeline_collect = None
    orchestrator._admin_pipeline_collect_last_loop_ts = None
    orchestrator._admin_pipeline_collect_last_progress_ts = None
    orchestrator._admin_pipeline_collect_last_warning_ts = 0
    orchestrator._admin_pipeline_collect_consecutive_failures = 0
    return orchestrator

  def test_collect_once_builds_and_dispatches_admin_inputs(self):
    orchestrator = self._make_orchestrator()

    dispatched = orchestrator._collect_admin_pipeline_inputs_once()

    self.assertEqual(dispatched, 1)
    self.assertEqual(
      orchestrator._capture_manager.updated_streams,
      [],
    )
    self.assertEqual(
      orchestrator._capture_manager.ensure_calls,
      [{"NAME": "admin_pipeline", "TYPE": "NetworkListener"}],
    )
    self.assertEqual(
      orchestrator._capture_manager.get_stream_calls,
      ["admin_pipeline"],
    )
    self.assertEqual(
      orchestrator._business_manager.built_from["admin_pipeline"]["STREAM_NAME"],
      "admin_pipeline",
    )
    self.assertEqual(
      orchestrator._business_manager.dispatched_inputs["admin_hash"]["INPUTS"][0]["STRUCT_DATA"]["payload"],
      "admin",
    )
    self.assertEqual(
      orchestrator._capture_manager.get_all_calls,
      [],
    )

  def test_running_streams_snapshot_respects_sequential_mode(self):
    orchestrator = self._make_orchestrator(sequential=True)

    snapshot = orchestrator._get_running_streams_snapshot()

    self.assertEqual(list(snapshot.keys()), ["admin_pipeline"])

  def test_dead_collection_thread_is_restarted_by_health_check(self):
    orchestrator = self._make_orchestrator()
    restarted = []
    orchestrator._thread_admin_pipeline_collect = SimpleNamespace(is_alive=lambda: False)
    orchestrator._start_admin_pipeline_collection_thread = lambda: restarted.append("restart")

    orchestrator._ensure_admin_pipeline_collection_health()

    self.assertEqual(restarted, ["restart"])

  def test_collection_health_warns_when_progress_is_stale(self):
    orchestrator = self._make_orchestrator()
    messages = []
    orchestrator.P = lambda message, **kwargs: messages.append(message)
    orchestrator._thread_admin_pipeline_collect = SimpleNamespace(is_alive=lambda: True)
    orchestrator._admin_pipeline_collect_last_progress_ts = time() - 2.0
    orchestrator._admin_pipeline_collect_last_warning_ts = 0
    orchestrator.config_data["ADMIN_PIPELINE_STALL_WARNING_SECONDS"] = 0.5

    orchestrator._ensure_admin_pipeline_collection_health()

    self.assertTrue(any("appears stalled" in message for message in messages))

  def test_bootstrap_admin_pipeline_startup_runs_before_serving_warmup(self):
    orchestrator = self._make_orchestrator()
    call_order = []
    orchestrator.choose_current_running_streams = lambda: call_order.append("choose")
    orchestrator._capture_manager.ensure_stream_capture = lambda config_stream: call_order.append(("capture_ensure", deepcopy(config_stream))) or True
    orchestrator._business_manager.bootstrap_admin_pipeline_instances = lambda dct_streams: call_order.append(("bootstrap_admin", deepcopy(dct_streams))) or ["admin_hash"]
    orchestrator._collect_admin_pipeline_inputs_once = lambda running_streams=None: call_order.append(("collect", deepcopy(running_streams))) or 1
    orchestrator._maybe_send_heartbeat = lambda **kwargs: call_order.append("heartbeat")
    orchestrator.P = lambda *args, **kwargs: None
    orchestrator.maybe_start_serving_processes = lambda warmup=False, **kwargs: call_order.append(("warmup", warmup))

    orchestrator._init_main_loop()

    self.assertEqual(
      call_order,
      [
        "heartbeat",
        "choose",
        ("capture_ensure", {"NAME": "admin_pipeline", "TYPE": "NetworkListener"}),
        ("bootstrap_admin", {"admin_pipeline": {"NAME": "admin_pipeline", "TYPE": "NetworkListener"}}),
        ("collect", {"admin_pipeline": {"NAME": "admin_pipeline", "TYPE": "NetworkListener"}}),
        ("warmup", True),
      ],
    )

  def test_bootstrap_admin_pipeline_startup_is_skipped_when_feature_disabled(self):
    orchestrator = self._make_orchestrator()
    orchestrator.config_data["ADMIN_PIPELINE_ASYNC_DISPATCH"] = False
    call_order = []
    orchestrator.choose_current_running_streams = lambda: call_order.append("choose")
    orchestrator.refresh_business_plugins = lambda: call_order.append("refresh")
    orchestrator._collect_admin_pipeline_inputs_once = lambda: call_order.append("collect") or 1
    orchestrator._maybe_send_heartbeat = lambda **kwargs: call_order.append("heartbeat")
    orchestrator.maybe_start_serving_processes = lambda warmup=False, **kwargs: call_order.append(("warmup", warmup))

    orchestrator._init_main_loop()

    self.assertEqual(call_order, ["heartbeat", ("warmup", True)])

  def test_admin_pipeline_collection_is_opt_out_by_default(self):
    orchestrator = self._make_orchestrator()
    orchestrator.config_data.pop("ADMIN_PIPELINE_ASYNC_DISPATCH", None)

    self.assertTrue(orchestrator.cfg_admin_pipeline_async_dispatch)


if __name__ == "__main__":
  unittest.main()

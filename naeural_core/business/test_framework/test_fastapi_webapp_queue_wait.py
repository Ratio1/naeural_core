"""FastApiWebAppPlugin answers requests it will not run instead of leaving callers waiting."""
import ast
import inspect
import pathlib
import queue
import textwrap
import threading
import time
import unittest

from naeural_core.utils.fastapi_utils import PostponedRequest


FASTAPI_WEBAPP_PATH = pathlib.Path(__file__).resolve().parents[1] / "default" / "web_app" / "fast_api_web_app.py"
HARNESS_METHODS = (
  "get_request_timeout",
  "get_max_queue_wait",
  "get_max_incoming_requests",
  "get_deques_stats_str",
  "get_postponed_dict",
  "_reply_unhandled_request",
  "_reject_expired_request",
  "_process_incoming_request",
  "_start_request_monitor_thread",
)


def _build_harness_cls():
  """Compile the request dispatch methods without importing the plugin stack."""
  source = FASTAPI_WEBAPP_PATH.read_text()
  module = ast.parse(source)
  class_def = next(
    node for node in module.body
    if isinstance(node, ast.ClassDef) and node.name == "FastApiWebAppPlugin"
  )
  # Module-level numeric constants such as INCOMING_DROP_LOG_INTERVAL_S.
  constants = {}
  for node in module.body:
    if (
      isinstance(node, ast.Assign) and len(node.targets) == 1
      and isinstance(node.targets[0], ast.Name) and node.targets[0].id.isupper()
      and isinstance(node.value, (ast.Constant, ast.BinOp))
    ):
      exec(ast.get_source_segment(source, node), {}, constants)
  methods = {
    node.name: ast.get_source_segment(source, node)
    for node in class_def.body
    if isinstance(node, ast.FunctionDef) and node.name in HARNESS_METHODS
  }
  class_source = "class Harness:\n" + "\n\n".join(
    textwrap.indent(methods[name], "  ") for name in HARNESS_METHODS
  )
  class_source += (
    "\n\n  def __fastapi_process_response(self, response):\n"
    "    return {'result': response}\n"
    "\n\n  def __fastapi_handle_response(self, id, value, profile, method):\n"
    "    self._client_queue.put({'id': id, 'value': {'result': value}})\n"
  )
  namespace = {}
  exec(class_source, {
    "inspect": inspect, "PostponedRequest": PostponedRequest,
    "queue": queue, "threading": threading, "time": time, **constants,
  }, namespace)
  return namespace["Harness"]


class TestFastApiWebAppQueueWait(unittest.TestCase):

  def setUp(self):
    plugin = _build_harness_cls()()
    plugin.cfg_request_timeout = 600
    plugin.cfg_max_queue_wait = 90
    plugin.cfg_max_incoming_requests = 1000
    plugin.cfg_log_requests = False
    plugin._client_queue = queue.Queue()
    plugin._incoming_requests = []
    plugin._incoming_lock = threading.Lock()
    plugin._loop_wakeup = threading.Event()  # the request monitor now wakes the plugin loop
    plugin.postponed_requests = []
    plugin._server_queue = queue.Queue()
    plugin._last_incoming_drop_log_ts = 0.0
    plugin._expired_requests_since_log = 0
    plugin._last_expired_log_ts = 0.0
    plugin._current_request_client_ip = None
    plugin.logged = []
    plugin.P = lambda msg, *args, **kwargs: plugin.logged.append(msg)
    plugin.get_exception = lambda: "trace"
    plugin.on_request = lambda request: None
    self.calls = []
    plugin._endpoints = {"work": lambda **kwargs: self.calls.append(kwargs) or {"ok": True}}
    self.plugin = plugin

  def _request(self, waited_s=None, request_id="r1"):
    request = {"id": request_id, "value": ("work", {"x": 1}), "profile": None, "client_ip": None}
    if waited_s is not None:
      request["t_put_wall_ns"] = time.time_ns() - int(waited_s * 1e9)
    return request

  def _replies(self):
    replies = []
    while not self.plugin._client_queue.empty():
      replies.append(self.plugin._client_queue.get_nowait())
    return replies

  def test_request_waiting_longer_than_the_limit_gets_503_without_running(self):
    self.assertIsNone(self.plugin._process_incoming_request(self._request(waited_s=120)))
    self.assertEqual(self.calls, [])
    [reply] = self._replies()
    self.assertEqual(reply["id"], "r1")
    self.assertEqual(reply["value"]["result"]["status_code"], 503)
    self.assertIn("waited 120s", reply["value"]["result"]["error"])
    self.assertIn("Dropped 1 request(s) that waited more than 90s", self.plugin.logged[-1])

  def test_fresh_or_untimed_requests_run(self):
    for request in (self._request(waited_s=5), self._request()):
      with self.subTest(timed="t_put_wall_ns" in request):
        self.plugin._process_incoming_request(request)
    self.assertEqual(self.calls, [{"x": 1}, {"x": 1}])
    self.assertEqual([reply["value"]["result"] for reply in self._replies()], [{"ok": True}] * 2)

  def test_queue_wait_defaults_to_and_never_exceeds_request_timeout(self):
    cases = ((None, 600.0), (0, 600.0), (90, 90.0), (900, 600.0))
    for configured, expected in cases:
      with self.subTest(configured=configured):
        self.plugin.cfg_max_queue_wait = configured
        self.assertEqual(self.plugin.get_max_queue_wait(), expected)
    self.plugin.cfg_request_timeout = None
    self.plugin.cfg_max_queue_wait = 90
    self.assertEqual(self.plugin.get_max_queue_wait(), 90.0)

  def test_full_queue_answers_the_dropped_request_at_once(self):
    self.plugin.cfg_max_incoming_requests = 1
    self.plugin._stop_request_monitor = threading.Event()
    self.plugin._request_monitor_thread = None
    for request_id in ("kept", "dropped"):
      self.plugin._server_queue.put(self._request(waited_s=0, request_id=request_id))
    self.plugin._start_request_monitor_thread()
    deadline = time.time() + 5
    while self.plugin._client_queue.empty() and time.time() < deadline:
      time.sleep(0.01)
    self.plugin._stop_request_monitor.set()
    self.plugin._request_monitor_thread.join(timeout=2)
    self.assertEqual([request["id"] for request in self.plugin._incoming_requests], ["kept"])
    self.assertTrue(self.plugin._loop_wakeup.is_set())  # the queued request woke the plugin loop
    [reply] = self._replies()
    self.assertEqual(reply["id"], "dropped")
    self.assertEqual(reply["value"]["result"]["status_code"], 503)


if __name__ == "__main__":
  unittest.main()

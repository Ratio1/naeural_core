"""FastApiWebAppPlugin answers requests it will not run instead of leaving callers waiting."""
import ast
import asyncio
import inspect
import pathlib
import queue
import textwrap
import threading
import time
import types
import unittest
from unittest import mock

from naeural_core.utils import uvicorn_fast_api_ipc_manager as ipc_module
from naeural_core.utils.fastapi_utils import PostponedRequest
from naeural_core.utils.uvicorn_fast_api_ipc_manager import UvicornPluginComms


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
  # The monitor's hook, which the monitor calls on each turn (the base web app's no-op).
  "on_request_monitor_tick",
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

  def _run_monitor_on(self, request):
    self.plugin._stop_request_monitor = threading.Event()
    self.plugin._request_monitor_thread = None
    self.plugin._server_queue.put({"id": None, "value": ("__profile__", {})})
    self.plugin._server_queue.put(request)
    self.plugin._start_request_monitor_thread()
    deadline = time.time() + 5
    while self.plugin._server_queue.qsize() and time.time() < deadline:
      time.sleep(0.01)
    time.sleep(0.05)
    self.plugin._stop_request_monitor.set()
    self.plugin._request_monitor_thread.join(timeout=2)

  def test_monitor_runs_its_hook_while_idle_and_after_each_request_even_when_the_hook_raises(self):
    """The request monitor calls on_request_monitor_tick before each read of the server queue: right after each
    request (with two requests queued before it starts, no read times out before both are admitted, yet the hook runs
    with 0, 1 and 2 admitted) and while idle; a hook that raises is logged in red and the requests are still queued."""
    # A hook that records each call, then one that also raises.
    for raising in (False, True):
      # One sub-test each.
      with self.subTest(raising=raising):
        # How many requests were admitted at each call of the hook.
        seen = []

        def tick():
          """
          Stand for a web app's hook: record how many requests were admitted, and raise in the raising sub-test.

          Returns
          -------
          None

          Raises
          ------
          RuntimeError
            In the sub-test of a raising hook.
          """
          # Recorded.
          seen.append(len(self.plugin._incoming_requests))
          if raising:
            # The hook's error, which the monitor must log and survive.
            raise RuntimeError("hook")
        # The plugin's hook, for this sub-test (the compiled one is the base web app's no-op).
        self.plugin.on_request_monitor_tick = tick
        # Nothing admitted, a fresh monitor.
        self.plugin._incoming_requests.clear()
        # Its stop flag.
        self.plugin._stop_request_monitor = threading.Event()
        # No thread yet.
        self.plugin._request_monitor_thread = None
        # Two requests queued before the monitor starts, so no read times out until both are admitted.
        for request_id in ("r1", "r2"):
          # Queued on the server side.
          self.plugin._server_queue.put(self._request(waited_s=0, request_id=request_id))
        # Started.
        self.plugin._start_request_monitor_thread()
        # Idle for 0.6 s after them: reads time out every 0.25 s.
        time.sleep(0.6)
        # Stopped.
        self.plugin._stop_request_monitor.set()
        # Its thread ends.
        self.plugin._request_monitor_thread.join(timeout=2)
        # One call before each read (0, then 1, then 2 admitted), then the idle calls; both queued; the raise logged.
        self.assertEqual((seen[:3], len(seen) > 3, [request["id"] for request in self.plugin._incoming_requests],
                          any("Exception in on_request_monitor_tick" in msg for msg in self.plugin.logged)),
                         ([0, 1, 2], True, ["r1", "r2"], raising))

  def test_an_endpoint_error_logs_the_argument_names_never_their_values(self):
    """When an endpoint raises, the red line names the endpoint and its arguments' names (the positional ones by the
    endpoint's parameters, the keyword ones by their keys) and holds none of their values; the caller gets a 500."""

    def add_secrets(token, payload, **kwargs):
      """
      Stand for an endpoint that takes a credential and raises.

      Parameters
      ----------
      token : str
        A credential.
      payload : dict
        A body holding a credential.
      **kwargs
        Extra arguments.

      Returns
      -------
      None

      Raises
      ------
      TypeError
        Always, as a call that fails inside the endpoint.
      """
      # The endpoint's failure.
      raise TypeError("unexpected keyword argument 'result_info'")
    # The plugin's only endpoint.
    self.plugin._endpoints = {"add_secrets": add_secrets}
    # A request with a token, a payload and a keyword dict, each holding a secret value.
    self.plugin._process_incoming_request({"id": "r1", "value": (
      "add_secrets", "tok-123", {"api_key": "cf-secret"}, {"extra": "x-secret"}), "profile": None, "client_ip": None})
    # The red line about the error.
    line = next(msg for msg in self.plugin.logged if msg.startswith("Exception occured"))
    # The answer to the caller.
    [reply] = self._replies()
    # The endpoint and the three names in the line, no value, and a 500.
    self.assertEqual(("add_secrets" in line, "['token', 'payload', 'extra']" in line,
                      [secret for secret in ("tok-123", "cf-secret", "x-secret") if secret in line],
                      reply["value"]["result"]["status_code"]), (True, True, [], 500))

  def test_monitor_refuses_at_once_while_the_oldest_queued_request_has_expired(self):
    self.plugin._incoming_requests.extend([self._request(waited_s=100, request_id="old"), {"id": "q1"}])
    self._run_monitor_on(self._request(waited_s=0, request_id="late"))
    [reply] = self._replies()  # the refused timing report gets no answer
    self.assertEqual((reply["id"], reply["value"]["result"]["status_code"]), ("late", 503))
    # One text for both causes: a refusal by age must not say only that the queue is full.
    self.assertEqual(
      reply["value"]["result"]["error"],
      "Server busy: request queue is full or its oldest entry has waited past the limit.",
    )
    self.assertEqual([item["id"] for item in self.plugin._incoming_requests], ["old", "q1"])
    self.assertIn("the oldest for 100s", self.plugin.logged[-1])

  def test_monitor_refuses_while_an_expired_timing_report_heads_the_queue(self):
    # A timing report sent by the uvicorn side 100 s ago is the oldest queued entry.
    comms = object.__new__(UvicornPluginComms)
    comms._server_queue = queue.Queue()
    sent_100s_ago = time.time_ns() - 100 * 10**9
    with mock.patch.object(ipc_module, "time", types.SimpleNamespace(time_ns=lambda: sent_100s_ago)):
      asyncio.run(comms.send_profile_event({"req_id": "r0"}))
    report = comms._server_queue.get_nowait()
    self.plugin._incoming_requests.extend([report, self._request(waited_s=0, request_id="q1")])
    self._run_monitor_on(self._request(waited_s=0, request_id="late"))
    [reply] = self._replies()  # the report carries its queue time, so its age turns the door on
    self.assertEqual((reply["id"], reply["value"]["result"]["status_code"]), ("late", 503))
    self.assertEqual([item["id"] for item in self.plugin._incoming_requests], [None, "q1"])

  def test_monitor_queues_behind_a_long_but_fresh_queue(self):
    # A burst or a short stall: nothing has expired yet, so nothing is refused (as before).
    self.plugin._incoming_requests.extend(self._request(waited_s=40, request_id=f"q{i}") for i in range(200))
    self._run_monitor_on(self._request(waited_s=0, request_id="late"))
    self.assertEqual(self._replies(), [])
    self.assertEqual(self.plugin._incoming_requests[-1]["id"], "late")
    self.assertNotIn("queued_ahead", self.plugin._incoming_requests[-1])


if __name__ == "__main__":
  unittest.main()

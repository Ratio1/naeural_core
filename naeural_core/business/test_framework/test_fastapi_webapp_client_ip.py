"""FastApiWebAppPlugin exposes the HTTP client address to the running endpoint."""
import ast
import inspect
import pathlib
import textwrap
import unittest

from naeural_core.utils.fastapi_utils import PostponedRequest


FASTAPI_WEBAPP_PATH = pathlib.Path(__file__).resolve().parents[1] / "default" / "web_app" / "fast_api_web_app.py"
HARNESS_METHODS = (
  "get_request_client_ip",
  "get_postponed_dict",
  "parse_postponed_dict",
  "_process_incoming_request",
  "_process_postponed_request",
)


def _build_harness_cls():
  """Compile the request dispatch methods without importing the plugin stack."""
  source = FASTAPI_WEBAPP_PATH.read_text()
  class_def = next(
    node for node in ast.parse(source).body
    if isinstance(node, ast.ClassDef) and node.name == "FastApiWebAppPlugin"
  )
  methods = {
    node.name: ast.get_source_segment(source, node)
    for node in class_def.body
    if isinstance(node, ast.FunctionDef) and node.name in HARNESS_METHODS
  }
  class_source = "class Harness:\n" + "\n\n".join(
    textwrap.indent(methods[name], "  ") for name in HARNESS_METHODS
  )
  # The response is the end of the dispatch path, so the test records it directly.
  class_source += (
    "\n\n  def __fastapi_handle_response(self, id, value, profile, method):\n"
    "    self.responses.append((id, value))\n"
  )
  namespace = {}
  exec(class_source, {"inspect": inspect, "PostponedRequest": PostponedRequest}, namespace)
  return namespace["Harness"]


class TestFastApiWebAppClientIp(unittest.TestCase):

  def setUp(self):
    self.plugin = _build_harness_cls()()
    self.plugin._current_request_client_ip = None
    self.plugin.cfg_log_requests = False
    self.plugin.responses = []
    self.plugin.logged = []
    self.plugin.P = lambda msg, *args, **kwargs: self.plugin.logged.append(msg)
    self.plugin.get_exception = lambda: "trace"

  def _endpoint(self, result=None, error=None):
    seen = []

    def endpoint(**kwargs):
      seen.append(self.plugin.get_request_client_ip())
      if error is not None:
        raise error
      return result if result is not None else {"ok": True}

    self.plugin._endpoints = {"ping": endpoint}
    return seen

  def test_endpoint_sees_client_ip_only_while_it_runs(self):
    seen = self._endpoint()

    self.plugin._process_incoming_request({"id": "r1", "value": ("ping",), "client_ip": "203.0.113.7"})

    self.assertEqual(seen, ["203.0.113.7"])
    self.assertEqual(self.plugin.responses, [("r1", {"ok": True})])
    self.assertIsNone(self.plugin.get_request_client_ip())

  def test_client_ip_is_cleared_when_endpoint_raises(self):
    seen = self._endpoint(error=RuntimeError("boom"))

    self.plugin._process_incoming_request({"id": "r1", "value": ("ping",), "client_ip": "203.0.113.7"})

    self.assertEqual(seen, ["203.0.113.7"])
    self.assertEqual(self.plugin.responses[0][1]["status_code"], 500)
    self.assertIsNone(self.plugin.get_request_client_ip())

  def test_request_without_client_ip_reads_none(self):
    seen = self._endpoint()

    self.plugin._process_incoming_request({"id": "r1", "value": ("ping",)})

    self.assertEqual(seen, [None])

  def test_postponed_continuation_keeps_client_ip(self):
    seen_later = []

    def solver():
      seen_later.append(self.plugin.get_request_client_ip())
      if len(seen_later) == 1:
        return PostponedRequest(solver_method=solver)
      return {"done": True}

    self._endpoint(result=PostponedRequest(solver_method=solver))

    postponed = self.plugin._process_incoming_request(
      {"id": "r1", "value": ("ping",), "client_ip": "203.0.113.7"},
    )
    self.assertIsNone(self.plugin.get_request_client_ip())
    postponed = self.plugin._process_postponed_request(postponed)
    self.assertIsNone(self.plugin.get_request_client_ip())
    self.assertIsNone(self.plugin._process_postponed_request(postponed))

    self.assertEqual(seen_later, ["203.0.113.7", "203.0.113.7"])
    self.assertEqual(self.plugin.responses, [("r1", {"done": True})])
    self.assertIsNone(self.plugin.get_request_client_ip())


if __name__ == "__main__":
  unittest.main()

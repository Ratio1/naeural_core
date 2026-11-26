import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from naeural_core.utils.uvicorn_fast_api_ipc_manager import (
  UvicornPluginComms,
  get_server_manager,
)


class UvicornPluginCommsTimeoutTest(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.auth = b'test-auth'
    self.server_manager = get_server_manager(self.auth)
    _, self.port = self.server_manager.address
    self.comms = None

  async def asyncTearDown(self):
    try:
      if self.comms is not None:
        self.comms._stop_reader.set()
        if self.comms._reader_thread:
          self.comms._reader_thread.join(timeout=1)
        try:
          self.comms.manager.shutdown()
        except Exception:
          pass
    except Exception:
      pass
    try:
      self.server_manager.shutdown()
    except Exception:
      pass

  async def test_timeout_sets_logged_flags(self):
    self.comms = UvicornPluginComms(
      port=self.port,
      auth=self.auth,
      timeout_s=0.01,
      additional_fastapi_data={'extra': 'data'},
    )

    result = await self.comms.call_plugin("dummy_method")

    self.assertIsInstance(result, dict)
    self.assertEqual(result.get("status_code"), 504)
    self.assertTrue(result.get("logged"))
    exception_metadata = result.get("exception_metadata") or {}
    self.assertTrue(exception_metadata.get("logged"))


if __name__ == "__main__":
  unittest.main()


class FastApiServerIntegrationTest(unittest.TestCase):
  @staticmethod
  def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
      s.bind(("127.0.0.1", 0))
      return s.getsockname()[1]

  def _render_basic_server(self, dst_dir, manager_port, manager_auth, endpoint_registry):
    template_dir = Path(__file__).resolve().parents[1] / "business" / "base" / "uvicorn_templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    template = env.get_template("basic_server.j2")
    ctx = {
      "additional_fastapi_data": {},
      "manager_port": manager_port,
      "manager_auth": repr(manager_auth),
      "request_timeout": 1,
      "api_title": repr("Test API"),
      "api_summary": repr("Test summary"),
      "api_description": repr("Test description"),
      "api_version": repr("0.0.0"),
      "static_directory": "assets",
      "debug_web_app": False,
      "debug_timings": False,
      "debug_timings_steps": 5,
      "default_route": None,
      "profile_rate": 0,
      "profile_log_per_request": False,
      # Use the plugin's real decorator metadata for the opted-in route fixture.
      "node_comm_params": endpoint_registry._node_comms_jinja_args if endpoint_registry else [{
        "name": "ping",
        "method": "get",
        "args": [],
        "params": [],
        "endpoint_doc": "ping endpoint",
        "require_token": False,
        "has_kwargs": False,
        "streaming_type": None,
        "chunk_size": 1024 * 1024,
      }],
      "html_files": [],
    }
    rendered = template.render(**ctx)
    os.makedirs(dst_dir, exist_ok=True)
    os.makedirs(Path(dst_dir) / "assets", exist_ok=True)
    with open(Path(dst_dir) / "main.py", "w") as f:
      f.write(rendered)
    # Provide a stub aiofiles module to satisfy imports without extra deps.
    with open(Path(dst_dir) / "aiofiles.py", "w") as f:
      f.write(
        "class DummyFile:\n"
        "  async def __aenter__(self):\n"
        "    return self\n"
        "  async def __aexit__(self, exc_type, exc, tb):\n"
        "    return False\n"
        "  async def read(self, *args, **kwargs):\n"
        "    return b''\n"
        "async def open(*args, **kwargs):\n"
        "  return DummyFile()\n"
      )

  def _serve(self, requests, extra_uvicorn_args=(), endpoint_registry=None):
    """
    Run the generated server against a fake plugin loop and send `requests`.

    Parameters
    ----------
    requests : list of tuple
      Endpoint name, HTTP headers, and an optional JSON body for each request.
    extra_uvicorn_args : tuple of str
      Extra uvicorn command line arguments.
    endpoint_registry : object or None
      A plugin fixture with the registered endpoint methods, or None for the default ping route.

    Returns
    -------
    list of tuple
      HTTP status, response body, and client address received by the plugin per request.

    Examples
    --------
    >>> test._serve([("ping", {}, None)])  # doctest: +SKIP
    """
    auth = b"int-test"
    manager = get_server_manager(auth)
    _, manager_port = manager.address
    server_queue = manager.get_server_queue()
    client_queue = manager.get_client_queue()

    # Keep rendered server files private, and remove them after the test.
    rendered_server = tempfile.TemporaryDirectory(prefix="uvicorn_int_test_")
    self.addCleanup(rendered_server.cleanup)
    tmp_dir = rendered_server.name
    port = self._find_free_port()
    self._render_basic_server(tmp_dir, manager_port, auth, endpoint_registry)

    stop_plugin = threading.Event()
    client_ips = queue.Queue()

    def _plugin_loop():
      while not stop_plugin.is_set():
        try:
          msg = server_queue.get(timeout=0.1)
        except queue.Empty:
          continue
        if msg is None:
          break
        req_id = msg.get("id")
        value = msg.get("value") or ()
        if isinstance(value, (list, tuple)) and value and value[0] == "__profile__":
          continue
        method = value[0] if value else ""
        if method == "ping":
          client_ips.put(msg.get("client_ip"))
          client_queue.put({"id": req_id, "value": {"result": "pong"}})
        elif endpoint_registry:
          # Invoke the fixture's registered method so application opt-in controls the generated route.
          client_ips.put(msg.get("client_ip"))
          reply = endpoint_registry._endpoints[method](*value[1:])
          client_queue.put({"id": req_id, "value": reply})

    plugin_thread = threading.Thread(target=_plugin_loop, daemon=True)
    plugin_thread.start()

    proc = subprocess.Popen(
      [
        sys.executable, "-m", "uvicorn", "--app-dir", tmp_dir, "main:app",
        "--host", "127.0.0.1", "--port", str(port), *extra_uvicorn_args,
      ],
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,
      cwd=tmp_dir,
      env={**os.environ, "PYTHONPATH": f"{os.getcwd()}:{os.environ.get('PYTHONPATH', '')}"},
    )

    results = []
    try:
      # Wait for server to accept connections
      deadline = time.time() + 10
      ready = False
      while time.time() < deadline:
        try:
          with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            ready = True
            break
        except OSError:
          time.sleep(0.05)
      if not ready:
        stderr = (proc.stderr.read() or b"").decode(errors="ignore")
        self.fail(f"Uvicorn server did not start in time. stderr:\n{stderr}")

      for endpoint, headers, payload in requests:
        # A supplied body makes a JSON POST; the original ping cases still use GET.
        body = json.dumps(payload).encode() if payload is not None else None
        http_request = urllib.request.Request(f"http://127.0.0.1:{port}/{endpoint}", data=body, headers=headers)
        try:
          response = urllib.request.urlopen(http_request, timeout=5)
        except urllib.error.HTTPError as exc:
          response = exc  # Error replies remain available for body and status assertions.
        with response:
          results.append((response.status, json.loads(response.read()), client_ips.get(timeout=5)))
    finally:
      stop_plugin.set()
      server_queue.put(None)
      plugin_thread.join(timeout=1)
      proc.terminate()
      try:
        proc.wait(timeout=5)
      except subprocess.TimeoutExpired:
        proc.kill()
      try:
        manager.shutdown()
      except Exception:
        pass
      if proc.stdout:
        proc.stdout.close()
      if proc.stderr:
        proc.stderr.close()
    return results

  def test_generated_server_end_to_end(self):
    # An ordinary generated endpoint retains its default wire status.
    (status, data, client_ip), = self._serve([("ping", {}, None)])
    self.assertEqual(status, 200)
    self.assertEqual(data.get("result"), "pong")
    self.assertEqual(client_ip, "127.0.0.1")

  def test_client_ip_comes_from_forwarded_for_of_local_proxy(self):
    # A tunnel agent on 127.0.0.1 forwards the caller address; a value the caller
    # sent itself stays to the left and is ignored.
    (_, _, client_ip), = self._serve([("ping", {"X-Forwarded-For": "198.51.100.1, 203.0.113.7"}, None)])
    self.assertEqual(client_ip, "203.0.113.7")

  def test_client_ip_ignores_forwarded_for_of_untrusted_peer(self):
    (_, _, client_ip), = self._serve(
      [("ping", {"X-Forwarded-For": "203.0.113.7"}, None)],
      extra_uvicorn_args=("--forwarded-allow-ips", "192.0.2.1"),
    )
    self.assertEqual(client_ip, "127.0.0.1")

  def test_only_opted_in_post_endpoints_forward_http_acceptance(self):
    """Verify status and body through a generated server and the local IPC transport.

    Parameters
    ----------
    None

    Returns
    -------
    None

    Examples
    --------
    >>> FastApiServerIntegrationTest('test_only_opted_in_post_endpoints_forward_http_acceptance').run()
    """
    from naeural_core.business.default.web_app.fast_api_web_app import FastApiWebAppPlugin

    class EndpointRegistry:
      # Both routes use only synthetic replies on the loopback test server.
      cfg_endpoints = []

      @staticmethod
      def P(*args, **kwargs):
        pass

      @FastApiWebAppPlugin.endpoint(method="post", forward_accepted_status=True)
      def submit(self, reply: dict):
        return reply  # Stand in for the plugin's already signed result.

      @FastApiWebAppPlugin.endpoint(method="post")
      def ordinary(self, reply: dict):
        return reply  # An app payload can include a status field without opting in.

    # Register exactly as the runtime does, then let the template render both routes.
    registry = EndpointRegistry()
    FastApiWebAppPlugin._init_endpoints(registry)
    accepted = {"status_code": 202, "status": "accepted", "operation_id": "fixture", "EE_SIGN": "fixture-signature"}
    conflict = {"status_code": 409, "status": "error", "error": "Active operation", "EE_SIGN": "fixture-signature"}
    headers = {"Content-Type": "application/json"}
    requests = [
      ("submit", headers, {"reply": {"status_code": 202, "result": accepted}}),
      ("submit", headers, {"reply": {"status_code": 409, "result": conflict, "logged": True}}),
      ("ordinary", headers, {"reply": {"status_code": 202, "result": accepted}}),
      ("submit", headers, {"reply": {"result": "ordinary success"}}),
    ]
    # Both status routes preserve their existing body shapes and only explicit opted-in acceptance changes at the wire.
    outcomes = [(status, body) for status, body, _ in self._serve(requests, endpoint_registry=registry)]
    self.assertEqual(outcomes, [
      (202, {"status_code": 202, "result": accepted}),
      (409, {"detail": conflict}),
      (200, {"status_code": 202, "result": accepted}),
      (200, {"result": "ordinary success"}),
    ])


class IpcModuleFootprintTest(unittest.TestCase):
  def test_ipc_module_loads_only_the_standard_library(self):
    # The uvicorn app process imports only this module; keep the node out of it. (The IPC manager process is
    # spawned and re-runs the node's main script, so this test says nothing about it.)
    code = (
      "import sys, naeural_core.utils.uvicorn_fast_api_ipc_manager\n"
      "heavy = [m for m in ('torch', 'numpy', 'pandas', 'ratio1', 'naeural_core.core_logging') if m in sys.modules]\n"
      "sys.exit(str(heavy) if heavy else 0)\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    self.assertEqual(res.returncode, 0, res.stderr)

import json
import os
import queue
import socket
import subprocess
import tempfile
import threading
import time
import unittest
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

  def _render_basic_server(self, dst_dir, manager_port, manager_auth, endpoint_name="ping"):
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
      "node_comm_params": [{
        "name": endpoint_name,
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

  def _serve(self, requests, extra_uvicorn_args=()):
    """
    Run the generated server against a fake plugin loop and send `requests`.

    Parameters
    ----------
    requests : list of dict
      HTTP headers of each GET /ping request.
    extra_uvicorn_args : tuple of str
      Extra uvicorn command line arguments.

    Returns
    -------
    list of tuple
      (response body, client_ip received by the plugin) per request.
    """
    auth = b"int-test"
    manager = get_server_manager(auth)
    _, manager_port = manager.address
    server_queue = manager.get_server_queue()
    client_queue = manager.get_client_queue()

    tmp_dir = tempfile.mkdtemp(prefix="uvicorn_int_test_")
    port = self._find_free_port()
    self._render_basic_server(tmp_dir, manager_port, auth)

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

    plugin_thread = threading.Thread(target=_plugin_loop, daemon=True)
    plugin_thread.start()

    proc = subprocess.Popen(
      [
        "python3", "-m", "uvicorn", "--app-dir", tmp_dir, "main:app",
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

      for headers in requests:
        http_request = urllib.request.Request(f"http://127.0.0.1:{port}/ping", headers=headers)
        with urllib.request.urlopen(http_request, timeout=5) as resp:
          data = json.loads(resp.read().decode())
        results.append((data, client_ips.get(timeout=5)))
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
    (data, client_ip), = self._serve([{}])
    self.assertEqual(data.get("result"), "pong")
    self.assertEqual(client_ip, "127.0.0.1")

  def test_client_ip_comes_from_forwarded_for_of_local_proxy(self):
    # A tunnel agent on 127.0.0.1 forwards the caller address; a value the caller
    # sent itself stays to the left and is ignored.
    (_, client_ip), = self._serve([{"X-Forwarded-For": "198.51.100.1, 203.0.113.7"}])
    self.assertEqual(client_ip, "203.0.113.7")

  def test_client_ip_ignores_forwarded_for_of_untrusted_peer(self):
    (_, client_ip), = self._serve(
      [{"X-Forwarded-For": "203.0.113.7"}],
      extra_uvicorn_args=("--forwarded-allow-ips", "192.0.2.1"),
    )
    self.assertEqual(client_ip, "127.0.0.1")

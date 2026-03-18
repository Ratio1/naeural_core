import ast
import pathlib
import unittest


FASTAPI_WEBAPP_PATH = pathlib.Path(__file__).resolve().parents[1] / "default" / "web_app" / "fast_api_web_app.py"


class TestFastApiWebAppSemaphoreEnv(unittest.TestCase):

  @classmethod
  def setUpClass(cls):
    cls.source = FASTAPI_WEBAPP_PATH.read_text()
    cls.module = ast.parse(cls.source)

  def _get_setup_semaphore_env(self):
    for node in self.module.body:
      if isinstance(node, ast.ClassDef) and node.name == "FastApiWebAppPlugin":
        for item in node.body:
          if isinstance(item, ast.FunctionDef) and item.name == "_setup_semaphore_env":
            return item
    self.fail("FastApiWebAppPlugin._setup_semaphore_env not found")

  def test_fastapi_webapp_defines_default_semaphore_env_hook(self):
    method = self._get_setup_semaphore_env()
    self.assertIsNotNone(method)

  def test_fastapi_webapp_uses_runtime_port_in_semaphore_env_hook(self):
    method = self._get_setup_semaphore_env()
    segment = ast.get_source_segment(self.source, method) or ""
    self.assertIn("port = self.port", segment)
    self.assertNotIn("port = self.cfg_port", segment)

  def test_fastapi_webapp_exports_host_and_runtime_port_keys(self):
    method = self._get_setup_semaphore_env()
    segment = ast.get_source_segment(self.source, method) or ""
    self.assertIn("self.semaphore_set_env('HOST', localhost_ip)", segment)
    self.assertIn("self.semaphore_set_env('HOST_IP', localhost_ip)", segment)
    self.assertIn("self.semaphore_set_env('PORT', str(port))", segment)
    self.assertIn("self.semaphore_set_env('HOST_PORT', str(port))", segment)
    self.assertIn("self.semaphore_set_env('URL', 'http://{}:{}'.format(localhost_ip, port))", segment)

  def test_fastapi_webapp_guards_host_resolution_failures(self):
    method = self._get_setup_semaphore_env()
    segment = ast.get_source_segment(self.source, method) or ""
    self.assertIn("try:", segment)
    self.assertIn("localhost_ip = self.log.get_localhost_ip()", segment)
    self.assertIn("except Exception as ex:", segment)
    self.assertIn("Skipping HOST/HOST_IP semaphore export", segment)
    self.assertIn("Skipping URL semaphore export", segment)


if __name__ == "__main__":
  unittest.main()

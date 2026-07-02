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

  def test_fastapi_webapp_exports_api_endpoint_keys(self):
    method = self._get_setup_semaphore_env()
    segment = ast.get_source_segment(self.source, method) or ""
    self.assertIn("self.semaphore_set_env('API_IP', localhost_ip)", segment)
    self.assertIn("self.semaphore_set_env('API_PORT', str(port))", segment)
    self.assertIn("self.semaphore_set_env('API_URL', 'http://{}:{}'.format(localhost_ip, port))", segment)
    self.assertNotIn("self.semaphore_set_env('HOST', localhost_ip)", segment)
    self.assertNotIn("self.semaphore_set_env('HOST_IP', localhost_ip)", segment)
    self.assertNotIn("self.semaphore_set_env('PORT', str(port))", segment)
    self.assertNotIn("self.semaphore_set_env('HOST_PORT', str(port))", segment)
    self.assertNotIn("self.semaphore_set_env('URL', 'http://{}:{}'.format(localhost_ip, port))", segment)
    self.assertIn("self._setup_api_identifier_semaphore_env()", segment)

  def test_fastapi_webapp_defines_api_identifier_config_default(self):
    for node in self.module.body:
      if isinstance(node, ast.Assign):
        for target in node.targets:
          if isinstance(target, ast.Name) and target.id == "_CONFIG":
            config_node = node.value
            break
        else:
          continue
        break
    else:
      self.fail("_CONFIG assignment not found")

    for key, value in zip(config_node.keys, config_node.values):
      if isinstance(key, ast.Constant) and key.value == "API_IDENTIFIER":
        self.assertIsInstance(value, ast.Constant)
        self.assertIsNone(value.value)
        return
    self.fail("API_IDENTIFIER default not found")

  def test_fastapi_webapp_exports_non_empty_api_identifier(self):
    source = self.source
    method = None
    for node in self.module.body:
      if isinstance(node, ast.ClassDef) and node.name == "FastApiWebAppPlugin":
        for item in node.body:
          if isinstance(item, ast.FunctionDef) and item.name == "_setup_api_identifier_semaphore_env":
            method = item
            break
    self.assertIsNotNone(method)
    segment = ast.get_source_segment(source, method) or ""
    self.assertIn("self.cfg_api_identifier", segment)
    self.assertIn("self.semaphore_set_env('API_IDENTIFIER', api_identifier)", segment)
    self.assertIn('api_identifier.lower() in ["none", "null"]', segment)

  def test_fastapi_webapp_guards_host_resolution_failures(self):
    method = self._get_setup_semaphore_env()
    segment = ast.get_source_segment(self.source, method) or ""
    self.assertIn("try:", segment)
    self.assertIn("localhost_ip = self.log.get_localhost_ip()", segment)
    self.assertIn("except Exception as ex:", segment)
    self.assertIn("Skipping API_IP semaphore export", segment)
    self.assertIn("Skipping API_URL semaphore export", segment)


if __name__ == "__main__":
  unittest.main()

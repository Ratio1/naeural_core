import ast
import pathlib
import textwrap
import unittest


FASTAPI_WEBAPP_PATH = pathlib.Path(__file__).resolve().parents[1] / "default" / "web_app" / "fast_api_web_app.py"
UVICORN_TEMPLATE_PATH = pathlib.Path(__file__).resolve().parents[1] / "base" / "uvicorn_templates" / "basic_server.j2"


def _make_profile(idx, endpoint="predict"):
  base = idx + 1
  return {
    "endpoint": endpoint,
    "t_http_start_ns": 0,
    "t_before_call_plugin_ns": 0,
    "t_put_start_ns": 1_000_000 * base,
    "t_put_end_ns": 2_000_000 * base,
    "t_wait_start_ns": 2_000_000 * base,
    "t_endpoint_start_ns": 3_000_000 * base,
    "t_endpoint_end_ns": 4_000_000 * base,
    "t_wait_end_ns": 5_000_000 * base,
    "t_after_call_plugin_ns": 6_000_000 * base,
    "t_before_return_ns": 7_000_000 * base,
    "t_put_wall_ns": 10_000_000_000,
    "t_plugin_dequeue_wall_ns": 10_000_000_000 + ((idx + 3) * 1_000_000),
    "exec_total_ns": 1_000_000 * base,
    "slice_count": 1 if idx % 2 == 0 else 2,
  }


class TestFastApiWebAppTimingDebug(unittest.TestCase):

  @classmethod
  def setUpClass(cls):
    cls.source = FASTAPI_WEBAPP_PATH.read_text()
    cls.module = ast.parse(cls.source)
    cls.class_def = next(
      node for node in cls.module.body
      if isinstance(node, ast.ClassDef) and node.name == "FastApiWebAppPlugin"
    )

  def _get_config_entries(self):
    for node in self.module.body:
      if not isinstance(node, ast.Assign):
        continue
      if len(node.targets) != 1:
        continue
      target = node.targets[0]
      if not isinstance(target, ast.Name) or target.id != "_CONFIG":
        continue
      config = {}
      for key, value in zip(node.value.keys, node.value.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
          try:
            config[key.value] = ast.literal_eval(value)
          except (ValueError, SyntaxError):
            continue
      return config
    self.fail("_CONFIG assignment not found")

  def _get_method_node(self, method_name):
    for item in self.class_def.body:
      if isinstance(item, ast.FunctionDef) and item.name == method_name:
        return item
    self.fail(f"FastApiWebAppPlugin.{method_name} not found")

  def _build_harness(self, entrypoint):
    method_nodes = {
      item.name: item
      for item in self.class_def.body
      if isinstance(item, ast.FunctionDef)
    }
    ordered_names = []
    seen = set()

    def visit(name):
      if name in seen:
        return
      seen.add(name)
      node = method_nodes[name]
      for subnode in ast.walk(node):
        func = getattr(subnode, "func", None)
        if not isinstance(func, ast.Attribute):
          continue
        if not isinstance(func.value, ast.Name) or func.value.id != "self":
          continue
        if func.attr in method_nodes:
          visit(func.attr)
      ordered_names.append(name)

    visit(entrypoint)
    class_source = "class Harness:\n"
    for method_name in ordered_names:
      method_source = ast.get_source_segment(self.source, method_nodes[method_name])
      class_source += textwrap.indent(method_source, "  ")
      class_source += "\n\n"
    namespace = {}
    exec(class_source, {}, namespace)
    return namespace["Harness"]

  def _new_harness(self):
    harness_cls = self._build_harness("_handle_profile_event")
    harness = harness_cls()
    harness._profile_stats = {}
    harness.cfg_profile_rate = 1.0
    harness.cfg_profile_log_per_request = True
    harness.cfg_profile_log_detailed = True
    harness.cfg_debug_timings = True
    harness.cfg_debug_timings_steps = 5
    harness.logged = []
    harness.P = harness.logged.append
    return harness

  def test_fastapi_webapp_exposes_debug_timing_defaults(self):
    config = self._get_config_entries()
    self.assertIn("DEBUG_TIMINGS", config)
    self.assertTrue(config["DEBUG_TIMINGS"])
    self.assertIn("DEBUG_TIMINGS_STEPS", config)
    self.assertEqual(config["DEBUG_TIMINGS_STEPS"], 5)

  def test_fastapi_webapp_exports_debug_timing_template_values(self):
    segment = ast.get_source_segment(self.source, self._get_method_node("jinja_args")) or ""
    self.assertIn("'debug_timings': self.cfg_debug_timings", segment)
    self.assertIn("'debug_timings_steps': self.get_debug_timings_steps()", segment)

  def test_uvicorn_template_exposes_debug_timing_constants(self):
    template = UVICORN_TEMPLATE_PATH.read_text()
    self.assertIn("DEBUG_TIMINGS = {{ debug_timings }}", template)
    self.assertIn("DEBUG_TIMINGS_STEPS = {{ debug_timings_steps }}", template)

  def test_timing_logs_wait_for_full_batch(self):
    harness = self._new_harness()

    for idx in range(4):
      harness._handle_profile_event(_make_profile(idx))

    self.assertEqual(harness.logged, [])

  def test_timing_logs_emit_lists_every_fifth_call(self):
    harness = self._new_harness()

    for idx in range(5):
      harness._handle_profile_event(_make_profile(idx))

    self.assertEqual(len(harness.logged), 1)
    message = harness.logged[0]
    self.assertIn("<predict> timings(ms): t [7.00, 14.00, 21.00, 28.00, 35.00]", message)
    self.assertIn("w [3.00, 6.00, 9.00, 12.00, 15.00]", message)
    self.assertIn("q [3.00, 4.00, 5.00, 6.00, 7.00]", message)
    self.assertIn("e [1.00, 2.00, 3.00, 4.00, 5.00]", message)
    self.assertIn("steps [1, 2, 1, 2, 1]", message)
    self.assertIn("detail call_start=[0.00, 0.00, 0.00, 0.00, 0.00]", message)

  def test_debug_timings_flag_suppresses_batched_log_output(self):
    harness = self._new_harness()
    harness.cfg_debug_timings = False

    for idx in range(5):
      harness._handle_profile_event(_make_profile(idx))

    self.assertEqual(harness.logged, [])


if __name__ == "__main__":
  unittest.main()

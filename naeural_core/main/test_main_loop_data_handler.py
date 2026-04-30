import unittest
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class _FakeDecentrAIObject:
  def __init__(self, log=None, prefix_log=None, **kwargs):
    self.log = log
    self.prefix_log = prefix_log

  def P(self, *args, **kwargs):
    return


class _FakeLogger:
  def flatten_2d_list(self, lst):
    return [item for sublist in lst for item in sublist]


def _load_utils_functions():
  """Load `utils.py` through the import system with stubbed dependencies."""
  source_path = ROOT / "serving" / "ai_engines" / "utils.py"
  fake_ai_engines_module = types.ModuleType("naeural_core.serving.ai_engines")
  fake_ai_engines_module.AI_ENGINES = {
    "text_classifier": {"SERVING_PROCESS": "th_text_classifier"},
  }
  module_name = "naeural_core.serving.ai_engines.utils_under_test"
  spec = importlib.util.spec_from_file_location(module_name, source_path)
  module = importlib.util.module_from_spec(spec)
  assert spec is not None and spec.loader is not None
  with patch.dict(
    sys.modules,
    {
      "naeural_core.serving.ai_engines": fake_ai_engines_module,
    },
  ):
    spec.loader.exec_module(module)
  return module


def _load_main_loop_data_handler():
  """Load `main_loop_data_handler.py` with stubbed `naeural_core` imports."""
  source_path = ROOT / "main" / "main_loop_data_handler.py"
  utils_module = _load_utils_functions()

  fake_core = types.ModuleType("naeural_core")
  fake_core.DecentrAIObject = _FakeDecentrAIObject
  fake_core.Logger = _FakeLogger
  fake_core.__path__ = []  # type: ignore[attr-defined]
  fake_serving = types.ModuleType("naeural_core.serving")
  fake_serving.__path__ = []  # type: ignore[attr-defined]
  fake_ai_engines_pkg = types.ModuleType("naeural_core.serving.ai_engines")
  fake_ai_engines_pkg.__path__ = []  # type: ignore[attr-defined]

  module_name = "naeural_core.main.main_loop_data_handler_under_test"
  spec = importlib.util.spec_from_file_location(module_name, source_path)
  module = importlib.util.module_from_spec(spec)
  assert spec is not None and spec.loader is not None
  with patch.dict(
    sys.modules,
    {
      "naeural_core": fake_core,
      "naeural_core.serving": fake_serving,
      "naeural_core.serving.ai_engines": fake_ai_engines_pkg,
      "naeural_core.serving.ai_engines.utils": utils_module,
    },
  ):
    spec.loader.exec_module(module)
  return module.MainLoopDataHandler, utils_module.get_serving_process_given_ai_engine


MainLoopDataHandler, get_serving_process_given_ai_engine = _load_main_loop_data_handler()


class _FakeServingManager:
  def server_runs_on_empty_input(self, serving_process):
    return False


class _FakeOwner:
  def __init__(self):
    self.serving_manager = _FakeServingManager()


class MainLoopDataHandlerTests(unittest.TestCase):
  def setUp(self):
    self.handler = MainLoopDataHandler(log=_FakeLogger(), owner=_FakeOwner())
    self.handler.update(
      dct_captures={
        "stream_alpha": {
          "STREAM_NAME": "stream_alpha",
          "STREAM_METADATA": {},
          "INPUTS": [{"STRUCT_DATA": {"text": "alpha"}, "TYPE": "STRUCT_DATA"}],
        },
        "stream_beta": {
          "STREAM_NAME": "stream_beta",
          "STREAM_METADATA": {},
          "INPUTS": [{"STRUCT_DATA": {"text": "beta"}, "TYPE": "STRUCT_DATA"}],
        },
      },
      dct_instances_details={},
      dct_serving_processes_details={
        ("text_classifier", "privacy_filter"): {
          ("stream_alpha", "{}"): ["instance_alpha"],
        },
        ("text_classifier", "compliance_classifier"): {
          ("stream_beta", "{}"): ["instance_beta"],
        },
      },
    )
    self.handler.append_captures()

  def test_ai_engine_handle_preserves_model_instance_id(self):
    self.assertEqual(
      get_serving_process_given_ai_engine(("text_classifier", "privacy_filter")),
      ("th_text_classifier", "privacy_filter"),
    )

  def test_aggregate_for_inference_separates_same_serving_class_instances(self):
    aggregated = self.handler.aggregate_for_inference()

    self.assertEqual(
      sorted(aggregated.keys()),
      [
        ("th_text_classifier", "compliance_classifier"),
        ("th_text_classifier", "privacy_filter"),
      ],
    )
    self.assertEqual(
      aggregated[("th_text_classifier", "privacy_filter")][0]["STREAM_NAME"],
      "stream_alpha",
    )
    self.assertEqual(
      aggregated[("th_text_classifier", "compliance_classifier")][0]["STREAM_NAME"],
      "stream_beta",
    )


if __name__ == "__main__":
  unittest.main()

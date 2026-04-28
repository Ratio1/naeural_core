import unittest
from pathlib import Path


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
  source_path = ROOT / "serving" / "ai_engines" / "utils.py"
  source = source_path.read_text(encoding="utf-8")
  source = source.replace("from naeural_core.serving.ai_engines import AI_ENGINES\n", "")
  namespace = {
    "AI_ENGINES": {
      "text_classifier": {"SERVING_PROCESS": "th_text_classifier"},
    },
    "__name__": "loaded_ai_engines_utils",
  }
  exec(compile(source, str(source_path), "exec"), namespace)  # noqa: S102
  return (
    namespace["get_serving_process_given_ai_engine"],
    namespace["get_ai_engine_given_serving_process"],
    namespace["get_params_given_ai_engine"],
  )


def _load_main_loop_data_handler():
  source_path = ROOT / "main" / "main_loop_data_handler.py"
  source = source_path.read_text(encoding="utf-8")
  source = source.replace("from naeural_core import DecentrAIObject\n", "")
  source = source.replace("from naeural_core import Logger\n", "")
  source = source.replace(
    "from naeural_core.serving.ai_engines.utils import (\n"
    "  get_serving_process_given_ai_engine,\n"
    "  get_ai_engine_given_serving_process,\n"
    "  get_params_given_ai_engine\n"
    ")\n",
    "",
  )
  (
    get_serving_process_given_ai_engine,
    get_ai_engine_given_serving_process,
    get_params_given_ai_engine,
  ) = _load_utils_functions()
  namespace = {
    "DecentrAIObject": _FakeDecentrAIObject,
    "Logger": _FakeLogger,
    "get_serving_process_given_ai_engine": get_serving_process_given_ai_engine,
    "get_ai_engine_given_serving_process": get_ai_engine_given_serving_process,
    "get_params_given_ai_engine": get_params_given_ai_engine,
    "__name__": "loaded_main_loop_data_handler",
  }
  exec(compile(source, str(source_path), "exec"), namespace)  # noqa: S102
  return namespace["MainLoopDataHandler"], get_serving_process_given_ai_engine


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

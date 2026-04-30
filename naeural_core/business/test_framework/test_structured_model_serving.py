import json
import os
import shutil
import tempfile
import unittest

from naeural_core import SBLogger
from naeural_core.core_logging.full_logger import Logger
from naeural_core.local_libraries.nn.th.training.pipelines.structured import StructuredTrainingPipeline
from naeural_core.serving.default_inference.th_structured import ThStructured


class TestStructuredModelServing(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls._original_get_localhost_ip = Logger.get_localhost_ip
    Logger.get_localhost_ip = lambda self: "127.0.0.1"
    return

  @classmethod
  def tearDownClass(cls):
    Logger.get_localhost_ip = cls._original_get_localhost_ip
    return

  def setUp(self):
    self.base_dir = tempfile.mkdtemp(prefix="structured_serving_test_", dir="/tmp")
    self._create_fixture_dataset(self.base_dir)
    self.log = SBLogger()
    self.input_fields = [
      {"path": "features.amount", "modality": "numeric"},
      {"path": "features.approved_hint", "modality": "bool"},
      {"path": "features.risk_tags", "modality": "list_categorical", "max_list_size": 4},
      {"path": "features.message", "modality": "text", "max_tokens": 8},
      {"path": "features.created_at", "modality": "datetime"},
    ]
    self.output_fields = [
      {"path": "targets.risk_score", "modality": "numeric"},
      {"path": "targets.approved", "modality": "bool"},
      {"path": "targets.risk_band", "modality": "categorical"},
    ]
    self.task_matrix = "policy_risk_scoring_v1"
    self.pipeline_config = {
      "MODEL_NAME": "structured_policy_serving_smoke",
      "BATCH_SIZE": 4,
      "EPOCHS": 1,
      "KEEP_TOP_K_ITERATIONS": 1,
      "DEVICE_LOAD_DATA": "cpu",
      "DEVICE_TRAINING": "cpu",
      "PRELOAD_DATA": True,
      "NUM_WORKERS": 0,
      "EXPORT_FORMATS": ["torchscript"],
      "INPUT_FIELDS": self.input_fields,
      "OUTPUT_FIELDS": self.output_fields,
      "TASK_MATRIX": self.task_matrix,
    }
    return

  def tearDown(self):
    shutil.rmtree(self.base_dir, ignore_errors=True)
    return

  def _make_record(self, idx):
    return {
      "features": {
        "amount": float(idx) / 10.0,
        "approved_hint": idx % 2,
        "risk_tags": ["alpha", "beta" if idx % 2 == 0 else "gamma"],
        "message": f"user {idx} triggered policy check",
        "created_at": f"2026-04-{(idx % 9) + 1:02d}T12:00:00+00:00",
      },
      "targets": {
        "risk_score": float(idx) / 7.0,
        "approved": idx % 2,
        "risk_band": "high" if idx % 3 == 0 else ("medium" if idx % 3 == 1 else "low"),
      },
    }

  def _create_fixture_dataset(self, base_dir):
    for subset, count in [("train", 12), ("dev", 4), ("test", 4)]:
      os.makedirs(os.path.join(base_dir, subset), exist_ok=True)
      with open(os.path.join(base_dir, subset, "data.jsonl"), "w", encoding="utf-8") as handle:
        for idx in range(count):
          handle.write(json.dumps(self._make_record(idx)) + "\n")
    return

  def _train_pipeline(self):
    pipeline = StructuredTrainingPipeline(
      log=self.log,
      signature="structured",
      config=self.pipeline_config,
      path_to_dataset=self.base_dir,
    )
    success = pipeline.run()
    self.assertTrue(success)
    return pipeline

  def test_structured_model_serving_runs_local_end_to_end(self):
    pipeline = self._train_pipeline()
    serving_config = pipeline.metadata["SERVING_MODEL_CONFIG"]
    serving_config_path = os.path.join(self.base_dir, "structured_serving_config.json")
    with open(serving_config_path, "w", encoding="utf-8") as handle:
      json.dump(serving_config, handle)

    server = ThStructured(
      server_name="th_structured_demo",
      comm_eng=None,
      inprocess=True,
      default_config=ThStructured.CONFIG,
      upstream_config={
        "MODEL_CONFIG_PATH": serving_config_path,
        "DEVICE": "cpu",
      },
      full_debug=False,
      log=self.log,
      environment_variables={},
      version="0.0.0",
      npy_shm_kwargs=None,
      comm_method="pipe",
    )

    payloads = [
      self._make_record(1),
      self._make_record(2),
    ]
    prep_inputs = server.pre_process({
      "DATA": payloads,
      "STREAM_NAME": ["stream_1", "stream_2"],
    })
    preds = server.predict(prep_inputs)
    results = server.post_process(preds)

    self.assertEqual(len(results), 2)
    self.assertIn("targets", results[0])
    self.assertIn("risk_score", results[0]["targets"])
    self.assertIn("approved", results[0]["targets"])
    self.assertIn("risk_band", results[0]["targets"])
    self.assertIsInstance(results[0]["targets"]["risk_score"], float)
    self.assertIsInstance(results[0]["targets"]["approved"], bool)
    self.assertIsInstance(results[0]["targets"]["risk_band"], str)
    self.assertIn(
      results[0]["targets"]["risk_band"],
      {"high", "medium", "low"},
    )
    return


if __name__ == "__main__":
  unittest.main()

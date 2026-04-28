import json

import torch as th

from naeural_core.serving.base.base_serving_process import ModelServingProcess as BaseServingProcess
from naeural_core.local_libraries.nn.th.training.data.structured import StructuredServingCodec


_CONFIG = {
  **BaseServingProcess.CONFIG,
  "PICKED_INPUT": "STRUCT_DATA",
  "MODEL_PATH": None,
  "MODEL_CONFIG_PATH": None,
  "DEVICE": "cpu",
  "THRESHOLD": 0.5,
  "INCLUDE_SCORES": False,
  "ALLOW_NESTED_INPUTS": True,
  "ALLOW_NESTED_OUTPUTS": True,
  "INPUT_FIELDS": [],
  "OUTPUT_FIELDS": [],
  "TASK_MATRIX": None,
  "SCHEMA_HASH": None,
  "VALIDATION_RULES": {
    **BaseServingProcess.CONFIG["VALIDATION_RULES"],
  },
}


class ThStructured(BaseServingProcess):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    self.model = None
    self.codec = None
    self.device = None
    super(ThStructured, self).__init__(**kwargs)
    return

  @property
  def th(self):
    return th

  def _load_local_model_config(self):
    cfg_path = self.config_model.get("MODEL_CONFIG_PATH")
    if cfg_path is None:
      return {}
    with open(cfg_path, "r", encoding="utf-8") as handle:
      return json.load(handle)

  def startup(self):
    model_config = self._load_local_model_config()
    if isinstance(model_config, dict) and len(model_config) > 0:
      self.config_model = {
        **self.config_model,
        **model_config,
      }

    model_path = self.config_model.get("MODEL_PATH")
    if model_path is None:
      raise ValueError("Structured serving requires `MODEL_PATH`")

    input_fields = self.config_model.get("INPUT_FIELDS") or []
    output_fields = self.config_model.get("OUTPUT_FIELDS") or []
    task_matrix = self.config_model.get("TASK_MATRIX")
    if not input_fields or not output_fields or task_matrix is None:
      raise ValueError(
        "Structured serving requires `INPUT_FIELDS`, `OUTPUT_FIELDS`, and `TASK_MATRIX`"
      )

    self.device = self.th.device(self.config_model.get("DEVICE", "cpu"))
    self.model = self.th.jit.load(model_path, map_location=self.device)
    self.model.eval()
    self.codec = StructuredServingCodec(
      log=self.log,
      input_fields=input_fields,
      output_fields=output_fields,
      task_matrix=task_matrix,
      allow_nested_inputs=bool(self.config_model.get("ALLOW_NESTED_INPUTS", True)),
      allow_nested_outputs=bool(self.config_model.get("ALLOW_NESTED_OUTPUTS", True)),
      threshold=float(self.config_model.get("THRESHOLD", 0.5)),
      include_scores=bool(self.config_model.get("INCLUDE_SCORES", False)),
    )
    return

  def get_additional_metadata(self):
    return {
      "MODEL_NAME": self.config_model.get("MODEL_NAME"),
      "SCHEMA_HASH": self.config_model.get("SCHEMA_HASH"),
    }

  def filter_relevant_payloads(self, payloads):
    relevant_payloads = []
    for payload in payloads:
      # Check if payload contains generic payload keys that are not relevant for structured serving
      if '_P_DEBUG_SAVE_PAYLOAD' not in payload:
        relevant_payloads.append(payload)
      else:
        self.P(f"[DEBUG] Skipping irrelevant payload: {json.dumps(payload, indent=2)}")
      # endif STRUCT_DATA in payload
    # endfor payload in payloads
    return relevant_payloads

  def pre_process(self, inputs):
    payloads = self.filter_relevant_payloads(payloads=inputs["DATA"])
    if not payloads:
      return None
    self.P(f"[DEBUG] Received payloads: {json.dumps(payloads, indent=2)}")
    batch_inputs = self.codec.prepare_batch(payloads)
    batch_inputs = [tensor.to(self.device) for tensor in batch_inputs]
    return batch_inputs

  def predict(self, prep_inputs):
    if prep_inputs is None:
      return []
    with self.th.no_grad():
      predictions = self.model(*prep_inputs)
    if isinstance(predictions, tuple):
      predictions = list(predictions)
    elif not isinstance(predictions, list):
      predictions = [predictions]
    return predictions

  def post_process(self, preds):
    if not preds:
      return []
    return self.codec.decode_batch(preds)

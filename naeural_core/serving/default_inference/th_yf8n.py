from naeural_core.serving.default_inference.th_yf_base import YfBase as ParentServingProcess

_CONFIG = {
  **ParentServingProcess.CONFIG,

  "MODEL_WEIGHTS_FILENAME": "20231117_y8n_nms.ths",
  "MODEL_WEIGHTS_FILENAME_DEBUG": "20231117_y8n_nms_top6.ths",

  "URL": "minio:Y8/20231117_y8n_nms.ths",
  "URL_DEBUG": "minio:Y8/20231117_y8n_nms_top6.ths",

  "MODEL_ONNX_FILENAME": "20240430_y8n_448x640_nms_f32.onnx",
  "MODEL_TRT_FILENAME": "20240430_y8n_448x640_nms_f32_trt.onnx",
  "ONNX_URL": "minio:Y8/20240430_y8n_448x640_nms_f32.onnx",
  "TRT_URL": "minio:Y8/20240430_y8n_448x640_nms_f32_trt.onnx",

  'IMAGE_HW': (448, 640),

  'VALIDATION_RULES': {
    **ParentServingProcess.CONFIG['VALIDATION_RULES'],
  },
}

DEBUG_COVERED_SERVERS = False

__VER__ = '0.2.0.0'


class ThYf8n(ParentServingProcess):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(ThYf8n, self).__init__(**kwargs)
    return

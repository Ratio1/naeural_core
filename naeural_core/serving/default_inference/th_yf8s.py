from naeural_core.serving.default_inference.th_yf_base import YfBase as ParentServingProcess

_CONFIG = {
  **ParentServingProcess.CONFIG,

  # "MODEL_WEIGHTS_FILENAME": "20230723_y8s_nms.ths",
  # "MODEL_WEIGHTS_FILENAME_DEBUG": "20230723_y8s_nms_top6.ths",
  #
  # "URL": "minio:Y8/20230723_y8s_nms.ths",
  # "URL_DEBUG": "minio:Y8/20230723_y8s_nms_top6.ths",
  #
  # 'IMAGE_HW': (448, 640),
  "MODEL_WEIGHTS_FILENAME": "20240305_y8s_640x1152_nms.ths",
  "MODEL_WEIGHTS_FILENAME_DEBUG": "20240305_y8s_640x1152_nms_top6.ths",

  "URL": "minio:Y8/20240305_y8s_640x1152_nms.ths",
  "URL_DEBUG": "minio:Y8/20240305_y8s_640x1152_nms_top6.ths",

  "MODEL_ONNX_FILENAME": "20240430_y8s_640x1152_nms_f32.onnx",
  "MODEL_TRT_FILENAME": "20240430_y8s_640x1152_nms_f32_trt.onnx",
  "ONNX_URL": "minio:Y8/20240430_y8s_640x1152_nms_f32.onnx",
  "TRT_URL": "minio:Y8/20240430_y8s_640x1152_nms_f32_trt.onnx",

  'IMAGE_HW': (640, 1152),

  'VALIDATION_RULES': {
    **ParentServingProcess.CONFIG['VALIDATION_RULES'],
  },
}

DEBUG_COVERED_SERVERS = False

__VER__ = '0.2.0.0'


class ThYf8s(ParentServingProcess):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(ThYf8s, self).__init__(**kwargs)
    return


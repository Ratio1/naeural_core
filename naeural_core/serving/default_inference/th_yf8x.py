from naeural_core.serving.default_inference.th_yf_base import YfBase as ParentServingProcess

_CONFIG = {
  **ParentServingProcess.CONFIG,

  "MODEL_WEIGHTS_FILENAME": "20240409_y8x_1152x2048_nms.ths",
  "MODEL_WEIGHTS_FILENAME_DEBUG": "20240409_y8x_1152x2048_nms_top6.ths",

  "URL": "minio:Y8/20240409_y8x_1152x2048_nms.ths",
  "URL_DEBUG": "minio:Y8/20240409_y8x_1152x2048_nms_top6.ths",

  'IMAGE_HW': (1152, 2048),

  'VALIDATION_RULES': {
    **ParentServingProcess.CONFIG['VALIDATION_RULES'],
  },
}

DEBUG_COVERED_SERVERS = False

__VER__ = '0.1.0.0'


class ThYf8x(ParentServingProcess):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(ThYf8x, self).__init__(**kwargs)
    return

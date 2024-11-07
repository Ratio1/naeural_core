from naeural_core.serving.default_inference.th_yf_base import YfBase as ParentServingProcess

_CONFIG = {
  **ParentServingProcess.CONFIG,

  "MODEL_WEIGHTS_FILENAME": "20230723_y5s6_nms.ths",
  "MODEL_WEIGHTS_FILENAME_DEBUG": "20230723_y5s6_nms_top6.ths",
  "URL": "minio:Y5/20230723_y5s6_nms.ths",
  "URL_DEBUG": "minio:Y5/20230723_y5s6_nms_top6.ths",

  'IMAGE_HW': (576, 960),


  'VALIDATION_RULES': {
    **ParentServingProcess.CONFIG['VALIDATION_RULES'],
  },
}

DEBUG_COVERED_SERVERS = False

__VER__ = '0.2.0.0'


class ThYf5s(ParentServingProcess):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(ThYf5s, self).__init__(**kwargs)
    return

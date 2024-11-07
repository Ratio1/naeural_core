# local dependencies
from naeural_core.serving.default_inference.th_rface import ThRface as ParentServingProcess

_CONFIG = {
  **ParentServingProcess.CONFIG,

  'IMAGE_HW': (640, 640),  # The model is size-invariant, but is trained on this size ( 1080, 1920),#
  'MAX_BATCH_FIRST_STAGE': 5,
  "MODEL_WEIGHTS_FILENAME": "20230723_RET_FACE_MOBILE_bs1_nms.ths",
  "URL": "minio:retina_face/20230723_RET_FACE_MOBILE_bs1_nms.ths",

  'VALIDATION_RULES': {
    **ParentServingProcess.CONFIG['VALIDATION_RULES'],
  },
}

DEBUG_COVERED_SERVERS = False


class ThRfaceS(ParentServingProcess):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(ThRfaceS, self).__init__(**kwargs)
    return

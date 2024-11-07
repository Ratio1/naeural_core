from naeural_core.serving.default_inference.th_yf8s import _CONFIG as Y8S_CONFIG
from naeural_core.serving.default_inference.th_yf_mspn import ThYfMspn as BaseServingProcess

_CONFIG = {
  **BaseServingProcess.CONFIG,
  **Y8S_CONFIG,

  'MAX_BATCH_SECOND_STAGE': 10,

  'COVERED_SERVERS': ['th_yf8s'],

  'VALIDATION_RULES': {
    **BaseServingProcess.CONFIG['VALIDATION_RULES'],
  },
}

DEBUG_COVERED_SERVERS = False


class ThYfsMspn(BaseServingProcess):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(ThYfsMspn, self).__init__(**kwargs)

    return

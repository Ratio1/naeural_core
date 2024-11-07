from naeural_core.serving.default_inference.th_yf8n import _CONFIG as Y8N_CONFIG
from naeural_core.serving.default_inference.th_yf_mspn import ThYfMspn as BaseServingProcess

_CONFIG = {
  **BaseServingProcess.CONFIG,
  **Y8N_CONFIG,

  'MAX_BATCH_SECOND_STAGE': 10,

  'COVERED_SERVERS': ['th_yf8n'],

  'VALIDATION_RULES': {
    **BaseServingProcess.CONFIG['VALIDATION_RULES'],
  },
}

DEBUG_COVERED_SERVERS = False


class ThYfnMspn(BaseServingProcess):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(ThYfnMspn, self).__init__(**kwargs)

    return

"""
This serving process is an experimental one.
It is designed for testing several Yolo models with different input sizes.
"""
from naeural_core.serving.default_inference.th_yf_base import YfBase as ParentServingProcess

FILENAMES = [
  r"C:\repos\edge-node\core\xperimental\th_y8\20240304_y8l_640x1152_nms.ths",
  r"C:\repos\edge-node\core\xperimental\th_y8\20240304_y8l_768x768_nms.ths",
  r"C:\repos\edge-node\core\xperimental\th_y8\20240304_y8l_896x896_nms.ths",
  r"C:\repos\edge-node\core\xperimental\th_y8\20240304_y8s_576x1024_nms.ths",
  r"C:\repos\edge-node\core\xperimental\th_y8\20240304_y8s_640x1152_nms.ths",
  r"C:\repos\edge-node\core\xperimental\th_y8\20240304_y8s_768x768_nms.ths",
]

_CONFIG = {
  **ParentServingProcess.CONFIG,

  "MODEL_WEIGHTS_FILENAME": FILENAMES[4],
  "MODEL_WEIGHTS_FILENAME_DEBUG": "20230723_y8l_nms_top6.ths",

  "URL": "minio:Y8/20230723_y8l_nms.ths",
  "URL_DEBUG": "minio:Y8/20230723_y8l_nms_top6.ths",

  'VALIDATION_RULES': {
    **ParentServingProcess.CONFIG['VALIDATION_RULES'],
  },
}

DEBUG_COVERED_SERVERS = False

__VER__ = '0.2.0.0'


class ThYfXp(ParentServingProcess):
  @property
  def cfg_input_size(self):
    fn = self.get_model_weights_filename
    cfg = self.graph_config[fn]
    imgsz = cfg['imgsz']
    return imgsz

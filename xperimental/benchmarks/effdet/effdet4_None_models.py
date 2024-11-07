#local dependenciess
from naeural_core.local_libraries.vision.benchmark.plugins.pb_model import PbModel

_CONFIG_ED4_None_BS1 = {
    'GRAPH': 'effdet4_None/efficientdetd4_b1_None/efficientdet-d4_frozen.pb',
    'INPUT_TENSORS': ['image_arrays:0', 'min_score_thresh:0', 'iou_threshold_thresh:0'],
    'OUTPUT_TENSORS': ['detections:0'],
    'IOU_THRESHOLD': 0.3,
    'MEMORY_FRACTION': 0.3,
    'MODEL_THRESHOLD': 0.3
  }

_CONFIG_ED4_None_BS2 = {
    'GRAPH': 'effdet4_None/efficientdetd4_b2_None/efficientdet-d4_frozen.pb',
    'INPUT_TENSORS': ['image_arrays:0', 'min_score_thresh:0', 'iou_threshold_thresh:0'],
    'OUTPUT_TENSORS': ['detections:0'],
    'IOU_THRESHOLD': 0.3,
    'MEMORY_FRACTION': 0.3,
    'MODEL_THRESHOLD': 0.3
  }

_CONFIG_ED4_None_BS4 = {
    'GRAPH': 'effdet4_None/efficientdetd4_b4_None/efficientdet-d4_frozen.pb',
    'INPUT_TENSORS': ['image_arrays:0', 'min_score_thresh:0', 'iou_threshold_thresh:0'],
    'OUTPUT_TENSORS': ['detections:0'],
    'IOU_THRESHOLD': 0.3,
    'MEMORY_FRACTION': 0.3,
    'MODEL_THRESHOLD': 0.3
  }

_CONFIG_ED4_None_BS8 = {
    'GRAPH': 'effdet4_None/efficientdetd4_b8_None/efficientdet-d4_frozen.pb',
    'INPUT_TENSORS': ['image_arrays:0', 'min_score_thresh:0', 'iou_threshold_thresh:0'],
    'OUTPUT_TENSORS': ['detections:0'],
    'IOU_THRESHOLD': 0.3,
    'MEMORY_FRACTION': 0.3,
    'MODEL_THRESHOLD': 0.3
  }

class ED4_None_BS1(PbModel):
  def __init__(self, **kwargs):
    config_model = kwargs.pop('config_model', _CONFIG_ED4_None_BS1)
    super().__init__(config_model=config_model, **kwargs)
    self.model_threshold = self.config_model['MODEL_THRESHOLD']
    self.iou_threshold = self.config_model['IOU_THRESHOLD']
    return
  
  def _process_input(self, inputs, **kwargs):    
    dct_inputs = {
      self.input_tensor_names[0] : inputs,
      self.input_tensor_names[1] : self.model_threshold,
      self.input_tensor_names[2] : self.iou_threshold
    }
    return dct_inputs


class ED4_None_BS2(ED4_None_BS1):
  def __init__(self, **kwargs):
    super().__init__(config_model=_CONFIG_ED4_None_BS2, **kwargs)
    return
  

class ED4_None_BS4(ED4_None_BS1):
  def __init__(self, **kwargs):
    super().__init__(config_model=_CONFIG_ED4_None_BS4, **kwargs)
    return
  
  
class ED4_None_BS8(ED4_None_BS1):
  def __init__(self, **kwargs):
    super().__init__(config_model=_CONFIG_ED4_None_BS8, **kwargs)
    return


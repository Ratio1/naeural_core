import tensorflow as tf
import tensorflow_hub as hub
import os
import numpy as np
import traceback

from naeural_core import Logger

POSE_POINTS = {
  'nose': 0,
  'left_eye': 1,
  'right_eye': 2,
  'left_ear': 3,
  'right_ear': 4,
  'left_shoulder': 5,
  'right_shoulder': 6,
  'left_elbow': 7,
  'right_elbow': 8,
  'left_wrist': 9,
  'right_wrist': 10,
  'left_hip': 11,
  'right_hip': 12,
  'left_knee': 13,
  'right_knee': 14,
  'left_ankle': 15,
  'right_ankle': 16
  }

class ModelWrapper(tf.Module):
  def __init__(self, model):
    self.model = model
    
  @tf.function(input_signature=(tf.TensorSpec(shape=(1,256, 256, 3), dtype=tf.int32),))
  def predict(self, inputs):
    return model(inputs)

if __name__ == '__main__':
  l = Logger("MVT", base_folder='.', app_folder='_cache')
  img = np.random.randint(0, 255, (1, 256, 256, 3), dtype='uint8')
  tf_img = tf.constant(img, dtype='int32')
  LOAD = False
  USE_SIGN = True
  fn = os.path.join(l.get_models_folder(), "movenet")
  if LOAD:
    l.P("Loading module", color='g')
    module = hub.load("https://tfhub.dev/google/movenet/singlepose/thunder/4")
    model = module.signatures['serving_default']
    pred = model(tf_img)
    try:
      l.P("Saving module to {}".format(fn))
      tf.saved_model.save(module, fn)
    except:
      l.P("Exception: {}".format(traceback.format_exc()))
  else:
    try:
      l.P("loading module to {}".format(fn))
      module = tf.saved_model.load(fn)
      model = module.inference_fn
      pred = model(tf_img).numpy()
      l.P("Result: {}".format(pred.shape), color='g')
    except:
      l.P("Exception: {}".format(traceback.format_exc()))
      

  
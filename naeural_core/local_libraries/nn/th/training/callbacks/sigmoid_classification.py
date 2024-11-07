import numpy as np
import abc
from naeural_core.local_libraries.nn.th.training.callbacks.classification import ClassificationTrainingCallbacks

class SigmoidClassificationTrainingCallbacks(ClassificationTrainingCallbacks, abc.ABC):
  def __init__(self, pos_thr=0.5, **kwargs):
    self.pos_thr = pos_thr
    super(SigmoidClassificationTrainingCallbacks, self).__init__(**kwargs)
    return

  def _get_y(self, lst_y):
    y = np.vstack(lst_y).reshape(-1)
    return y

  def _get_y_hat(self, lst_y_hat):
    y_hat = np.vstack(lst_y_hat)
    y_hat = y_hat >= self.pos_thr
    y_hat = y_hat.reshape(-1)
    return y_hat

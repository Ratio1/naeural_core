import numpy as np
import abc
from naeural_core.local_libraries.nn.th.training.callbacks.classification import ClassificationTrainingCallbacks

class SoftmaxClassificationTrainingCallbacks(ClassificationTrainingCallbacks, abc.ABC):
  def __init__(self, **kwargs):
    super(SoftmaxClassificationTrainingCallbacks, self).__init__(**kwargs)
    return

  def _get_y(self, lst_y):
    y = np.hstack(lst_y)
    return y

  def _get_y_hat(self, lst_y_hat):
    y_hat = np.vstack(lst_y_hat)
    y_hat = y_hat.argmax(axis=1)
    y_hat = y_hat.reshape(-1)
    return y_hat

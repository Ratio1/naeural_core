# TODO Bleo: WIP
from typing import List, Tuple, Any
import torch as th

import numpy as np
from naeural_core.local_libraries.nn.th.training.callbacks.base import TrainingCallbacks


class AddWhiteNoise:
  def __init__(self, prc_noise):
    self.prc_noise = prc_noise
    return

  def __call__(self, img : th.tensor):
    b,c,h,w = img.shape
    l = b*h*w
    mask = th.tensor([False for _ in range(l)])
    np_indexes = np.random.choice(np.arange(l), size=int(self.prc_noise*l))
    mask[np_indexes] = True
    mask = mask.reshape((b,h,w)).to(img.device)
    w = th.where(mask)
    img = img.transpose(1,2).transpose(2,3)
    img[w] = 255
    img = img.transpose(2,3).transpose(1,2)
    return img


class AutoencoderTrainingCallbacks(TrainingCallbacks):

  def __init__(self, prc_noise=0.3, **kwargs):
    self._prc_noise = prc_noise
    super(AutoencoderTrainingCallbacks, self).__init__(**kwargs)
    return

  def _lst_augmentations(self, **kwargs) -> List[Tuple[Any, dict]]:
    lst_transformations = []
    if self._prc_noise != 0:
      noise_transformation = (AddWhiteNoise, dict(prc_noise=self._prc_noise))
      lst_transformations.append(noise_transformation)
    #endif
    return lst_transformations

  def _get_y(self, lst_y):
    y = np.vstack(lst_y)
    return y

  def _get_y_hat(self, lst_y_hat):
    y_hat = np.vstack(lst_y_hat)
    return y_hat

  def _evaluate_callback(self, epoch: int, dataset_info: dict, y: np.ndarray, y_hat: np.ndarray, idx: np.ndarray, key: str = 'dev') -> dict:
    return {'{}_loss'.format(key): self._owner.average_loss}

  def _test_callback(self, dataset_info: dict, y: np.ndarray, y_hat: np.ndarray, idx: np.ndarray) -> dict:
    return {}

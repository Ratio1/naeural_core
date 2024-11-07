from typing import List, Tuple, Union, Any

import torch as th
import numpy as np
from naeural_core.local_libraries.nn.th.training.data.base import BaseDataLoaderFactory
from naeural_core.local_libraries.nn.th.training_utils import read_image
from naeural_core.local_libraries.nn.th.image_dataset_stage_preprocesser import PreprocessResizeWithPad, PreprocessMinMaxNorm

class SimpleImageClassifierDataLoaderFactory(BaseDataLoaderFactory):

  def __init__(self, image_height, image_width, **kwargs):
    self._image_height = image_height
    self._image_width = image_width
    super(SimpleImageClassifierDataLoaderFactory, self).__init__(**kwargs)
    return

  def startup(self):
    super().startup()
    return

  def _lst_on_load_preprocess(self) -> List[Tuple[Any, dict]]:
    return [
      (PreprocessResizeWithPad, dict(h=self._image_height, w=self._image_width, normalize=False))
    ]

  def _lst_right_before_forward_preprocess(self) -> List[Tuple[Any, dict]]:
    return [
      (PreprocessMinMaxNorm, dict(min_val=0, max_val=255, target_min_val=0, target_max_val=1))
    ]

  def _get_not_loaded_observations_and_labels(self) -> Tuple[Union[List, np.ndarray], Union[List, np.ndarray]]:
    observations = self.dataset_info['paths']
    labels = self.dataset_info['path_to_idpath'][:, 0]
    return observations, labels

  def _load_x_and_y(self, observations, labels, idx) -> Tuple[Union[th.tensor, np.ndarray], Union[th.tensor, np.ndarray]]:
    path_to_img = observations[idx]
    lbl = labels[idx]
    np_img = read_image(path_to_img)
    np_lbl = np.array([lbl])
    return np_img, np_lbl

  def _preprocess(self, x, y, transform):
    x = transform(x)
    return x, y

# TODO Bleo: WIP
from typing import List, Tuple, Union, Any

import numpy as np
import torch as th
from naeural_core.local_libraries.nn.th.training.data.base import BaseDataLoaderFactory
from naeural_core.local_libraries.nn.th.training_utils import read_image
from naeural_core.local_libraries.nn.th.image_dataset_stage_preprocesser import PreprocessResizeWithPad, PreprocessMinMaxNorm


class AutoencoderDataLoaderFactory(BaseDataLoaderFactory):

  def __init__(self, image_size, **kwargs):
    self.image_height, self.image_width = image_size
    super(AutoencoderDataLoaderFactory, self).__init__(**kwargs)
    return

  def startup(self):
    super().startup()
    return

  def _lst_on_load_preprocess(self) -> List[Tuple[Any, dict]]:
    return [
      (PreprocessResizeWithPad, dict(h=self.image_height, w=self.image_width, normalize=False))
    ]

  def _lst_right_before_forward_preprocess(self) -> List[Tuple[Any, dict]]:
    return [
      (PreprocessMinMaxNorm, dict(min_val=0, max_val=255, target_min_val=0, target_max_val=1))
    ]

  def _get_not_loaded_observations_and_labels(self) -> Tuple[Union[List, np.ndarray], Union[List, np.ndarray]]:
    all_paths = self.dataset_info['paths']
    image_paths = [path for path in all_paths if path.endswith(('.png', '.jpg', '.jpeg'))]
    observations = image_paths
    labels = image_paths
    return observations, labels

  def _load_x_and_y(self, observations, labels, idx) -> Tuple[Union[th.tensor, np.ndarray], Union[th.tensor, np.ndarray]]:
    x = th.tensor(read_image(observations[idx]))
    y = th.tensor(read_image(labels[idx]))
    return x,y

  def _preprocess(self, x, y, transform):
    x = transform(x)
    y = transform(y)
    return x,y

if __name__ == '__main__':

  from naeural_core import SBLogger

  log = SBLogger()
  train_data_factory = AutoencoderDataLoaderFactory(
    log=log,
    num_workers=0,
    batch_size=32,
    path_to_dataset='./_local_cache/_data/MNIST/dev',
    data_subset_name='dev',
    image_height=None,
    image_width=None,
    device='cpu',
    files_extensions=None,
    prc_noise=0.3
  )
  train_data_factory.create()

  for step, data in enumerate(train_data_factory.data_loader):
    break





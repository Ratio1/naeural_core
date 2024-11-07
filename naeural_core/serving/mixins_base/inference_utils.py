from naeural_core.local_libraries.nn import utils as nn_utils
from naeural_core.local_libraries.nn.th import utils as th_utils
from naeural_core.local_libraries.nn.th.image_dataset_stage_preprocesser import preprocess_to_str, str_to_preprocess

from naeural_core.utils.plugins_base.plugin_base_utils import _UtilsBaseMixin

class _InferenceUtilsMixin(_UtilsBaseMixin):
  @property
  def nn_utils(self):
    """
    Proxy method for using the nn_utils module
    Returns
    -------
    nn_utils - module, module containing utility functions for neural networks
    """
    return nn_utils

  @property
  def th_utils(self):
    """
    Proxy method for using the th_utils module
    Returns
    -------
    th_utils - module, module containing utility functions for torch
    """
    return th_utils

  def str_to_preprocess(self, preprocess_str):
    return str_to_preprocess(preprocess_str)

  def preprocess_to_str(self, preprocess_obj):
    return preprocess_to_str(preprocess_obj)

  def __init__(self):
    super(_InferenceUtilsMixin, self).__init__()
    return
  
  
  def th_resize_with_pad(self,img, h, w, 
                         device=None, 
                         pad_post_normalize=False,
                         normalize=True,
                         sub_val=0, div_val=255,
                         half=False,
                         return_original=False,
                         normalize_callback=None,
                         fill_value=None,
                         return_pad=False,
                         **kwargs
                         ):
    return th_utils.th_resize_with_pad(
      img, h, w, 
      device=device, 
      pad_post_normalize=pad_post_normalize,
      normalize=normalize,
      sub_val=sub_val, div_val=div_val,
      half=half,
      return_original=return_original,
      normalize_callback=normalize_callback,
      fill_value=fill_value,
      return_pad=return_pad,
      **kwargs
    )
  

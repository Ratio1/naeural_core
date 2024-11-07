import numpy as np
import torch as th
import torchvision.transforms as T
import torchvision.transforms.functional as F

from naeural_core.local_libraries.nn.th.utils import th_resize_with_pad

class PreprocessBrightness:
  def __init__(self, max_delta):
    self.transform = T.ColorJitter(brightness=max_delta)
    return

  def __call__(self, img):
    return self.transform(img)

class PreprocessRotation:
  def __init__(self, max_degrees):
    if max_degrees > 20:
      print("WARNING! In rotation preproc, |max_degrees| is greater than 20. Please be sure that you want higher rotation")
    self.transform = T.RandomRotation(max_degrees)
    return

  def __call__(self, img):
    return self.transform(img)

class PreprocessCropBottom:
  def __init__(self, max_prc, min_prc):
    self.max_prc = max_prc
    self.min_prc = min_prc
    return

  def __call__(self, img):
    shape = img.shape
    crop_prc = th.rand([]) * (self.max_prc - self.min_prc) + self.min_prc

    cropped_img = F.crop(img, 0, 0, shape[-2] - (crop_prc*shape[-2]).type(th.int32), shape[-1])

    return th_resize_with_pad(cropped_img, shape[-2], shape[-1], normalize=False)[0]

class PreprocessMinMaxNorm:
  def __init__(self, min_val, max_val, target_min_val, target_max_val):
    self.min_val = min_val
    self.max_val = max_val
    self.target_min_val = target_min_val
    self.target_max_val = target_max_val

  def __call__(self, th_x, **kwargs):
    th_x = (th_x - self.min_val) / (self.max_val - self.min_val)
    th_x = th_x * (self.target_max_val - self.target_min_val) + self.target_min_val

    return th_x


class PreprocessResizeWithPad:
  def __init__(self, h, w, normalize, normalize_callback=None, **kwargs):
    self.h = h
    self.w = w
    self.normalize = normalize
    self.normalize_callback = normalize_callback
    self.kwargs = kwargs

  def __call__(self, img, return_original_size=False):
    img, original_size = th_resize_with_pad(img, self.h, self.w, normalize=self.normalize, normalize_callback=self.normalize_callback, **self.kwargs)
    if return_original_size:
      th_original_size = th.tensor(original_size).to(img.device)
      return img, th_original_size
    else:
      return img


class NumpyPreprocessRandomCrop:
  def __init__(self, max_prc, min_prc):
    self.max_prc = max_prc
    self.min_prc = min_prc

  def __call__(self, img):
    shape = img.shape

    crop_prc_t = np.random.uniform(self.min_prc, self.max_prc)
    crop_prc_l = np.random.uniform(self.min_prc, self.max_prc)
    crop_prc_b = np.random.uniform(self.min_prc, self.max_prc)
    crop_prc_r = np.random.uniform(self.min_prc, self.max_prc)

    cropped_img = img[
      np.int(crop_prc_t * shape[0]):np.int((1- crop_prc_b) * shape[1]),
      np.int(crop_prc_l * shape[0]):np.int((1 - crop_prc_r) * shape[1])
    ]


    return cropped_img


PREPROCESS_TO_STR = {
  PreprocessResizeWithPad: 'PreprocessResizeWithPad',
  PreprocessMinMaxNorm: 'PreprocessMinMaxNorm',
  T.Resize: 'Resize'
}
STR_TO_PREPROCESS = {v: k for k, v in PREPROCESS_TO_STR.items()}


def preprocess_to_str(preprocess):
  """
  Method used to convert a preprocess object to a string
  Parameters
  ----------
  preprocess : object, The preprocess object to be converted to a string

  Returns
  -------
  str, The string representation of the preprocess object
  """
  return PREPROCESS_TO_STR.get(preprocess, None)


def str_to_preprocess(s=''):
  """
  Method used to convert a string to a preprocess object
  Parameters
  ----------
  s : str, The string representation of the preprocess object

  Returns
  -------
  object, The preprocess object
  """
  return STR_TO_PREPROCESS.get(s, None)


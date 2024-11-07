import os
import numpy as np
import torch as th
import cv2
import functools
from naeural_core import Logger
from typing import Union, List

def read_image(path_to_img, mode='rgb', max_height=None):
  _modes = ['rgb', 'gray']
  mode = mode.lower()
  if mode == 'rgb' or mode not in _modes:
    np_img = cv2.cvtColor(cv2.imread(path_to_img), cv2.COLOR_BGR2RGB)
  elif mode == 'gray':
    np_img = np.expand_dims(cv2.cvtColor(cv2.imread(path_to_img), cv2.COLOR_BGR2GRAY), 0)

  if max_height is not None and np_img.shape[0] > max_height:
    ratio = np_img.shape[0] / max_height
    new_w = np_img.shape[1] / ratio
    np_img = cv2.resize(np_img, (int(new_w), max_height))
  # endif should resize

  return np_img

def to_device(device_key):
  def decorator(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
      owner = args[0]

      new_args = [owner]
      for inp in args[1:]:
        retransform = False
        if not isinstance(inp, (list, tuple)):
          inp = [inp]
          retransform = True

        new_inp = []
        for _x in inp:
          if isinstance(_x, np.ndarray):
            new_inp.append(th.tensor(_x, device=vars(owner)[device_key]))
          elif _x is not None:
            new_inp.append(_x.to(vars(owner)[device_key]))
          else:
            new_inp.append(_x)

        if retransform:
          new_inp = new_inp[0]

        new_args.append(new_inp)
      #endfor

      res = func(*new_args, **kwargs)
      return res

    return wrapper

  return decorator

def get_shallow_dataset(log : Logger, path_to_dataset : str, extensions : Union[List[str], str] = None):
  if isinstance(extensions, str):
    extensions = [extensions]

  def _get_dirs(path):
    _lst_dirs = os.listdir(path)
    _lst_dirs = list(filter(
      lambda x: os.path.isdir(os.path.join(path, x)),
      _lst_dirs
    ))
    return sorted(_lst_dirs)
  #enddef

  def _filter_files(lst_files):
    lst_files = sorted(list(filter(
      lambda x: not x.startswith('.'),
      lst_files
    )))

    if extensions is None:
      return lst_files

    return list(filter(
      lambda x: any([x.endswith(ext) for ext in extensions]),
      lst_files
    ))
  #enddef

  lst_classes = _get_dirs(path_to_dataset)
  if len(lst_classes) == 0:
    lst_classes = ['']

  dataset_info = {
    'class_to_id' : dict(zip(lst_classes, list(range(len(lst_classes))))),
    'categ_to_id_per_lvl' : [],
    'path_to_idpath' : [],
    'paths' : [],
  }
  dataset_info['categ_to_id_per_lvl'].append(dataset_info['class_to_id'])

  for cls, lbl in dataset_info['class_to_id'].items():
    path_to_cls = os.path.join(path_to_dataset, cls)

    for root, dirs, files in os.walk(path_to_cls):
      relative_path = root.replace(path_to_cls, '').lstrip(os.sep)
      level = 1
      if relative_path != '':
        level = len(relative_path.split(os.sep)) + 1
      good_files = _filter_files(files)
      if len(good_files) == 0:
        # distributions ... all levels handled here
        if len(dataset_info['categ_to_id_per_lvl']) < level+1:
          dataset_info['categ_to_id_per_lvl'].append({})

        crt_registered_categs = list(dataset_info['categ_to_id_per_lvl'][level].keys())
        lst_new_categs = list(set(dirs) - set(crt_registered_categs))
        dct_new_categs = {d: len(crt_registered_categs) + i for i, d in enumerate(lst_new_categs)}
        dataset_info['categ_to_id_per_lvl'][level] = {
          **dataset_info['categ_to_id_per_lvl'][level],
          **dct_new_categs
        }
      else:
        # images
        id_path = [lbl]
        if relative_path != '':
          for lidx, categ in enumerate(relative_path.split(os.sep)):
            id_categ = dataset_info['categ_to_id_per_lvl'][lidx+1][categ]
            id_path.append(id_categ)

        dataset_info['paths'] += list(map(lambda x: os.path.join(root, x), good_files))
        dataset_info['path_to_idpath'] += [id_path for _ in range(len(good_files))]
      #endif
    #endfor - os.walk
  #endfor - cls

  dataset_info['path_to_idpath'] = np.array(dataset_info['path_to_idpath'])
  return dataset_info
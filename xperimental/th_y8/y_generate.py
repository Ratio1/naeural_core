from ultralytics import YOLO
import torch as th
import json
import os
from pathlib import Path
import shutil
import gc
import torchvision

from naeural_core import Logger

def yolo_models(
    log, classnames=None, classnames_donate_from=None,
):
  model_cfgs = {
    # '8n': {
    #   'yaml': 'yolov8n.yaml',
    #   'weights': 'yolov8n.pt',
    # },
    # '8s': {
    #   'yaml': 'yolov8s.yaml',
    #   'weights': 'yolov8s.pt',
    # },
    # '8l': {
    #   'yaml': 'yolov8l.yaml',
    #   'weights': 'yolov8l.pt',
    # },
    '11s_drone_v1': {
      'yaml': 'yolo11s.yaml',
      # 'weights': r'C:\repos\HyFy_E2\xperimental\lpd\drone_v1_y11s.pt',
      'weights': r'C:\repos\HyFy_E2\xperimental\lpd\runs\detect\train42\weights\best.pt',
    }
  }

  sizes = {
    # '8n': [
    #   [448, 640],
    # ],
    # '8s': [
    #   [640, 1152],
    # ],
    # '8l': [
    #   [640, 896],
    # ],
    # '8x': [
    #   [1152, 2048]
    # ],
    '11s_drone_v1': [
      [896, 1280],
    ],
  }
  if classnames_donate_from is None:
    classnames_donate_from = os.path.join(log.get_models_folder(), '20230723_y8l_nms.ths')
  # endif classnames_donate_from is None
  if classnames is None:
    extra_files = {'config.txt': ''}
    model = th.jit.load(classnames_donate_from, map_location='cpu', _extra_files=extra_files)
    classnames = json.loads(extra_files['config.txt'])['names']
    model = None
    gc.collect()
  # endif classnames is None

  for model_name in sizes.keys():
    gc.collect()
    th.cuda.empty_cache()

    # model = YOLO(model_cfgs[model_name]['yaml']).load(model_cfgs[model_name]['weights'])
    model = YOLO(model_cfgs[model_name]['weights'])  # to avoid loading optimizer etc.
    device = 'cuda:0'
    model = model.to(device)
    format = "torchscript"

    for imgsz in sizes[model_name]:
      log.P(f'Exporting y{model_name} with imgsz={imgsz}')
      export_kwargs = {
        'format': format,
        'imgsz': imgsz,
        'names': classnames,
      }
      pt_path = f'y{model_name}_{imgsz[0]}x{imgsz[1]}.torchscript'
      setattr(model.model, 'pt_path', pt_path)
      setattr(model.model, 'names', classnames)
      if os.path.exists(pt_path):
        log.P(f"Extracting config from torchscript {pt_path}")
        extra_files = {'config.txt': ''}
        _ = th.jit.load(pt_path, map_location='cpu', _extra_files=extra_files)
        file_config = json.loads(extra_files['config.txt'])['names']
        export_kwargs = {
          **file_config,
          **export_kwargs
        }
        gc.collect()
      # endif os.path.exists(pt_path)
      yield (model, export_kwargs)

    # endfor imgsz
    # Delete the model and make sure we've freed the memory.
  # endfor model_name

if __name__ == '__main__':
  log = Logger('Y8', base_folder='.', app_folder='_local_cache')
  models_folder = log.get_models_folder()
  for model, export_kwargs in yolo_models(log=log):
    exported = model.export(**export_kwargs)
    shutil.move(model.model.pt_path, os.path.join(models_folder, model.model.pt_path))
    del model
    gc.collect()


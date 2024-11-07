from ultralytics import YOLO
import torch as th
import json
import os
from pathlib import Path
import shutil
import gc
import torchvision

from naeural_core import Logger

def yolo_models(log):
  model_cfgs = {
     'n': {
      'yaml': 'yolov8n.yaml',
      'weights': 'yolov8n.pt',
    },
    's': {
      'yaml': 'yolov8s.yaml',
      'weights': 'yolov8s.pt',
    },
    'l': {
      'yaml': 'yolov8l.yaml',
      'weights': 'yolov8l.pt',
    },
  }

  sizes = {
    'n': [
      [448, 640],
    ],
    's': [
      [640, 1152],
    ],
    'l': [
      [640, 896],
    ],
    'x': [
      [1152, 2048]
    ]
  }
  classnames_model_path = os.path.join(log.get_models_folder(), '20230723_y8l_nms.ths')
  extra_files = {'config.txt': ''}
  model = th.jit.load(classnames_model_path, map_location='cpu', _extra_files=extra_files)
  CLASSNAMES = json.loads(extra_files['config.txt'])['names']
  model = None
  gc.collect()

  for model_name in sizes.keys():
    gc.collect()
    th.cuda.empty_cache()

    model = YOLO(model_cfgs[model_name]['yaml']).load(model_cfgs[model_name]['weights'])
    device = 'cuda:0'
    model = model.to(device)
    format = "torchscript"

    for imgsz in sizes[model_name]:
      print(f'Exporting y8{model_name} with imgsz={imgsz}')
      export_kwargs = {
        'format': format,
        'imgsz': imgsz,
      }
      pt_path = f'y8{model_name}_{imgsz[0]}x{imgsz[1]}.torchscript'
      setattr(model.model, 'pt_path', pt_path)
      setattr(model.model, 'names', CLASSNAMES)
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


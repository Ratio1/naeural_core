"""
[Y8][23-03-31 20:50:43] Timing results at 2023-03-31 20:50:43:
[Y8][23-03-31 20:50:43] Section 'main' last seen 0.0s ago
[Y8][23-03-31 20:50:43]  y5l6 = 0.0429s/q:0.0429s/nz:0.0429s, max: 0.0468s, lst: 0.0429s, c: 100/L:6%
[Y8][23-03-31 20:50:43]    y5l6_nms = 0.0245s/q:0.0245s/nz:0.0245s, max: 0.0296s, lst: 0.0270s, c: 100/L:14%
[Y8][23-03-31 20:50:43]      y5l6_nms_cpu = 0.0002s/q:0.0002s/nz:-1.0000s, max: 0.0004s, lst: 0.0001s, c: 100/L:100%
[Y8][23-03-31 20:50:43]  y5l6_inclnms = 0.0433s/q:0.0433s/nz:0.0433s, max: 0.0471s, lst: 0.0426s, c: 100/L:9%
[Y8][23-03-31 20:50:43]    y5l6_inclnms_cpu = 0.0002s/q:0.0002s/nz:0.0199s, max: 0.0006s, lst: 0.0001s, c: 100/L:99%
[Y8][23-03-31 20:50:43]  y5s6 = 0.0126s/q:0.0126s/nz:0.0126s, max: 0.0307s, lst: 0.0283s, c: 100/L:0%
[Y8][23-03-31 20:50:43]    y5s6_nms = 0.0017s/q:0.0017s/nz:0.0017s, max: 0.0037s, lst: 0.0023s, c: 100/L:0%
[Y8][23-03-31 20:50:43]      y5s6_nms_cpu = 0.0002s/q:0.0002s/nz:-1.0000s, max: 0.0004s, lst: 0.0002s, c: 100/L:100%
[Y8][23-03-31 20:50:43]  y5s6_inclnms = 0.0123s/q:0.0123s/nz:0.0123s, max: 0.0271s, lst: 0.0234s, c: 100/L:0%
[Y8][23-03-31 20:50:43]    y5s6_inclnms_cpu = 0.0002s/q:0.0002s/nz:-1.0000s, max: 0.0003s, lst: 0.0002s, c: 100/L:100%
[Y8][23-03-31 20:50:43]  y8l = 0.0329s/q:0.0329s/nz:0.0329s, max: 0.0356s, lst: 0.0326s, c: 100/L:11%
[Y8][23-03-31 20:50:43]    y8l_nms = 0.0187s/q:0.0187s/nz:0.0187s, max: 0.0223s, lst: 0.0203s, c: 100/L:12%
[Y8][23-03-31 20:50:43]      y8l_nms_cpu = 0.0002s/q:0.0002s/nz:-1.0000s, max: 0.0004s, lst: 0.0002s, c: 100/L:100%
[Y8][23-03-31 20:50:43]  y8l_inclnms = 0.0329s/q:0.0329s/nz:0.0329s, max: 0.0371s, lst: 0.0329s, c: 100/L:4%
[Y8][23-03-31 20:50:43]    y8l_inclnms_cpu = 0.0002s/q:0.0002s/nz:-1.0000s, max: 0.0005s, lst: 0.0002s, c: 100/L:100%
[Y8][23-03-31 20:50:43]  y8s = 0.0110s/q:0.0110s/nz:0.0110s, max: 0.0268s, lst: 0.0094s, c: 100/L:0%
[Y8][23-03-31 20:50:43]    y8s_nms = 0.0018s/q:0.0018s/nz:0.0018s, max: 0.0035s, lst: 0.0016s, c: 100/L:0%
[Y8][23-03-31 20:50:43]      y8s_nms_cpu = 0.0002s/q:0.0002s/nz:-1.0000s, max: 0.0003s, lst: 0.0002s, c: 100/L:100%
[Y8][23-03-31 20:50:43]  y8s_inclnms = 0.0100s/q:0.0100s/nz:0.0100s, max: 0.0254s, lst: 0.0089s, c: 100/L:0%
[Y8][23-03-31 20:50:43]    y8s_inclnms_cpu = 0.0002s/q:0.0002s/nz:-1.0000s, max: 0.0003s, lst: 0.0001s, c: 100/L:100%
[Y8][23-03-31 20:50:43] Section 'LOGGER_internal' last seen 0.0s ago
[Y8][23-03-31 20:50:43]  _logger = 0.0030s/q:0.0032s/nz:0.0032s, max: 0.0047s, lst: 0.0020s, c: 122/L:17%
[Y8][23-03-31 20:50:43]    _logger_add_log = 0.0010s/q:0.0011s/nz:0.0011s, max: 0.0019s, lst: 0.0006s, c: 122/L:18%
[Y8][23-03-31 20:50:43]    _logger_save_log = 0.0019s/q:0.0021s/nz:0.0021s, max: 0.0035s, lst: 0.0014s, c: 122/L:17%
[Y8][23-03-31 20:50:43] Model y5l6 gain -0.00047s, equal: True
[Y8][23-03-31 20:50:43] Model y5s6 gain 0.00031s, equal: True
[Y8][23-03-31 20:50:43] Model y8l gain -0.00007s, equal: True
[Y8][23-03-31 20:50:43] Model y8s gain 0.00097s, equal: True


pip install ultralytics[export]


TODO:
    - finish comparision nms y5 vs y8
    - add nms in y5 DONE
    - add nms in y8 DONE
    - optimize nms5
    - optimize nms8
    - resave, load and send to testing!
    
"""
import numpy as np
import os
import sys
import torch as th
import torchvision as tv
import json
import cv2

import time
import argparse
import shutil
import gc
from pathlib import Path
import itertools as it

try:
  from ultralytics import YOLO
except:
  print("WARNING: Only TESTS and GENERATE_FULL will work !", flush=True)

from naeural_core import Logger
from decentra_vision.draw_utils import DrawUtils
from naeural_core.local_libraries.nn.th.utils import th_resize_with_pad
from plugins.serving.architectures.y5.general import scale_coords

from naeural_core.xperimental.th_y8.utils import predict
from naeural_core.xperimental.th_y8.utils import Y5, Y8, BackendType
  
from naeural_core.xperimental.onnx.utils import create_from_torch
from naeural_core.xperimental.th_y8.y_generate import yolo_models

def get_test_images():
  folder = os.path.split(__file__)[0]
  img_names = [
    'bus.jpg',
    'faces9.jpg',
    'faces21.jpg',
    'img.png',
    '688220.png',
    'bmw_man3.png',
    'faces3.jpg',
    'LP3.jpg',
    'pic1_crop.jpg'
  ]
  models_for_nms_prep = [
    {
      'pts': r'C:\repos\ultralytics\__personal\traces\y8x_1152x2048.torchscript',
      'model': 'y8x_1152x2048'
    }
  ]

  # assert img is not None
  imgs = [
    cv2.imread(os.path.join(folder, im_name))
    for im_name in img_names
  ]
  return imgs


def get_shape_for_pt_file(filename):
  sizes = filename.split('_')
  sizes = sizes[2]
  sizes = sizes.split('x')
  return (int(sizes[0]), int(sizes[1]))

def load_ths(path, log, dev, half=None, max_batch_size=None):
  extra_files = {'config.txt' : ''}
  loaded_model = th.jit.load(
    path,
    map_location=dev,
    _extra_files=extra_files
  )
  config = json.loads(extra_files['config.txt' ].decode('utf-8'))
  if half is not None and half:
    loaded_model = loaded_model.half()
  return loaded_model, config

def load_trt(path, log, dev, half=None, max_batch_size=None):
  from naeural_core.serving.base.backends.trt import TensorRTModel
  loaded_model = TensorRTModel(log=log)
  if half is None and max_batch_size is None:
    engine_p, config_p = TensorRTModel._get_engine_and_config_path(path, half, max_batch_size)

    loaded_model.load_model(engine_p, config_p, device=dev)
    max_batch_size = loaded_model._metadata[TensorRTModel.METADATA_MAX_BATCH]
    half = (loaded_model._metadata[TensorRTModel.ONNX_METADATA_KEY]['precision'] == 'fp16')
    loaded_model.check_engine_file(
      dev,
      path,
      config_p,
      engine_p,
      half,
      max_batch_size
    )
  else:
    loaded_model.load_or_rebuild_model(
      onnx_path=path,
      fp16=half,
      max_batch_size=max_batch_size,
      device=dev
  )
  config = loaded_model._metadata[TensorRTModel.ONNX_METADATA_KEY]
  return loaded_model, config

def load_onnx(path, log, dev, half=None, max_batch_size=None):
  from naeural_core.serving.base.backends.onnx import ONNXModel
  if max_batch_size is None:
    max_batch_size = 64
  loaded_model = ONNXModel()
  loaded_model.load_model(
    model_path=path,
    max_batch_size=max_batch_size,
    device=dev
  )
  config = loaded_model.get_metadata()
  return loaded_model, config

def load_model(path, log, dev, half=None, max_batch_size=None):
  if path.endswith('.ths') or path.endswith('.torchscript'):
    return load_ths(path, log, dev, half, max_batch_size)
  elif path.endswith('_trt.onnx'):
    return load_trt(path, log, dev, half, max_batch_size)
  elif path.endswith('.onnx'):
    return load_onnx(path, log, dev, half, max_batch_size)
  raise ValueError("Unknown model extension")

def is_valid_model_input(filename):
  if ('_nms' not in file_name) and ('y' in file_name):
    return False

def get_model_config_name(file_name):
  ext_pos = file_name.rfind('.')
  if ext_pos == -1:
    # File doesn't have extension, no idea what this is so skip
    return None

  extension = file_name[ext_pos:]
  if extension not in ['.ths', '.torchscript']:
    return None

  # Get the filename without the extension.
  fn = file_name[:ext_pos]

  if ('_nms' in file_name) or ('y' not in fn):
    return None

  if 'lpd' in fn:
    fn = fn[fn.find('lpd'):]
  else:
    fn = fn[fn.find('y'):]
  return fn

def is_valid_for_export(precision, device_str, topk, backend):
  # TODO: maybe make all traces on precision 16 invalid because we can
  # always cast them to 16 at the model loading.
  if device_str == 'cpu' and precision == 16:
    # FP16 will crash and burn on cpu
    return False
  # device 'cpu' and backend 'ths' is not valid because the
  # 'ths' trace on gpu should work on cpu too
  if device_str == 'cpu' and backend != 'onnx':
    return False
  if device_str == 'cuda' and backend not in ['ths', 'trt']:
    return False
  if topk and backend != 'ths':
    # Only torch support topk at the moment
    return False
  if precision == 16 and backend not in ['onnx', 'trt']:
    # We only need fp16 binaries when exporting to onnx
    return False
  return True


def get_generation_configs(log):
  models_folder = log.get_models_folder()
  files = os.listdir(models_folder)
  # Maybe filter only yolo models or have only yolo models in the folder
  export_grid = {
    'topk' : [True, False],
    'device' : ['cpu', 'cuda'],
    'precision' : [16, 32]
  }

  combinations = list(it.product(*(export_grid[Param] for Param in export_grid.keys())))

  for file_name in files:
    fn = get_model_config_name(file_name)
    if fn is None:
      continue

    for topk, device, precision in combinations:
      if is_valid_for_export(precision, device, topk, 'ths'):
        continue
        conf = {
          'path' : os.path.join(models_folder, file_name),
          'model_name': fn,
          'backend' : 'ths',
          'precision' : precision,
          'device' : device,
          'topk' : topk
        }
        yield conf
    #endfor all combinations
  #endfor all models

  # For onnx/trt we need to start from pytorch model directly, so the exported
  # torchscript files won't help us (except for getting the actual configuration).
  for model, export_kwargs in yolo_models(log):
    pt_path = os.path.join(models_folder, model.model.pt_path)
    extra_files = {'config.txt' : ''}
    fn = get_model_config_name(pt_path)

    # Read the config from the already exported model.
    # Ideally YOLO would give us a way to generate this ourselves.
    ths_m = th.jit.load(pt_path, map_location='cpu', _extra_files=extra_files)
    config = json.loads(extra_files['config.txt' ].decode('utf-8'))
    shape = config['imgsz']
    for backend in ['trt', 'onnx']:
      for topk, device, precision in combinations:
        if is_valid_for_export(precision, device, topk, backend):
          real_model = model.model
          if precision == 16:
            real_model = real_model.half()
          conf = {
            'path' : os.path.join(models_folder, pt_path),
            'config' : config,
            'backend' : backend,
            'model_name' : fn,
            'th_model' : real_model,
            'precision' : precision,
            'device' : device,
            'topk' : topk
          }
          yield conf
  #endfor
  return

def get_test_configs(log):
  models_folder = log.get_models_folder()
  files = os.listdir(models_folder)

  test_grid = {
    'device' : ['cpu', 'cuda'],
    'precision' : [16, 32]
  }
  combinations = list(it.product(*(test_grid[Param] for Param in test_grid.keys())))

  for file_name in files:
    backend = None
    is_onnx = file_name.endswith('.onnx')
    is_trt = file_name.endswith('_trt.onnx')
    is_ths = file_name.endswith('.ths') or file_name.endswith('.torchscript')
    if is_onnx and not is_trt:
      backend = 'onnx'
    if is_trt:
      backend = 'trt'
    if is_ths:
      backend = 'ths'
    if backend is None:
      continue

    is_half = 'f16' in file_name
    for device, precision in combinations:
      if (is_onnx or is_trt) and is_half != (precision==16):
        continue
      if is_trt and device != 'cuda':
        continue
      if (is_onnx and not is_trt) and device != 'cpu':
        continue
      if precision == 16 and device == 'cpu':
        continue
      if is_half and precision != 16:
        continue
      yield {
        'path': os.path.join(models_folder, file_name),
        'model_name': file_name,
        'precision': precision,
        'backend': backend,
        'device': device
      }
    #endfor
  #endfor
  return

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description='Build flavours of YOLO')
  parser.add_argument(
    "--notest",
    action='store_true',
    help='Skip running validation tests'
  )
  parser.add_argument(
    "--show",
    action='store_true',
    help='Show image results'
  )
  args = parser.parse_args()

  GENERATE_FULL = True
  GENERATE_TOPK = True
  TOP_K_VALUES = [False, True] if GENERATE_TOPK else [False]
  generated_models = []
  TRANSFER_CLASSNAMES = False
  CLASSNAMES = None
  CLASSNAMES_DONATE_FROM = os.path.join('_local_cache', '_models', '20230723_y8l_nms.ths') 
  TEST = not args.notest
  SHOW = args.show
  SHOW_LABELS = True
  dev = th.device('cuda')

  top_candidates = 6

  if not th.cuda.is_available():
    log.P('CUDA is not available, exiting')
    exit(-1)
  if th.cuda.device_count() < 1:
    log.P('No available CUDA devices, exiting')
    exit(-1)

  if CLASSNAMES is None:
    extra_files = {'config.txt': ''}
    model = th.jit.load(CLASSNAMES_DONATE_FROM, map_location='cpu', _extra_files=extra_files)
    CLASSNAMES = json.loads(extra_files['config.txt'])['names']
  # end if CLASSNAMES is None

  log = Logger('Y8', base_folder='.', app_folder='_local_cache')
  models_folder = log.get_models_folder()
  model_date = time.strftime('%Y%m%d', time.localtime(time.time()))
  pyver = sys.version.split()[0].replace('.','')
  thver = th.__version__.replace('.','')

  # assert img is not None
  origs = get_test_images()
  images = origs
  
  model_ext = {
    'ths' : 'ths',
    'trt' : 'onnx',
    'onnx' : 'onnx'
  }

  if GENERATE_FULL:
    for m in get_generation_configs(log):
      export_type = m['backend']
      trt = (export_type == 'trt')
      onnx = (export_type == 'onnx')
      fn_path = m['path']
      y_model = fn_path
      topk = m['topk']
      device = m['device']
      config = m.get('config')
      precision = m['precision']
      if device.startswith('cuda'):
        dev = th.device('cuda:0')
      else:
        dev = th.device(device)

      backend_type = BackendType.TORCH
      if trt and not device.startswith('cuda'):
        # Only the cuda device is supported for trt
        continue

      if trt or onnx:
        # We can't use torchscript in the model for trt so load it as a torch model.
        y_model_arg = (m['th_model'], m['config'])
        backend_type = BackendType.TENSORRT if trt else BackendType.ONNX
      else:
        y_model_arg = fn_path

      log.P("Generating for: {} and backend {}".format(m['path'], m['backend']))
      fn_path = m['path']
      model_name = os.path.splitext(os.path.split(fn_path)[1])[0]
      is_y5 = '_y5' in model_name

      if is_y5 and (onnx or trt):
        # No support for y5 in onnx at the moment (although
        # may be trivial to add).
        continue

      if is_y5:
        model = Y5(y_model_arg, dev=dev, topk=topk)
      else:
        model = Y8(y_model_arg, dev=dev, topk=topk, backend_type=backend_type)

      model.eval()
      # fn_model_name = model_name[model_name.find('y'):]
      # # Remove model extension
      # fn_model_name = fn_model_name[:fn_model_name.rfind('.')]
      fn_model_name = model_name[model_name.find('y'):].split('.ths')[0]
      imgsz = model.config.get('imgsz')
      if imgsz is None:
        imgsz = model.config.get('shape')

      input_names = ['inputs']
      output_names = ['preds', 'num_preds']
      config = {
        **model.config,
        'input_shape': (*imgsz, 3),
        'python': sys.version.split()[0],
        'torch': th.__version__,
        'torchvision': tv.__version__,
        'device': device,
        'optimize': False,
        'n_candidates': top_candidates,
        'date': model_date,
        'model': fn_model_name,
        'names': CLASSNAMES if TRANSFER_CLASSNAMES else model.config['names'],
        'input_names' : input_names,
        'output_names' : output_names,
        'includes_nms' : True,
        'includes_topk' : topk
      }
      config['includes_nms'] = True
      config['includes_topk'] = topk
      if export_type == 'trt' or export_type == 'onnx':
        # ONNX is strongly typed so whatever datatype we have is baked
        # into it. We therefore need to record the precision since we
        # can't cast the model on the target. This is not related to TRT.
        config['precision'] = 'fp' + str(precision)

      log.P("  Model {}: {},{}".format(model_name, fn_path, list(model.config.keys())), color='m')

      h, w = imgsz[-2:]
      log.P("  Resizing from {} to {}".format([x.shape for x in images],(h,w)))
      results = th_resize_with_pad(
        img=images,
        h=h,
        w=w,
        device=dev,
        normalize=True,
        half=(precision==16),
        return_original=False
      )
      if len(results) < 3:
        prep_inputs, lst_original_shapes = results
      else:
        prep_inputs, lst_original_shapes, lst_original_images = results

      model.to(dev)

      model_name_suffix = f'nms_top{top_candidates}' if topk else 'nms'
      dtype_suffix = '' if not (trt or onnx) else ('_f' + str(precision))
      # Add a backend info descriminator in the name to separate pure ONNX models
      # from tensorrt ones.
      backend_info = ''
      if trt:
        backend_info = '_trt'
      extension = model_ext[export_type]
      fn = os.path.join(
        models_folder,
        f'{model_date}_{fn_model_name}_{model_name_suffix}{dtype_suffix}{backend_info}.{extension}'
      )

      if export_type == 'ths':
        log.P("  Scripting...")
        traced_model = th.jit.trace(model, prep_inputs, strict=False)
        log.P("  Forwarding using traced...")
        output = traced_model(prep_inputs)
        extra_files = {'config.txt' : json.dumps(config)}
        log.P(f"  Saving '{fn}'...")
        traced_model.save(fn, _extra_files=extra_files)
      elif export_type == 'trt' or export_type == 'onnx':
        # This should be irrelevant. Left in case of error at testing
        # from naeural_core.serving.base.backends.trt import TensorRTModel
        if export_type == 'trt':
          log.P("  Exporting to ONNX for TensorRT...")
          log.P("  Exporting with config {}".format(config))
          log.P("  Saving '{}'...".format(fn))
        elif export_type == 'onnx':
          log.P("  Exporting to ONNX for ONNX/OpenVINO...")
          log.P("  Exporting with config {}".format(config))
          log.P("  Saving '{}'...".format(fn))
        create_from_torch(
          model,
          dev,
          fn,
          precision==16,
          config['input_names'],
          config['output_names'],
          args=prep_inputs,
          batch_axes=None,
          metadata=config
        )
        log.P("  Done")
      else:
        raise ValueError("Unknown backend {}".format())

      del model
      gc.collect()
      th.cuda.empty_cache()

      loaded_model, config = load_model(fn, log, dev, precision==16, 2*len(images))
      log.P("  Loaded config with {}".format(list(config.keys())))
      prep_inputs_test = th.cat([prep_inputs,prep_inputs])
      log.P("  Running forward...")
      res, n_det = loaded_model(prep_inputs_test)
      log.P("  Done running forward. Ouput:/n{}".format(res.shape))

      del loaded_model
      gc.collect()
      th.cuda.empty_cache()
    # endfor m in models
  # endif GENERATE_FULL

  dev = th.device('cuda')

  if TEST:
    painter = DrawUtils(log=log)
    dct_results = {}
    for m in get_test_configs(log):
      print("Running for {}".format(m))
      dev = th.device(m['device'])
      fn_path = m['path']
      use_fp16 = m['precision'] == 16
      model, config = load_model(
        fn_path,
        log,
        dev,
        half=use_fp16,
        max_batch_size=2*len(images)
      )
      if use_fp16 and m['backend'] == 'ths':
        model = model.half()
      imgsz = config.get('imgsz', config.get('shape'))
      includes_nms = config.get('includes_nms')
      includes_topk = config.get('includes_topk')
      model_name = m['model_name']
      model_result_name = model_name
      if m['backend'] == 'ths':
        model_result_name = (model_name + '_fp16') if use_fp16 else model_name
      if model_result_name not in dct_results:
        dct_results[model_result_name] = {}
      is_y5 = 'y5' in model_name
      if includes_topk:
        model_name = model_result_name + '_topk'
      else:
        model_name = model_result_name + ('_inclnms' if includes_nms else '')
      log.P("Model {}: {}, {}:".format(model_name, imgsz, fn_path,), color='m')
      maxl = max([len(k) for k in config])
      for k,v in config.items():
        if not (isinstance(v, dict) and len(v) > 5):
          log.P("  {}{}".format(k + ':' + " " * (maxl - len(k) + 1), v), color='m')

      h, w = imgsz[-2:]
      log.P("  Resizing from {} to {}".format([x.shape for x in images],(h,w)))
      class_names = config['names']
      results = th_resize_with_pad(
        img=images + images,
        h=h,
        w=w,
        device=dev,
        normalize=True,
        return_original=False,
        half=use_fp16
      )
      if len(results) < 3:
        prep_inputs, lst_original_shapes = results
      else:
        prep_inputs, lst_original_shapes, lst_original_images = results

      # warmup
      log.P("  Warming up...")
      for _ in range(20):
        print('.', flush=True, end='')
        pred_nms_cpu = predict(model, prep_inputs, model_name, config, log=log, timing=False)
      print('')

      N_TESTS = 100 if 'cuda' in m['device'].lower() else 10
      # timing
      log.P("  Predicting...")
      for _ in range(N_TESTS):
        print('.', flush=True, end='')
        pred_nms_cpu = predict(model, prep_inputs, model_name, config, log=log, timing=True)
      print('')

      log.P("  Last preds:\n{}".format(pred_nms_cpu))

      mkey = 'includes_topk' if includes_topk else 'includes_nms' if includes_nms else 'normal'

      dct_results[model_result_name][mkey] = {
          'res'  : pred_nms_cpu,
          'time' : log.get_timer_mean(model_name),
          'name' : model_name
      }

      if SHOW:
        log.P("  Showing...")
        for i in range(len(images)):
          # now we have each individual image and we generate all objects
          # what we need to do is to match `second_preds` to image id & then
          # match second clf with each box
          img_bgr = origs[i].copy()
          np_pred_nms_cpu = pred_nms_cpu[i]
          original_shape = lst_original_shapes[i]
          np_pred_nms_cpu[:, :4] = scale_coords(
            img1_shape=(h, w),
            coords=np_pred_nms_cpu[:, :4],
            img0_shape=original_shape,
          ).round()
          lst_inf = []
          for det in np_pred_nms_cpu:
            det = [float(x) for x in det]
            # order is [left, top, right, bottom, proba, class] => [L, T, R, B, P, C, RP1, RC1, RP2, RC2, RP3, RC3]
            L, T, R, B, P, C = det[:6]  # order is [left, top, right, bottom, proba, class]
            label = class_names[str(int(C))] if SHOW_LABELS else ''
            img_bgr = painter.draw_detection_box(image=img_bgr, top=int(T), left=int(L), bottom=int(B), right=int(R), label=label, prc=P)
          painter.show(fn_path, img_bgr, orig=(0, 0))
        #endfor plot images
      #endif show

      del model
      gc.collect()
      th.cuda.empty_cache()
    # endfor each model
  # endif test models
  log.show_timers()
  for mn in dct_results:
    if 'normal' not in dct_results[mn] or 'includes_nms' not in dct_results[mn]:
      continue
    a1 = dct_results[mn]['normal']['res']
    t1 = dct_results[mn]['normal']['time']
    a2 = dct_results[mn]['includes_nms']['res']
    t2 = dct_results[mn]['includes_nms']['time']
    # a3 = dct_results[mn]['includes_topk']['res']
    # t3 = dct_results[mn]['includes_topk']['time']
    # a3 = [x[:, :6] for x in a3]
    
    ok1 = all([np.allclose(a1[i], a2[i]) for i in range(len(a1))])
    # ok2 = all([np.allclose(a1[i], a3[i]) for i in range(len(a1))])

    gain1 = t1-t2
    rel_gain1 = gain1 / t1
    # gain2 = t1-t3
    log.P(f'Model with NMS {mn} {t1} => {t2}[gain {gain1:.5f}s({rel_gain1:.2f}%)], equal: {ok1}', color='r' if not ok1 else 'g')
    # log.P("Model with NMS {} gain {:.5f}s, equal: {}".format(mn, gain1, ok1), color='r' if not ok1 else 'g')
    # log.P("Model with TopK {} gain {:.5f}s, equal: {}".format(mn, gain2, ok2), color='r' if not ok2 else 'g')

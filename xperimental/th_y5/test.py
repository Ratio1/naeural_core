"""
[Y5F][2021-10-08 08:26:09] Full process GPU memory amprent: 2.62 GB
[Y5F][2021-10-08 08:26:09] Timing results GPU_SYNC=True, TO_CPU=True:
[Y5F][2021-10-08 08:26:09]  load all = 1.5653s, max: 1.5911s, curr: 1.5820s, itr(B14): 0.1118s
[Y5F][2021-10-08 08:26:09]    imread = 0.0987s, max: 0.1247s, curr: 0.0940s, itr(B14): 0.0070s
[Y5F][2021-10-08 08:26:09]    resize = 0.0132s, max: 0.0362s, curr: 0.0161s, itr(B14): 0.0009s
[Y5F][2021-10-08 08:26:09]  np_batch = 0.0141s, max: 0.0170s, curr: 0.0130s, itr(B14): 0.0010s
[Y5F][2021-10-08 08:26:09]  predict_batch = 0.4262s, max: 0.4544s, curr: 0.4198s, itr(B14): 0.0304s
[Y5F][2021-10-08 08:26:09]    load_gpu = 0.0089s, max: 0.0120s, curr: 0.0061s, itr(B14): 0.0006s
[Y5F][2021-10-08 08:26:09]    normalize = 0.0000s, max: 0.0010s, curr: 0.0000s, itr(B14): 0.0000s
[Y5F][2021-10-08 08:26:09]    b14 = 0.4006s, max: 0.4324s, curr: 0.4014s, itr(B14): 0.0286s
[Y5F][2021-10-08 08:26:09]    nms = 0.0166s, max: 0.0329s, curr: 0.0123s, itr(B14): 0.0012s
[Y5F][2021-10-08 08:26:09]      to_cpu = 0.0007s, max: 0.0040s, curr: 0.0000s, itr(B14): 0.0000s


  
[Y5F][2021-10-08 08:28:29] Full process GPU memory amprent: 2.65 GB
[Y5F][2021-10-08 08:28:29] Timing results GPU_SYNC=False, TO_CPU=True:
[Y5F][2021-10-08 08:28:29]  predict_batch = 0.4269s, max: 0.4592s, curr: 0.4229s, itr(B14): 0.0305s
[Y5F][2021-10-08 08:28:29]    load_gpu = 0.0092s, max: 0.0121s, curr: 0.0100s, itr(B14): 0.0007s
[Y5F][2021-10-08 08:28:29]    normalize = 0.0000s, max: 0.0030s, curr: 0.0000s, itr(B14): 0.0000s
[Y5F][2021-10-08 08:28:29]    b14 = 0.0167s, max: 0.0423s, curr: 0.0289s, itr(B14): 0.0012s
[Y5F][2021-10-08 08:28:29]    nms = 0.4010s, max: 0.4239s, curr: 0.3840s, itr(B14): 0.0286s
[Y5F][2021-10-08 08:28:29]      to_cpu = 0.0008s, max: 0.0040s, curr: 0.0010s, itr(B14): 0.0001s



[Y5F][2021-10-08 08:31:39] Full process GPU memory amprent: 2.64 GB
[Y5F][2021-10-08 08:31:39] Timing results GPU_SYNC=False, TO_CPU=False:
[Y5F][2021-10-08 08:31:39]  predict_batch = 0.4240s, max: 0.4518s, curr: 0.4219s, itr(B14): 0.0303s
[Y5F][2021-10-08 08:31:39]    load_gpu = 0.0089s, max: 0.0144s, curr: 0.0080s, itr(B14): 0.0006s
[Y5F][2021-10-08 08:31:39]    normalize = 0.0000s, max: 0.0010s, curr: 0.0000s, itr(B14): 0.0000s
[Y5F][2021-10-08 08:31:39]    b14 = 0.0164s, max: 0.0439s, curr: 0.0290s, itr(B14): 0.0012s
[Y5F][2021-10-08 08:31:39]    nms = 0.3986s, max: 0.4184s, curr: 0.3849s, itr(B14): 0.0285s


[Y5F][2021-10-08 10:38:03] Timing results GPU_SYNC=True, TO_CPU=True, AMP=True:
[Y5F][2021-10-08 10:38:03]  predict_batch = 0.2400s, max: 0.2703s, curr: 0.2364s, itr(B14): 0.0171s
[Y5F][2021-10-08 10:38:03]    load_gpu = 0.0085s, max: 0.0123s, curr: 0.0081s, itr(B14): 0.0006s
[Y5F][2021-10-08 10:38:03]    normalize = 0.0001s, max: 0.0040s, curr: 0.0000s, itr(B14): 0.0000s
[Y5F][2021-10-08 10:38:03]    b14 = 0.2183s, max: 0.2463s, curr: 0.2163s, itr(B14): 0.0156s
[Y5F][2021-10-08 10:38:03]    nms = 0.0131s, max: 0.0354s, curr: 0.0121s, itr(B14): 0.0009s
[Y5F][2021-10-08 10:38:03]      to_cpu = 0.0008s, max: 0.0041s, curr: 0.0000s, itr(B14): 0.0001s

[Y5F][2021-10-08 13:03:52] Timing results GPU_SYNC=True, AMP=True @(14, 3, 896, 1280):
[Y5F][2021-10-08 13:03:52]  predict_batch = 0.3139s, max: 0.3326s, curr: 0.3256s, itr(B14): 0.0224s
[Y5F][2021-10-08 13:03:52]    load_gpu = 0.0115s, max: 0.0161s, curr: 0.0081s, itr(B14): 0.0008s
[Y5F][2021-10-08 13:03:52]    normalize = 0.0074s, max: 0.0142s, curr: 0.0079s, itr(B14): 0.0005s
[Y5F][2021-10-08 13:03:52]    b14 = 0.2785s, max: 0.2916s, curr: 0.2816s, itr(B14): 0.0199s
[Y5F][2021-10-08 13:03:52]    nms = 0.0166s, max: 0.0345s, curr: 0.0280s, itr(B14): 0.0012s
[Y5F][2021-10-08 13:03:52]      to_cpu = 0.0010s, max: 0.0040s, curr: 0.0000s, itr(B14): 0.0001s

[Y5F][2021-10-08 19:24:22] Full process GPU memory amprent: 3.84 GB
[Y5F][2021-10-08 19:24:22] Timing results GPU_SYNC=True, AMP=False @(14, 3, 896, 1280):
[Y5F][2021-10-08 19:24:22]  predict_batch = 0.4822s, max: 0.5201s, curr: 0.5201s, itr(B14): 0.0344s
[Y5F][2021-10-08 19:24:22]    load_gpu = 0.0112s, max: 0.0180s, curr: 0.0110s, itr(B14): 0.0008s
[Y5F][2021-10-08 19:24:22]    normalize = 0.0073s, max: 0.0143s, curr: 0.0060s, itr(B14): 0.0005s
[Y5F][2021-10-08 19:24:22]    b14 = 0.4473s, max: 0.4718s, curr: 0.4718s, itr(B14): 0.0319s
[Y5F][2021-10-08 19:24:22]    nms = 0.0165s, max: 0.0337s, curr: 0.0313s, itr(B14): 0.0012s
[Y5F][2021-10-08 19:24:22]      to_cpu = 0.0009s, max: 0.0041s, curr: 0.0031s, itr(B14): 0.0001s

[Y5F][2021-10-12 15:56:01] Full process GPU memory amprent: 3.33 GB
[Y5F][2021-10-12 15:56:01] Timing results GPU_SYNC:True, AMP:True @(14, 3, 768, 1280):
[Y5F][2021-10-12 15:56:01]  load all = 0.4279s, max: 0.4558s, curr: 0.4558s, itr(B14): 0.0306s
[Y5F][2021-10-12 15:56:01]    imread = 0.0216s, max: 0.0389s, curr: 0.0199s, itr(B14): 0.0015s
[Y5F][2021-10-12 15:56:01]    resize = 0.0080s, max: 0.0130s, curr: 0.0080s, itr(B14): 0.0006s
[Y5F][2021-10-12 15:56:01]  np_batch = 0.0150s, max: 0.0170s, curr: 0.0130s, itr(B14): 0.0011s
[Y5F][2021-10-12 15:56:01]  predict_batch = 0.2738s, max: 0.3029s, curr: 0.2665s, itr(B14): 0.0196s
[Y5F][2021-10-12 15:56:01]    load_gpu = 0.0099s, max: 0.0209s, curr: 0.0100s, itr(B14): 0.0007s
[Y5F][2021-10-12 15:56:01]    normalize = 0.0061s, max: 0.0150s, curr: 0.0065s, itr(B14): 0.0004s
[Y5F][2021-10-12 15:56:01]    b14 = 0.2418s, max: 0.2541s, curr: 0.2371s, itr(B14): 0.0173s
[Y5F][2021-10-12 15:56:01]    nms = 0.0159s, max: 0.0329s, curr: 0.0130s, itr(B14): 0.0011s
[Y5F][2021-10-12 15:56:01]      to_cpu = 0.0007s, max: 0.0011s, curr: 0.0010s, itr(B14): 0.0000s


[Y5F][2021-10-18 19:19:53] Full process GPU memory amprent: 3.15 GB
[Y5F][2021-10-18 19:19:53] Timing results 'yolov5l6' FP16 GPU_SYNC:True, AMP:True @(14, 3, 896, 1280):
[Y5F][2021-10-18 19:19:53]  load all = 0.4264s, max: 0.4279s, curr: 0.4279s, itr(B14): 0.0305s
[Y5F][2021-10-18 19:19:53]    imread = 0.0226s, max: 0.0409s, curr: 0.0199s, itr(B14): 0.0016s
[Y5F][2021-10-18 19:19:53]    resize = 0.0082s, max: 0.0110s, curr: 0.0080s, itr(B14): 0.0006s
[Y5F][2021-10-18 19:19:53]  np_batch = 0.0150s, max: 0.0150s, curr: 0.0150s, itr(B14): 0.0011s
[Y5F][2021-10-18 19:19:53]  predict_batch = 0.3080s, max: 0.3291s, curr: 0.3043s, itr(B14): 0.0220s
[Y5F][2021-10-18 19:19:53]    load_gpu = 0.0218s, max: 0.0309s, curr: 0.0203s, itr(B14): 0.0016s
[Y5F][2021-10-18 19:19:53]    normalize = 0.0045s, max: 0.0053s, curr: 0.0047s, itr(B14): 0.0003s
[Y5F][2021-10-18 19:19:53]    b14 = 0.2675s, max: 0.2832s, curr: 0.2662s, itr(B14): 0.0191s
[Y5F][2021-10-18 19:19:53]    nms = 0.0143s, max: 0.0319s, curr: 0.0130s, itr(B14): 0.0010s
[Y5F][2021-10-18 19:19:53]      to_cpu = 0.0006s, max: 0.0010s, curr: 0.0000s, itr(B14): 0.0000s


[Y5F][2021-10-18 19:23:10] Timing results 'yolov5l6' FP16 GPU_SYNC:True, AMP:False @(14, 3, 896, 1280):
[Y5F][2021-10-18 19:23:10]  load all = 0.4264s, max: 0.4279s, curr: 0.4279s, itr(B14): 0.0305s
[Y5F][2021-10-18 19:23:10]    imread = 0.0222s, max: 0.0339s, curr: 0.0199s, itr(B14): 0.0016s
[Y5F][2021-10-18 19:23:10]    resize = 0.0082s, max: 0.0090s, curr: 0.0090s, itr(B14): 0.0006s
[Y5F][2021-10-18 19:23:10]  np_batch = 0.0185s, max: 0.0219s, curr: 0.0150s, itr(B14): 0.0013s
[Y5F][2021-10-18 19:23:10]  predict_batch = 0.3081s, max: 0.3428s, curr: 0.3143s, itr(B14): 0.0220s
[Y5F][2021-10-18 19:23:10]    load_gpu = 0.0217s, max: 0.0263s, curr: 0.0258s, itr(B14): 0.0015s
[Y5F][2021-10-18 19:23:10]    normalize = 0.0045s, max: 0.0070s, curr: 0.0046s, itr(B14): 0.0003s
[Y5F][2021-10-18 19:23:10]    b14 = 0.2677s, max: 0.2870s, curr: 0.2699s, itr(B14): 0.0191s
[Y5F][2021-10-18 19:23:10]    nms = 0.0143s, max: 0.0259s, curr: 0.0140s, itr(B14): 0.0010s
[Y5F][2021-10-18 19:23:10]      to_cpu = 0.0006s, max: 0.0011s, curr: 0.0000s, itr(B14): 0.0000s



[Y5F][2021-10-18 19:25:11] Full process GPU memory amprent: 3.49 GB
[Y5F][2021-10-18 19:25:11] Timing results 'yolov5l6' FP32 GPU_SYNC:True, AMP:True @(14, 3, 896, 1280):
[Y5F][2021-10-18 19:25:11]  load all = 0.4269s, max: 0.4289s, curr: 0.4289s, itr(B14): 0.0305s
[Y5F][2021-10-18 19:25:11]    imread = 0.0235s, max: 0.0389s, curr: 0.0209s, itr(B14): 0.0017s
[Y5F][2021-10-18 19:25:11]    resize = 0.0092s, max: 0.0279s, curr: 0.0080s, itr(B14): 0.0007s
[Y5F][2021-10-18 19:25:11]  np_batch = 0.0214s, max: 0.0279s, curr: 0.0150s, itr(B14): 0.0015s
[Y5F][2021-10-18 19:25:11]  predict_batch = 0.3368s, max: 0.3680s, curr: 0.3312s, itr(B14): 0.0241s
[Y5F][2021-10-18 19:25:11]    load_gpu = 0.0418s, max: 0.0539s, curr: 0.0439s, itr(B14): 0.0030s
[Y5F][2021-10-18 19:25:11]    normalize = 0.0073s, max: 0.0083s, curr: 0.0056s, itr(B14): 0.0005s
[Y5F][2021-10-18 19:25:11]    b14 = 0.2737s, max: 0.2925s, curr: 0.2687s, itr(B14): 0.0195s
[Y5F][2021-10-18 19:25:11]    nms = 0.0140s, max: 0.0259s, curr: 0.0130s, itr(B14): 0.0010s
[Y5F][2021-10-18 19:25:11]      to_cpu = 0.0006s, max: 0.0011s, curr: 0.0010s, itr(B14): 0.0000s

[Y5F][2021-10-18 19:27:56] Full process GPU memory amprent: 3.49 GB
[Y5F][2021-10-18 19:27:56] Timing results 'yolov5l6' FP32 GPU_SYNC:True, AMP:True @(14, 3, 896, 1280):
[Y5F][2021-10-18 19:27:56]  load all = 0.4264s, max: 0.4289s, curr: 0.4289s, itr(B14): 0.0305s
[Y5F][2021-10-18 19:27:56]    imread = 0.0223s, max: 0.0279s, curr: 0.0199s, itr(B14): 0.0016s
[Y5F][2021-10-18 19:27:56]    resize = 0.0088s, max: 0.0279s, curr: 0.0090s, itr(B14): 0.0006s
[Y5F][2021-10-18 19:27:56]  np_batch = 0.0155s, max: 0.0160s, curr: 0.0150s, itr(B14): 0.0011s
[Y5F][2021-10-18 19:27:56]  predict_batch = 0.3363s, max: 0.3551s, curr: 0.3311s, itr(B14): 0.0240s
[Y5F][2021-10-18 19:27:56]    load_gpu = 0.0413s, max: 0.0519s, curr: 0.0394s, itr(B14): 0.0029s
[Y5F][2021-10-18 19:27:56]    normalize = 0.0073s, max: 0.0121s, curr: 0.0080s, itr(B14): 0.0005s
[Y5F][2021-10-18 19:27:56]    b14 = 0.2735s, max: 0.2862s, curr: 0.2708s, itr(B14): 0.0195s
[Y5F][2021-10-18 19:27:56]    nms = 0.0142s, max: 0.0309s, curr: 0.0130s, itr(B14): 0.0010s
[Y5F][2021-10-18 19:27:56]      to_cpu = 0.0007s, max: 0.0020s, curr: 0.0000s, itr(B14): 0.0001s

"""
from decentra_vision.draw_utils import DrawUtils
from naeural_core import Logger
import numpy as np
import torch as th
import cv2
import os

import sys
from pathlib import Path

LOAD_HUB = False


if LOAD_HUB:
  FILE = Path('utils/y5/models/yolo.py').resolve()
  ROOT = FILE.parents[1]  # YOLOv5 root directory
  if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))  # add ROOT to PATH
  # ROOT = ROOT.relative_to(Path.cwd())  # relative
  from models.experimental import attempt_load
  from y5utils.augmentations import letterbox
  from y5utils.general import non_max_suppression, scale_coords, set_logging, check_yaml
  from models.yolo import Model
else:
  from plugins.serving.architectures.y5.yolo import Model
  from plugins.serving.architectures.y5.augmentations import letterbox
  from plugins.serving.architectures.y5.general import non_max_suppression, scale_coords, set_logging, check_yaml
  YAML_PATH = 'inference/architectures/y5/configs'


_D1 = [
  'xperimental/_images/FPs/686097.png',
  'xperimental/_images/FPs/686102.png',
  'xperimental/_images/FPs/700006.png',
  'xperimental/_images/FPs/699720.png',
  'xperimental/_images/FPs/688220.png',
]

_D2 = [
  ]

_MODELS = {
    'yolov5l6': 'https://www.dropbox.com/s/1o4ad4y93w9a93j/yolov5l6.pt?dl=1',
    'yolov5x6': 'https://www.dropbox.com/s/sc2i1qpuj5te0lg/yolov5x6.pt?dl=1',
    'yolov5s6': 'https://www.dropbox.com/s/yj4gnqugsp8tpzv/yolov5s6.pt?dl=1',
}


_WEIGHTS = {
    'yolov5l6': 'https://www.dropbox.com/s/kciushsvber05y5/yolov5l6_weights.pt?dl=1',
    'yolov5x6': 'https://www.dropbox.com/s/n1wr3sysk3zaglu/yolov5x6_weights.pt?dl=1',
    'yolov5s6': 'https://www.dropbox.com/s/98ct9x0ckaprzsu/yolov5s6_weights.pt?dl=1',
}

_COCO = 'https://www.dropbox.com/s/krzygbdl6qkzf8e/coco.txt?dl=1'


def load_yolo(log, model_name, dev, fused=True):
  fn_weights_base = model_name + '_weights.pt'
  url = _WEIGHTS[model_name]
  l.maybe_download_model(url, fn_weights_base)
  l.maybe_download_model(_COCO, 'coco_classes.txt')
  fn_weights = l.get_models_file(fn_weights_base)
  fn_def = os.path.join(YAML_PATH, model_name + '.yaml')
  if fn_weights is None:
    log.P("Cant find weights file '{}'. Attempting download".format(fn))
  if fn_def is None:
    raise ValueError("Cant find definition file '{}'".format(fn))

  cfg = check_yaml(fn_def)  # check YAML
  log.P("Creating model from '{}'".format(cfg))
  model = Model(cfg)
  model.float()
  model.eval()
  if fused:
    model.fuse()
  log.P("Loading weights from '{}'".format(fn_weights))
  model.load_state_dict(th.load(fn_weights))
  model.to(dev)
  return model


def th_predict(log, model, np_inputs, gpu_sync, use_amp, fp16, dev):
  th_dtype = th.float16 if fp16 else th.float32
  np_dtype = 'float16' if fp16 else 'float32'
  np_inputs = np_inputs.astype(np_dtype)
  with th.cuda.amp.autocast(enabled=use_amp):
    with th.no_grad():
      log.start_timer('predict_batch')

      log.start_timer('load_gpu')
      th_x = th.tensor(np_inputs, device=dev, dtype=th_dtype)
      if gpu_sync:
        th.cuda.synchronize()
      log.stop_timer('load_gpu')

      log.start_timer('normalize')
      th_x = th_x / 255.
      log.stop_timer('normalize')

      log.start_timer('b14')
      res = model(th_x)
      preds = res[0]
      if gpu_sync:
        th.cuda.synchronize()
      log.stop_timer('b14')

      log.start_timer('nms')
      pred_nms = non_max_suppression(
        prediction=preds,
        conf_thres=0.25,
        iou_thres=0.45,
        classes=None,
        agnostic=False,
        max_det=300
      )

      if gpu_sync:
        th.cuda.synchronize()

      log.start_timer('to_cpu')
      cpu_pred_nms = [x.cpu().numpy() for x in pred_nms]
      log.stop_timer('to_cpu')

      log.stop_timer('nms')

      log.stop_timer('predict_batch')

      return cpu_pred_nms


if __name__ == '__main__':
  l = Logger('Y5F', base_folder='.', app_folder='_cache', TF_KERAS=False)
  l.gpu_info()

  painter = DrawUtils(log=l)

  RUNS_IMG = 3  # runs to determine resize speed
  RUNS_INF = 10  # runs to determine inference speed
  BATCH_SIZE = 14
  GPU_SYNC = True
  AMP = False
  _BASE = _D1  # images to use
  SHOW = False
  MODEL = 'yolov5l6'
  FP16 = True
  # AMP = AMP if not FP16 else False

  NR_SHOW = len(_BASE)
  fns = (_BASE * 20)[:BATCH_SIZE]
  url = _MODELS[MODEL]
  fn_base = MODEL + '.pt'
  INPUT_SIZE = (896, 1280)
  AUTO = len(INPUT_SIZE) == 1

  g1 = l.gpu_info()[0]
  l.P("Pre-load model GPU free: {:.1f} GB".format(g1['FREE_MEM']))
  selected_dev = th.device('cuda')

  if LOAD_HUB:
    l.P("Preparing model '{}' using TorchHub approach".format(
      MODEL), color='green')
    model_type = MODEL
    l.maybe_download_model(url, fn_base)
    fn_model = l.get_models_file(fn_base)
    set_logging()
    model = attempt_load(fn_model, map_location=selected_dev)
    fn_weights = os.path.join(l.get_models_folder(), MODEL + '_weights.pt')
    if l.get_models_file(fn_weights) is None:
      th.save(model.state_dict(), fn_weights)
    class_names = model.module.names if hasattr(model, 'module') else model.names
    l.save_pickle_to_models(class_names, 'coco_class_names.pkl')
  else:
    model_type = MODEL + '_W'
    l.P("Preparing model '{}' using simplified approach".format(
      MODEL), color='green')
    model = load_yolo(
      log=l,
      model_name=MODEL,
      dev=selected_dev
    )
    class_names = l.load_models_json('coco_classes.txt')
    if class_names is None:
      raise ValueError('Could not find class names')

  g2 = l.gpu_info()[0]
  l.P("Post-load model GPU free: {:.1f} GB => initial amprent {:.1f} GB".format(
    g2['FREE_MEM'], g1['FREE_MEM'] - g2['FREE_MEM']))
  stride = model.stride.max().cpu().item()
  imgs_bgr = []
  l.P("Loading images...")
  for _ in range(RUNS_IMG):
    l.start_timer('load all')
    imgs = []
    for fn in fns:
      l.start_timer('imread')
      img = cv2.imread(fn)
      if img is None:
        raise ValueError('Error reading {}'.format(fn))
      imgs_bgr.append(img)
      l.stop_timer('imread')
      l.start_timer('resize')
      img_resized = letterbox(
        img,
        new_shape=INPUT_SIZE,
        stride=stride,
        auto=AUTO,
      )[0]
      img_rgb = img_resized[:, :, ::-1]
      img_th = np.transpose(img_rgb, [2, 0, 1])
      np_img = np.ascontiguousarray(img_th)
      l.stop_timer('resize')
      imgs.append(np_img)
    l.stop_timer('load all')

  l.P("To batch...")
  t_np = []
  for _ in range(RUNS_IMG):
    l.start_timer("np_batch")
    np_imgs = np.array(imgs)
    t_np.append(l.stop_timer("np_batch"))
  input_size = np_imgs.shape[-2:]
  if len(np_imgs.shape) == 3:
    np_imgs = np.expand_dims(np_imgs, 0)

  model.eval()
  if FP16:
    model.half()

  model_dtype = next(model.parameters()).dtype
  l.P("Model is using '{}'".format(model_dtype))
  t_upl = []
  t_inf = []
  t_nms = []
  t_pre = []

  l.P("Warm-up section...")
  for _ in range(2):
    th_predict(
      log=l,
      model=model,
      np_inputs=np_imgs,
      gpu_sync=GPU_SYNC,
      use_amp=AMP,
      fp16=FP16,
      dev=selected_dev,
    )

  g3 = l.gpu_info()[0]
  l.P("Post-warming model GPU free: {:.1f} GB => amprent {:.2f} GB".format(
    g3['FREE_MEM'], g2['FREE_MEM'] - g3['FREE_MEM']))

  title = "'{}' {} GPU_SYNC:{}, AMP:{} @{}".format(
    MODEL, "FP16" if FP16 else "FP32",
    GPU_SYNC, AMP, np_imgs.shape)
  l.P("Starting loop inference with {}...".format(title))
  for itr in range(RUNS_INF):
    print("\r {:.1f}%".format((itr + 1) / RUNS_INF * 100), end='', flush=True)
    np_imgs2 = np_imgs.copy()
    np.random.shuffle(np_imgs2)
    th.cuda.empty_cache()

    th_predict(
      log=l,
      model=model,
      np_inputs=np_imgs2,
      gpu_sync=GPU_SYNC,
      use_amp=AMP,
      fp16=FP16,
      dev=selected_dev,
    )

  g4 = l.gpu_info()[0]
  l.P("Post-loop GPU free: {:.1f} GB => amprent {:.2f} GB".format(
    g4['FREE_MEM'], g3['FREE_MEM'] - g4['FREE_MEM']))

  l.P("Full process GPU memory amprent: {:.2f} GB".format(g1['FREE_MEM'] - g4['FREE_MEM']), color='y')

  l.show_timers(
    title=title,
    div=np_imgs.shape[0])

  l.P("Last inference...")
  cpu_preds_nms = th_predict(
    log=l,
    model=model,
    np_inputs=np_imgs,
    gpu_sync=GPU_SYNC,
    use_amp=AMP,
    dev=selected_dev,
    fp16=FP16,
  )
  for i in range(NR_SHOW):
    np_pred = cpu_preds_nms[i]
    im0 = imgs_bgr[i]
    np_pred[:, :4] = scale_coords(input_size, np_pred[:, :4], im0.shape).round()
    lst_inf = []
    for det in np_pred:
      L, T, R, B, P, C = det  # order is [left, top, right, proba, class]
      dct_obj = {
        'TLBR_POS': [T, L, B, R],
        'PROB_PRC': P,
        'TYPE': class_names[int(C)],
      }
      lst_inf.append(dct_obj)
    img_bgr = painter.draw_inference_boxes(
      image=im0,
      lst_inf=lst_inf,
    )
    img_bgr = painter.resize(img_bgr, 1000, int(900 / img_bgr.shape[0] * img_bgr.shape[1]))
    if SHOW:
      painter.show(fns[i], img_bgr)
    fn = os.path.split(fns[i])[1][:-4]
    fn = '{}_{}_{}_{}_A{}_{}.png'.format(
      fn, model_type, *input_size, int(AMP),
      'FP16' if FP16 else 'FP32'
    )
    l.P("  Saving '{}'".format(fn))
    painter.save(img_bgr, fn=fn)

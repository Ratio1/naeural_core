# -*- coding: utf-8 -*-
import numpy as np
import os
import sys
import torch as th
import torchvision as tv
import json
import cv2

from naeural_core import Logger
from decentra_vision.draw_utils import DrawUtils
from naeural_core.local_libraries.nn.th.utils import th_resize_with_pad
from plugins.serving.architectures.y5.general import scale_coords

from naeural_core.xperimental.th_y8.utils import predict

USE_LOCAL_MODELS = False
MODELS_TORCHSCRIPT = [
  {
    'pts': "C:/repos/edge-node/_local_cache/_models/20230723_y8l_nms.ths",
    'model': 'y8l'
  },
]

if __name__ == "__main__":
  TEST = True
  TEST_FP16 = True
  TEST_ONLY_FP16 = True
  FP_VALUES = [True] if TEST_ONLY_FP16 else [False, True] if TEST_FP16 else [False]
  SHOW = True
  N_TESTS = 1
  USE_IMAGES_FROM_FOLDER = True
  DEVICE = 'cuda'
  dev = th.device(DEVICE)

  log = Logger('Y8', base_folder='.', app_folder='_local_cache')
  log.P("Using {}".format(dev))
  models_folder = log.get_models_folder()
  folder = os.path.split(__file__)[0]
  img_names = [
    # 'bus.jpg',
    # 'faces9.jpg',
    # 'faces21.jpg',
    # 'img.png',
    # '688220.png',
    # 'bmw_man3.png',
    # 'faces3.jpg',
    # 'LP3.jpg',
    # 'pic1_crop.jpg'
    "C:/resources/test_lpr/25mai/6cf0d612-617a-4411-97ae-40910733f5ff_O_0.jpg",
    "C:/resources/test_lpr/25mai/dbd5e154-47fd-404b-8b21-871937d5c7db_O_0.jpg",
    "C:/resources/test_lpr/25mai/c3fc83c9-005f-45c3-bfb5-837637ec47e4_O_0.jpg",
    "C:/resources/test_lpr/25mai/bd225a82-54e7-4bfa-8776-636a519cf907_O_0.jpg",

    # "C:/Users/bleot/Dropbox/DATA/_vapor_data/__tests/LPLR/test_lpr/25mai/6cf0d612-617a-4411-97ae-40910733f5ff_O_0.jpg",
    # "C:/Users/bleot/Dropbox/DATA/_vapor_data/__tests/LPLR/test_lpr/25mai/dbd5e154-47fd-404b-8b21-871937d5c7db_O_0.jpg",
    # "C:/Users/bleot/Dropbox/DATA/_vapor_data/__tests/LPLR/test_lpr/25mai/c3fc83c9-005f-45c3-bfb5-837637ec47e4_O_0.jpg",
    # "C:/Users/bleot/Dropbox/DATA/_vapor_data/__tests/LPLR/test_lpr/25mai/bd225a82-54e7-4bfa-8776-636a519cf907_O_0.jpg",
    "_local_cache/_data/Dataset_LPLR_v5.2.2_for_yolo/images/2.Dev/Cars_with_LP/Complexity_2/4.GTS/dataset_builder_1__DATASET_BUILDER_01__DATASET_BUILDER_603.jpg"
  ]

  # assert img is not None
  if USE_IMAGES_FROM_FOLDER:
    # img_dir = r'C:\repos\edge-node\_local_cache\_output\no_human_detected_PER CV 2-2'
    img_dir = r'C:\repos\edge-node\_local_cache\_output\no_human_detected_PER CV 2-1'

    origs = [
      cv2.imread(os.path.join(img_dir, x))
      for x in os.listdir(img_dir)
      if x.endswith('.jpg') or x.endswith('.png')
    ]
  else:
    origs = [
      # cv2.imread(os.path.join(folder, im_name))
      cv2.imread(im_name)
      for im_name in img_names
    ]
  images = origs
  # images = [
  #   np.ascontiguousarray(img[:,:,::-1])
  #   for img in origs
  # ]

  # models_folder = "C:/repos/edge-node/yn_lpd"
  models_folder = "C:/repos/edge-node/core/xperimental/lpd/traces"
  if USE_LOCAL_MODELS:
    models_for_test = [
      {
        'pts': os.path.join(models_folder, x),
        'model': (
          x[x.find('lpd'):] if 'lpd' in x else x[x.find('y'):]
        ).split('.')[0]
      } for x in os.listdir(models_folder) if ('.ths' in x or '.torchscript' in x) and ('_nms' not in x) and ('y' in x)
    ]
  else:
    models_for_test = MODELS_TORCHSCRIPT

  dev = th.device('cuda')
  if TEST:
    painter = DrawUtils(log=log)

    log.P(
      f"Testing {len(models_for_test)} models{' with FP16' if TEST_FP16 else ''} with {N_TESTS} runs (+{20} for warmup) each on {DEVICE}")
    log.P("Testing on :\n   {}".format("\n   ".join([m['pts'] for m in models_for_test])))
    dct_results = {}
    for m in models_for_test:
      for use_fp16 in FP_VALUES:
        fn_path = m['pts']
        extra_files = {'config.txt': ''}
        model = th.jit.load(
          f=fn_path,
          map_location=dev,
          _extra_files=extra_files,
        )
        if use_fp16:
          model.half()
        # endif use_fp16
        log.P("Done loading model on device {}".format(dev))
        config = json.loads(extra_files['config.txt'].decode('utf-8'))
        imgsz = config.get('imgsz', config.get('shape'))
        includes_nms = config.get('includes_nms')
        includes_topk = config.get('includes_topk')
        model_name = m['model']
        model_result_name = (model_name + '_fp16') if use_fp16 else model_name
        if model_result_name not in dct_results:
          dct_results[model_result_name] = {}
        is_y5 = 'y5' in model_name
        if includes_topk:
          model_name = model_result_name + '_topk'
        else:
          model_name = model_result_name + ('_inclnms' if includes_nms else '')
        log.P("Model {}: {}, {}:".format(model_name, imgsz, fn_path, ), color='m')
        maxl = max([len(k) for k in config])
        for k, v in config.items():
          if not (isinstance(v, dict) and len(v) > 5):
            log.P("  {}{}".format(k + ':' + " " * (maxl - len(k) + 1), v), color='m')

        h, w = imgsz[-2:]
        log.P("  Resizing from {} to {}".format([x.shape for x in images], (h, w)))
        class_names = config['names']
        results = th_resize_with_pad(
          img=images,
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
          pred_nms_cpu = predict(model, prep_inputs[:5], model_name, config, log=log, timing=False)
        print('')

        # timing
        log.P("  Predicting...")
        for _ in range(N_TESTS):
          print('.', flush=True, end='')
          pred_nms_cpu = predict(model, prep_inputs, model_name, config, log=log, timing=True)
        print('')

        log.P("  Last preds:\n{}".format(pred_nms_cpu))

        mkey = 'includes_topk' if includes_topk else 'includes_nms' if includes_nms else 'normal'

        dct_results[model_result_name][mkey] = {
          'res': pred_nms_cpu,
          'time': log.get_timer_mean(model_name),
          'name': model_name
        }

        if SHOW:
          log.P(f"  {'[FP16]' if use_fp16 else ''}Showing...")
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
              label = class_names[str(int(C))]
              img_bgr = painter.draw_detection_box(image=img_bgr, top=int(T), left=int(L), bottom=int(B), right=int(R),
                                                   label=label, prc=P)
            cv2.imshow(fn_path, img_bgr)
            cv2.moveWindow(fn_path, 0, 0)
            pressed_key = cv2.waitKey(0)
            cv2.destroyAllWindows()
            if pressed_key == 27:
              break
          # endfor plot images
        # endif show
      # endfor fp16
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

    gain1 = t1 - t2
    rel_gain1 = gain1 / t1
    # gain2 = t1-t3
    log.P(f'Model with NMS {mn} {t1} => {t2}[gain {gain1:.5f}s({rel_gain1:.2f}%)], equal: {ok1}',
          color='r' if not ok1 else 'g')
    # log.P("Model with NMS {} gain {:.5f}s, equal: {}".format(mn, gain1, ok1), color='r' if not ok1 else 'g')
    # log.P("Model with TopK {} gain {:.5f}s, equal: {}".format(mn, gain2, ok2), color='r' if not ok2 else 'g')


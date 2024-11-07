import numpy as np
import torch as th
import cv2
from collections import OrderedDict

import matplotlib.pyplot as plt


def letterbox(im, new_shape=(640, 640), color=(114, 114, 114), auto=True, scaleFill=False, scaleup=True, stride=32):
    # Resize and pad image while meeting stride-multiple constraints
    shape = im.shape[:2]  # current shape [height, width]
    if isinstance(new_shape, int):
        new_shape = (new_shape, new_shape)

    # Scale ratio (new / old)
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    if not scaleup:  # only scale down, do not scale up (for better val mAP)
        r = min(r, 1.0)

    # Compute padding
    ratio = r, r  # width, height ratios
    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]  # wh padding
    if auto:  # minimum rectangle
        dw, dh = np.mod(dw, stride), np.mod(dh, stride)  # wh padding
    elif scaleFill:  # stretch
        dw, dh = 0.0, 0.0
        new_unpad = (new_shape[1], new_shape[0])
        ratio = new_shape[1] / shape[1], new_shape[0] / shape[0]  # width, height ratios

    dw /= 2  # divide padding into 2 sides
    dh /= 2

    if shape[::-1] != new_unpad:  # resize
        im = cv2.resize(im, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    im = cv2.copyMakeBorder(im, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)  # add border
    return im, ratio, (dw, dh)

from naeural_core import Logger

if __name__ == '__main__':
  
  l = Logger('THY5', base_folder='.', app_folder='_cache', TF_KERAS=False)
  l.set_nice_prints()
  DEV = 'cuda'

  fns = [
    'xperimental/_images/H2048_W3072/bmw_man1.png',
    'xperimental/_images/H2048_W3072/bmw_man3.png',
    'xperimental/_images/H2048_W3072/bmw_man4.png',
    'xperimental/_images/H2048_W3072/bmw_man5.png',    

    'xperimental/_images/H2048_W3072/bmw_man1.png',
    'xperimental/_images/H2048_W3072/bmw_man3.png',
    'xperimental/_images/H2048_W3072/bmw_man4.png',
    'xperimental/_images/H2048_W3072/bmw_man5.png',    

    ]

  MODEL_BATCH = 1 # len(fns)

  FP16 = True  

  MODEL_H, MODEL_W = 896, 1280
  MODEL_NAME = 'yolov5l6'
  STRIDE = 64 if MODEL_NAME[-1] == '6' else 32
  

  
  fn_model = l.get_models_file('u_y5/{}_{}_{}x{}_B{}_S{}_torchscript.pt'.format(
    MODEL_NAME, DEV, MODEL_H, MODEL_W, MODEL_BATCH, STRIDE))
    
  imgs = []
  for fn in fns:
    img = cv2.imread(fn)
    img_resized = cv2.resize(img, (MODEL_W, MODEL_H)) #letterbox(img, new_shape=(640,640))[0]
    imgs.append(img_resized)
  
  np_imgs = np.ascontiguousarray(np.array(imgs)[:,:,:,::-1])
  plt.imshow(np_imgs[0])
  plt.show()
    
  l.P("Loading '{}'...".format(fn_model), color='g')
  model = th.jit.load(fn_model)
  if FP16:
    model.half()
  _par0 = next(model.parameters())
  model_dev = _par0.device
  model_dtype = _par0.dtype
  model_dev_type = model_dev.type
  l.P("Warming-up on {}/{}...".format(model_dev_type, model_dtype))
  if model_dev_type != 'cpu':
    _par0 = next(model.parameters())
    model_dtype = _par0.dtype
    model_device = _par0.device
    th_warm = th.zeros(
      MODEL_BATCH, 3, MODEL_H, MODEL_W, 
      device=model_device,
      dtype=model_dtype
      )
    _ = model(th_warm)   

  
  np_imgs = np_imgs.transpose((0, 3, 1, 2))
  np_imgs = (np_imgs / 255.).astype('float16' if FP16 else 'float32')
  
  th_imgs = th.tensor(np_imgs, dtype=th.float16 if FP16 else th.float32)
  
  th_imgs = th_imgs.to(model_dev)
  
  th_x  = th_imgs
    
  with th.no_grad():
    res = model(th_x)
  
  l.P("Received inferences: {}".format(res[0].shape))
  
  
  
  
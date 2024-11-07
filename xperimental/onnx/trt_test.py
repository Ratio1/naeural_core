import os
import torch as th

from naeural_core.serving.base.backends.trt import TensorRTModel
from naeural_core.utils.tracing.onnx.utils import create_from_torch
from naeural_core import Logger

if __name__ == '__main__':
  from ultralytics import YOLO
  from xperimental.th_y8.generate import get_test_images
  from naeural_core.local_libraries.nn.th.utils import th_resize_with_pad

  logger = Logger('TRT', base_folder='.', app_folder='_local_cache')

  device = th.device('cuda:0')
  imgs = get_test_images()
  imgs = th_resize_with_pad(
    img=imgs,
    h=640,
    w=640,
    device=device,
    normalize=True,
    half=True,
    return_original=False
  )[0]
  imgs=imgs.half()

  model = YOLO('yolov8n.pt')
  model = model.model
  path = 'tmp/yolo.engine'
  onnx_path = 'tmp/yolo.onnx'
  try:
    os.unlink(path)
  except Exception:
    pass

  create_from_torch(
    model, device, onnx_path, half=True,
    input_names=['input0'],
    output_names=['output0', 'output1', 'output2', 'output3'],
    args=imgs,
    aggressive_shape_inference=True,
    metadata={
      'precision' : 'fp16'
    }
  )

  trt_model = TensorRTModel(logger)
  trt_model.load_or_rebuild_model(onnx_path, True, 4, device)

  o0, o1, o2, o3 = trt_model(
    imgs[:4],
  )

  trt_model = TensorRTModel(logger)
  trt_model.load_or_rebuild_model(onnx_path, True, 4, device)

  o0, o1, o2, o3 = trt_model(
    imgs[:4],
  )

  print(o0.shape)
  print(o1.shape)
  print(o2.shape)
  print(o3.shape)

  from naeural_core.xperimental.th_y8.utils import Y8, BackendType
  model = YOLO('yolov8n.pt')
  model = model.model
  config = {
    'foo' : 'bar',
    'precision' : 'fp16'
  }
  model = Y8(
    (model, config),
    device,
    topk=False,
    backend_type=BackendType.TENSORRT
  )
  path = 'tmp/yoloy.engine'
  onnx_path = 'tmp/yoloy.onnx'

  try:
    # Make sure we always rebuild this as it is part of the test.
    os.unlink(path)
  except Exception:
    pass

  create_from_torch(
    model, device, onnx_path, half=True,
    input_names=['input0'],
    output_names=['output0', 'output1'],
    args=imgs,
    aggressive_shape_inference=False,
    metadata={
      'precision' : 'fp16'
    }
  )

  # Try this with a reduced batch size
  trt_model = TensorRTModel(logger)
  trt_model.load_or_rebuild_model(onnx_path, True, 8, device)
  o0, o1 = trt_model(imgs[:4])
  for i in range(o0.shape[0]):
    print('==============================')
    for j in range(o1[i].item()):
      print(o0[i, j, :].cpu().numpy())

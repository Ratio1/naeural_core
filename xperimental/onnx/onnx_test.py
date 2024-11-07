import os

import torch as th

from naeural_core.serving.base.backends.onnx import ONNXModel
from naeural_core.utils.tracing.onnx.utils import create_from_torch


if __name__ == '__main__':
  from ultralytics import YOLO
  from xperimental.th_y8.generate import get_test_images
  from naeural_core.local_libraries.nn.th.utils import th_resize_with_pad

  device = th.device('cpu')
  imgs = get_test_images()
  imgs = th_resize_with_pad(
    img=imgs,
    h=640,
    w=640,
    device=device,
    normalize=True,
    half=False,
    return_original=False
  )[0].float()

  model = YOLO('yolov8n.pt')
  model = model.model
  path = 'tmp/yolo.onnx'
  os.makedirs('tmp', exist_ok=True)

  create_from_torch(
    model, device, path, half=False,
    input_names=['input0'],
    output_names=['output0', 'output1', 'output2', 'output3'],
    args=imgs,
    aggressive_shape_inference=True)

  model = ONNXModel()
  model.load_model(path, 4, device, False)

  o0, o1, o2, o3 = model(
    imgs[:4],
  )
  print(o0.shape)
  print(o1.shape)
  print(o2.shape)
  print(o3.shape)

  from xperimental.th_y8.utils import Y8, BackendType
  model = YOLO('yolov8n.pt').model
  config = {
    'foo' : 'bar'
  }
  model = Y8(
    (model, config),
    device,
    topk=False,
    backend_type=BackendType.ONNX
  )
  path = 'tmp/yoloy.onnx'

  try:
    # Make sure we always rebuild this as it is part of the test.
    os.unlink(path)
  except Exception:
    pass

  create_from_torch(
    model, device, path, half=False,
    input_names=['input0'],
    output_names=['output0', 'output1'],
    args=imgs,
    aggressive_shape_inference=False)

  # Try this with a reduced batch size
  onnx_model = ONNXModel()
  onnx_model.load_model(path, 8, device, True)
  imgs = imgs.half()
  o0, o1 = onnx_model(imgs[:4])
  for i in range(o0.shape[0]):
    print('==============================')
    for j in range(o1[i].item()):
      print(o0[i, j, :].numpy())

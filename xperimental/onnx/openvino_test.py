from naeural_core.serving.base.backends.ovino import OpenVINOModel
from naeural_core.utils.tracing.onnx.utils import create_from_torch
from xperimental.th_y8.generate import get_test_images
from xperimental.th_y8.utils import Y8, BackendType
from naeural_core.local_libraries.nn.th.utils import th_resize_with_pad

import torch as th
from ultralytics import YOLO

if __name__ == '__main__':

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

  # Note no half support for CPU so half is always false here.
  path = '/tmp/yolo.onnx'
  create_from_torch(
    model, device, path, half=False,
    input_names=['input0'],
    output_names=['output0', 'output1'],
    args=imgs,
    aggressive_shape_inference=False,
    metadata=config)

  vmodel = OpenVINOModel()
  vmodel.load_model(model_path=path, half=True)
  imgs = imgs.half()
  print(vmodel.get_metadata())
  print(vmodel.get_input_dtype(0))
  o0, o1 = vmodel(imgs)
  print(o1.shape)

  o0, o1 = vmodel(imgs[:4])
  for i in range(o0.shape[0]):
    print('==============================')
    for j in range(o1[i].item()):
      print(o0[i, j, :].numpy())

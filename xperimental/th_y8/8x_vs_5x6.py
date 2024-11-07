from ultralytics import YOLO
import torch as th
import json
from torch.jit._script import RecursiveScriptModule


class TsWrapper(th.nn.Module):
  def __init__(self, model, _extra_files, **kwargs):
    super(TsWrapper, self).__init__(**kwargs)
    self.model = model
    self._extra_files = _extra_files

  def fuse(self, **kwargs):
    return self.model

  def to(self, *args, **kwargs):
    return TsWrapper(
      model=self.model.to(*args, **kwargs),
      _extra_files=self._extra_files
    )

  def __getattr__(self, item):
    if item in ['model', '_extra_files', 'fuse', 'to']:
      return super(TsWrapper, self).__getattr__(item)
    if self._extra_files is not None and item in self._extra_files:
      return self._extra_files[item]
    return getattr(self.model, item)

  # def __setattr__(self, key, value):
  #   if key == 'model':
  #     self.model = value
  #   else:
  #     setattr(self.model, key, value)


if __name__ == '__main__':
  if True:
    _extra_files = {'config.txt': ''}
    ts_path = r"C:\repos\edge-node\core\xperimental\th_y8\y8s_640x1152.torchscript"

    direct_results = YOLO(ts_path).val(data='coco8.yaml', batch=8, imgsz=[640, 1152], device='cpu')


    ts = th.jit.load(ts_path, map_location='cuda:0', _extra_files=_extra_files)
    extra_files = json.loads(_extra_files['config.txt'].decode('utf-8'))
    ts = TsWrapper(ts, extra_files)
    print(f'Loaded model: {ts}')
    # exit(0)

    cfg = {
      'name': 'y8n',
      'cfg': 'yolov8n.yaml',
      'weights': 'yolov8n.pt'
    }
    model = YOLO(cfg['cfg']).load(cfg['weights'])
    results = model.val(data='coco8.yaml', batch=8, imgsz=[640, 1152], device='cpu')
    setattr(model, 'model', ts)
    ts_results = model.val(data='coco8.yaml', batch=8, imgsz=[640, 1152], device='cpu')
    print(f'Original results:\n\t{results.results_dict}\nTsWrapper results:\n\t{ts_results.results_dict}')



  model_configs = [
    {
      'name': 'y5x',
      'cfg': 'yolov5x.yaml',
      'weights': 'yolov5x.pt',
    },
    {
      'name': 'y8x',
      'cfg': 'yolov8x.yaml',
      'weights': 'yolov8x.pt',
    },
    {
      'name': 'y8n',
      'cfg': 'yolov8n.yaml',
      'weights': 'yolov8n.pt',
    },
  ]

  for model_config in model_configs:
    model = YOLO(model_config['cfg']).load(model_config['weights'])
    model.val(data='coco128.yaml', batch=8, imgsz=[640, 1152], device='cpu')
    model.export()
    model.eval()
    model = None



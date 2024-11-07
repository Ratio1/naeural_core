from typing import Tuple
from enum import Enum
import torch as th
import torchvision as tv
import json

from naeural_core.xperimental.th_y8.graph_nms import y5_nms, y8_nms, y5_nms_topk, y8_nms_topk
from naeural_core.xperimental.th_y8.graph_nms import y8_nms_trt_efficient, y8_nms_onnx

from plugins.serving.architectures.y5.general import non_max_suppression as y5_nms_orig
# try:
#   from ultralytics.yolo.utils.ops import non_max_suppression as y8_nms_orig
# except:
#   print("EXCEPTION in loading original nms functions. Defaulting to graph ones.", flush=True)
#   y8_nms_orig = y8_nms

from naeural_core.xperimental.th_y8.graph_nms import xywh2xyxy

class BackendType(Enum):
  TORCH = 1
  TENSORRT = 2
  ONNX = 3
  OpenVINO = 4

def y8_nms_orig(
        prediction,
        # conf_thres:float=0.25,
        # iou_thres:float=0.45,
        # classes=None,
        # agnostic=False,
        # multi_label:bool=False,
        # max_det:int=300,
        # max_time_img:float=0.05,
        # max_nms:int=30000,
        # max_wh:int=7680,
):
  """
  Perform non-maximum suppression (NMS) on a set of boxes, with support for masks and multiple labels per box.

  Arguments:
      prediction (th.Tensor): A tensor of shape (batch_size, num_boxes, num_classes + 4 + num_masks)
          containing the predicted boxes, classes, and masks. The tensor should be in the format
          output by a model, such as YOLO.
      conf_thres (float): The confidence threshold below which boxes will be filtered out.
          Valid values are between 0.0 and 1.0.
      iou_thres (float): The IoU threshold below which boxes will be filtered out during NMS.
          Valid values are between 0.0 and 1.0.
      classes (List[int]): A list of class indices to consider. If None, all classes will be considered.
      agnostic (bool): If True, the model is agnostic to the number of classes, and all
          classes will be considered as one.
      multi_label (bool): If True, each box may have multiple labels.
      labels (List[List[Union[int, float, th.Tensor]]]): A list of lists, where each inner
          list contains the apriori labels for a given image. The list should be in the format
          output by a dataloader, with each label being a tuple of (class_index, x1, y1, x2, y2).
      max_det (int): The maximum number of boxes to keep after NMS.
      nc (int): (optional) The number of classes output by the model. Any indices after this will be considered masks.
      max_time_img (float): The maximum time (seconds) for processing one image.
      max_nms (int): The maximum number of boxes into tv.ops.nms().
      max_wh (int): The maximum box width and height in pixels

  Returns:
      (List[th.Tensor]): A list of length batch_size, where each element is a tensor of
          shape (num_boxes, 6 + num_masks) containing the kept boxes, with columns
          (x1, y1, x2, y2, confidence, class, mask1, mask2, ...).
  """

  # # Checks
  # assert 0 <= conf_thres <= 1, f'Invalid Confidence threshold {conf_thres}, valid values are between 0.0 and 1.0'
  # assert 0 <= iou_thres <= 1, f'Invalid IoU {iou_thres}, valid values are between 0.0 and 1.0'

  # if isinstance(prediction, (list, tuple)):  # YOLOv8 model in validation model, output = (inference_out, loss_out)
  #     prediction = prediction[0]  # select only inference output

  # device = prediction.device
  # mps = 'mps' in device.type  # Apple MPS
  # if mps:  # MPS not fully supported yet, convert tensors to CPU before NMS
  #     prediction = prediction.cpu()
  bs = prediction.shape[0]  # batch size
  nc = prediction.shape[1] - 4  # number of classes
  nm = 0 # prediction.shape[1] - nc - 4
  mi = 4 + nc  # mask start index
  xc = prediction[:, 4:mi].amax(1) > 0.25  # candidates  #conf_thres

  # Settings
  # min_wh = 2  # (pixels) minimum box width and height
  # time_limit = 0.5 + max_time_img * bs  # seconds to quit after
  # redundant = True  # require redundant detections
  # multi_label = False and (nc > 1) # multi_label &= nc > 1  # multiple labels per box (adds 0.5ms/img)
  # merge = False  # use merge-NMS

  # t = time.time()
  output = [th.zeros((0, 6 + nm), device=prediction.device)] * bs
  for xi in range(prediction.shape[0]):  # image index, image inference
    # Apply constraints
    # x[((x[:, 2:4] < min_wh) | (x[:, 2:4] > max_wh)).any(1), 4] = 0  # width-height
    x = prediction[xi]
    x = x.transpose(0, -1)[xc[xi]]  # confidence

    # Cat apriori labels if autolabelling
    # if labels and len(labels[xi]):
    #     lb = labels[xi]
    #     lbsz = len(lb)
    #     v = th.zeros((lbsz, nc + nm + 5), device=x.device)
    #     v[:, :4] = lb[:, 1:5]  # box
    #     v[th.range(lbsz), lb[:, 0].long() + 4] = 1.0  # cls
    #     x = th.cat((x, v), 0)

    # If none remain process next image
    if x.shape[0] > 0:

      # Detections matrix nx6 (xyxy, conf, cls)
      box, cls, mask = x.split((4, nc, nm), 1)
      box = xywh2xyxy(box)  # center_x, center_y, width, height) to (x1, y1, x2, y2)
      # if multi_label:
      #     i, j = (cls > conf_thres).nonzero(as_tuple=False).T
      #     x = th.cat((box[i], x[i, 4 + j, None], j[:, None].float(), mask[i]), 1)
      # else:  # best class only
      conf, j = cls.max(1, keepdim=True)
      x = th.cat((box, conf, j.float(), mask), 1)[conf.view(-1) > 0.25]

      # # Filter by class
      # if classes is not None:
      #     x = x[(x[:, 5:6] == th.tensor(classes, device=x.device)).any(1)]

      # Apply finite constraint
      # if not th.isfinite(x).all():
      #     x = x[th.isfinite(x).all(1)]

      # Check shape
      n = x.shape[0]  # number of boxes
      if n > 0:  # no boxes
        x = x[x[:, 4].argsort(descending=True)[:30000]]  # sort by confidence and remove excess boxes #max_nms

        # Batched NMS
        c = x[:, 5:6] * 7680  # classes #max_wh
        boxes = x[:, :4] + c
        scores = x[:, 4]  # boxes (offset by class), scores
        i = tv.ops.nms(boxes, scores, 0.45)  # NMS #iou_thres
        i = i[:300]  # limit detections #max_det
        # if merge and (1 < n < 3E3):  # Merge NMS (boxes merged using weighted mean)
        #     # update boxes as boxes(i,4) = weights(i,n) * boxes(n,4)
        #     iou = box_iou(boxes[i], boxes) > iou_thres  # iou matrix
        #     weights = iou * scores[None]  # box weights
        #     x[i, :4] = th.mm(weights, x[:, :4]).float() / weights.sum(1, keepdim=True)  # merged boxes
        #     if redundant:
        #         i = i[iou.sum(1) > 1]  # require redundancy

        output[xi] = x[i]
        # if mps:
        #     output[xi] = output[xi].to(device)
        # if (time.time() - t) > time_limit:
        #     LOGGER.warning(f'WARNING ⚠️ NMS time limit {time_limit:.3f}s exceeded')
        #     break  # time limit exceeded

  return output



# class ModelWrapper(th.nn.Module):
#   def __init__(self, path):
#     super(ModelWrapper, self).__init__()
#     extra_files = {'config.txt' : ''}
#     self.backbone_model = th.jit.load(path, map_location=th.device('cpu'), _extra_files=extra_files)
#     self.config = json.loads(extra_files['config.txt' ].decode('utf-8'))
#     return

#   def forward(self, x):
#     return self.backbone_model(x)


class Y5(th.nn.Module):
  def __init__(self, path, dev, topk=False):
    super(Y5, self).__init__()
    # self.backbone_model = ModelWrapper(path=path)
    # self.config = self.backbone_model.config
    extra_files = {'config.txt' : ''}
    self.backbone_model = th.jit.load(path, map_location=dev, _extra_files=extra_files)
    self.config = json.loads(extra_files['config.txt' ].decode('utf-8'))
    self.y5_nms = y5_nms_topk if topk else y5_nms
    return

  def forward(self, inputs):
    th_x = self.backbone_model(inputs)
    th_x = th_x[0]
    th_x = self.y5_nms(th_x)
    return th_x

class Y8(th.nn.Module):

  def __init__(
    self,
    model,
    dev : th.device,
    topk : bool = False,
    backend_type : BackendType = BackendType.TORCH
  ):
    """
    Constructs an Y8 module.

    Parameters:
      model - either a path on disk (string) to the torchscript model or a tuple
        containing the YOLO model and config dict.
        If a torchscript path is provided this will be exportable only to Torchscript.
        Otherwise, the model will be exportable to any backend of Torchscript, ONNX, OpenVino
        and TensorRT depending on the backend_type parameter.
      dev - the torch device that should be used.
      topk - True if we should use the topk NMS (top 6)
      backend_type - The backend type this model is build for
    """
    super(Y8, self).__init__()
    extra_files = {'config.txt' : ''}
    if isinstance(model, str):
      self.backbone_model = th.jit.load(model, map_location=dev, _extra_files=extra_files)
      self.config = json.loads(extra_files['config.txt' ].decode('utf-8'))
      if backend_type != BackendType.TORCH:
        raise ValueError("Non-torch backends should get the model as tuple of th.nn.Module and config!")
    elif isinstance(model, tuple):
      self.backbone_model, self.config = model
    else:
      raise ValueError("Unexpected value for model")

    self.y8_nms = y8_nms_topk if topk else y8_nms
    if backend_type == BackendType.TENSORRT:
      self.y8_nms = y8_nms_trt_efficient
    elif backend_type in [BackendType.ONNX, BackendType.OpenVINO]:
      # OpenVINO as ONNX use the same model for distribution
      # (in onnx format).
      self.y8_nms = y8_nms_onnx
    return

  def forward(self, inputs):
    th_x = self.backbone_model(inputs)
    if isinstance(th_x, tuple):
      # The non-exported torch Y8 model will return a tuple here,
      # but we're only interested in the first value (the detections).
      th_x = th_x[0]
    th_x = self.y8_nms(th_x)
    return th_x


def predict(m, data, m_name, config, log, timing=False):
  if timing:
    log.start_timer(m_name)
  with th.no_grad():
    th_preds = m(data)
  if not config.get('includes_nms', False):
    if 'v5' in config.get('model',''):
      preds = th_preds[0]
      if timing:
        log.start_timer(m_name + '_nms')
      pred_nms = y5_nms_orig(
         prediction=preds,
       )

      if timing:
        log.start_timer(m_name + '_nms_cpu')
      pred_nms_cpu = [x.cpu().numpy() for x in pred_nms]
      if timing:
        log.stop_timer(m_name + '_nms_cpu')

      if timing:
        log.stop_timer(m_name + '_nms')
    else:
      if timing:
        log.start_timer(m_name + '_nms')
      pred_nms = y8_nms_orig(
         prediction=th_preds,
      )

      if timing:
        log.start_timer(m_name + '_nms_cpu')
      pred_nms_cpu = [x.cpu().numpy() for x in pred_nms]
      if timing:
        log.stop_timer(m_name + '_nms_cpu')

      if timing:
        log.stop_timer(m_name + '_nms')
    #endif y5 vs y8

  else:
    if timing:
      log.start_timer(m_name + '_cpu')
    th_preds, th_n_det = th_preds
    pred_nms_cpu = [x[:th_n_det[i]].cpu().numpy() for i, x in enumerate(th_preds)]
    if timing:
      log.stop_timer(m_name  + '_cpu')
  #end requires nms
  if timing:
    log.stop_timer(m_name)
  return pred_nms_cpu

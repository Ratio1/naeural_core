import numpy as np
import torch as th
import torchvision as tv

def box_iou(box1, box2):
  # https://github.com/pytorch/vision/blob/master/torchvision/ops/boxes.py
  """
  Return intersection-over-union (Jaccard index) of boxes.
  Both sets of boxes are expected to be in (x1, y1, x2, y2) format.
  Arguments:
      box1 (Tensor[N, 4])
      box2 (Tensor[M, 4])
  Returns:
      iou (Tensor[N, M]): the NxM matrix containing the pairwise
          IoU values for every element in boxes1 and boxes2
  """

  def box_area(box):
    # box = 4xn
    return (box[2] - box[0]) * (box[3] - box[1])

  area1 = box_area(box1.T)
  area2 = box_area(box2.T)

  # inter(N,M) = (rb(N,M,2) - lt(N,M,2)).clamp(0).prod(2)
  inter = (th.min(box1[:, None, 2:], box2[:, 2:]) - th.max(box1[:, None, :2], box2[:, :2])).clamp(0).prod(2)
  return inter / (area1[:, None] + area2 - inter)  # iou = inter / (area1 + area2 - inter)


def xywh2xyxy(x):
  """
  Convert bounding box coordinates from (x, y, width, height) format to (x1, y1, x2, y2) format where (x1, y1) is the
  top-left corner and (x2, y2) is the bottom-right corner.

  Args:
      x (np.ndarray) or (torch.Tensor): The input bounding box coordinates in (x, y, width, height) format.
  Returns:
      y (np.ndarray) or (torch.Tensor): The bounding box coordinates in (x1, y1, x2, y2) format.
  """
  y = x.clone()
  y[..., 0] = x[..., 0] - x[..., 2] / 2  # top left x
  y[..., 1] = x[..., 1] - x[..., 3] / 2  # top left y
  y[..., 2] = x[..., 0] + x[..., 2] / 2  # bottom right x
  y[..., 3] = x[..., 1] + x[..., 3] / 2  # bottom right y
  return y

@th.jit.script
def y5_nms_topk(prediction,
           # conf_thres:float=0.25,
           # iou_thres:float=0.45,
           # classes=None,
           # agnostic=False,
           # multi_label:bool=False,
           # max_det:int=300
           ):
  """Runs Non-Maximum Suppression (NMS) on inference results

  Returns:
        list of detections, on (n,6) tensor per image [xyxy, conf, cls]
  """

  nc = prediction.shape[2] - 5  # number of classes
  xc = prediction[..., 4] > 0.25  # candidates #conf_thres
  # Settings
  max_wh = 7680  # (pixels) minimum and maximum box width and height
  max_nms = 30000  # maximum number of boxes into tv.ops.nms()
  n_candidates = 6
  bs = prediction.shape[0]

  # output = [th.zeros((0, 6), device=prediction.device)] * 256 # max batch size 256
  th_output = th.zeros((bs, 300, 4 + 2 * n_candidates), device=prediction.device)
  th_n_det = th.zeros(bs, device=prediction.device, dtype=th.int32)
  for xi in range(bs):  # image index, image inference
    x = prediction[xi]
    x = x[xc[xi]]  # confidence

    # If none remain process next image
    if x.shape[0] > 0:
      # Compute conf
      x[:, 5:] *= x[:, 4:5]  # conf = obj_conf * cls_conf

      # Box (center x, center y, width, height) to (x1, y1, x2, y2)
      box = xywh2xyxy(x[:, :4])

      # Detections matrix nx(4+2*n_candidates) (xyxy, conf1, cls1, .., confi, clsi)
      # conf, j = x[:, 5:].max(1, keepdim=True) # confidente & class-id
      # x = th.cat((box, conf, j.float()), 1)[conf.view(-1) > 0.25] #conf_thres

      top_conf, top_idxs = x[:, 5:].sort(1, descending=True)
      mask = top_conf[:, 0] > 0.25
      runners = th.stack([top_conf[:, :n_candidates], top_idxs[:, :n_candidates]], dim=2).view(top_conf.shape[0],
                                                                                                   2 * n_candidates)
      x = th.cat((box, runners), 1)[mask]

      # Check shape
      n = x.shape[0]  # number of boxes
      if n > 0:  # no boxes
        x = x[x[:, 4].argsort(descending=True)[:max_nms]]  # sort by confidence

        # Batched NMS
        c = x[:, 5:6] * max_wh  # classes preparred as offsets
        boxes = x[:, :4] + c # add offeset to put each class in "different scene"
        scores = x[:, 4]  # boxes (offset by class), scores
        i = tv.ops.nms(boxes, scores, 0.45)  # NMS #iou_thres
        i = i[:300] #max_det

        th_n_det[xi] = i.shape[0]
        th_output[xi, :th_n_det[xi], :] = x[i]

  return th_output, th_n_det


@th.jit.script
def y5_nms(
    prediction,
   # conf_thres:float=0.25,
   # iou_thres:float=0.45,
   # classes=None,
   # agnostic=False,
   # multi_label:bool=False,
   # max_det:int=300
):
  """Runs Non-Maximum Suppression (NMS) on inference results

    Returns:
          list of detections, on (n,6) tensor per image [xyxy, conf, cls]
    """

  nc = prediction.shape[2] - 5  # number of classes
  xc = prediction[..., 4] > 0.25  # candidates #conf_thres
  # Settings
  max_wh = 7680  # (pixels) minimum and maximum box width and height
  max_nms = 30000  # maximum number of boxes into tv.ops.nms()
  bs = prediction.shape[0]

  # output = [th.zeros((0, 6), device=prediction.device)] * 256 # max batch size 256
  th_output = th.zeros((bs, 300, 6), device=prediction.device)
  th_n_det = th.zeros(bs, device=prediction.device, dtype=th.int32)
  for xi in range(bs):  # image index, image inference
    x = prediction[xi]
    x = x[xc[xi]]  # confidence

    # If none remain process next image
    if x.shape[0] > 0:
      # Compute conf
      x[:, 5:] *= x[:, 4:5]  # conf = obj_conf * cls_conf

      # Box (center x, center y, width, height) to (x1, y1, x2, y2)
      box = xywh2xyxy(x[:, :4])

      # Detections matrix nx(4+2*n_candidates) (xyxy, conf1, cls1, .., confi, clsi)
      conf, j = x[:, 5:].max(1, keepdim=True) # confidente & class-id
      x = th.cat((box, conf, j.float()), 1)[conf.view(-1) > 0.25] #conf_thres

      # Check shape
      n = x.shape[0]  # number of boxes
      if n > 0:  # no boxes
        x = x[x[:, 4].argsort(descending=True)[:max_nms]]  # sort by confidence

        # Batched NMS
        c = x[:, 5:6] * max_wh  # classes preparred as offsets
        boxes = x[:, :4] + c  # add offeset to put each class in "different scene"
        scores = x[:, 4]  # boxes (offset by class), scores
        i = tv.ops.nms(boxes, scores, 0.45)  # NMS #iou_thres
        i = i[:300]  # max_det

        th_n_det[xi] = i.shape[0]
        th_output[xi, :th_n_det[xi], :] = x[i]

  return th_output, th_n_det


@th.jit.script
def y8_nms_topk(
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

  bs = prediction.shape[0]  # batch size
  nc = prediction.shape[1] - 4  # number of classes
  nm = 0 # prediction.shape[1] - nc - 4
  mi = 4 + nc  # mask start index
  xc = prediction[:, 4:mi].amax(1) > 0.25  # candidates  #conf_thres
  n_candidates = 6

  th_output = th.zeros((bs, 300, 4 + 2 * n_candidates + nm), device=prediction.device)
  th_n_det = th.zeros(bs, device=prediction.device, dtype=th.int32)

  for xi in range(prediction.shape[0]):  # image index, image inference
    x = prediction[xi]
    x = x.transpose(0, -1)[xc[xi]]  # confidence

    # If none remain process next image
    if x.shape[0] > 0:

      # Detections matrix nx6 (xyxy, conf, cls)
      box, cls, mask = x.split((4, nc, nm), 1)
      box = xywh2xyxy(box)  # center_x, center_y, width, height) to (x1, y1, x2, y2)

      # conf, j = cls.max(1, keepdim=True)
      # x = th.cat((box, conf, j.float(), mask), 1)

      top_conf, top_idxs = x[:, 4:].sort(1, descending=True)
      runners = th.stack([top_conf[:, :n_candidates], top_idxs[:, :n_candidates]], dim=2)
      runners = runners.view(top_conf.shape[0], 2 * n_candidates)
      x = th.cat((box, runners, mask), 1)

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

        th_n_det[xi] = i.shape[0]
        th_output[xi, :th_n_det[xi], :] = x[i]

  return th_output, th_n_det


@th.jit.script
def y8_nms(
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

  bs = prediction.shape[0]  # batch size
  nc = prediction.shape[1] - 4  # number of classes
  nm = 0  # prediction.shape[1] - nc - 4
  mi = 4 + nc  # mask start index
  xc = prediction[:, 4:mi].amax(1) > 0.25  # candidates  #conf_thres

  th_output = th.zeros((bs, 300, 6 + nm), device=prediction.device)
  th_n_det = th.zeros(bs, device=prediction.device, dtype=th.int32)

  for xi in range(prediction.shape[0]):  # image index, image inference
    x = prediction[xi]
    x = x.transpose(0, -1)[xc[xi]]  # confidence

    # If none remain process next image
    if x.shape[0] > 0:

      # Detections matrix nx6 (xyxy, conf, cls)
      box, cls, mask = x.split((4, nc, nm), 1)
      box = xywh2xyxy(box)  # center_x, center_y, width, height) to (x1, y1, x2, y2)

      conf, j = cls.max(1, keepdim=True)
      x = th.cat((box, conf, j.float(), mask), 1)

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

        th_n_det[xi] = i.shape[0]
        th_output[xi, :th_n_det[xi], :] = x[i]

  return th_output, th_n_det

# See https://github.com/NVIDIA/TensorRT/tree/32c64a324e58f252eae4e5681f5c39dbe22ef2d5/plugin/efficientNMSPlugin
class TRT_EfficientNMS(th.autograd.Function):
  """
  An autograd.Function that produces an invocation of the
  TensorRT EfficientNMS plugin operation in its graph. The torch
  results are invalid and should only be used for tracing.
  """

  @staticmethod
  def forward(
    ctx,
    boxes,
    scores,
    background_class=-1,
    box_coding=0,
    iou_threshold=0.45,
    max_output_boxes=300,
    plugin_version="1",
    score_activation=0,
    score_threshold=0.25,
  ):
    # Only required to produce data of the right type to continue tracing.
    # This will not be part of the ONNX graph.
    batch_size, _, num_classes = scores.shape
    num_det = th.randint(0, max_output_boxes, (batch_size, 1), dtype=th.int32)
    det_boxes = th.randn(batch_size, max_output_boxes, 4)
    det_scores = th.randn(batch_size, max_output_boxes)
    det_classes = th.randint(0, num_classes, (batch_size, max_output_boxes), dtype=th.int32)
    return num_det, det_boxes, det_scores, det_classes

  # See https://pytorch.org/docs/stable/onnx_torchscript.html#static-symbolic-method
  @staticmethod
  def symbolic(
    g : th.Graph, # The ONNX graph
    boxes,
    scores,
    background_class=-1,
    box_coding=0,
    iou_threshold=0.45,
    max_output_boxes=300,
    plugin_version="1",
    score_activation=0,
    score_threshold=0.25
  ):
    # The args have _i, _f, _s suffixes to indicate the type of the argument.
    # boxes and scores are positional arguments so there is no need for _i, _f, _s.
    det_nums, det_boxes, det_scores, det_classes = g.op(
      "TRT::EfficientNMS_TRT",
      boxes,
      scores,
      background_class_i=background_class,
      box_coding_i=box_coding,
      iou_threshold_f=iou_threshold,
      max_output_boxes_i=max_output_boxes,
      plugin_version_s=plugin_version,
      score_activation_i=score_activation,
      score_threshold_f=score_threshold,
      class_agnostic_i=0,
      outputs=4
    )

    # This is not a node that ONNX knows about and we need to
    # manually set the output shapes.
    # bs x 1, int32
    det_nums.setType(det_nums.type().with_dtype(th.int32).with_sizes([None, 1]))
    # bs x max_output_boxes x 4
    det_boxes.setType(det_boxes.type().with_sizes([None, 300, 4]))
    # bs x max_output_boxes
    det_scores.setType(det_scores.type().with_sizes([None, 300]))
    # bs x max_output_boxes, int32
    det_classes.setType(det_classes.type().with_dtype(th.int32).with_sizes([None, 300]))

    return det_nums, det_boxes, det_scores, det_classes

def y8_nms_trt_efficient(prediction : th.Tensor):
  """
  A NMS implementation compatible with TensorRT which can be exported to an ONNX
  graph. The IOU threshold used is 0.45 and the score threshold is 0.25.
  Invoking this in torch will not produce correct results and should only
  be used for tracing purposes.

  Arguments:
    prediction (th.Tensor): A tensor of shape (batch_size, num_classes + 4, num_boxes)
      containing the predicted boxes, classes, and masks. The tensor should be in the format
      output by a model, such as YOLO.

  Returns:
    Tensor containg the detections. The tensor has shape batch_size x 300 x 6.
      Each input image will have a maximum of 300 detections, each detection
      being in the format x1, y1, x2, y2, confidence, class.

    Tensor containing the number of detections for each image in the batch. The
      tensor has a shape of batch_size x 1.
  """
  bs = prediction.shape[0]  # batch size
  nc = prediction.shape[1] - 4  # number of classes
  nb = prediction.shape[2]
  nm = 0  # prediction.shape[1] - nc - 4
  mi = 4 + nc  # mask start index

  # Shuffle input data to match the TRT NMS expected format.
  th_output = th.zeros((bs, 300, 6 + nm), device=prediction.device)
  th_n_det = th.zeros(bs, device=prediction.device, dtype=th.int32)
  prediction = prediction.transpose(1, -1)  # confidence

  box, cls, _ = prediction.split((4, nc, nm), 2)
  box = xywh2xyxy(box)  # center_x, center_y, width, height) to (x1, y1, x2, y2)

  # The original y8_nms will suppress non maximum classes so we do
  # the same here. Set confidences to zero for all classes not selected
  # by tensor.max. Note this will select only one class, even if we have
  # two classes with the same confidence.
  # Not sure I understand the point of this operation. It might be
  # perfectly sensible to have more than one class per box, so this might be
  # a limiation of the original implementation.
  # The below commented code would be faster but not the same in edge cases.
  # Code:
  #    th_max, _ = cls.max(2, keepdim=True)
  #    cls = cls * cls.eq(th_max).to(cls.dtype)
  # The fastest alternative is of course to not do this at all.
  _, indices = cls.max(2, keepdim=True)
  max_mask = th.zeros(cls.shape, dtype=cls.dtype, device=prediction.device)
  cls = cls * max_mask.scatter_(2, indices, 1.)

  # Run the TRT Efficient NMS plugin.
  num_detections, nmsed_boxes, nmsed_scores, nmsed_classes = TRT_EfficientNMS.apply(
    box, # boxes
    cls, # scores
    -1, # background_class
    0, # box_coding, boxes in xyxy format
    0.45, # iou_threshold
    300, # max_output_boxes
    '1', # plugin_version
    0, # score_activation
    0.25 # score_threshold
  )

  # Use th.view as a way to tell shape inference about the shape of the output.
  num_detections = num_detections.view(bs, 1)
  nmsed_scores = nmsed_scores.view(bs, 300, 1)
  nmsed_classes = nmsed_classes.view(bs, 300, 1)
  nmsed_boxes = nmsed_boxes.view(bs, 300, 4)

  # Reorder the TRT NMS output data to our expected NMS output format.
  th_output[:, :nmsed_boxes.shape[1], :4] = nmsed_boxes     # xyxy
  th_output[:, :nmsed_scores.shape[1], 4:5] = nmsed_scores  # confidence
  th_output[:, :nmsed_scores.shape[1], 5:6] = nmsed_classes # class

  # Once more, make sure shape inference can properly see the output shape.
  th_n_det = num_detections.view(bs)
  th_output = th_output.view(bs, 300, 6)

  return th_output, th_n_det

class ONNX_NMS(th.autograd.Function):
  @staticmethod
  def forward(
    ctx,
    boxes,
    scores,
    max_output_boxes_per_class,
    iou_threshold,
    score_threshold
  ):
    import random
    batch_size, num_classes, num_boxes = scores.shape

    all_dets = []
    for _ in range(batch_size):
      num_det = random.randint(2, 5)

      det_batch_idx = th.randint(0, batch_size, (num_det, 1), device=boxes.device)
      det_class_idx = th.randint(0, num_classes, (num_det, 1), device=boxes.device)
      det_box_idx = th.randint(0, num_boxes, (num_det, 1), device=boxes.device)

      all_dets.append(th.cat((det_batch_idx, det_class_idx, det_box_idx), 1))
    return th.cat(all_dets, 0).to(th.int64)

  @staticmethod
  def symbolic(
    g : th._C.Graph, # The ONNX graph
    boxes,
    scores,
    max_output_boxes_per_class,
    iou_threshold,
    score_threshold
  ):
    out = g.op(
      "NonMaxSuppression",
      boxes,
      scores,
      max_output_boxes_per_class,
      iou_threshold,
      score_threshold,
      center_point_box_i=0, #xyxy
      outputs=1
    )
    return out


def bin_count(th_x, batch_size):
  # An implementation of a one dimensional integer bincount.
  # This is used because th.bincount will stop at the max value
  # seen in th_x.
  i = th.arange(0, batch_size, 1, dtype=th.int32, device=th_x.device)
  th_idx = i.view(-1, 1).repeat(1, th_x.shape[0])
  return (th_idx[i, :] == th_x).sum(dim=1).view(batch_size, 1)


def y8_nms_onnx(prediction):
  device=prediction.device
  bs = prediction.shape[0]  # batch size
  nc = prediction.shape[1] - 4  # number of classes
  nb = prediction.shape[2]
  nm = 0  # prediction.shape[1] - nc - 4

  # FIXME: y8_nms would remove boxes with according to the
  # confidence threshold here. We don't really need to do this
  # as the ONNX nms does have a confidence threshold.

  prediction = prediction.transpose(1, -1)  # confidence

  th_output = th.zeros((bs, 300, 6 + nm), device=device)

  box, cls, _ = prediction.split((4, nc, nm), 2)
  box = xywh2xyxy(box)  # center_x, center_y, width, height) to (x1, y1, x2, y2)

  conf, j = cls.max(2, keepdim=True)
  x = th.cat((box, conf, j.float()), 2)

  c = x[:, :, 5:6] * 7680  # classes #max_wh
  boxes = x[:, :, :4] + c
  scores = x[:, :, 4]  # boxes (offset by class), scores
  scores = scores.view(bs, 1, nb) # bs X 1 X nb

   # We're using shared classes so the number of classes in boxes_in is 1.
  boxes = boxes.view(bs, nb, 4)

  # Note this does NOT have a constant shape.
  indices = ONNX_NMS.apply(
    boxes, # boxes,
    scores, # scores
    th.tensor([300], dtype=th.int64, device=device), # max_output_boxes_per_class
    th.tensor([0.45], dtype=th.float32, device=device), # iou_threshold
    th.tensor([0.25], dtype=th.float32, device=device) # score_threshold
  )

  # For every detection left we get the initial batch index and the box list index.
  batch_idx, box_idx = indices[:, 0], indices[:, 2]

  # Sort by batch indices. Looking at the ONNXRT code
  # this might not be required (at least not on the CPU side).
  sorted_batch_idx = th.argsort(batch_idx, dim=0)
  batch_idx = batch_idx[sorted_batch_idx]
  box_idx = box_idx[sorted_batch_idx]

  # We can use bin_count to get the number of detections per each batch.
  # th.bincount would not work here because it stops at the last value
  # for which there is a count.
  th_n_det = bin_count(batch_idx, bs)
  end_idx = th.cumsum(th_n_det, dim=0, dtype=th.int32)
  start_idx = end_idx - th_n_det

  batch_idx = batch_idx.view(indices.shape[0], 1)
  box_idx = box_idx.view(indices.shape[0], 1)
  boxes = x[batch_idx, box_idx].view(indices.shape[0], 6)

  # Construct a 1d scatter mask to get the output in the format
  # that we expect. There may be an easier way to do this but I
  # have no idea what that would be.
  # We need to use a scatter and cannot index because ONNX falls
  # over with indexing.
  batch_offsets = th.arange(0, batch_idx.shape[0], 1, device=device)
  batch_offsets = batch_offsets.view(-1, 1) - start_idx[batch_idx].view(-1, 1)
  batch_offsets = batch_offsets + batch_idx * 300

  # Change the mask so we can pick up all elements.
  batch_offsets = batch_offsets.repeat((1, 6))
  batch_offsets = (6 * batch_offsets).view(-1,6) + th.arange(0, 6, 1, dtype=th.int32, device=device).view(-1, 6)

  # Change all tensors to 1d and do the scatter.
  batch_offsets = batch_offsets.view(-1)
  th_output = th_output.view(-1)
  boxes = boxes.view(-1)
  th_output = th_output.scatter_(dim=0, index=batch_offsets, src=boxes)

  # This helps with the onnx shape inference.
  th_output = th_output.view(bs, 300, 6)

  # Sort outputs by confidence. Only needed for OpenVINO, however this triggers
  # an assert... Maybe it's time for scatter again?
  # This is not needed for now, since the sorting of the detections does not affect
  # the plugins that use this output
  # batch_idx = th.arange(bs).repeat_interleave(300).view(bs * 300)
  # conf_idx = th.argsort(th_output[:,:,4], dim=1, descending=True).view(bs * 300)
  # The following 2 lines produce the same output, but neither will work for ONNX.
  # th_output = th_output[batch_idx, conf_idx, :].view(bs, 300, 6)
  # th_output = th_output.view(bs * 300, 6)[batch_offsets].view(bs, 300, 6)

  return th_output, th_n_det.view(-1)

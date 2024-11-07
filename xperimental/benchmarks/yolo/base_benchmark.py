import os
import torch as th
import torchvision as tv
from ultralytics import YOLO


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


class BaseYoloExtended(th.nn.Module):
  def __init__(self, base_model, **kwargs):
    super().__init__()
    self.backbone = base_model
    self.y8_nms = y8_nms
    return

  def forward(self, inputs):
    th_x = self.backbone_model(inputs)
    th_x = self.y8_nms(th_x)
    return th_x


class YoloExtended(YOLO):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    return

  def convert_model(self):
    self.model = BaseYoloExtended(self.model)
    return


if __name__ == '__main__':
  pass
  # load images
  img_dir = r'C:\Users\bleot\Dropbox\DATA\_vapor_data\__tests\Y8'
  img_paths = [
    os.path.join(img_dir, img_name) for img_name in os.listdir(img_dir) if img_name.endswith(('.jpg', 'png'))
  ]

  # preprocess images

  # load model

  # convert model

  # for format in formats export in format

  # for format in formats load model

  # for format in formats warmup model

  # for format in formats benchmark model



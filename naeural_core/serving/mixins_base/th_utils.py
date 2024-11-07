class _ThUtilsMixin:
  def scale_coords(self, img1_shape, coords, img0_shape, ratio_pad=None, shrink_factor=None):
    # Rescale coords (xyxy) from img1_shape to img0_shape
    if ratio_pad is None:  # calculate from img0_shape
      gain = min(img1_shape[0] / img0_shape[0], img1_shape[1] / img0_shape[1])  # gain  = old / new
      if shrink_factor is not None:
        gain /= shrink_factor
      pad = (img1_shape[1] - img0_shape[1] * gain) / 2, (img1_shape[0] - img0_shape[0] * gain) / 2  # wh padding
    else:
      gain = ratio_pad[0][0]
      pad = ratio_pad[1]

    coords[:, [x for x in range(coords.shape[1]) if x % 2 == 0]] -= pad[0]  # x padding
    coords[:, [x for x in range(coords.shape[1]) if x % 2 == 1]] -= pad[1]  # y padding
    coords[:, :] /= gain
    self.clip_coords(coords, img0_shape)
    return coords

  def clip_coords(self, boxes, shape):
    # Clip bounding xyxy bounding boxes to image shape (height, width)
    if isinstance(boxes, self.th.Tensor):  # faster individually
      boxes[:, 0].clamp_(0, shape[1])  # x1
      boxes[:, 1].clamp_(0, shape[0])  # y1
      boxes[:, 2].clamp_(0, shape[1])  # x2
      boxes[:, 3].clamp_(0, shape[0])  # y2
    else:  # np.array (faster grouped)
      boxes[:, [x for x in range(boxes.shape[1]) if x % 2 == 0]] = boxes[:, [
          x for x in range(boxes.shape[1]) if x % 2 == 0]].clip(0, shape[1])  # x1, x2
      boxes[:, [x for x in range(boxes.shape[1]) if x % 2 == 1]] = boxes[:, [
          x for x in range(boxes.shape[1]) if x % 2 == 1]].clip(0, shape[0])  # y1, y2

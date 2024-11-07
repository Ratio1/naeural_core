import numpy as np

def _box_iou_batch(
	boxes_a: np.ndarray, boxes_b: np.ndarray
) -> np.ndarray:

  def box_area(box):
    return (box[2] - box[0]) * (box[3] - box[1])

  area_a = box_area(boxes_a.T)
  area_b = box_area(boxes_b.T)

  top_left = np.maximum(boxes_a[:, None, :2], boxes_b[:, :2])
  bottom_right = np.minimum(boxes_a[:, None, 2:], boxes_b[:, 2:])

  area_inter = np.prod(
  	np.clip(bottom_right - top_left, a_min=0, a_max=None), 2)
      
  return area_inter / (area_a[:, None] + area_b - area_inter)
  
def class_non_max_suppression(
   predictions: np.ndarray, iou_threshold: float = 0.5
) -> np.ndarray:
  rows, columns = predictions.shape

  sort_index = np.flip(predictions[:, 4].argsort())
  predictions = predictions[sort_index]

  boxes = predictions[:, :4]
  categories = predictions[:, 5]
  ious = _box_iou_batch(boxes, boxes)
  ious = ious - np.eye(rows)

  keep = np.ones(rows, dtype=bool)

  for index, (iou, category) in enumerate(zip(ious, categories)):
    if not keep[index]:
      continue

    condition = (iou > iou_threshold) & (categories == category)
    keep = keep & ~condition

  return keep[sort_index.argsort()]


def simple_nms(dets, thresh):
  """
  Non-maximum suppression. Returns indexes for kept boxes
  
  """
  y1 = dets[:, 0] # T
  x1 = dets[:, 1] # L
  y2 = dets[:, 2] # B
  x2 = dets[:, 3] # R
  scores = dets[:, 4]

  areas = (x2 - x1 + 1) * (y2 - y1 + 1)
  order = scores.argsort()[::-1]

  keep = []
  while order.size > 0:
    i = order[0]
    keep.append(i)
    xx1 = np.maximum(x1[i], x1[order[1:]])
    yy1 = np.maximum(y1[i], y1[order[1:]])
    xx2 = np.minimum(x2[i], x2[order[1:]])
    yy2 = np.minimum(y2[i], y2[order[1:]])

    w = np.maximum(0.0, xx2 - xx1 + 1)
    h = np.maximum(0.0, yy2 - yy1 + 1)
    intersection = w * h
    overlap = intersection / (areas[i] + areas[order[1:]] - intersection)

    inds = np.where(overlap <= thresh)[0]
    order = order[inds + 1]
  return keep
  

if __name__ == '__main__':
  # top1, left1, bottom1, right1 = [98, 1100, 265, 1252]
  # top2, left2, bottom2, right2 = [115, 1092, 377, 1253]
  
  top1, left1, bottom1, right1 = [116, 967, 238, 1103]
  top2, left2, bottom2, right2 = [126, 965, 383, 1104]

  
  top = max(top1, top2)
  left = max(left1, left2)
  right = min(right1, right2)
  bottom = min(bottom1, bottom2)
  
  h_overlap = bottom - top + 1
  w_overlap = right - left + 1
  
  area_overlap = h_overlap * w_overlap
  
  area_union = (right1 - left1 + 1) * (bottom1 - top1 + 1) + \
          (right2 - left2 + 1) * (bottom2 - top2 + 1) - \
          area_overlap
  iou = area_overlap / area_union
  print(area_overlap, area_union, area_overlap / area_union)
  
  data = np.array([
    [top1, left1, bottom1, right1, 69, 0],
    [top1+1, left1+2, bottom1+2, right1+2, 50, 1],
    [top2, left2, bottom2, right2, 22, 0],
  ])
  
  keep = simple_nms(data[:,:5], 0.5)
  print(keep)
  keep2 = class_non_max_suppression(data, 0.5)
  print(keep2)


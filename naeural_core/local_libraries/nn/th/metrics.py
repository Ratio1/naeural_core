import torch as th
import numpy as np
from shapely import Polygon


def intersect_over_union(th_y, th_y_hat):
  """
  :param y:
    Target tensor with size (bs, 4)
  :param y_hat:
    Predicted tensor with size (bs, 4)
  :return:
    IOU with size (bs)
  """
  dev1 = th_y.device
  dev2 = th_y_hat.device
  assert dev1 == dev2, 'Tensors should be placed on the same device!'

  th_xA = th.max(th_y[:, 0], th_y_hat[:, 0])
  th_yA = th.max(th_y[:, 1], th_y_hat[:, 1])
  th_xB = th.min(th_y[:, 2], th_y_hat[:, 2])
  th_yB = th.min(th_y[:, 3], th_y_hat[:, 3])
  th_inter_area = th.max(th.zeros(th_xA.shape).to(dev1), th_xB - th_xA + 0.01) * \
      th.max(th.zeros(th_yA.shape).to(dev1), th_yB - th_yA + 0.01)
  th_boxA_area = (th_y[:, 2] - th_y[:, 0] + 0.01) * (th_y[:, 3] - th_y[:, 1] + 0.01)
  th_boxB_area = (th_y_hat[:, 2] - th_y_hat[:, 0] + 0.01) * (th_y_hat[:, 3] - th_y_hat[:, 1] + 0.01)
  th_iou = th_inter_area / (th_boxA_area + th_boxB_area - th_inter_area)
  return th_iou


def intersect_over_union_polygons(th_y, th_y_hat):
  """
  :param y:
    Target tensor with size (bs, 4, 2)
  :param y_hat:
    Predicted tensor with size (bs, 4, 2)
  :return:
    IOU with size (bs)
  """
  dev1 = th_y.device
  dev2 = th_y_hat.device
  assert dev1 == dev2, 'Tensors should be placed on the same device!'

  ious = []
  for poly1, poly2 in zip(th_y, th_y_hat):
    # Define each polygon
    polygon1_shape = Polygon(poly1)
    polygon2_shape = Polygon(poly2)

    # Calculate intersection and union, and tne IOU
    polygon_intersection = polygon1_shape.intersection(polygon2_shape).area
    polygon_union = polygon1_shape.area + polygon2_shape.area - polygon_intersection
    ious.append(polygon_intersection / polygon_union)

  return th.tensor(ious)


def intersect_over_gtarea(y, y_hat):
  """
  :param y:
    Target tensor with size (bs, 4)
  :param y_hat:
    Predicted tensor with size (bs, 4)
  :return:
    IOU with size (bs)
  """

  xA = th.max(y[:, 0], y_hat[:, 0])
  yA = th.max(y[:, 1], y_hat[:, 1])
  xB = th.min(y[:, 2], y_hat[:, 2])
  yB = th.min(y[:, 3], y_hat[:, 3])
  interArea = th.max(th.zeros(xA.shape), xB - xA + 0.01) * th.max(th.zeros(yA.shape), yB - yA + 0.01)
  boxAArea = (y[:, 2] - y[:, 0] + 0.01) * (y[:, 3] - y[:, 1] + 0.01)
  ioa = interArea / boxAArea
  return ioa


def neighbour_accuracy(y, y_pred, neighbour_distance=1, neighbour_multiplier=1):
  assert neighbour_distance > 0, "`neighbour_distance` must be positive"
  exact_preds = np.sum(np.abs(y - y_pred) == 0)
  neighbour_preds = np.sum((0 < np.abs(y - y_pred)) & (np.abs(y - y_pred) <= neighbour_distance)) * neighbour_multiplier
  return (exact_preds + neighbour_preds) / y.shape[0]

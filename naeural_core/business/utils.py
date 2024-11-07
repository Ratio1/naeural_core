#global dependencies
import numpy as np
from itertools import combinations

from shapely.geometry import Polygon, LineString

###
###START OBJECTS INTERSECTION
###


def np_vec_iou(boxes1, boxes2):
  """
  This method calculates iou between two sets of boxes
  """
  top, left, bottom, right = np.split(boxes1, 4, axis=1)
  _top, _left, _bottom, _right = np.split(boxes2, 4, axis=1)
  xA = np.maximum(left, np.transpose(_left))
  yA = np.maximum(top, np.transpose(_top))
  xB = np.minimum(right, np.transpose(_right))
  yB = np.minimum(bottom, np.transpose(_bottom))
  inter_length = (xB - xA + 1)
  inter_height = (yB - yA + 1)
  inter_area = np.maximum(inter_length, 0) * np.maximum(inter_height, 0)
  boxA_area = (right - left + 1) * (bottom - top + 1)
  boxB_area = (_right - _left + 1) * (_bottom - _top + 1)
  iou = inter_area / (boxA_area + np.transpose(boxB_area) - inter_area)
  return iou, (inter_area / boxA_area, inter_area / np.transpose(boxB_area)), (inter_length, inter_height)

def np_intersect_over_min_area(boxes1, boxes2):
  """
  This method calculates iou between two sets of boxes
  """
  top, left, bottom, right = np.split(boxes1, 4, axis=1)
  _top, _left, _bottom, _right = np.split(boxes2, 4, axis=1)
  xA = np.maximum(left, _left)
  yA = np.maximum(top, _top)
  xB = np.minimum(right, _right)
  yB = np.minimum(bottom, _bottom)
  inter_length = (xB - xA + 1)
  inter_height = (yB - yA + 1)
  inter_area = np.maximum(inter_length, 0) * np.maximum(inter_height, 0)
  boxA_area = (right - left + 1) * (bottom - top + 1)
  boxB_area = (_right - _left + 1) * (_bottom - _top + 1)
  # iou = inter_area / (boxA_area + np.transpose(boxB_area) - inter_area)
  return inter_area / np.minimum(boxA_area, boxB_area) #(inter_area / boxA_area, inter_area / np.transpose(boxB_area)), (inter_length, inter_height)

def intersection_boxes_box(boxes, box):
  """
  This method receives a list of bounding boxes and a single bounding box and
  computes for each box in the list the intersection area with the single box.
  """
  top, left, bottom, right = np.split(boxes, 4, axis=1)
  _top, _left, _bottom, _right = box

  xA = np.maximum(left, _left)
  yA = np.maximum(top, _top)
  xB = np.minimum(right, _right)
  yB = np.minimum(bottom, _bottom)

  inter_length = (xB - xA + 1)
  inter_height = (yB - yA + 1)
  inter_area = np.maximum(inter_length, 0) * np.maximum(inter_height, 0)
  return inter_area


def boxes_intersect_box(boxes, box):
  """
  This method receives a list of bounding boxes and a single bounding box and
  computes for each box in the list whether or it intersects the single box.
  """
  inter_area = intersection_boxes_box(boxes, box)
  return inter_area > 0


def boxes_areas(boxes):
  "This method receives a list of bounding boxes and computes the area for each of them"
  top, left, bottom, right = np.split(boxes, 4, axis=1)
  areas = (bottom - top + 1) * (right - left + 1)
  return areas


def iou_boxes_box(boxes, box):
  """
  This method receives a list of bounding boxes and a single bounding box and
  computes the IoU scores for each bounding box from boxes with the one in box
  """
  inter_area = intersection_boxes_box(boxes, box)
  areas = boxes_areas(boxes)
  t, l, b, r = box
  area = (b - t + 1) * (r - l + 1)

  iou = inter_area / (areas + area - inter_area)

  return iou


def count_unique_boxes_intersection(dict_boxes, direction=None):
  """
  This method returns the number of unique boxes that intersect. 
  Method expects `dict_boxes` to contain all the boxes that you need to verify for intersection between them.
  `dict_boxes` keys will be of type int or str
  `dict_boxes` values will contain [TOP, LEFT, BOTTOM, RIGHT] values specific to each box
    dict_boxes = {
      0: [10, 20, 30, 40],
      1: [50, 60, 70, 80],
      3: [10, 20, 40, 60]
      }  
  """
  
  _len = len(dict_boxes)
  if _len < 2: 
    return _len
  
  unique_boxes = set()
  combs = list(combinations(dict_boxes.keys(), 2))
  for comb in combs:
    TLBR1, TLBR2 = dict_boxes[comb[0]], dict_boxes[comb[1]]
    iou, _, (inter_length, inter_height) = np_vec_iou(
      boxes1=np.array([TLBR1]), 
      boxes2=np.array([TLBR2])
      )
    res = False
    if iou > 0:
      res = True
      if direction is not None and direction != 'BOTH':
        intersection_direction = 'VERTICAL' if inter_length > inter_height else 'HORIZONTAL'
        res = direction == intersection_direction
      #endif
    #endif
    if res:
      unique_boxes.update([comb[0], comb[1]])
  #endfor
  return len(unique_boxes) or 1


def boxes_are_intersecting(tlbr_box1, tlbr_box2):
  """
  This method verifies if two boxes to intersecting with each other based on their 
  [TOP, LEFT, BOTTOM, RIGHT] coordinates.
  """
  
  _top, _left, _bottom, _right = tlbr_box1
  top, left, bottom, right = tlbr_box2
  if _left > right or _right < left or _top > bottom or _bottom < top:
    return False
  return True


def intersect_box_irregular_target(box, target_area):
  """
  This method calculates the intersection percent between a `box` (TLBR area) and a
  polygon/non-regular area
  
  The `box` argument can be provided either in a simple TLBR list format or
  as a polygon format.
  
  The method returns the intersection percent between provided shapes.
  """
  
  if not isinstance(box[0], list):
    top    = box[0]
    left   = box[1]
    bottom = box[2]
    right  = box[3]
    box = [
      [left, top], 
      [right, top], 
      [right, bottom], 
      [left, bottom], 
      [left, top]
      ]
  elif len(box) == 4:# box[-1] != box[0]
    box.append(box[0])
  box = np.array(box).astype(np.float64)
  if target_area[0] == target_area[-1]:
    target_area_shapely = Polygon(target_area)
  else:
    target_area_shapely = LineString(target_area)
  box_shapely = Polygon(box)
  intersect = box_shapely.intersection(target_area_shapely)
  if box_shapely.area == 0:
    intersect_value = 0
  else:
    intersect_value = intersect.area / box_shapely.area
  return intersect_value
  
  
def intersect_box_regular_target(lst_boxes_tlbr, target_area):
  """
  This method calculates the intersection percent between a list of boxes
  described by their TLBR and a regular target area also decribed by TLBR.
  
  This method return the intersection percent for each box with the target area.
  """
  
  _, intersect, _ = np_vec_iou(lst_boxes_tlbr, np.array([target_area]))
  intersect = intersect[0].ravel()
  return intersect      



# def filter_boxes_irregular_target(lst_box, target_area, prc_intersect):
def keep_boxes_inside_irregular_target_area(lst_box, target_area, prc_intersect):
  """
  This method filter a list of boxes described by their TLBR values with a target area
  non-rectangular. 
  The filtering steps takes into account `prc_intersect` and only the boxes with intersection
  over the the `prc_intersect` will be kept.
  """
  
  l = [intersect_box_irregular_target(x['TLBR_POS'], target_area) for x in lst_box]
  arr_intersect = np.array(l)
  sel = arr_intersect >= prc_intersect
  intersect_values = arr_intersect[sel]
  lst_box = np.array(lst_box)[sel].tolist()
  return lst_box, intersect_values


# def filter_boxes_regular_target(lst_box, target_area, prc_intersect):
def keep_boxes_inside_regular_target_area(lst_box, target_area, prc_intersect):
  """
  This method filter a list of boxes described by their TLBR values with a target area
  also described by TLBR value
  The filtering steps takes into account `prc_intersect` and only the boxes with intersection
  over the the `prc_intersect` will be kept.
  """
  
  lst_boxes_tlbr = np.array([x['TLBR_POS'] for x in lst_box])
  intersect = intersect_box_regular_target(
    lst_boxes_tlbr=lst_boxes_tlbr,
    target_area=target_area
    )
  sel = intersect >= prc_intersect
  intersect_values = intersect[sel]
  lst_box = np.array(lst_box)[sel].tolist()
  return lst_box, intersect_values

def convert_points_to_tlbr(points):
  """
  This method converts points to TLBR coordinates
  """
  
  arr = np.array(points)
  left = arr[:, 0].min()
  right = arr[:, 0].max()
  top = arr[:, 1].min()
  bottom = arr[:, 1].max()
  return [top, left, bottom, right]

def convert_tlbr_to_points(tlbr):
  top, left, bottom, right = tlbr
  points = [[left, top], [right, top], [right, bottom], [left, bottom], [left, top]]
  return points

def keep_boxes_outside_irregular_target_area(lst_box, target_area):
  """
  This method keeps only boxes that are outside of a irregular target area. Boxes are described by their TLBR values.
  The filtering steps takes into account `prc_intersect` and only the boxes with intersection
  over the the `prc_intersect` will be kept.
  """
  
  l = [intersect_box_irregular_target(x['TLBR_POS'], target_area) for x in lst_box]
  arr_intersect = np.array(l)
  sel = arr_intersect == 0
  lst_box = np.array(lst_box)[sel].tolist()
  return lst_box


def keep_boxes_outside_regular_target_area(lst_box, target_area):
  """
  This method keeps only boxes that are outside of a regular target area. Boxes are described by their TLBR values with a target area also described by TLBR value
  The filtering steps takes into account `prc_intersect` and only the boxes with intersection
  over the the `prc_intersect` will be kept.
  """
  
  lst_boxes_tlbr = np.array([x['TLBR_POS'] for x in lst_box])
  intersect = intersect_box_regular_target(
    lst_boxes_tlbr=lst_boxes_tlbr,
    target_area=target_area
    )
  sel = intersect == 0
  lst_box = np.array(lst_box)[sel].tolist()
  return lst_box


def intersect(np_box1, np_box2):
  assert np_box1.shape[0] == np_box2.shape[0]
  np_top     = np_box1[:,0] <= np_box2[:,0]
  np_left    = np_box1[:,1] <= np_box2[:,1]
  np_bottom  = np_box1[:,2] >= np_box2[:,2]
  np_right   = np_box1[:,3] >= np_box2[:,3]
  result = np.all([np_top, np_left, np_bottom, np_right], axis=0)
  return result


def get_boxes_overlapp_relations(np_boxes1, np_boxes2):
  """
  This method returns the overlapp mapping between two list of boxes.

  Parameters
  ----------
  np_boxes1 : np.ndarray
    Array of TLBR boxes. (ex: persons)
  np_boxes2 : np.ndarray
    Array of TLBR boxes. (ex: faces)

  Returns
  -------
  rel_boxes1 : dictionary
    Each box1 item will be maped to a list of box2 items that do intersect with the box1 item
    -> keys will be formed by boxes1 ids
    -> values will be formed by lists of ids from boxes2 that are intersected with the current box2
  rel_boxes2 : dictionary
    Each box2 item will be maped to a list of box1 items that do intersect with the box2 item
    -> keys will be formed by boxes2 ids
    -> values will be formed by lists of ids from boxes1 that are intersected with the current box2

  """
  nr_boxes1 = np_boxes1.shape[0]
  nr_boxes2 = np_boxes2.shape[0]
  
  if nr_boxes1 == 0 or nr_boxes2 == 0:
    rel_boxes1 = {i:[] for i in range(nr_boxes1)}
    rel_boxes2 = {i:[] for i in range(nr_boxes2)}
  else:
    np_boxes1 = np.repeat(np_boxes1, repeats=nr_boxes2, axis=0) #repeat each box1 item to compare it with each box2 item. shape = (nr_boxes1*nr_boxes2, N)
    np_boxes2 = np.vstack([np_boxes2] * nr_boxes1) #for each item in box1, create a replica of box2 items. shape = (nr_boxes2*nr_boxes1, N)
    
    np_overlapps = intersect(np_boxes1, np_boxes2)  #check overlapping boxes. shape = (nr_boxes1*nr_boxes2,)
    
    #create overlapp relations between box1 items and box2 items
    np_overlapps1 = np_overlapps.reshape(nr_boxes1, nr_boxes2) #reshape elements such that each box1 item contains box2 items. shape = (nr_boxes1, nr_boxes2)
    rel_boxes1 = {i: np.argwhere(x).ravel().tolist() for i,x in enumerate(np_overlapps1)} #key represented by box1 item id, value represented by list of box2 ids that overlapp over current box1 item
    
    #create overlapp relations between box1 items and box2 items
    np_overlapps2 = np_overlapps1.T ##reshape such that each box2 item gets its corresponding box1 items. shape = (nr_boxes2, nr_boxes1)
    rel_boxes2 = {i: np.argwhere(x).ravel().tolist() for i,x in enumerate(np_overlapps2)} #key represented by box2 item id, value represented by list of box1 ids that overlapp over current box2 item
  return rel_boxes1, rel_boxes2


def get_non_overlappping_boxes(np_boxes1, np_boxes2):
  """
  This method analyzes two ndarray of TLBR boxes and return only the boxes that are non-overlapping

  Parameters
  ----------
  np_boxes1 : np.ndarray
    Array of TLBR boxes (ex: persons)
  np_boxes2 : np.ndarray
    Array of TLBR boxes (ex: faces)

  Returns
  -------
  np_box1 : np.ndarray
    Only the initial box1 items that are non overlapping with box2 items
  np_box2 : np.ndarray
    Only the initial box2 items that are non overlapping with box1 items

  """
  
  rel_boxes1, rel_boxes2 = get_boxes_overlapp_relations(np_boxes1, np_boxes2)
  
  #extract boxes1 that are not overlapping
  sel_box1 = [k for k,v in rel_boxes1.items() if not v]
  np_box1 = np_boxes1[sel_box1]
  
  #extract boxes2 that are not overlapping
  sel_box2 = [k for k,v in rel_boxes2.items() if not v]
  np_box2 = np_boxes2[sel_box2]
    
  return np_box1, np_box2


def get_only_overlapping_boxes(np_boxes1, np_boxes2):
  """
  This method analyzes two arrays of TLBR boxes and returns only boxes that are intersecting.

  Parameters
  ----------
  np_boxes1 : np.ndarray
    Array of TLBR boxes (ex: persons)
  np_boxes2 : np.ndarray
    Array of TLBR boxes (ex: faces)

  Returns
  -------
  np_box1 : np.ndarray
    Only the initial box1 items that are overlapping with box2 items
  np_box2 : np.ndarray
    Only the initial box2 items that are overlapping with box1 items

  """
  rel_boxes1, rel_boxes2 = get_boxes_overlapp_relations(np_boxes1, np_boxes2)
  
  #extract boxes1 that are overlapping
  sel_box1 = [k for k,v in rel_boxes1.items() if v]
  np_box1 = np_boxes1[sel_box1]
  
  #extract boxes2 that are not overlapping
  sel_box2 = [k for k,v in rel_boxes2.items() if v]
  np_box2 = np_boxes2[sel_box2]
    
  return np_box1, np_box2


def unify_overlapping_boxes(np_boxes1, np_boxes2, box1_priority_on_overlap=True):
  """
  This method analyzes two arrays of TLBR boxes and returns the "union" of those two arrays, based on choosen box priority.
  For example: if `np_boxes1` are persons, `np_boxes2` are faces and `box1_priority_on_overlap` is True, then the method will:
    - keep & return all persons that are non intersecting with a face box
    - keep & return all faces that are non intersecting with a person box
    - keep person in case that person is intersecting a face box
  

  Parameters
  ----------
  np_boxes1 : np.ndarray
    Array of TLBR boxes (ex: persons)
  np_boxes2 : np.ndarray
    Array of TLBR boxes (ex: faces)
  box1_priority_on_overlap : TYPE, optional
    Choose he box that will be kept in case of intersection

  Returns
  -------
  np_box1 : np.ndarray
    Will contain all non-intersecting box1 items. In case `box1_priority_on_overlap` is set to True, will also contain box1 items that do intersect with box2 items
  np_box2 : np.ndarray
    Will contain all non-intersecting box2 items. In case `box1_priority_on_overlap` is set to False, will also contain box2 items that do intersect with box1 items

  """
  rel_boxes1, rel_boxes2 = get_boxes_overlapp_relations(np_boxes1, np_boxes2)
  
  #extract boxes1 that are not overlapping
  sel_box1 = [k for k,v in rel_boxes1.items() if not v]
  
  #extract boxes2 that are not overlapping
  sel_box2 = [k for k,v in rel_boxes2.items() if not v]
  
  if box1_priority_on_overlap:
    sel_box1+= [k for k,v in rel_boxes1.items() if v]
    
  else:
    sel_box2+= [k for k,v in rel_boxes2.items() if v]
  
  np_box1 = np_boxes1[sel_box1]
  np_box2 = np_boxes2[sel_box2]
  
  return np_box1, np_box2

###
###STOP OBJECTS INTERSECTION
###

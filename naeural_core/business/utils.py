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


def intersect_box_irregular_target(box, target_area, *, _target_geometry_cache=None):
  """
  Return the fraction of a box covered by an irregular target.

  Parameters
  ----------
  box : list
      TLBR coordinates or polygon points. A four-point polygon list is closed
      in place, preserving the legacy input-mutation behavior.
  target_area : list
      Target points. Equal endpoints select a Polygon; otherwise a LineString
      is used, not an implicitly closed area.
  _target_geometry_cache : list or None, optional
      Internal single-slot holder for one batch of proven plain, non-mutating
      TLBR inputs. Other callers must leave this unset. No cross-call cache.

  Returns
  -------
  float or int
      Intersection area divided by box area, or integer zero for zero-area
      boxes. Invalid input retains the legacy exception and mutation order.
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
  target_area_shapely = (
    _target_geometry_cache[0] if _target_geometry_cache is not None else None
  )
  if target_area_shapely is None:
    # Build lazily after box normalization: malformed boxes must still fail
    # before inspecting the target, and an empty batch must not inspect it.
    if target_area[0] == target_area[-1]:
      target_area_shapely = Polygon(target_area)
    else:
      target_area_shapely = LineString(target_area)
    if _target_geometry_cache is not None:
      _target_geometry_cache[0] = target_area_shapely
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
  Keep detections whose target overlap is at least the given threshold.

  Parameters
  ----------
  lst_box : list of dict
      Detections with ``TLBR_POS`` coordinates, in input order.
  target_area : list
      Irregular target points, with legacy open/closed endpoint semantics.
  prc_intersect : float
      Inclusive minimum intersection fraction; comparison is unchanged.

  Returns
  -------
  tuple[list, numpy.ndarray]
      Selected detections and their intersection fractions, retaining order,
      duplicate identities and NumPy's original inferred result dtype.

  Notes
  -----
  Bounded integer TLBRs against a closed axis-aligned integer rectangle use
  continuous rectangle areas. Other plain numeric batches reuse the target
  geometry within this call only.
  Polygon boxes, array/custom inputs and effectful numeric subclasses use the
  scalar path, preserving alias mutations and conversion/exception ordering.
  """

  l = _irregular_target_fractions(lst_box, target_area, prc_intersect)
  arr_intersect = np.array(l)
  sel = arr_intersect >= prc_intersect
  intersect_values = arr_intersect[sel]
  lst_box = np.array(lst_box)[sel].tolist()
  return lst_box, intersect_values


def _irregular_target_fractions(lst_box, target_area, prc_intersect):
  """Compute overlap fractions without changing the public selection path.

  Parameters
  ----------
  lst_box, target_area : list
      Detections and target points passed unchanged to the scalar fallback.
  prc_intersect : float
      Only exact builtin thresholds opt into arithmetic; custom comparisons
      keep the original execution path.

  Returns
  -------
  list
      One fraction per row, in order, with legacy scalar types on fallback.
  """
  # Recognize only ordinary JSON-shaped data. At +/-2**20 even the largest
  # rectangle area (2**42) is an exact binary64 integer before division.
  limit = 1 << 20
  if (
    type(lst_box) is list and lst_box
    and (type(prc_intersect) is float or type(prc_intersect) is int)
    and type(target_area) is list and len(target_area) == 5
    and all(type(p) is list and len(p) == 2
            and all(type(v) is int and -limit <= v <= limit for v in p)
            for p in target_area)
    and target_area[0] == target_area[-1]
  ):
    a, b, c, d, _ = target_area
    x0, y0 = a
    x1, y1 = c
    # A and C must be opposite corners, with the two remaining corners in
    # perimeter order. Four corners alone would also admit a bow-tie.
    if x0 != x1 and y0 != y1 and (
      (b == [x0, y1] and d == [x1, y0]) or (b == [x1, y0] and d == [x0, y1])
    ):
      left, right = min(x0, x1), max(x0, x1)
      top, bottom = min(y0, y1), max(y0, y1)
      fractions = []
      for row in lst_box:
        # A colliding custom key may execute code even on an exact dict.
        if type(row) is not dict or not all(type(k) is str for k in row):
          break
        box = row.get('TLBR_POS')
        if (type(box) is not list or len(box) != 4
            or not all(type(v) is int and -limit <= v <= limit for v in box)):
          break
        t, l, btm, r = box
        if t >= btm or l >= r:
          break
        h = max(0, min(btm, bottom) - max(t, top))
        w = max(0, min(r, right) - max(l, left))
        fractions.append((h * w) / ((btm - t) * (r - l)))
      else:
        return fractions
      # Any rejected row replays the *entire* batch in original order. The
      # discarded prefix used only side-effect-free builtin reads/arithmetic.

  # Only inspect exact builtin containers/scalars here. Pre-validating or
  # converting arbitrary inputs could run user code before the first box, or
  # hide a target mutation caused by closing an aliased four-point polygon.
  reuse_target = (
    type(lst_box) is list
    and len(lst_box) > 1
    and type(target_area) in (list, tuple)
    and all(
      type(point) in (list, tuple)
      and all(type(value) in (int, float) for value in point)
      for point in target_area
    )
    and all(
      type(row) is dict
      # Even an exact dict can run a custom key's __eq__ during lookup. Check
      # keys without looking up values before visiting any later detection.
      and all(type(key) is str for key in row)
      and type(row.get('TLBR_POS')) in (list, tuple)
      and all(type(value) in (int, float) for value in row['TLBR_POS'])
      for row in lst_box
    )
  )
  target_cache = [None] if reuse_target else None
  return [
    intersect_box_irregular_target(
      x['TLBR_POS'], target_area, _target_geometry_cache=target_cache,
    )
    for x in lst_box
  ]


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

import numpy as np
from time import time


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
  
  rel_box1, rel_box2 = get_boxes_overlapp_relations(np_boxes1, np_boxes2)
  
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
  rel_box1, rel_box2 = get_boxes_overlapp_relations(np_boxes1, np_boxes2)
  
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
  rel_box1, rel_box2 = get_boxes_overlapp_relations(np_boxes1, np_boxes2)
  
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


if __name__ == '__main__':
  BOXES1 = np.array([[i*10, 0, (i+1) * 10, 10] for i in range(5)])
  BOXES2 = np.array([[i*10, 20, (i+1) * 10, 30] for i in range(5)] + [[i*5, 0, (i+1) * 5, 5] for i in range(5)])
  
  start = time()
  rel_boxes1, rel_boxes2 = get_boxes_overlapp_relations(BOXES1, BOXES2)
  stop = time()
  print('First: {}'.format(stop - start))

  start = time()
  np_boxes1, np_boxes2 = unify_overlapping_boxes(BOXES1, BOXES2)
  stop = time()
  print('Second: {}'.format(stop - start))


  



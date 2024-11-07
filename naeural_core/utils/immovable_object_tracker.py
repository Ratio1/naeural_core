import numpy as np

from collections import deque
from naeural_core.business.utils import np_vec_iou

class ImmovableObjectTracker(object):
  def __init__(self, object_max_age, iou_threshold, max_history, min_history=0):
    self._immovable_objects = {}
    self._last_track_id_key = 0
    self._object_max_age = object_max_age
    self._max_history = max_history
    self._iou_threshold = iou_threshold
    self._min_history = min_history

  def _increment_age(self):
    for key in self._immovable_objects.keys():
      self._immovable_objects[key]['age'] += 1
    return

  def _remove_old_objects(self):
    lst_to_be_removed = []
    for key in self._immovable_objects.keys():
      if self._immovable_objects[key]['age'] > self._object_max_age:
        lst_to_be_removed.append(key)
      #endif
    #endfor
    for key in lst_to_be_removed:
      self._immovable_objects.pop(key)
    return

  def _update_object(self, object, detection): ##TODO rename
    object['age'] = 0
    object['tlbr_history'].append(detection)

  def _add_object(self, detection):
    self._last_track_id_key += 1
    self._immovable_objects[self._last_track_id_key] = {
      'age': 0,
      'tlbr_history': deque(maxlen=self._max_history)
    }
    self._immovable_objects[self._last_track_id_key]['tlbr_history'].append(detection)

  def _get_object_tlbr(self, object):
    return np.mean([x for x in object['tlbr_history']], axis=0)

  def _get_np_tlbrs(self):
    return list(self._immovable_objects.keys()), np.array([self._get_object_tlbr(x) for x in self._immovable_objects.values()]) ##TODO

  def _update_detections(self, detections):
    iou = None

    np_detections = np.array(detections)
    object_keys, np_objects = self._get_np_tlbrs()

    if np_detections.shape[0] > 0 and np_objects.shape[0] > 0:
      iou, _,_ = np_vec_iou(np_detections, np_objects)

    for i, detection in enumerate(detections):
      if iou is not None and np.max(iou[i,:]) > self._iou_threshold:
        self._update_object(self._immovable_objects[object_keys[np.argmax(iou[i,:])]], detection)
        continue
      #endif

      self._add_object(detection)
      #endif
    #endfor

  def get_objects(self):
    return [
      {'TLBR_POS': self._get_object_tlbr(x)}
      for x in self._immovable_objects.values()
      if len(x['tlbr_history']) > self._min_history
    ]

  def update_objects(self, detections):
    self._increment_age()
    self._update_detections(detections)
    self._remove_old_objects()

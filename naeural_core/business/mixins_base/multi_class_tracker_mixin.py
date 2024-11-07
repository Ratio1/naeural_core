"""
Module: multi_class_tracker_mixin.py

This module provides the _MultiClassTrackerMixin class, which handles tracking of 
multiple object classes using the CentroidObjectTracker.

IMPORTANT: 
	This class should be modified/tuned within individual implementations of complex ecosystems. It is recommended to
	create a `extensions.business.mixin_base.multi_class_tracker_mixin` unit that will automaticaly replace the current file

It is intended to be used as a mixin in classes that provide certain attributes and methods, including:

- self.cfg_tracking_enabled : bool
- self.cfg_tracking_mode
- self.cfg_linear_max_age
- self.cfg_linear_max_distance_ratio
- self.cfg_linear_max_relative_dist
- self.cfg_sort_min_hits
- self.cfg_sort_max_age
- self.cfg_sort_min_iou
- self.cfg_linear_max_dist_scale
- self.cfg_linear_center_dist_weight
- self.cfg_linear_hw_dist_weight
- self.cfg_linear_reset_minutes
- self._get_detector_ai_engines()
- self.dataapi_image()
- self.start_timer(name)
- self.end_timer(name)
- self.log
- self._type_to_meta_type_map

"""

import numpy as np
from datetime import datetime
from naeural_core import constants as ct
from naeural_core.utils.centroid_object_tracker import CentroidObjectTracker
from decentra_vision import geometry_methods as gmt


class _MultiClassTrackerMixin:
  """
  Mixin class for tracking multiple object classes using CentroidObjectTracker.

  This mixin depends on several attributes and methods that should be defined
  in the class that uses it. These include configuration settings, logging,
  and data API methods.

  """

  def __init__(self):
    self._object_trackers = {}
    super().__init__()

  def _match_tracking_results(self, img_inference, img_tracking_results, valid_class):
    """
    Matches all inferences to the returned objects of the tracker,
    then updates the inference dictionary with required info.

    Parameters
    ----------
    img_inference : list of dict
        List of inference dictionaries for the current image.
    img_tracking_results : dict
        Dictionary of tracking results from the tracker.
    valid_class : str
        The class of objects being tracked.

    """
    for inference in img_inference:
        inference_tlbr = np.array(self.get_inference_track_tlbr(inference)).astype(np.int32)
        for track_id, object_info in img_tracking_results.items():
            object_rectangle = np.array(object_info['rectangle']).astype(np.int32)
            if (
                np.all(inference_tlbr == object_rectangle)
                and self.__get_track_class(inference) == valid_class
            ):
                inference[ct.TRACK_ID] = track_id
                inference[ct.TRACKING_STARTED_AT] = object_info['first_update']
                alive_time = (object_info['last_update'] - object_info['first_update']).total_seconds()
                inference[ct.TRACKING_ALIVE_TIME] = alive_time
                inference[ct.APPEARANCES] = object_info['appearances']

  def __get_track_class(self, inference):
      """
      Returns the class of the object that will be tracked.
      If the object has a META_TYPE it will be used, otherwise the TYPE will be used.
      In case neither is present, None will be returned.

      Parameters
      ----------
      inference : dict
          Inference dictionary.

      Returns
      -------
      str or None
          Class of the object that will be tracked.

      """
      if self.const.META_TYPE in inference:
          return inference[self.const.META_TYPE]
      if self.const.TYPE in inference:
          return inference[self.const.TYPE]
      return None

  def get_tracking_type(self, inference):
      """
      Public method for accessing the tracking type of an inference.

      Parameters
      ----------
      inference : dict
          Inference dictionary.

      Returns
      -------
      str or None
          Tracking type of the inference.

      """
      return self.__get_track_class(inference)

  def get_inference_track_tlbr(self, inference):
      """
      Returns the TLBR that will be used for tracking an inference.
      This allows using a different TLBR for tracking than the detected one.

      Parameters
      ----------
      inference : dict
          Inference dictionary.

      Returns
      -------
      list of int
          List of 4 integers representing the TLBR used for tracking.

      """
      return inference.get(ct.TLBR_POS_TRACK, inference[ct.TLBR_POS])

  def _track_objects(self, dct_inference, img_shape=None):
      """
      Tracks inferences for multiple object classes.

      Parameters
      ----------
      dct_inference : dict
          Dictionary of inferences per AI engine.
      img_shape : tuple, optional
          Shape of the image (height, width). If None, it will be obtained from data API.

      Returns
      -------
      dict
          Updated dictionary of inferences.

      """
      if not self.cfg_tracking_enabled:
          return dct_inference

      self.start_timer('obj_track')
      detector_ai_engines = self._get_detector_ai_engines()
      inferences = []

      for engine, engine_inferences in dct_inference.items():
          if engine in detector_ai_engines:
              inferences.extend(engine_inferences)

      # Gather all valid classes
      valid_classes = set()
      for inference_list in inferences:
          for inference in inference_list:
              track_class = self.__get_track_class(inference)
              if track_class is not None:
                  valid_classes.add(track_class)

      # Ensure all previously seen classes are included
      valid_classes.update(self._object_trackers.keys())

      for valid_class in valid_classes:
          if valid_class not in self._object_trackers:
              if img_shape is None:
                  img_shape = self.dataapi_image().shape
              self._object_trackers[valid_class] = CentroidObjectTracker(
                  object_tracking_mode=self.cfg_tracking_mode,
                  linear_max_age=self.cfg_linear_max_age,
                  linear_max_distance=np.sqrt(img_shape[0] * img_shape[1]) / self.cfg_linear_max_distance_ratio,
                  linear_max_relative_distance=self.cfg_linear_max_relative_dist,
                  sort_min_hits=self.cfg_sort_min_hits,
                  sort_max_age=self.cfg_sort_max_age,
                  sort_min_iou=self.cfg_sort_min_iou,
                  max_dist_scale=self.cfg_linear_max_dist_scale,
                  center_dist_weight=self.cfg_linear_center_dist_weight,
                  hw_dist_weight=self.cfg_linear_hw_dist_weight,
                  linear_reset_minutes=self.cfg_linear_reset_minutes,
                  moved_delta_ratio=0.005  # TODO: Implement moved_delta_ratio functionality
              )

          for img_inference in inferences:
              filtered_img_inferences = [
                  inference for inference in img_inference
                  if self.__get_track_class(inference) == valid_class
              ]
              np_inferences = np.array([
                  self.get_inference_track_tlbr(inference) for inference in filtered_img_inferences
              ])
              img_tracking_results = self._object_trackers[valid_class].update_tracker(np_inferences)
              self._match_tracking_results(filtered_img_inferences, img_tracking_results, valid_class)
              self._object_trackers[valid_class].add_to_type_history(filtered_img_inferences)

      self.end_timer('obj_track')
      return dct_inference

  def _get_tracker(self, object_type):
      """
      Retrieves the tracker for a specific object type.

      Parameters
      ----------
      object_type : str
          Type or meta-type of the tracker needed.

      Returns
      -------
      CentroidObjectTracker or None
          Tracker object used for the specified type/meta-type.

      """
      if object_type in self._type_to_meta_type_map:
          object_type = self._type_to_meta_type_map[object_type]
      return self._object_trackers.get(object_type, None)

  def _get_object_appearances(self, object_id, object_type):
      """
      Returns the number of appearances for an object.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.

      Returns
      -------
      int
          Number of appearances.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_object_appearances(object_id) if tracker else 0

  def _track_in_zone_objects(self, dct_inference):
      """
      Updates the time in the target zone and adds it to the inference dictionary.

      Parameters
      ----------
      dct_inference : dict
          Dictionary of inferences.

      Returns
      -------
      dict
          Updated dictionary of inferences.

      """
      if not self.cfg_tracking_enabled:
          return dct_inference

      self.start_timer('obj_track_in_zone')
      inferences = dct_inference.get(self._get_detector_ai_engine(), [])

      for obj_class, obj_tracker in self._object_trackers.items():
          for img_inference in inferences:
              filtered_img_inferences = [
                  inference for inference in img_inference
                  if self.__get_track_class(inference) == obj_class
              ]
              obj_tracker.update_in_zone_history(filtered_img_inferences)
              for inference in filtered_img_inferences:
                  inference[ct.TIME_IN_TARGET] = self.trackapi_in_zone_total_seconds(
                      object_id=inference[ct.TRACK_ID],
                      object_type=obj_class
                  )

      self.end_timer('obj_track_in_zone')
      return dct_inference

  def _get_object_history(self, object_id, object_type):
      """
      Returns the centroid history of an object.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.

      Returns
      -------
      list
          List of centroids.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_object_history(object_id) if tracker else []

  def _get_object_type_history(self, object_id, object_type):
      """
      Returns the type history summary of an object.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.

      Returns
      -------
      dict
          Type history summary.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_object_type_history(object_id) if tracker else {'total': 0}

  def _get_object_type_history_deque(self, object_id, object_type):
      """
      Returns the type history deque of an object.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.

      Returns
      -------
      deque
          Type history deque.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_object_type_history_deque(object_id) if tracker else self.deque(maxlen=100)

  def _get_type_history_info(self, object_id, object_type):
      """
      Returns a string summarizing the type history of an object.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.

      Returns
      -------
      str
          String summarizing type frequencies and recent history.

      """
      type_history = self._get_object_type_history(object_id, object_type)
      type_history_deque = self._get_object_type_history_deque(object_id, object_type)
      deque_info = [class_name[0] for class_name in type_history_deque]
      freq_info = " ".join([f"{k[:2]}:{v}" for k, v in type_history.items() if k != 'total'])
      return f'Freq: {freq_info} | Last: {"".join(deque_info)}'

  def _get_object_class_count(self, object_id, object_type, object_subtype):
      """
      Returns the count of times an object was classified as a specific subtype.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.
      object_subtype : str
          Subtype to count.

      Returns
      -------
      int
          Count of appearances as the specified subtype.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_class_count(object_id, object_subtype) if tracker else 0

  def _get_object_non_class_count(self, object_id, object_type, object_subtype):
      """
      Returns the count of times an object was not classified as a specific subtype.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.
      object_subtype : str
          Subtype to count non-appearances.

      Returns
      -------
      int
          Count of non-appearances as the specified subtype.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_non_class_count(object_id, object_subtype) if tracker else 0

  def _get_object_class_ratio(self, object_id, object_type, object_subtype):
      """
      Returns the ratio of times an object was classified as a specific subtype.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.
      object_subtype : str
          Subtype to calculate ratio for.

      Returns
      -------
      float
          Ratio of appearances as the specified subtype.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_class_ratio(object_id, object_subtype) if tracker else 0.0

  def _get_object_absolute_orientation(self, object_id, object_type):
      """
      Returns a tuple containing the original and current positions of the object.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.

      Returns
      -------
      tuple
          (original_position, current_position)

      """
      tracker = self._get_tracker(object_type)
      if not tracker:
          raise ValueError(f"No tracker found for object type '{object_type}'")
      original_position = tracker.get_original_position(object_id)
      last_position = tracker.get_object_history(object_id)[-1]
      return (original_position, last_position)

  def _get_current_orientation(self, object_id, object_type, number_of_points=3):
      """
      Returns the orientation vector based on the most recent movements.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.
      number_of_points : int, optional
          Number of recent points to consider.

      Returns
      -------
      tuple
          Orientation vector (dx, dy).

      """
      tracker = self._get_tracker(object_type)
      if not tracker:
          raise ValueError(f"No tracker found for object type '{object_type}'")
      positions = tracker.get_object_history(object_id)[-number_of_points:]
      if len(positions) < 2:
          return (0, 0)
      point1 = positions[0]
      point2 = positions[-1]
      return (point2[0] - point1[0], point2[1] - point1[1])

  def _distance_point_to_line(self, point, line):
      """
      Calculates the distance from a point to a line.

      Parameters
      ----------
      point : tuple
          Coordinates of the point (x, y).
      line : tuple
          Two points defining the line ((x1, y1), (x2, y2)).

      Returns
      -------
      float
          Distance from the point to the line.

      """
      (x0, y0) = point
      (x1, y1), (x2, y2) = line
      numerator = abs((x2 - x1) * (y1 - y0) - (x1 - x0) * (y2 - y1))
      denominator = np.hypot(x2 - x1, y2 - y1)
      return numerator / denominator if denominator != 0 else 0.0

  def _get_zone_points(self, line):
      """
      Generates two points in different zones separated by a line.

      Parameters
      ----------
      line : tuple
          Two points defining the line ((x1, y1), (x2, y2)).

      Returns
      -------
      tuple
          Two points in different zones.

      """
      (x1, y1), (x2, y2) = line
      center = ((x1 + x2) // 2, (y1 + y2) // 2)
      var_x = (x1 - x2) // 2
      var_y = (y1 - y2) // 2
      zone_1_point = (int(center[0] + var_y), int(center[1] - var_x))
      zone_2_point = (int(center[0] - var_y), int(center[1] + var_x))
      return zone_1_point, zone_2_point

  def get_movement_relative_to_line(
      self, object_id, object_type,
      line, zone1_point=None,
      zone2_point=None, threshold=10,
      start_point=None
  ):
      """
      Determines the movement of an object relative to a line.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.
      line : tuple
          Two points defining the line ((x1, y1), (x2, y2)).
      zone1_point : tuple, optional
          A point in Zone 1. If None, it will be generated automatically.
      zone2_point : tuple, optional
          A point in Zone 2. If None, it will be generated automatically.
      threshold : float, optional
          Minimum movement required to consider crossing.
      start_point : tuple, optional
          Starting point of the object. If None, the object's original position is used.

      Returns
      -------
      tuple or None
          (zone1_point, zone2_point) if moved from Zone 1 to Zone 2,
          (zone2_point, zone1_point) if moved from Zone 2 to Zone 1,
          None if movement is below threshold.

      """
      original, current = self._get_object_absolute_orientation(object_id, object_type)
      start_point = start_point or original

      distance_1 = self._distance_point_to_line(start_point, line)
      distance_2 = self._distance_point_to_line(current, line)
      distance_diff = distance_1 - distance_2

      if abs(distance_diff) < threshold:
          return None

      if zone1_point is None or zone2_point is None:
          zone1_point, zone2_point = self._get_zone_points(line)

      zone1_sign = self._distance_point_to_line(zone1_point, line) < 0
      zone2_sign = self._distance_point_to_line(zone2_point, line) < 0
      movement_sign = distance_diff < 0

      if movement_sign == zone1_sign:
          return (zone1_point, zone2_point)
      else:
          return (zone2_point, zone1_point)

  def get_line_passing_direction(
      self, object_id, object_type,
      line, zone1_point=None,
      zone2_point=None, start_point=None,
      eps=1e-5
  ):
      """
      Determines the direction in which an object passed a line.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.
      line : tuple
          Two points defining the line ((x1, y1), (x2, y2)).
      zone1_point : tuple, optional
          A point in Zone 1. If None, it will be generated automatically.
      zone2_point : tuple, optional
          A point in Zone 2. If None, it will be generated automatically.
      start_point : tuple, optional
          Starting point of the object. If None, the object's original position is used.
      eps : float, optional
          Tolerance for floating-point comparisons.

      Returns
      -------
      tuple or None
          (0, 1) if passed from Zone 1 to Zone 2,
          (1, 0) if passed from Zone 2 to Zone 1,
          None if no crossing detected.

      """
      original, current = self._get_object_absolute_orientation(object_id, object_type)
      start_point = start_point or original

      distance_1 = self._distance_point_to_line(start_point, line)
      distance_2 = self._distance_point_to_line(current, line)

      if distance_1 * distance_2 >= 0:
          return None

      movement = self.get_movement_relative_to_line(
          object_id, object_type, line, zone1_point, zone2_point, threshold=0, start_point=start_point
      )
      if movement is None:
          return None

      A, B = movement
      if np.linalg.norm(np.array(A) - np.array(zone1_point)) < eps:
          return (0, 1)
      else:
          return (1, 0)

  # TRACKAPI SECTION

  def trackapi_in_zone_total_seconds(self, object_id, object_type):
      """
      Returns the total seconds an object has been in the target zone.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.

      Returns
      -------
      int
          Total seconds in zone.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_in_zone_total_seconds_additional(object_id) if tracker else 0

  def trackapi_in_zone_history(self, object_id, object_type):
      """
      Returns the in-zone history of an object.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.

      Returns
      -------
      list
          List of intervals the object was in the target zone.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_in_zone_history(object_id) if tracker else []

  def trackapi_centroid_history(self, object_id, object_type):
      """
      Returns the centroid history of an object.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.

      Returns
      -------
      list
          List of centroids.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_object_history(object_id) if tracker else []

  def trackapi_type_history(self, object_id, object_type):
      """
      Returns the type history summary of an object.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.

      Returns
      -------
      dict
          Type history summary.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_object_type_history(object_id) if tracker else {'total': 0}

  def trackapi_type_history_deque(self, object_id, object_type):
      """
      Returns the type history deque of an object.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.

      Returns
      -------
      deque
          Type history deque.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_object_type_history_deque(object_id) if tracker else self.deque(maxlen=100)

  def trackapi_most_seen_type(self, object_id, object_type):
      """
      Returns the most frequently observed type for an object.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.

      Returns
      -------
      str
          Most seen type.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_most_seen_type(object_id) if tracker else ''

  def trackapi_class_count(self, object_id, object_type, class_name):
      """
      Returns the count of times an object was classified as a specific class.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.
      class_name : str or list of str
          Class name(s) to count.

      Returns
      -------
      int
          Count of appearances.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_class_count(object_id, class_name) if tracker else 0

  def trackapi_non_class_count(self, object_id, object_type, class_name):
      """
      Returns the count of times an object was not classified as a specific class.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.
      class_name : str or list of str
          Class name(s) to count non-appearances.

      Returns
      -------
      int
          Count of non-appearances.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_non_class_count(object_id, class_name) if tracker else 0

  def trackapi_class_ratio(self, object_id, object_type, class_name):
      """
      Returns the ratio of times an object was classified as a specific class.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.
      class_name : str or list of str
          Class name(s) to calculate ratio for.

      Returns
      -------
      float
          Ratio of appearances.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_class_ratio(object_id, class_name) if tracker else 0.0

  def trackapi_original_position(self, object_id, object_type):
      """
      Returns the original position of an object.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.

      Returns
      -------
      ndarray
          Original centroid position.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_original_position(object_id) if tracker else np.array([0, 0])

  def trackapi_max_movement(self, object_id, object_type, steps=None, method='l2'):
      """
      Returns the maximum movement of an object from its original position.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.
      steps : int or None, optional
          Number of recent steps to consider.
      method : str, optional
          Distance metric to use ('l1' or 'l2').

      Returns
      -------
      float
          Maximum movement distance.

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_object_max_movement(object_id, steps, method) if tracker else 0.0

  def trackapi_last_rectangle(self, object_id, object_type):
      """
      Returns the last seen rectangle of an object.

      Parameters
      ----------
      object_id : int
          ID of the object.
      object_type : str
          Type or meta-type of the object.

      Returns
      -------
      list
          Last rectangle in the format [startX, startY, endX, endY].

      """
      tracker = self._get_tracker(object_type)
      return tracker.get_last_rectangle(object_id) if tracker else [0, 0, 0, 0]

  # END TRACKAPI SECTION


if __name__ == '__main__':
  
  eng = _MultiClassTrackerMixin()
  
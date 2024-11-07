"""
Module: centroid_object_tracker.py

This module provides the CentroidObjectTracker class, which implements object tracking using 
centroid-based linear tracking and the SORT algorithm.

IMPORTANT / WARNING: 
	This class should be modified/tuned within individual implementations of complex ecosystems. It is recommended to
	create a `extensions.utils.centroid_object_tracker` unit that will automaticaly replace the current file

The tracker can be configured to use linear tracking, SORT tracking, or both for debugging purposes. 
It maintains the history of detected objects, updates their positions, and handles object registration 
and deregistration based on specified criteria.

"""

import numpy as np
from scipy.spatial import distance as dist
from collections import OrderedDict, deque
from datetime import datetime

from naeural_core.utils.sort import Sort
from naeural_core import constants as ct
from decentra_vision import geometry_methods as gmt


class CentroidObjectTracker:
    def __init__(
        self,
        object_tracking_mode=0,
        linear_max_age=4,
        linear_max_distance=300,
        linear_max_relative_distance=1.2,
        max_dist_scale=1.4,
        center_dist_weight=1,
        hw_dist_weight=0.8,
        sort_min_hits=1,
        sort_max_age=3,
        sort_min_iou=0,
        moved_delta_ratio=0.005,
        linear_reset_minutes=60,
        **kwargs
    ):
        """
        Initialize the CentroidObjectTracker.

        Parameters
        ----------
        object_tracking_mode : int, optional
            Tracking mode. Options are:
            0: Linear tracking (default).
            1: SORT tracking (not recommended).
            2: Both (for debugging purposes only).

        linear_max_age : int, optional
            Maximum number of frames to keep an object without detection before deregistering.

        linear_max_distance : float, optional
            Maximum allowable distance between centroids to consider the same object.

        linear_max_relative_distance : float, optional
            Maximum relative distance allowed between consecutive positions of the same object.
            Calculated as the centroid distance divided by the maximum dimension of the object.

        max_dist_scale : float, optional
            Scale factor for computing maximum distances for distance components.

        center_dist_weight : float, optional
            Weight of the centroid distance in the total distance calculation.

        hw_dist_weight : float, optional
            Weight of the size (height/width) distance in the total distance calculation.

        sort_min_hits : int, optional
            Minimum number of hits to consider a track as valid in SORT tracking.

        sort_max_age : int, optional
            Maximum number of frames to keep a track without detection in SORT tracking.

        sort_min_iou : float, optional
            Minimum IOU for matching in SORT tracking.

        moved_delta_ratio : float, optional
            Not implemented yet.

        linear_reset_minutes : int, optional
            Maximum number of minutes to track an object before resetting.

        **kwargs
            Additional keyword arguments.

        """
        self.nextObjectID = 0
        self.objects = OrderedDict()
        self.disappeared = OrderedDict()
        self.objects_history = OrderedDict()

        self.object_tracking_mode = object_tracking_mode
        self.linear_max_age = linear_max_age
        self.linear_max_distance = linear_max_distance
        self.linear_max_relative_distance = linear_max_relative_distance
        self.linear_reset_minutes = linear_reset_minutes

        self.center_dist_weight = center_dist_weight
        self.hw_dist_weight = hw_dist_weight
        self.max_dist_scale = max_dist_scale

        total_weight = self.hw_dist_weight + self.center_dist_weight
        self.linear_max_distance_center = (
            self.linear_max_distance * self.center_dist_weight / total_weight * self.max_dist_scale
        )
        self.linear_max_distance_hw = (
            self.linear_max_distance * self.hw_dist_weight / total_weight * self.max_dist_scale
        )

        self.sort_min_hits = sort_min_hits
        self.sort_max_age = sort_max_age
        self.sort_min_iou = sort_min_iou

        self.moved_delta_ratio = moved_delta_ratio  # TODO: Implement moved_delta_ratio functionality

        self.sort_tracker = None  # Will be initialized when needed

        # Ensure no kwargs are passed since we don't call super().__init__()
        if kwargs:
            raise ValueError(f"Unexpected keyword arguments: {kwargs}")

    def _maybe_init(self):
        """
        Initialize the SORT tracker if it has not been initialized yet.
        """
        if self.sort_tracker is None:
            self.sort_tracker = Sort(
                min_hits=self.sort_min_hits,
                max_age=self.sort_max_age,
                iou_threshold=self.sort_min_iou
            )

    def register(self, centroid, rectangle):
        """
        Register a new object with a unique ID.

        Parameters
        ----------
        centroid : ndarray
            The centroid of the new object.
        rectangle : list or ndarray
            The bounding box coordinates [startX, startY, endX, endY].

        """
        self.objects[self.nextObjectID] = {
            'appearances': 0,
            'centroid': centroid,
            'rectangle': list(rectangle[:4]),
            'color': ct.RED,
            'first_update': datetime.now(),
            'last_update': datetime.now(),
            'centroid_history': deque([centroid], maxlen=1000),
            'original_position': centroid.copy(),
            'type_history': {'total': 0},
            'type_history_deque': deque(maxlen=100),
            'in_zone_history': deque(maxlen=1000),
            'in_zone_total_seconds': 0
        }
        self.disappeared[self.nextObjectID] = 0
        self.nextObjectID += 1

    def deregister(self, objectID):
        """
        Deregister an object ID by moving its data to history and removing it from tracking.

        Parameters
        ----------
        objectID : int
            The ID of the object to deregister.

        """
        self.objects_history[objectID] = self.objects.pop(objectID)
        self.disappeared.pop(objectID, None)

    def reset_old_objects(self):
        """
        Deregister objects that have been tracked longer than the reset time.

        """
        to_be_removed = []
        current_time = datetime.now()
        for objectID, obj in self.objects.items():
            elapsed_minutes = (current_time - obj['first_update']).total_seconds() / 60
            if elapsed_minutes > self.linear_reset_minutes:
                to_be_removed.append(objectID)

        for objectID in to_be_removed:
            self.deregister(objectID)

    def update_tracker(self, rectangles):
        """
        Update the tracker with the provided rectangles.

        Parameters
        ----------
        rectangles : ndarray
            Array of bounding boxes in the format [startX, startY, endX, endY].

        Returns
        -------
        dict
            Updated tracked objects.

        """
        if self.object_tracking_mode == 0:
            return self.update_linear(rectangles)
        elif self.object_tracking_mode == 1:
            return self.update_sort(rectangles)
        elif self.object_tracking_mode == 2:
            print("WARNING - TRACKING MODE 2 SHOULD BE USED ONLY FOR DEBUGGING PURPOSES")
            linear_results = self.update_linear(rectangles)
            sort_results = self.update_sort(rectangles)
            return {**linear_results, **sort_results}
        else:
            raise NotImplementedError(f"Tracking mode {self.object_tracking_mode} not implemented")

    def update_sort(self, rectangles):
        """
        Update the tracker using SORT algorithm.

        Parameters
        ----------
        rectangles : ndarray
            Array of bounding boxes in the format [startX, startY, endX, endY].

        Returns
        -------
        dict
            Tracked objects using SORT algorithm.

        """
        self._maybe_init()

        if len(rectangles) > 0:
            rectangles_with_confidence = np.hstack((rectangles, np.ones((rectangles.shape[0], 1))))
        else:
            rectangles_with_confidence = np.empty((0, 5))

        tracked_objects = self.sort_tracker.update(rectangles_with_confidence)
        result = {
            f"{int(track[4])}_SORT": {
                'rectangle': [track[0], track[1], track[2], track[3]],
                'color': ct.DARK_GREEN
            } for track in tracked_objects
        }
        return result

    def update_linear(self, rectangles):
        """
        Update the tracker using linear centroid-based tracking.

        Parameters
        ----------
        rectangles : ndarray
            Array of bounding boxes in the format [startX, startY, endX, endY].

        Returns
        -------
        dict
            Updated tracked objects.

        """
        self.reset_old_objects()

        if len(rectangles) == 0:
            # No new rectangles, increase disappearance counter
            for objectID in list(self.disappeared.keys()):
                self.disappeared[objectID] += 1
                if self.disappeared[objectID] > self.linear_max_age:
                    self.deregister(objectID)
            return self.objects.copy()

        # Compute centroids
        inputCentroids = np.zeros((len(rectangles), 2), dtype=int)
        for i, (startX, startY, endX, endY) in enumerate(rectangles):
            cX = int((startX + endX) / 2.0)
            cY = int((startY + endY) / 2.0)
            inputCentroids[i] = (cX, cY)

        if len(self.objects) == 0:
            # No existing objects, register all input centroids
            for i in range(len(inputCentroids)):
                self.register(inputCentroids[i], rectangles[i])
            return self.objects.copy()

        # Existing objects, attempt to match
        objectIDs = list(self.objects.keys())
        objectCentroids = np.array([obj['centroid'] for obj in self.objects.values()])
        objectRectangles = np.array([obj['rectangle'] for obj in self.objects.values()])

        # Compute distances
        # Size differences
        objects_Hs = objectRectangles[:, 2] - objectRectangles[:, 0]
        inputs_Hs = rectangles[:, 2] - rectangles[:, 0]
        h_D = np.abs(objects_Hs[:, np.newaxis] - inputs_Hs[np.newaxis, :])

        objects_Ws = objectRectangles[:, 3] - objectRectangles[:, 1]
        inputs_Ws = rectangles[:, 3] - rectangles[:, 1]
        w_D = np.abs(objects_Ws[:, np.newaxis] - inputs_Ws[np.newaxis, :])

        hw_D = np.minimum(h_D, w_D)
        centroid_D = dist.cdist(objectCentroids, inputCentroids)

        total_weight = self.hw_dist_weight + self.center_dist_weight
        D = (hw_D * self.hw_dist_weight + centroid_D * self.center_dist_weight) / total_weight

        usedRows = set()
        usedCols = set()
        for row in D.min(axis=1).argsort():
            col = D.argmin(axis=1)[row]
            if row in usedRows or col in usedCols:
                continue
            if D[row, col] > self.linear_max_distance:
                continue
            if centroid_D[row, col] > self.linear_max_distance_center:
                continue
            if hw_D[row, col] > self.linear_max_distance_hw:
                continue

            max_size = max(
                objectRectangles[row][2] - objectRectangles[row][0],
                objectRectangles[row][3] - objectRectangles[row][1],
                rectangles[col][2] - rectangles[col][0],
                rectangles[col][3] - rectangles[col][1]
            )
            if centroid_D[row, col] / max_size > self.linear_max_relative_distance:
                continue

            objectID = objectIDs[row]
            self.objects[objectID]['centroid_history'].append(inputCentroids[col])
            self.objects[objectID]['last_update'] = datetime.now()
            self.objects[objectID]['appearances'] = len(self.objects[objectID]['centroid_history'])
            self.objects[objectID]['centroid'] = inputCentroids[col]
            self.objects[objectID]['rectangle'] = list(rectangles[col][:4])
            self.disappeared[objectID] = 0

            usedRows.add(row)
            usedCols.add(col)

        unusedRows = set(range(D.shape[0])) - usedRows
        unusedCols = set(range(D.shape[1])) - usedCols

        # Increase disappearance counter for unused rows (objects)
        for row in unusedRows:
            objectID = objectIDs[row]
            self.disappeared[objectID] += 1
            if self.disappeared[objectID] > self.linear_max_age:
                self.deregister(objectID)

        # Register new objects for unused columns (input detections)
        for col in unusedCols:
            self.register(inputCentroids[col], rectangles[col])

        # Return copy of objects to avoid side-effects
        return self.objects.copy()

    def get_object_appearances(self, object_id):
        """
        Get the number of appearances of an object.

        Parameters
        ----------
        object_id : int
            The ID of the object.

        Returns
        -------
        int
            Number of appearances.

        """
        if object_id in self.objects:
            return self.objects[object_id]['appearances']
        elif object_id in self.objects_history:
            return self.objects_history[object_id]['appearances']
        else:
            return 0

    def get_in_zone_history_deque(self, object_id):
        """
        Get the in-zone history deque of an object.

        Parameters
        ----------
        object_id : int
            The ID of the object.

        Returns
        -------
        deque
            In-zone history deque.

        """
        if object_id in self.objects:
            return self.objects[object_id]['in_zone_history']
        elif object_id in self.objects_history:
            return self.objects_history[object_id]['in_zone_history']
        else:
            return deque()

    def get_in_zone_history(self, object_id):
        """
        Get the in-zone history list of an object.

        Parameters
        ----------
        object_id : int
            The ID of the object.

        Returns
        -------
        list
            In-zone history list.

        """
        return list(self.get_in_zone_history_deque(object_id))

    def get_in_zone_total_seconds(self, object_id):
        """
        Get the total seconds an object has been in the zone, excluding any ongoing intervals.

        Parameters
        ----------
        object_id : int
            The ID of the object.

        Returns
        -------
        int
            Total seconds in zone.

        """
        if object_id in self.objects:
            return self.objects[object_id]['in_zone_total_seconds']
        elif object_id in self.objects_history:
            return self.objects_history[object_id]['in_zone_total_seconds']
        else:
            return 0

    def set_in_zone_total_seconds(self, object_id, value):
        """
        Set the total seconds an object has been in the zone.

        Parameters
        ----------
        object_id : int
            The ID of the object.
        value : int
            Total seconds to set.

        """
        if object_id in self.objects:
            self.objects[object_id]['in_zone_total_seconds'] = value
        elif object_id in self.objects_history:
            self.objects_history[object_id]['in_zone_total_seconds'] = value

    def get_in_zone_total_seconds_additional(self, object_id):
        """
        Get the total seconds an object has been in the zone, including ongoing intervals.

        Parameters
        ----------
        object_id : int
            The ID of the object.

        Returns
        -------
        int
            Total seconds in zone including ongoing intervals.

        """
        total_seconds = self.get_in_zone_total_seconds(object_id)
        in_zone_history = self.get_in_zone_history(object_id)
        if in_zone_history and len(in_zone_history[-1]) == 1:
            # Last interval is open, include current duration
            additional_seconds = (datetime.now() - in_zone_history[-1][0]).total_seconds()
            total_seconds += int(additional_seconds)
        return total_seconds

    def maybe_close_in_zone_interval(self, object_id):
        """
        Close the in-zone interval if it is still open.

        Parameters
        ----------
        object_id : int
            The ID of the object.

        """
        history_deque = self.get_in_zone_history_deque(object_id)
        if history_deque and len(history_deque[-1]) == 1:
            last_start = history_deque[-1][0]
            now = datetime.now()
            duration = (now - last_start).total_seconds()
            current_total = self.get_in_zone_total_seconds(object_id)
            self.set_in_zone_total_seconds(object_id, current_total + int(duration))
            history_deque[-1].append(now)

    def update_in_zone_history(self, in_zone_objects):
        """
        Update the in-zone history for the provided objects.

        Parameters
        ----------
        in_zone_objects : list of dict
            List of objects currently in the zone.

        """
        current_object_ids = set()
        for obj in in_zone_objects:
            track_id = obj[ct.TRACK_ID]
            current_object_ids.add(track_id)
            history_deque = self.get_in_zone_history_deque(track_id)
            if not history_deque or len(history_deque[-1]) == 2:
                # Start a new interval
                history_deque.append([datetime.now()])

        # For objects not in zone anymore, close the interval if open
        for object_id in self.objects.keys():
            if object_id not in current_object_ids:
                self.maybe_close_in_zone_interval(object_id)

    def get_object_history(self, object_id):
        """
        Get the centroid history of an object.

        Parameters
        ----------
        object_id : int
            The ID of the object.

        Returns
        -------
        list
            List of centroids.

        """
        if object_id in self.objects:
            return list(self.objects[object_id]['centroid_history'])
        elif object_id in self.objects_history:
            return list(self.objects_history[object_id]['centroid_history'])
        else:
            return []

    def get_object_max_movement(self, object_id, steps=None, method='l2'):
        """
        Get the maximum distance the object has moved from its original position.

        Parameters
        ----------
        object_id : int
            The ID of the object.
        steps : int or None, optional
            Number of recent steps to consider. If None, consider all history.
        method : str, optional
            Distance metric to use ('l1' or 'l2').

        Returns
        -------
        float
            Maximum movement distance.

        """
        centroids = self.get_object_history(object_id)
        if not centroids:
            return 0.0

        if steps is not None and isinstance(steps, int):
            centroids = centroids[-steps:]
            original_position = np.array(centroids[0])
        else:
            original_position = np.array(self.get_original_position(object_id))

        centroids_array = np.array(centroids)
        if method == 'l1':
            distances = np.sum(np.abs(centroids_array - original_position), axis=1)
        else:  # Default to 'l2'
            distances = np.linalg.norm(centroids_array - original_position, axis=1)

        return np.max(distances)

    def get_object_type_history(self, object_id):
        """
        Get the type history summary of an object.

        Parameters
        ----------
        object_id : int
            The ID of the object.

        Returns
        -------
        dict
            Type history summary.

        """
        if object_id in self.objects:
            return self.objects[object_id]['type_history']
        elif object_id in self.objects_history:
            return self.objects_history[object_id]['type_history']
        else:
            return {'total': 0}

    def get_object_type_history_deque(self, object_id):
        """
        Get the type history deque of an object.

        Parameters
        ----------
        object_id : int
            The ID of the object.

        Returns
        -------
        deque
            Type history deque.

        """
        if object_id in self.objects:
            return self.objects[object_id]['type_history_deque']
        elif object_id in self.objects_history:
            return self.objects_history[object_id]['type_history_deque']
        else:
            return deque()

    def add_to_type_history(self, inferences):
        """
        Update the type history with new inferences.

        Parameters
        ----------
        inferences : list of dict
            List of inferences containing object IDs and types.

        """
        for inference in inferences:
            track_id = inference[ct.TRACK_ID]
            obj_type = inference[ct.TYPE]
            type_history_deque = self.get_object_type_history_deque(track_id)
            type_history_deque.append(obj_type)
            type_history = self.get_object_type_history(track_id)
            type_history['total'] += 1
            type_history[obj_type] = type_history.get(obj_type, 0) + 1

    def get_most_seen_type(self, object_id):
        """
        Get the most frequently observed type for an object.

        Parameters
        ----------
        object_id : int
            The ID of the object.

        Returns
        -------
        str
            The most frequently observed type.

        """
        type_history = self.get_object_type_history(object_id)
        if not type_history or type_history['total'] == 0:
            return ''
        return max(
            (key for key in type_history if key != 'total'),
            key=lambda k: type_history[k],
            default=''
        )

    def get_class_count(self, object_id, class_name, return_complement=False, return_ratio=False):
        """
        Get the count or ratio of times an object was of a specified class.

        Parameters
        ----------
        object_id : int
            The ID of the object.
        class_name : str or list of str
            The class name(s) to check.
        return_complement : bool, optional
            If True, return count/ratio of times the object was not of the specified class.
        return_ratio : bool, optional
            If True, return the ratio instead of the count.

        Returns
        -------
        int or float
            The count or ratio.

        """
        type_history = self.get_object_type_history(object_id)
        total = type_history.get('total', 0)
        if total == 0:
            return 0.0 if return_ratio else 0

        if isinstance(class_name, list):
            count = sum(type_history.get(name, 0) for name in class_name)
        else:
            count = type_history.get(class_name, 0)

        if return_complement:
            count = total - count

        if return_ratio:
            return count / total
        else:
            return count

    def get_non_class_count(self, object_id, class_name):
        """
        Get the count of times an object was not of a specified class.

        Parameters
        ----------
        object_id : int
            The ID of the object.
        class_name : str or list of str
            The class name(s) to check.

        Returns
        -------
        int
            The count.

        """
        return self.get_class_count(object_id, class_name, return_complement=True)

    def get_class_ratio(self, object_id, class_name):
        """
        Get the ratio of times an object was of a specified class.

        Parameters
        ----------
        object_id : int
            The ID of the object.
        class_name : str or list of str
            The class name(s) to check.

        Returns
        -------
        float
            The ratio.

        """
        return self.get_class_count(object_id, class_name, return_ratio=True)

    def get_original_position(self, object_id):
        """
        Get the original centroid position of an object.

        Parameters
        ----------
        object_id : int
            The ID of the object.

        Returns
        -------
        ndarray
            The original centroid position.

        """
        if object_id in self.objects:
            return self.objects[object_id]['original_position']
        elif object_id in self.objects_history:
            return self.objects_history[object_id]['original_position']
        else:
            return np.array([0, 0])

    def get_last_rectangle(self, object_id):
        """
        Get the last known rectangle of an object.

        Parameters
        ----------
        object_id : int
            The ID of the object.

        Returns
        -------
        list
            The last rectangle in the format [startX, startY, endX, endY].

        """
        if object_id in self.objects:
            return self.objects[object_id]['rectangle']
        elif object_id in self.objects_history:
            return self.objects_history[object_id]['rectangle']
        else:
            return [0, 0, 0, 0]


if __name__ == "__main__":

  eng = CentroidObjectTracker()
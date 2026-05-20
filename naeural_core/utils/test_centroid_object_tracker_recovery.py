"""
Regression tests for linear tracker recovery on plate-like approach motion.

These tests load ``centroid_object_tracker.py`` directly with lightweight stubs
for the broader runtime modules. The tracker itself still exercises real NumPy
and SciPy distance calculations, which are the relevant dependencies for the
linear assignment behavior under test.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path

import numpy as np


B31CSL_APPROACH_RECTS = [
  [199, 406, 208, 435],
  [207, 441, 218, 478],
  [221, 497, 234, 546],
  [255, 626, 275, 694],
  [295, 829, 324, 899],
  [339, 1017, 376, 1066],
]


def _install_tracker_import_stubs():
  """Install the minimum modules needed to load the tracker file directly."""
  naeural_core_mod = types.ModuleType("naeural_core")
  constants_mod = types.ModuleType("naeural_core.constants")
  constants_mod.RED = "red"
  constants_mod.DARK_GREEN = "dark_green"

  sort_mod = types.ModuleType("naeural_core.utils.sort")

  class _SortStub:
    """Minimal SORT stub; these tests exercise only linear tracking."""

    def __init__(self, *args, **kwargs):
      _ = args, kwargs

    def update(self, rectangles):
      _ = rectangles
      return []

  sort_mod.Sort = _SortStub

  utils_mod = types.ModuleType("naeural_core.utils")
  decentra_mod = types.ModuleType("decentra_vision")
  geometry_mod = types.ModuleType("decentra_vision.geometry_methods")
  decentra_mod.geometry_methods = geometry_mod

  sys.modules["naeural_core"] = naeural_core_mod
  sys.modules["naeural_core.constants"] = constants_mod
  sys.modules["naeural_core.utils"] = utils_mod
  sys.modules["naeural_core.utils.sort"] = sort_mod
  sys.modules["decentra_vision"] = decentra_mod
  sys.modules["decentra_vision.geometry_methods"] = geometry_mod
  naeural_core_mod.constants = constants_mod
  naeural_core_mod.utils = utils_mod


def _load_tracker_class():
  """Load ``CentroidObjectTracker`` without importing the full runtime."""
  _install_tracker_import_stubs()
  tracker_path = Path(__file__).with_name("centroid_object_tracker.py")
  spec = importlib.util.spec_from_file_location(
    "_stage2_centroid_object_tracker",
    tracker_path,
  )
  module = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = module
  spec.loader.exec_module(module)
  return module.CentroidObjectTracker


class TestCentroidObjectTrackerRecovery(unittest.TestCase):
  """Regression coverage for bounded linear recovery behavior."""

  def _new_tracker(self, **kwargs):
    tracker_cls = _load_tracker_class()
    base_kwargs = {
      "object_tracking_mode": 0,
      "linear_max_age": 4,
      "linear_max_distance": 240,
      "linear_max_relative_distance": 1.2,
      "max_dist_scale": 1.4,
      "center_dist_weight": 1,
      "hw_dist_weight": 0.8,
    }
    base_kwargs.update(kwargs)
    return tracker_cls(**base_kwargs)

  def _update_one(self, tracker, rectangle):
    result = tracker.update_tracker(np.array([rectangle], dtype=float))
    matches = [
      object_id
      for object_id, object_info in result.items()
      if list(object_info["rectangle"]) == list(rectangle)
    ]
    self.assertEqual(matches, list(matches[:1]))
    self.assertEqual(len(matches), 1)
    return matches[0]

  def _track_ids_for_sequence(self, tracker, rectangles):
    return [self._update_one(tracker, rectangle) for rectangle in rectangles]

  def test_default_linear_tracker_still_fragments_logged_plate_sequence(self):
    tracker = self._new_tracker()

    ids = self._track_ids_for_sequence(tracker, B31CSL_APPROACH_RECTS)

    self.assertGreater(len(set(ids)), 1)

  def test_recovery_keeps_logged_plate_sequence_on_one_track(self):
    tracker = self._new_tracker(
      linear_recovery_enabled=True,
      linear_recovery_max_age=2,
      linear_recovery_max_relative_dist=3.0,
      linear_recovery_center_scale=1.25,
    )

    ids = self._track_ids_for_sequence(tracker, B31CSL_APPROACH_RECTS)

    self.assertEqual(len(set(ids)), 1)

  def test_recovery_requires_velocity_consistent_prediction(self):
    tracker = self._new_tracker(
      linear_recovery_enabled=True,
      linear_recovery_max_age=2,
      linear_recovery_max_relative_dist=3.0,
      linear_recovery_center_scale=1.25,
    )
    first_id = self._update_one(tracker, [40, 40, 190, 190])
    self._update_one(tracker, [220, 40, 370, 190])

    next_id = self._update_one(tracker, [0, 0, 150, 150])

    self.assertNotEqual(first_id, next_id)

  def test_recovery_does_not_merge_two_detections_in_one_frame(self):
    tracker = self._new_tracker(
      linear_recovery_enabled=True,
      linear_recovery_max_age=2,
      linear_recovery_max_relative_dist=3.0,
      linear_recovery_center_scale=1.25,
    )
    first_frame = np.array([
      [100, 100, 130, 130],
      [260, 100, 290, 130],
    ], dtype=float)
    second_frame = np.array([
      [135, 100, 165, 130],
      [295, 100, 325, 130],
    ], dtype=float)

    first_result = tracker.update_tracker(first_frame)
    second_result = tracker.update_tracker(second_frame)

    self.assertEqual(len(first_result), 2)
    self.assertEqual(len(second_result), 2)
    self.assertEqual(set(first_result.keys()), set(second_result.keys()))

  def test_recovery_does_not_resurrect_beyond_recovery_age(self):
    tracker = self._new_tracker(
      linear_recovery_enabled=True,
      linear_recovery_max_age=1,
      linear_recovery_max_relative_dist=3.0,
      linear_recovery_center_scale=1.25,
    )
    first_id = self._update_one(tracker, [100, 100, 130, 130])
    self._update_one(tracker, [130, 100, 160, 130])
    tracker.update_tracker(np.empty((0, 4), dtype=float))
    tracker.update_tracker(np.empty((0, 4), dtype=float))

    next_id = self._update_one(tracker, [220, 100, 250, 130])

    self.assertNotEqual(first_id, next_id)

  def test_recovery_keeps_incompatible_geometry_as_new_track(self):
    tracker = self._new_tracker(
      linear_recovery_enabled=True,
      linear_recovery_max_age=2,
      linear_recovery_max_relative_dist=3.0,
      linear_recovery_center_scale=1.25,
    )
    first_id = self._update_one(tracker, [100, 100, 130, 130])
    self._update_one(tracker, [130, 100, 160, 130])

    next_id = self._update_one(tracker, [160, 100, 500, 500])

    self.assertNotEqual(first_id, next_id)


if __name__ == "__main__":
  unittest.main(verbosity=2)

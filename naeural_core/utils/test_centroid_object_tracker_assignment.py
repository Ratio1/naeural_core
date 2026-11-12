"""Linear assignment regressions using normal installed runtime imports.

Run in the controlled E2 image. NumPy, SciPy, SORT and the SDK must be the real
bundled dependencies; missing imports must not be replaced with test stubs.
"""
import copy
from datetime import datetime, timedelta
import sys
import unittest
from unittest.mock import patch

import numpy as np

from naeural_core.utils import centroid_object_tracker as tracker_module
from naeural_core.utils import sort as sort_module
from naeural_core.utils import test_centroid_object_tracker_recovery as recovery


class LinearAssignmentTests(unittest.TestCase):
  """Pin assignment decisions and the once-per-update reduction contract."""

  def setUp(self):
    """Freeze timestamps without replacing any runtime package imports."""
    self.clock = patch.object(tracker_module, 'datetime')
    self.now = self.clock.start()
    self.addCleanup(self.clock.stop)
    self.now.now.return_value = datetime(2026, 1, 1)
    self.tracker = tracker_module.CentroidObjectTracker()

  def count_argmin_calls(self, rectangles):
    """Count real ndarray reductions during one tracker update.

    Parameters
    ----------
    rectangles : ndarray
        Synthetic input frame passed unchanged to the installed tracker.

    Returns
    -------
    int
        Number of ndarray ``argmin`` C calls on this thread. This structural
        assertion deliberately does not depend on wall time or GIL scheduling.
    """
    calls = 0

    def observe(frame, event, function):
      """Count ndarray argmin calls without inspecting frame-local data."""
      nonlocal calls
      if (event == 'c_call' and getattr(function, '__name__', '') == 'argmin'
          and isinstance(getattr(function, '__self__', None), np.ndarray)):
        calls += 1

    previous = sys.getprofile()
    sys.setprofile(observe)
    try:
      self.tracker.update_tracker(rectangles)
    finally:
      sys.setprofile(previous)
    return calls

  def test_argmin_once_for_nonempty_assignment_and_never_for_early_returns(self):
    """Keep the structural reduction bound independently of timing noise."""
    self.assertEqual(self.count_argmin_calls(np.empty((0, 4))), 0)
    frame = np.array([[i * 30, 0, i * 30 + 10, 10] for i in range(32)])
    self.assertEqual(self.count_argmin_calls(frame), 0)  # Registration only.
    self.assertEqual(self.count_argmin_calls(frame + 1), 1)
    self.assertEqual(self.count_argmin_calls(np.empty((0, 4))), 0)

  def test_more_detections_preserves_first_tie_and_used_column_conflict(self):
    """A 2-by-3 matrix keeps the first column tie, without trying a runner-up."""
    first = np.array([[0, 0, 10, 10], [30, 0, 40, 10]], dtype=float)
    # Columns 0 and 1 have equal centroids and equal minimum side differences,
    # but distinct rectangles make the chosen first-column tie observable.
    frame = np.array([[10, 0, 20, 10], [9, 0, 21, 10], [100, 0, 110, 10]])
    self.tracker.update_tracker(first)
    before = frame.copy()
    result = self.tracker.update_tracker(frame)
    self.assertEqual(list(result), [0, 1, 2, 3])
    self.assertEqual(result[0]['rectangle'], frame[0].tolist())
    self.assertEqual(result[1]['rectangle'], first[1].tolist())
    self.assertEqual(result[2]['rectangle'], frame[1].tolist())
    self.assertEqual(result[3]['rectangle'], frame[2].tolist())
    self.assertEqual(dict(self.tracker.disappeared), {0: 0, 1: 1, 2: 0, 3: 0})
    self.assertEqual(len(result[0]['centroid_history']), 2)
    np.testing.assert_array_equal(frame, before)

  def test_more_tracks_preserves_nearest_row_priority(self):
    """A 3-by-1 matrix must update the nearest row, not insertion-order row 0."""
    first = np.array([[0, 0, 10, 10], [30, 0, 40, 10], [60, 0, 70, 10]])
    frame = np.array([[61, 0, 71, 10]])
    self.tracker.update_tracker(first)
    result = self.tracker.update_tracker(frame)
    self.assertEqual(list(result), [0, 1, 2])
    self.assertEqual(result[2]['rectangle'], frame[0].tolist())
    self.assertEqual(dict(self.tracker.disappeared), {0: 1, 1: 1, 2: 0})
    self.assertEqual(len(result[0]['centroid_history']), 1)
    self.assertEqual(len(result[2]['centroid_history']), 2)

  def test_columns_are_recomputed_after_detection_order_changes(self):
    """Never retain nearest-column indices across frames or tracker state."""
    first = np.array([[0, 0, 10, 10], [100, 0, 110, 10]])
    self.tracker.update_tracker(first)
    self.tracker.update_tracker((first + 1)[::-1])
    self.assertEqual(self.tracker.objects[0]['rectangle'], (first[0] + 1).tolist())
    self.assertEqual(self.tracker.objects[1]['rectangle'], (first[1] + 1).tolist())
    self.tracker.update_tracker(first + 2)
    self.assertEqual(self.tracker.objects[0]['rectangle'], (first[0] + 2).tolist())
    self.assertEqual(self.tracker.objects[1]['rectangle'], (first[1] + 2).tolist())
    self.assertEqual(self.tracker.nextObjectID, 2)

  def test_relative_distance_boundary_and_rejection(self):
    """Preserve the inclusive normal gate and registration after rejection."""
    for shift, accepted in ((12, True), (13, False)):
      with self.subTest(shift=shift):
        tracker = tracker_module.CentroidObjectTracker()
        tracker.update_tracker(np.array([[0, 0, 10, 10]]))
        result = tracker.update_tracker(np.array([[shift, 0, shift + 10, 10]]))
        self.assertEqual(list(result), [0] if accepted else [0, 1])
        self.assertEqual(tracker.disappeared[0], 0 if accepted else 1)
        self.assertEqual(len(result[0]['centroid_history']), 2 if accepted else 1)

  def test_missing_frames_deregister_to_history_without_reductions(self):
    """Keep disappearance age, history and the empty-input early return."""
    self.tracker.linear_max_age = 1
    self.tracker.update_tracker(np.array([[0, 0, 10, 10]]))
    saved = copy.deepcopy(self.tracker.objects[0])
    self.assertEqual(self.count_argmin_calls(np.empty((0, 4))), 0)
    self.assertEqual(self.tracker.disappeared[0], 1)
    self.assertEqual(self.count_argmin_calls(np.empty((0, 4))), 0)
    self.assertEqual(dict(self.tracker.objects), {})
    self.assertEqual(dict(self.tracker.disappeared), {})
    self.assertEqual(self.tracker.objects_history[0]['rectangle'], saved['rectangle'])
    np.testing.assert_array_equal(self.tracker.objects_history[0]['centroid'], saved['centroid'])

  def test_expired_tracks_are_reset_before_assignment(self):
    """Preserve age reset and the resulting registration-only early return."""
    self.tracker.update_tracker(np.array([[0, 0, 10, 10]]))
    self.now.now.return_value += timedelta(minutes=61)
    self.assertEqual(self.count_argmin_calls(np.array([[1, 0, 11, 10]])), 0)
    self.assertEqual(list(self.tracker.objects), [1])
    self.assertEqual(list(self.tracker.objects_history), [0])


class LinearRecoveryRuntimeTests(recovery.TestCentroidObjectTrackerRecovery):
  """Reuse recovery policy cases with the real normally imported tracker.

  Notes
  -----
  The older standalone suite installs import stubs in its loader. Replace only
  that loader for these inherited cases; it must never execute here or change
  ``sys.modules``. Production NumPy, SciPy, SORT and constants remain intact.
  """

  def setUp(self):
    """Route existing recovery fixtures through the installed tracker class."""
    loader = patch.object(recovery, '_load_tracker_class',
                          return_value=tracker_module.CentroidObjectTracker)
    loader.start()
    self.addCleanup(loader.stop)
    self.assertIs(tracker_module.Sort, sort_module.Sort)

  def tearDown(self):
    """Ensure inherited tests did not swap real runtime imports for stubs."""
    self.assertIs(sys.modules['naeural_core.utils.sort'], sort_module)
    self.assertIs(tracker_module.Sort, sort_module.Sort)


if __name__ == '__main__':
  unittest.main(verbosity=2)

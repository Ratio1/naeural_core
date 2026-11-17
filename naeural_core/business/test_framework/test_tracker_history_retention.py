"""Retired-history count bounds using normal installed runtime imports.

Run in the controlled E2 image with its real SDK, NumPy, SciPy and SORT. These
synthetic fixtures start no node or external service and replace no imports.
"""

import pickle
import unittest
import weakref
from collections import deque
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from naeural_core import constants as ct
from naeural_core.business.base.cv_plugin_executor import CVPluginExecutor
from naeural_core.business.mixins_base.multi_class_tracker_mixin import _MultiClassTrackerMixin
from naeural_core.local_libraries.config_handler_mixin import _ConfigHandlerMixin
from naeural_core.utils import centroid_object_tracker as tracker_module


class TrackingConfigHarness(_MultiClassTrackerMixin, _ConfigHandlerMixin):
  """Exercise real CV defaults, config properties and tracker construction.

  Notes
  -----
  Only the plugin's image/timer/engine boundary is synthetic. No plugin thread
  or serving process is required to validate the tracking config path.
  """

  def __init__(self, cap=0):
    """Initialize CV config handlers with a chosen archive cap.

    Parameters
    ----------
    cap : object, optional
        Candidate value passed unchanged through the runtime config handlers.
    """
    super().__init__()
    self.config_data = dict(CVPluginExecutor.CONFIG, TRACKING_HISTORY_MAX_ENTRIES=cap,
                            LINEAR_MAX_AGE=0)
    self.const = ct
    self._type_to_meta_type_map = {}
    self.start_timer = self.end_timer = lambda name: None
    self._get_detector_ai_engines = lambda: ['detector']
    self.create_config_handlers()


class TrackerHistoryRetentionTests(unittest.TestCase):
  """Pin retirement order, active isolation and existing getter contracts."""

  def setUp(self):
    """Freeze the real tracker clock so active-state comparisons are exact."""
    clock = patch.object(tracker_module, 'datetime')
    self.clock = clock.start()
    self.addCleanup(clock.stop)
    self.clock.now.return_value = datetime(2026, 1, 1)

  def test_default_and_explicit_zero_keep_all_retired_records(self):
    """Omitted and zero settings preserve unlimited archive retention."""
    for options in ({}, {'tracking_history_max_entries': 0}):
      with self.subTest(options=options):
        tracker = tracker_module.CentroidObjectTracker(linear_max_age=0, **options)
        for track_id in range(128):
          tracker.update_tracker(np.array([[0, 0, 10, 10]]))
          tracker.update_tracker(np.empty((0, 4)))
        self.assertEqual(list(tracker.objects_history), list(range(128)))
        self.assertEqual(tracker.nextObjectID, 128)
        self.assertFalse(tracker.objects)
        self.assertFalse(tracker.disappeared)
        self.assertEqual(len(tracker.get_object_history(0)), 1)

  def test_cap_evicts_retirement_order_not_id_or_read_order(self):
    """Archive reads never promote records; active entries are independent."""
    tracker = tracker_module.CentroidObjectTracker(tracking_history_max_entries=2)
    for index in range(5):
      tracker.register(np.array([index, index]), [index, index, index + 10, index + 10])
    active_record = tracker.objects[4]
    for track_id in (2, 0):
      tracker.deregister(track_id)
    self.assertEqual(list(tracker.objects_history), [2, 0])
    tracker.get_object_history(2)
    tracker.deregister(3)
    self.assertEqual(list(tracker.objects_history), [0, 3])
    tracker.deregister(1)
    self.assertEqual(list(tracker.objects_history), [3, 1])
    self.assertEqual(list(tracker.objects), [4])
    self.assertIs(tracker.objects[4], active_record)
    self.assertEqual(dict(tracker.disappeared), {4: 0})
    self.assertEqual(tracker.nextObjectID, 5)
    with self.assertRaises(KeyError):
      tracker.deregister(0)
    self.assertEqual(list(tracker.objects_history), [3, 1])

  def test_repeated_expiry_reset_preserves_ids_and_bounds_archive(self):
    """Age resets use the same cap and never recycle object identifiers."""
    tracker = tracker_module.CentroidObjectTracker(tracking_history_max_entries=3,
                                                   linear_reset_minutes=1)
    frame = np.array([[0, 0, 10, 10], [100, 0, 110, 10]])
    for generation in range(40):
      tracker.update_tracker(frame)
      self.assertEqual(list(tracker.objects), [generation * 2, generation * 2 + 1])
      self.assertEqual(len(tracker.objects_history), min(3, generation * 2))
      self.clock.now.return_value += timedelta(minutes=2)
    self.assertEqual(list(tracker.objects_history), [75, 76, 77])
    self.assertEqual(tracker.nextObjectID, 80)

  def test_cap_changes_no_active_assignment_or_disappearance_state(self):
    """Compare bounded and legacy trackers over movement, misses and resets."""
    legacy = tracker_module.CentroidObjectTracker(linear_max_age=1, linear_reset_minutes=1)
    bounded = tracker_module.CentroidObjectTracker(linear_max_age=1, linear_reset_minutes=1,
                                                   tracking_history_max_entries=2)
    rng = np.random.default_rng(23)
    base = np.array([[0, 0, 10, 10], [100, 0, 110, 10], [200, 0, 210, 10]])
    for frame_index in range(60):
      frame = (base + frame_index % 3)[rng.random(3) > .3]
      self.clock.now.return_value += timedelta(seconds=5)
      expected, actual = legacy.update_tracker(frame), bounded.update_tracker(frame)
      self.assertEqual(pickle.dumps(actual), pickle.dumps(expected))
      self.assertEqual(dict(bounded.disappeared), dict(legacy.disappeared))
      self.assertEqual(bounded.nextObjectID, legacy.nextObjectID)
      self.assertLessEqual(len(bounded.objects_history), 2)
    self.assertGreater(len(legacy.objects_history), len(bounded.objects_history))

  def test_evicted_getters_preserve_existing_missing_id_defaults(self):
    """No getter invents retained data or raises a new error after eviction."""
    tracker = tracker_module.CentroidObjectTracker(tracking_history_max_entries=1)
    for index in range(2):
      tracker.register(np.array([index, index]), [index, index, index + 10, index + 10])
      tracker.add_to_type_history([{ct.TRACK_ID: index, ct.TYPE: 'person'}])
      tracker.update_in_zone_history([{ct.TRACK_ID: index}])
      tracker.set_in_zone_total_seconds(index, 9)
      tracker.deregister(index)
    self.assertEqual(tracker.get_object_history(0), [])
    self.assertEqual(tracker.get_object_appearances(0), 0)
    self.assertEqual(tracker.get_in_zone_history_deque(0), deque())
    self.assertEqual(tracker.get_in_zone_history(0), [])
    self.assertEqual(tracker.get_in_zone_total_seconds(0), 0)
    self.assertEqual(tracker.get_in_zone_total_seconds_additional(0), 0)
    self.assertEqual(tracker.get_object_max_movement(0), 0.0)
    self.assertEqual(tracker.get_object_type_history(0), {'total': 0})
    self.assertEqual(tracker.get_object_type_history_deque(0), deque())
    self.assertEqual(tracker.get_most_seen_type(0), '')
    self.assertEqual(tracker.get_class_count(0, 'person'), 0)
    self.assertEqual(tracker.get_non_class_count(0, 'person'), 0)
    self.assertEqual(tracker.get_class_ratio(0, 'person'), 0.0)
    np.testing.assert_array_equal(tracker.get_original_position(0), [0, 0])
    self.assertEqual(tracker.get_last_rectangle(0), [0, 0, 0, 0])
    tracker.set_in_zone_total_seconds(0, 100)
    tracker.maybe_close_in_zone_interval(0)
    self.assertEqual(tracker.get_in_zone_total_seconds(0), 0)
    self.assertEqual(tracker.get_object_type_history(1), {'total': 1, 'person': 1})
    self.assertEqual(tracker.get_in_zone_total_seconds(1), 9)
    self.assertEqual(len(tracker.get_object_history(1)), 1)

  def test_churn_releases_evicted_records_without_retaining_tombstones(self):
    """Count reachable records and verify their unique arrays are released."""
    tracker = tracker_module.CentroidObjectTracker(tracking_history_max_entries=4,
                                                   linear_max_age=0)
    first_centroids = []
    for index in range(256):
      tracker.update_tracker(np.array([[index, 0, index + 10, 10]]))
      if index < 4:
        first_centroids.append(weakref.ref(tracker.objects[index]['centroid']))
      tracker.update_tracker(np.empty((0, 4)))
      self.assertLessEqual(len(tracker.objects_history), 4)
    self.assertEqual(list(tracker.objects_history), [252, 253, 254, 255])
    self.assertTrue(all(reference() is None for reference in first_centroids))
    self.assertFalse(tracker.objects)
    self.assertFalse(tracker.disappeared)
    self.assertEqual(tracker.nextObjectID, 256)

  def test_invalid_caps_fail_without_coercion(self):
    """Reject booleans and invalid types rather than silently losing history."""
    for value in (True, False, -1, 1.0, 1.5, '1', None, [], np.int64(1)):
      with self.subTest(value=value):
        with self.assertRaisesRegex(ValueError, 'nonnegative non-boolean integer'):
          tracker_module.CentroidObjectTracker(tracking_history_max_entries=value)


class TrackerHistoryConfigTests(unittest.TestCase):
  """Pin CV-to-multiclass config wiring and opt-in consumer prerequisites."""

  def test_old_non_cv_schema_omission_survives_class_descriptor_creation(self):
    """Keep absent options unlimited before/after another instance defines them.

    Notes
    -----
    This real mixin/config-handler consumer does not inherit CVPluginExecutor.
    Both config-handler cache modes install descriptors on its class, so missing
    keys must be distinguished from explicitly supplied invalid values.
    """
    key = 'TRACKING_HISTORY_MAX_ENTRIES'
    for cached in (False, True):
      class OldSchemaHarness(_MultiClassTrackerMixin, _ConfigHandlerMixin):
        """Represent a conforming non-CV tracking consumer with an older schema."""

      self.assertFalse(hasattr(OldSchemaHarness, 'cfg_tracking_history_max_entries'))
      for options in ({}, {key: 1}, {}, {key: None}, {key: True}, {key: False},
                      {key: -1}, {key: '1'}):
        with self.subTest(cached=cached, options=options):
          plugin = OldSchemaHarness()
          plugin.config_data = {
            k: v for k, v in CVPluginExecutor.CONFIG.items()
            if k.startswith(('TRACKING_', 'LINEAR_', 'SORT_')) and k != key
          }
          plugin.config_data.update(options, LINEAR_MAX_AGE=0)
          plugin.log = SimpleNamespace(config_data={'BM_CACHE_CONFIG_HANDLERS': cached})
          plugin.const = ct
          plugin._type_to_meta_type_map = {}
          plugin.start_timer = plugin.end_timer = lambda name: None
          plugin._get_detector_ai_engines = lambda: ['detector']
          plugin.create_config_handlers()
          inferences = {'detector': [[{ct.TYPE: 'person', ct.TLBR_POS: [0, 0, 10, 10]}]]}
          if key in options and (type(options[key]) is not int or options[key] < 0):
            with self.assertRaisesRegex(ValueError, 'nonnegative non-boolean integer'):
              plugin._track_objects(inferences, img_shape=(100, 100, 3))
            self.assertFalse(plugin._object_trackers)
            continue
          for _ in range(3):
            plugin._track_objects(inferences, img_shape=(100, 100, 3))
            plugin._track_objects({'detector': [[]]}, img_shape=(100, 100, 3))
          tracker = plugin._object_trackers['person']
          self.assertEqual(tracker.tracking_history_max_entries, options.get(key, 0))
          self.assertEqual(len(tracker.objects_history), 1 if options else 3)
          self.assertEqual(tracker.nextObjectID, 3)

  def test_default_schema_and_real_integer_validator(self):
    """Use the production schema validator, including its boolean limitation."""
    key = 'TRACKING_HISTORY_MAX_ENTRIES'
    self.assertEqual(CVPluginExecutor.CONFIG[key], 0)
    rules = CVPluginExecutor.CONFIG['VALIDATION_RULES'][key]
    self.assertEqual(rules['TYPE'], 'int')
    self.assertEqual(rules['MIN_VAL'], 0)
    plugin = TrackingConfigHarness()
    for value in (0, 1, 1024, True, False):
      plugin.config_data[key] = value
      self.assertTrue(plugin._cfg_validate_int(key, rules)[0])
    for value in (-1, 1.0, '1', None):
      plugin.config_data[key] = value
      self.assertFalse(plugin._cfg_validate_int(key, rules)[0])
    # Shared integer validation admits bool; the constructor must fail closed
    # instead of treating True as irreversible one-record retention.
    for value in (True, False):
      plugin.config_data[key] = value
      with self.assertRaisesRegex(ValueError, 'non-boolean'):
        plugin._track_objects({'detector': [[{ct.TYPE: 'person', ct.TLBR_POS: [0, 0, 10, 10]}]]},
                              img_shape=(100, 100, 3))
      self.assertFalse(plugin._object_trackers)

  def test_per_class_cap_is_applied_at_construction_only(self):
    """Propagate the cap to real trackers without changing existing instances."""
    for cap in (0, 2):
      with self.subTest(cap=cap):
        plugin = TrackingConfigHarness(cap)
        for _ in range(5):
          plugin._track_objects({'detector': [[
            {ct.TYPE: kind, ct.TLBR_POS: [0, 0, 10, 10]} for kind in ('person', 'car')
          ]]}, img_shape=(100, 100, 3))
          plugin._track_objects({'detector': [[]]}, img_shape=(100, 100, 3))
        for tracker in plugin._object_trackers.values():
          self.assertEqual(tracker.tracking_history_max_entries, cap)
          self.assertEqual(len(tracker.objects_history), cap or 5)
          self.assertEqual(tracker.nextObjectID, 5)
        plugin.config_data['TRACKING_HISTORY_MAX_ENTRIES'] = 3
        plugin._track_objects({'detector': [[{ct.TYPE: 'dog', ct.TLBR_POS: [0, 0, 10, 10]}]]},
                              img_shape=(100, 100, 3))
        self.assertEqual(plugin._object_trackers['person'].tracking_history_max_entries, cap)
        self.assertEqual(plugin._object_trackers['dog'].tracking_history_max_entries, 3)

  def test_late_absolute_orientation_retains_existing_missing_history_error(self):
    """Positive caps require auditing delayed orientation/line-crossing calls."""
    plugin = TrackingConfigHarness(1)
    for _ in range(2):
      plugin._track_objects({'detector': [[{ct.TYPE: 'person', ct.TLBR_POS: [0, 0, 10, 10]}]]},
                            img_shape=(100, 100, 3))
      plugin._track_objects({'detector': [[]]}, img_shape=(100, 100, 3))
    self.assertEqual(plugin.trackapi_centroid_history(0, 'person'), [])
    self.assertEqual(plugin._get_current_orientation(0, 'person'), (0, 0))
    with self.assertRaises(IndexError):
      plugin._get_object_absolute_orientation(0, 'person')
    original, last = plugin._get_object_absolute_orientation(1, 'person')
    np.testing.assert_array_equal(original, last)


if __name__ == '__main__':
  unittest.main(verbosity=2)

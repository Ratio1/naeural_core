"""Matcher regressions using the installed core and its real runtime imports.

Run in the controlled E2 image environment. Missing SDK/native dependencies must
fail import rather than being replaced by test-only package or SORT stubs.
"""

import copy
import pickle
import types
import unittest
import warnings
from datetime import datetime, timedelta
from unittest.mock import patch

import numpy as np

from naeural_core import constants as _CT
from naeural_core.business.mixins_base import multi_class_tracker_mixin as _MIXIN
from naeural_core.utils import centroid_object_tracker as _TRACKER
_NOW = datetime(2026, 1, 1)


def _legacy_match(self, inferences, tracks, valid_class):
  """Keep the pre-optimization loop as a behavioral oracle.

  Parameters
  ----------
  self : _MultiClassTrackerMixin
      Harness exposing the production getters.
  inferences : list of dict
      Mutable detections whose partial writes are compared even on failure.
  tracks : dict
      Tracking results in their original insertion order.
  valid_class : str or None
      Required class, resolved only after a rectangle match.
  """
  for inference in inferences:
    tlbr = np.array(self.get_inference_track_tlbr(inference)).astype(np.int32)
    for track_id, info in tracks.items():
      rectangle = np.array(info['rectangle']).astype(np.int32)
      if np.all(tlbr == rectangle) and self._MultiClassTrackerMixin__get_track_class(inference) == valid_class:
        inference[_CT.TRACK_ID] = track_id
        inference[_CT.TRACKING_STARTED_AT] = info['first_update']
        inference[_CT.TRACKING_ALIVE_TIME] = (info['last_update'] - info['first_update']).total_seconds()
        inference[_CT.APPEARANCES] = info['appearances']


def _track(rectangle, age=5):
  """Build ordinary tracker metadata for a synthetic rectangle.

  Parameters
  ----------
  rectangle : array_like
      Coordinates retained without normalization, including adversarial shapes.
  age : int, optional
      Distinct age/appearance values for duplicate-order checks.

  Returns
  -------
  dict
      Tracker-shaped record with deterministic timestamps.
  """
  return dict(rectangle=rectangle, first_update=_NOW,
              last_update=_NOW + timedelta(seconds=age), appearances=age)


class MatcherTests(unittest.TestCase):
  """Pin semantics, exceptional ordering, per-call lifetime and operation cost."""

  def setUp(self):
    """Create a mixin with no running engine or external service dependencies."""
    self.plugin = _MIXIN._MultiClassTrackerMixin()
    self.plugin.const = _CT
    self.base = {'TLBR_POS': [0, 1, 10, 21], 'TYPE': 'person'}

  def assert_parity(self, inferences, tracks, kind='person', warning_action='always'):
    """Compare values, partial writes, exceptions, warnings and tracker state.

    Parameters
    ----------
    inferences : list of dict
        Synthetic detections; deepcopy preserves shared inference aliases.
    tracks : dict
        Synthetic tracker results, which must remain unchanged.
    kind : str or None, optional
        Class passed to both methods.
    warning_action : str, optional
        RuntimeWarning policy, including promotion to exceptions.
    """
    outcomes = []
    for method in (_legacy_match, _MIXIN._MultiClassTrackerMixin._match_tracking_results):
      data, records = copy.deepcopy((inferences, tracks))
      before = pickle.dumps(records)
      error = None
      with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter(warning_action)
        try:
          method(self.plugin, data, records, kind)
        except Exception as exc:
          error = (type(exc), str(exc))
      self.assertEqual(pickle.dumps(records), before)
      outcomes.append((pickle.dumps(data), error,
                       [(item.category, str(item.message)) for item in caught]))
    self.assertEqual(outcomes[0], outcomes[1])

  def test_flat_rectangles_classes_and_aliases(self):
    """Preserve keys, normalization, class precedence and aliased detections."""
    for rectangle in ([0.8, 1.8, 10.8, 21.8], (0, 1, 10, 21),
                      np.array([0, 1, 10, 21], dtype=np.int64)):
      for overrides in ({}, {'META_TYPE': 'car'}, {'TYPE': 'car', 'META_TYPE': 'person'},
                        {'TLBR_POS_TRACK': [9, 9, 9, 9]}, {'TRACK_ID': 'existing'}):
        with self.subTest(rectangle=rectangle, overrides=overrides):
          item = dict(self.base, TLBR_POS=rectangle, **overrides)
          self.assert_parity([item, item, dict(self.base)], {
            'first': _track([0.1, 1.1, 10.1, 21.1]),
            7: _track([0.9, 1.9, 10.9, 21.9], 9),
            'unmatched': {'rectangle': [100, 100, 200, 200]},
          })
    self.assert_parity([{'TLBR_POS': [0, 1, 10, 21]}] * 3,
                       {1: _track([0, 1, 10, 21])}, kind=None)
    self.assert_parity([dict(self.base, TLBR_POS=[-.9, -1.9, 10.9, 21.9])] * 3,
                       {1: _track([-.1, -1.1, 10.1, 21.1])})

  def test_unusual_shapes_retain_broadcast_and_errors(self):
    """Keep NumPy broadcasting, including scalar, nested and incompatible boxes."""
    for rectangle in (1, [1], [[1, 1, 1, 1]], [], [1, 2], [[1], [1], [1], [1]]):
      with self.subTest(rectangle=rectangle):
        ordinary = dict(self.base, TLBR_POS=[1, 1, 1, 1])
        unusual = dict(self.base, TLBR_POS=rectangle)
        self.assert_parity([ordinary, unusual, ordinary], {1: _track([1, 1, 1, 1])})
        self.assert_parity([ordinary] * 3, {1: _track(rectangle)})

  def test_empty_and_getter_failures(self):
    """Do not visit tracks for no detections or skip existing getter failures."""
    self.assert_parity([], {1: {}})
    self.assert_parity([self.base] * 3, {})
    self.assert_parity([{}, self.base], {})
    self.assert_parity([self.base, self.base, {}], {1: _track([0, 1, 10, 21])})
    self.assert_parity([{'TLBR_POS_TRACK': [0, 1, 10, 21]}], {})

  def test_failures_preserve_partial_writes_and_duplicate_order(self):
    """Later bad rectangles cannot preempt earlier successful metadata writes."""
    good = _track([0, 1, 10, 21])
    bad_match = {'rectangle': [0, 1, 10, 21]}
    for records in (
      {1: good, 2: {}}, {1: good, 2: {'rectangle': ['bad']}},
      {1: bad_match, 2: good}, {1: good, 2: bad_match},
      {1: good, 2: _track([9, 9, 9, 9]), 3: {'rectangle': [3, 3, 3, 3]}},
    ):
      with self.subTest(records=records):
        self.assert_parity([self.base, self.base, dict(self.base, TLBR_POS=[3, 3, 3, 3])], records)

  def test_warning_sensitive_casts_repeat_legacy_behavior(self):
    """Retain repeated cast warnings and warning-as-error ordering on fallback."""
    for value in (float('nan'), float('inf'), -float('inf'), 2.0 ** 40,
                  -(2.0 ** 40), 1 + 2j):
      for policy in ('always', 'error'):
        with self.subTest(value=value, policy=policy):
          self.assert_parity([self.base] * 3, {
            1: _track([0, 1, 10, 21]), 2: _track([value, 1, 10, 21]),
          }, warning_action=policy)
    self.assert_parity([self.base] * 3, {1: _track(np.array([0, 1, 10, 21], dtype=object))})

  def test_numeric_dtypes_and_int32_boundaries(self):
    """Keep float promotion, integer wrapping and cast-boundary behavior."""
    for dtype in (np.bool_, np.int16, np.int64, np.uint64,
                  np.float16, np.float32, np.float64, np.longdouble):
      with self.subTest(dtype=dtype):
        rectangle = np.array([0, 1, 10, 21], dtype=dtype)
        self.assert_parity([dict(self.base, TLBR_POS=rectangle)] * 3, {1: _track(rectangle)})
    for value in (-2147483648.9, -2147483648.0, 2147483647.9, 2147483648.0,
                  np.uint64(2 ** 64 - 1), np.longdouble('1e4000')):
      with self.subTest(value=value):
        rectangle = [value, value, value, value]
        self.assert_parity([dict(self.base, TLBR_POS=rectangle)] * 3, {1: _track(rectangle)})
    signaling_nan = np.array([0x7f800001], dtype=np.uint32).view(np.float32)[0]
    self.assert_parity([self.base] * 3, {1: _track([signaling_nan, 1, 10, 21])})

  def test_seeded_numeric_parity(self):
    """Compare repeatable varied sizes, duplicates and int32 truncation."""
    rng = np.random.default_rng(1536)
    for case in range(60):
      with self.subTest(case=case):
        rectangles = rng.integers(-50, 50, size=(int(rng.integers(1, 12)), 4))
        records = {i: _track(rectangle + rng.uniform(0, .9, size=4), i)
                   for i, rectangle in enumerate(rectangles)}
        if case % 2 == 0:
          records['duplicate'] = copy.deepcopy(records[0])
          records['duplicate']['appearances'] = 99
        data = [dict(self.base, TLBR_POS=rectangles[int(rng.integers(len(rectangles)))])
                for _ in range(int(rng.integers(1, 20)))]
        self.assert_parity(data, records)

  def test_unsized_iterable_keeps_scan_behavior(self):
    """Retain iterable inputs without a length query or persistent state."""
    records = {1: _track([0, 1, 10, 21])}
    expected, actual = copy.deepcopy([self.base] * 2), copy.deepcopy([self.base] * 2)
    _legacy_match(self.plugin, iter(expected), records, 'person')
    self.plugin._match_tracking_results(iter(actual), records, 'person')
    self.assertEqual(actual, expected)

  def test_normalization_cost_is_linear_and_index_is_per_call(self):
    """Pin removable work deterministically without a flaky wall-clock limit."""
    count = 8
    records = {i: _track([i, i + 1, i + 10, i + 21]) for i in range(count)}
    data = [dict(self.base, TLBR_POS=records[i]['rectangle']) for i in range(count)]
    with patch.object(_MIXIN.np, 'array', wraps=np.array) as converter:
      self.plugin._match_tracking_results(data, records, 'person')
    self.assertEqual(converter.call_count, count + count)
    self.assertEqual([item['TRACK_ID'] for item in data], list(range(count)))
    records[0]['rectangle'] = [99, 99, 100, 100]
    records[7]['rectangle'] = [0, 1, 10, 21]
    self.plugin._match_tracking_results(data, records, 'person')
    self.assertEqual(data[0]['TRACK_ID'], 7)

  def test_tiny_batches_keep_original_scan_cost(self):
    """One/two detections do not amortize the index's eligibility checks."""
    records = {i: _track([i, i + 1, i + 10, i + 21]) for i in range(8)}
    for count in (1, 2):
      with self.subTest(count=count):
        with patch.object(_MIXIN.np, 'array', wraps=np.array) as converter:
          self.plugin._match_tracking_results([self.base] * count, records, 'person')
        self.assertEqual(converter.call_count, count + count * len(records))

  def test_track_objects_linear_history_integration(self):
    """Exercise class filtering, real assignment and type/zone history consumers."""
    results = []
    for legacy in (True, False):
      plugin = _MIXIN._MultiClassTrackerMixin()
      plugin.const = _CT
      plugin.cfg_tracking_enabled = True
      plugin._get_detector_ai_engines = lambda: ['detector']
      plugin._get_detector_ai_engine = lambda: 'detector'
      plugin._type_to_meta_type_map = {}
      plugin.start_timer = plugin.end_timer = lambda name: None
      plugin._object_trackers = {kind: _TRACKER.CentroidObjectTracker()
                                 for kind in ('person', 'car')}
      if legacy:
        plugin._match_tracking_results = types.MethodType(_legacy_match, plugin)
      frames = []
      with patch.object(_TRACKER, 'datetime') as clock:
        for offset in (0, 2, 4):
          clock.now.return_value = _NOW + timedelta(seconds=offset)
          data = {'detector': [[
            dict(self.base, TLBR_POS=[offset, 1, 10 + offset, 21]),
            dict(self.base, TLBR_POS=[40 + offset, 1, 50 + offset, 21]),
            dict(self.base, TLBR_POS=[80 + offset, 1, 90 + offset, 21]),
            dict(self.base, TYPE='car', TLBR_POS=[120 + offset, 1, 130 + offset, 21]),
          ]]}
          plugin._track_objects(data)
          # Exercise both opening and closing intervals with the mapped IDs.
          zone_objects = data['detector'][0][:1] if offset == 2 else data['detector'][0]
          plugin._track_in_zone_objects({'detector': [zone_objects]})
          frames.append(data)
      results.append(pickle.dumps((frames, {kind: tracker.objects
        for kind, tracker in plugin._object_trackers.items()})))
    self.assertEqual(results[0], results[1])

  def test_sort_and_debug_modes_retain_existing_metadata_failure(self):
    """Run real SORT and preserve its existing incomplete-metadata failure."""
    for mode in (1, 2):
      with self.subTest(mode=mode):
        tracker = _TRACKER.CentroidObjectTracker(object_tracking_mode=mode)
        with patch.object(_TRACKER, 'datetime') as clock:
          clock.now.return_value = _NOW
          records = tracker.update_tracker(np.array([[0, 1, 10, 21]]))
        self.assertTrue(any(str(track_id).endswith('_SORT') for track_id in records))
        self.assert_parity([self.base] * 3, records)
        with self.assertRaisesRegex(KeyError, 'first_update'):
          self.plugin._match_tracking_results(copy.deepcopy([self.base] * 3), records, 'person')


if __name__ == '__main__':
  unittest.main(verbosity=2)

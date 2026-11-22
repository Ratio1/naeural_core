"""Differential geometry tests using real NumPy/Shapely in the E2 image."""

from copy import deepcopy
from itertools import permutations
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from shapely.geometry import Polygon, LineString
from naeural_core.business import utils


def legacy_intersection(box, target_area):
  """Evaluate the frozen pre-reuse scalar implementation.

  Parameters
  ----------
  box, target_area : list
      Legacy TLBR/polygon and target inputs, including intentionally bad cases.

  Returns
  -------
  float or int
      Original intersection fraction; original exceptions propagate.
  """
  if not isinstance(box[0], list):
    top, left, bottom, right = box[0], box[1], box[2], box[3]
    box = [[left, top], [right, top], [right, bottom], [left, bottom], [left, top]]
  elif len(box) == 4:
    box.append(box[0])
  box = np.array(box).astype(np.float64)
  if target_area[0] == target_area[-1]:
    target = Polygon(target_area)
  else:
    target = LineString(target_area)
  shape = Polygon(box)
  intersection = shape.intersection(target)
  return 0 if shape.area == 0 else intersection.area / shape.area


def legacy_filter(rows, target, threshold):
  """Run the frozen pre-reuse selection.

  Parameters
  ----------
  rows : list
      Detection dictionaries.
  target : list
      Target coordinates.
  threshold : float
      Inclusive cutoff.

  Returns
  -------
  tuple
      Original selected rows and NumPy fractions.
  """
  fractions = np.array([legacy_intersection(row['TLBR_POS'], target) for row in rows])
  selected = fractions >= threshold
  return np.array(rows)[selected].tolist(), fractions[selected]


class IrregularTargetReuseTests(unittest.TestCase):
  """Pin semantic equivalence, alias effects and actual construction reduction."""

  def assert_equivalent(self, rows, target, threshold=0.5):
    """Compare results/errors and post-call input mutations with shared aliases.

    Parameters
    ----------
    rows, target : object
        Inputs copied together so internal alias relationships survive.
    threshold : float, optional
        Selection threshold supplied to both paths.
    """
    old_rows, old_target = deepcopy((rows, target))
    new_rows, new_target = deepcopy((rows, target))
    try:
      expected = legacy_filter(old_rows, old_target, threshold)
    except Exception as error:
      with self.assertRaises(type(error)) as actual:
        utils.keep_boxes_inside_irregular_target_area(new_rows, new_target, threshold)
      self.assertEqual(str(actual.exception), str(error))
    else:
      actual = utils.keep_boxes_inside_irregular_target_area(new_rows, new_target, threshold)
      self.assertEqual(repr(actual[0]), repr(expected[0]))
      np.testing.assert_array_equal(actual[1], expected[1])
      self.assertEqual(actual[1].dtype, expected[1].dtype)
    self.assertEqual(repr(new_rows), repr(old_rows))
    self.assertEqual(repr(new_target), repr(old_target))

  def test_numeric_matrix_and_thresholds(self):
    """Keep open/closed, degenerate, reversed, duplicate and threshold behavior."""
    target = [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]
    row = {'TLBR_POS': [0, 0, 4, 8]}
    rows = [row, row, {'TLBR_POS': [8, 8, 9, 9]}, {'TLBR_POS': [0, 0, 0, 0]},
            {'TLBR_POS': [4, 4, -1, -1]}]
    for area in (target, target[:-1]):
      for threshold in (0, .5, np.nextafter(.5, 1), 1, float('nan'), float('inf'), -float('inf')):
        with self.subTest(area=area, threshold=threshold):
          self.assert_equivalent(rows, area, threshold)
    self.assert_equivalent([{'TLBR_POS': [0, 0, 0, 0]}] * 3, target, 0)
    actual, _ = utils.keep_boxes_inside_irregular_target_area(rows, target, .5)
    self.assertIs(actual[0], row)
    self.assertIs(actual[1], row)

  def test_target_is_constructed_once_per_plain_batch(self):
    """Count actual target builds independently of per-box polygon builds."""
    # A genuinely irregular polygon must still use the construction cache.
    target = [[0, 0], [4, 0], [3, 4], [0, 4], [0, 0]]
    rows = [{'TLBR_POS': [0, 0, 2, 2]} for _ in range(8)]
    with patch.object(utils, 'Polygon', wraps=Polygon) as polygons:
      utils.keep_boxes_inside_irregular_target_area(rows, target, .5)
      self.assertEqual(polygons.call_count, len(rows) + 1)
    with patch.object(utils, 'LineString', wraps=LineString) as lines:
      utils.keep_boxes_inside_irregular_target_area(rows, target[:-1], 0)
      self.assertEqual(lines.call_count, 1)

  def test_only_perimeter_order_rectangles_skip_geometry(self):
    """Compare all corner orders; only the eight simple rings bypass GEOS."""
    corners = [[-7, -3], [5, -3], [5, 9], [-7, 9]]
    rows = [{'TLBR_POS': [-8, -8, 8, 8]}]
    fast = 0
    for order in permutations(corners):
      area = [*order, order[0]]
      with patch.object(utils, 'Polygon', wraps=Polygon) as polygons:
        # The frozen oracle may raise on self-crossing rings; require the
        # same exception and count only the candidate's actual polygon builds.
        self.assert_equivalent(rows, area, 0)
      # Neighbors must share exactly one axis, including the closing edge.
      simple = all((a[0] == b[0]) != (a[1] == b[1]) for a, b in zip(area, area[1:]))
      self.assertEqual(polygons.call_count, 0 if simple else 2)
      fast += simple
    self.assertEqual(fast, 8)

  def test_bounded_fraction_bytes_and_threshold_neighbors(self):
    """Include exact extremes, randomized coordinates and both cutoff neighbors."""
    rng = np.random.default_rng(192)
    limit = 1 << 20
    for i in range(65):
      xs, ys = (sorted(rng.choice(2 * limit, 2, replace=False).tolist()) for _ in range(2))
      x, r = (v - limit for v in xs)
      y, b = (v - limit for v in ys)
      if i == 0:
        x, r, y, b = -limit, limit, -limit, 0
      area = [[x, y], [r, y], [r, b], [x, b], [x, y]]
      row = {'TLBR_POS': [-limit, -limit, limit, limit]}
      other = deepcopy(row)
      rows = [row, other, row, {'TLBR_POS': [b, x, b + 1, r]}]
      for _ in range(8):
        t, btm = sorted(rng.choice(2 * limit + 1, 2, replace=False).tolist())
        l, right = sorted(rng.choice(2 * limit + 1, 2, replace=False).tolist())
        rows.append({'TLBR_POS': [t - limit, l - limit, btm - limit, right - limit]})
      score = legacy_intersection(row['TLBR_POS'], area)
      for cutoff in (0, float(np.nextafter(score, 0)), score,
                     float(np.nextafter(score, 1)), 1, float('nan')):
        old, expected = legacy_filter(rows, area, cutoff)
        new, actual = utils.keep_boxes_inside_irregular_target_area(
          lst_box=rows, target_area=area, prc_intersect=cutoff,
        )
        self.assertEqual([id(v) for v in new], [id(v) for v in old])
        self.assertEqual((actual.dtype, actual.tobytes()), (expected.dtype, expected.tobytes()))

  def test_opaque_or_nonpositive_row_replays_the_whole_batch(self):
    """Never combine arithmetic results with stock polygon/exception side effects."""
    area = [[0, 0], [6, 0], [6, 3], [0, 3], [0, 0]]
    plain = {'TLBR_POS': [0, 0, 4, 4]}
    for box in ([0., 0, 4, 4], [False, 0, 4, 4], [0, 0, 4, 2**20+1],
                [0, 0, 0, 4], [4, 4, 0, 0], (0, 0, 4, 4), np.array([0, 0, 4, 4])):
      for rows in ([{'TLBR_POS': box}, plain], [plain, {'TLBR_POS': box}]):
        with patch.object(utils, 'intersect_box_irregular_target',
                          wraps=utils.intersect_box_irregular_target) as scalar:
          utils.keep_boxes_inside_irregular_target_area(rows, area, 0)
        self.assertEqual(scalar.call_count, len(rows))
        self.assert_equivalent(rows, area, 0)

  def test_custom_selector_keeps_stock_indexing_order(self):
    """An opaque threshold can observe which numpy result is indexed first."""
    class Selector:
      calls = 0

      def __array__(self, dtype=None, copy=None):
        self.calls += 1
        return np.asarray([self.calls == 1, self.calls != 1], dtype=dtype)

    class Threshold:
      def __array_ufunc__(self, *args, **kwargs):
        return Selector()

    rows = [{'TLBR_POS': [0, 0, 4, 8]}, {'TLBR_POS': [0, 0, 4, 4]}]
    area = [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]
    with patch.object(utils, 'intersect_box_irregular_target',
                      wraps=utils.intersect_box_irregular_target) as scalar:
      selected, scores = utils.keep_boxes_inside_irregular_target_area(rows, area, Threshold())
    self.assertEqual(scalar.call_count, 2)
    self.assertIs(selected[0], rows[1])
    np.testing.assert_array_equal(scores, [.5])

  def test_real_executor_annotates_only_selected_objects(self):
    """Exercise the installed caller's keyword dispatch, identity and annotation."""
    from naeural_core import constants as ct
    from naeural_core.business.base.cv_plugin_executor import CVPluginExecutor

    cfg = SimpleNamespace(_coords_type=ct.COORDS_POINTS, cfg_prc_intersect=.5,
                          cfg_points=[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]])
    a = {'TLBR_POS': [0, 0, 4, 8]}
    b = deepcopy(a)
    miss = {'TLBR_POS': [4, 4, 8, 8]}
    result = CVPluginExecutor._keep_only_intersected_objects(cfg, {'det': [[a, miss, b, a], []]})
    self.assertEqual([id(x) for x in result['det'][0]], [id(a), id(b), id(a)])
    self.assertEqual(result['det'][1], [])
    self.assertEqual(a[ct.PRC_INTERSECT], .5)
    self.assertEqual(b[ct.PRC_INTERSECT], .5)
    self.assertNotIn(ct.PRC_INTERSECT, miss)

  def test_alias_closure_between_rows(self):
    """An aliased four-point box can change a target from line to polygon."""
    target = [[0, 0], [4, 0], [4, 4], [0, 4]]
    rows = [{'TLBR_POS': [0, 0, 2, 2]}, {'TLBR_POS': target}, {'TLBR_POS': [0, 0, 2, 2]}]
    self.assert_equivalent(rows, target, 0)
    self.assert_equivalent([rows[1], rows[1], rows[0]], target, 0)

  def test_invalid_inputs_keep_failure_and_mutation_order(self):
    """Do not move target failures ahead of earlier box conversions/mutations."""
    valid = {'TLBR_POS': [0, 0, 2, 2]}
    polygon = {'TLBR_POS': [[0, 0], [2, 0], [2, 2], [0, 2]]}
    targets = ([], [[0, 0]], [[0, 0], [2, 2]], np.array([[0, 0], [2, 2]]))
    for target in targets:
      for rows in ([{'TLBR_POS': []}], [polygon, {}], [valid, {'TLBR_POS': ['bad', 0, 1, 1]}], [valid, valid]):
        with self.subTest(rows=rows, target=repr(target)):
          self.assert_equivalent(rows, target)

  def test_empty_batch_does_not_inspect_target(self):
    """Even a non-indexable target is irrelevant when there are no boxes."""
    self.assert_equivalent([], None)
    self.assert_equivalent([], [])

  def test_custom_rows_and_numeric_arrays_fall_back(self):
    """Do not pre-read effectful mappings or cache across their target edits."""
    target = [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]

    class MutatingRow(dict):
      """Model a mapping whose access changes target coordinates."""
      def __getitem__(self, key):
        self.target[1][0] = 1
        return super().__getitem__(key)

    row = MutatingRow(TLBR_POS=[0, 0, 3, 3])
    row.target = target
    self.assert_equivalent([{'TLBR_POS': [0, 0, 2, 2]}, row], target)
    self.assert_equivalent([{'TLBR_POS': np.array([0, 0, 2, 2])}] * 2, target)

  def test_randomized_plain_batches(self):
    """Exercise the optimized path over varied deterministic numeric inputs."""
    rng = np.random.default_rng(42)
    target = [[0, 0], [9, 0], [4, 8], [0, 0]]
    for _ in range(50):
      rows = [{'TLBR_POS': box.tolist()} for box in rng.integers(-5, 15, (12, 4))]
      self.assert_equivalent(rows, target, float(rng.random()))

  def test_custom_key_cannot_preempt_an_earlier_box_failure(self):
    """Exact dictionaries may run effectful key equality during ordinary lookup."""
    class HostileKey:
      """Collide with the lookup key to detect premature eligibility accesses."""
      def __hash__(self):
        return hash('TLBR_POS')

      def __eq__(self, other):
        raise RuntimeError('custom key compared')

      def __repr__(self):
        return 'HostileKey()'

    target = [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]
    hostile_row = {HostileKey(): 1}
    self.assert_equivalent([{'TLBR_POS': []}, hostile_row], target)
    self.assert_equivalent([{'TLBR_POS': [0, 0, 2, 2]}, hostile_row], [])
    self.assert_equivalent([{'TLBR_POS': [0, 0, 2, 2]}, hostile_row], target)

  def test_no_cross_call_geometry_cache(self):
    """Configuration changes take effect on the next filtering call."""
    target = [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]
    rows = [{'TLBR_POS': [0, 0, 4, 4]}] * 2
    _, before = utils.keep_boxes_inside_irregular_target_area(rows, target, 0)
    target[1][0] = 2
    _, after = utils.keep_boxes_inside_irregular_target_area(rows, target, 0)
    self.assertFalse(np.array_equal(before, after))
    self.assert_equivalent(rows, target, 0)


if __name__ == '__main__':
  unittest.main()

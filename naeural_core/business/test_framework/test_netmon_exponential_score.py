"""exponential_score must give what the previous eval() version gave, and not raise on NaN/inf."""
import random
import unittest

import numpy as np

from naeural_core.main.net_mon import exponential_score


def _eval_score(left, right, val, right_is_better=False, normed=False):
  """The previous implementation, kept as the reference."""
  num = 50
  interval = np.linspace(left, right, num=num)
  scores = np.linspace(100, 1, num=num)
  if right_is_better:
    rng = range(len(interval)-1, -1, -1)
    sgn = '>='
  else:
    rng = range(len(interval))
    sgn = '<='
  for s,i in enumerate(rng):
    if eval('{}{}{}'.format(val, sgn, interval[i])):  # noqa: S307
      return scores[s] / 100.1 if normed else scores[s]
  return 0


class TestExponentialScore(unittest.TestCase):

  def test_same_score_as_eval_for_finite_values(self):
    rnd = random.Random(11)
    # (left, right) pairs used by the callers: GPU memory/load percentages and the uptime trust.
    bounds = [(0, 100), (0, 1), (4, 24), (0, 4 * 4 * 60)]
    for left, right in bounds:
      edges = list(np.linspace(left, right, num=50)) + [left - 1, right + 1, left, right]
      values = edges + [rnd.uniform(left - 5, right + 5) for _ in range(100)] + [rnd.randint(left - 5, right + 5) for _ in range(30)]
      values += [np.float64(v) for v in values[54:84]] + [True, False]
      for val in values:
        for right_is_better in (False, True):
          for normed in (False, True):
            self.assertEqual(
              exponential_score(left, right, val, right_is_better=right_is_better, normed=normed),
              _eval_score(left, right, val, right_is_better=right_is_better, normed=normed),
              (left, right, val, right_is_better, normed),
            )

  def test_nan_and_inf_score_instead_of_raising(self):
    # eval() raised NameError on 'nan'/'inf', which aborted the whole status pass.
    for val in (float("nan"), float("inf"), float("-inf")):
      with self.assertRaises(NameError):
        _eval_score(0, 100, val)
    self.assertEqual(exponential_score(0, 100, float("nan")), 0)
    self.assertEqual(exponential_score(0, 100, float("nan"), right_is_better=True), 0)
    self.assertEqual(exponential_score(0, 100, float("inf")), 0)
    self.assertEqual(exponential_score(0, 100, float("inf"), right_is_better=True), 100)
    self.assertEqual(exponential_score(0, 100, float("-inf")), 100)


if __name__ == "__main__":
  unittest.main()

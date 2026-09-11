import unittest

from naeural_core.comm.message_buffer import ObservableMessageBuffer


class _Clock:
  def __init__(self):
    self.value = 0.0

  def __call__(self):
    return self.value


class TestObservableMessageBuffer(unittest.TestCase):

  def test_oldest_age_is_read_from_head_admission_timestamp(self):
    clock = _Clock()
    buffer = ObservableMessageBuffer(capacity=2, clock=clock)
    buffer.try_append("first")
    clock.value = 3.0
    buffer.try_append("second")
    clock.value = 8.0

    self.assertEqual(buffer.snapshot().oldest_age_seconds, 8.0)
    self.assertEqual(buffer.popleft(), "first")
    self.assertEqual(buffer.snapshot().oldest_age_seconds, 5.0)

  def test_full_buffer_rejects_newest_and_conserves_accepted_entries(self):
    buffer = ObservableMessageBuffer(capacity=2)

    self.assertTrue(buffer.try_append("first"))
    self.assertTrue(buffer.try_append("second"))
    self.assertFalse(buffer.try_append("third"))

    self.assertEqual(buffer.popleft(), "first")
    self.assertEqual(buffer.popleft(), "second")
    snapshot = buffer.snapshot()
    self.assertEqual(snapshot.admitted, 2)
    self.assertEqual(snapshot.rejected_full, 1)
    self.assertEqual(snapshot.dequeued, 2)
    self.assertTrue(snapshot.conserved)
    self.assertTrue(snapshot.degraded)

  def test_closed_buffer_rejection_is_not_reported_as_saturation(self):
    buffer = ObservableMessageBuffer(capacity=1)
    buffer.close()

    self.assertFalse(buffer.try_append("late"))

    snapshot = buffer.snapshot()
    self.assertEqual(snapshot.rejected_closed, 1)
    self.assertEqual(snapshot.rejected_full, 0)
    self.assertTrue(snapshot.conserved)


if __name__ == "__main__":
  unittest.main()

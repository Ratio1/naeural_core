"""Heartbeat timestamps: the fast parse must give exactly what ``strptime`` gives."""
import random
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

FORMAT = "%Y-%m-%d %H:%M:%S.%f"
SOURCE = Path(__file__).resolve().parents[2] / "main" / "net_mon.py"


def _load_parser():
  """The helper alone: importing the network monitor would import the whole node."""
  text = SOURCE.read_text()
  start = text.index("def parse_heartbeat_time(text):")
  space = {"dt": datetime, "ct": type("ct", (), {"HB": type("HB", (), {"TIMESTAMP_FORMAT": FORMAT})})}
  exec(text[start:text.index("NETMON_MUTEX = ", start)], space)  # noqa: S102
  return space["parse_heartbeat_time"]


parse_heartbeat_time = _load_parser()


class TestHeartbeatTime(unittest.TestCase):

  def test_same_result_as_strptime_for_written_timestamps(self):
    rnd = random.Random(7)
    base = datetime(2020, 1, 1)
    for _ in range(20000):
      moment = base + timedelta(seconds=rnd.randrange(10 * 365 * 86400), microseconds=rnd.randrange(1000000))
      text = moment.strftime(FORMAT)
      self.assertEqual(parse_heartbeat_time(text), datetime.strptime(text, FORMAT))
      self.assertEqual(parse_heartbeat_time(text), moment)

  def test_other_shapes_go_through_strptime(self):
    for text in ("2026-01-31 23:59:59.5", "2026-1-31 3:59:59.123456"):
      self.assertEqual(parse_heartbeat_time(text), datetime.strptime(text, FORMAT))

  def test_malformed_text_raises_as_before(self):
    for text in ("", "not a timestamp at all, sorry", "2026-01-31T23:59:59+02:00", "2026-13-41 23:59:59.123456", "2026-01-31 23:59:59+02:00:00"):
      with self.assertRaises(ValueError):
        parse_heartbeat_time(text)
    with self.assertRaises(TypeError):
      parse_heartbeat_time(None)

  def test_result_is_naive(self):
    self.assertIsNone(parse_heartbeat_time("2026-01-31 23:59:59.123456").tzinfo)

  def test_faster_than_strptime(self):
    text = "2026-01-31 23:59:59.123456"
    start = time.perf_counter()
    for _ in range(20000):
      datetime.strptime(text, FORMAT)
    slow = time.perf_counter() - start
    start = time.perf_counter()
    for _ in range(20000):
      parse_heartbeat_time(text)
    fast = time.perf_counter() - start
    self.assertGreater(slow / fast, 5, "strptime {:.1f} us, fast parse {:.1f} us".format(slow * 50, fast * 50))


if __name__ == "__main__":
  unittest.main()

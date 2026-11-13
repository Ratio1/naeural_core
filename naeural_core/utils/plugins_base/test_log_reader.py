"""LogReader stops polling a pipe at end of file and keeps what the child wrote."""
import subprocess
import sys
import unittest

from naeural_core.utils.plugins_base.plugin_base_utils import LogReader


class _Owner:
  def P(self, *args, **kwargs):
    pass


class TestLogReader(unittest.TestCase):
  def test_reader_exits_at_end_of_file_and_keeps_the_output(self):
    # bufsize=0 as in the web-app and tunnel producers: a read after select returns b'' only at EOF.
    proc = subprocess.Popen([sys.executable, "-c", "print('hello')"], stdout=subprocess.PIPE, bufsize=0)
    reader = LogReader(_Owner(), proc.stdout)
    self.addCleanup(reader.stop)
    proc.wait(timeout=30)
    reader.thread.join(timeout=5)
    self.assertFalse(reader.thread.is_alive())  # it used to spin on select + b'' until stop()
    self.assertTrue(reader.exited)
    self.assertEqual(reader.get_next_characters(), "hello\n")


if __name__ == "__main__":
  unittest.main()

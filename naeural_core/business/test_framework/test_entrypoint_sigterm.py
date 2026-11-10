"""SIGTERM must stop the node like SIGINT: as PID 1 an unhandled SIGTERM is ignored."""
import os
import signal
import subprocess
import sys
import textwrap
import threading
import unittest
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[2] / "main" / "entrypoint.py"


def _stop_on_sigterm_source():
  """The helper alone: importing the entrypoint module would import the whole node."""
  text = ENTRYPOINT.read_text()
  start = text.index("def stop_on_sigterm():")
  return "import signal\n" + text[start:text.index("def main(", start)]


class TestSigterm(unittest.TestCase):

  def test_sigterm_raises_keyboard_interrupt_in_the_main_thread(self):
    script = _stop_on_sigterm_source() + textwrap.dedent("""
      import os, sys, time
      assert stop_on_sigterm()
      print("ready", flush=True)
      try:
        time.sleep(30)
      except KeyboardInterrupt:
        print("orderly", flush=True)
        sys.exit(0)
      sys.exit(1)
    """)
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    self.assertEqual(proc.stdout.readline().strip(), "ready")
    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=20)
    self.assertEqual((out.strip(), proc.returncode), ("orderly", 0))

  def test_without_the_handler_sigterm_kills_without_cleanup(self):
    script = "import time\nprint('ready', flush=True)\ntry:\n  time.sleep(30)\nexcept KeyboardInterrupt:\n  print('orderly', flush=True)\n"
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    self.assertEqual(proc.stdout.readline().strip(), "ready")
    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=20)
    self.assertEqual((out.strip(), proc.returncode), ("", -signal.SIGTERM))

  def test_outside_the_main_thread_it_reports_instead_of_raising(self):
    space, result = {}, []
    exec(_stop_on_sigterm_source(), space)  # noqa: S102
    thread = threading.Thread(target=lambda: result.append(space["stop_on_sigterm"]()))
    thread.start()
    thread.join()
    self.assertEqual(result, [False])


if __name__ == "__main__":
  unittest.main()

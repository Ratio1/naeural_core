"""SIGTERM must stop the node in an orderly way: as PID 1 an unhandled SIGTERM is ignored."""
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
  start = text.index("def stop_on_sigterm(running=None):")
  return "import signal\n" + text[start:text.index("def main(", start)]


class TestSigterm(unittest.TestCase):

  def test_before_the_node_exists_sigterm_raises_keyboard_interrupt(self):
    script = _stop_on_sigterm_source() + textwrap.dedent("""
      import os, sys, time
      assert stop_on_sigterm()
      try:
        # inside the try: the signal can land before print() returns
        print("ready", flush=True)
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

  def test_a_running_node_is_asked_to_stop_and_nothing_is_raised(self):
    # The error path would send an EXCEPTION heartbeat, after which other nodes treat this one as offline for an hour.
    script = _stop_on_sigterm_source() + textwrap.dedent("""
      import os, sys, time
      class Engine:
        asked = 0
        def request_stop(self):
          self.asked += 1
      running = {}
      assert stop_on_sigterm(running)
      running['eng'] = Engine()
      print("ready", flush=True)
      try:
        deadline = time.time() + 30
        while not running['eng'].asked and time.time() < deadline:
          time.sleep(0.05)
      except KeyboardInterrupt:
        print("raised", flush=True)
        sys.exit(2)
      print("asked %d" % running['eng'].asked, flush=True)
      sys.exit(0)
    """)
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    self.assertEqual(proc.stdout.readline().strip(), "ready")
    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=20)
    self.assertEqual((out.strip(), proc.returncode), ("asked 1", 0))

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

  def test_request_stop_sets_the_shutdown_code_once(self):
    from naeural_core import constants as ct
    from naeural_core.main.orchestrator import Orchestrator
    eng = Orchestrator.__new__(Orchestrator)  # the method only needs the return code
    eng._return_code = None
    eng.request_stop()
    self.assertEqual(eng._return_code, ct.CODE_SHUTDOWN)
    eng._return_code = ct.CODE_EXCEPTION  # a stop decided earlier is kept
    eng.request_stop()
    self.assertEqual(eng._return_code, ct.CODE_EXCEPTION)


if __name__ == "__main__":
  unittest.main()

"""The node's entry point writes the log lines still held in memory before it returns to the caller that exits."""

# The log files of the child process.
import glob
# The child's folder and file paths.
import os
# The child process, which ends without running exit hooks, as the node does.
import subprocess
# The interpreter of the child.
import sys
# A folder for the child's log.
import tempfile
# The test framework.
import unittest
# Stand-ins for the logger, the orchestrator and the start-up helpers of main().
from unittest import mock

# The return codes.
from naeural_core import constants as ct
# The module under test.
from naeural_core.main import entrypoint

# A child that logs a burst of ordinary lines, drains the writer as main() does, and ends with os._exit, which
# runs no exit hook: the way the node's launcher ends the process after main() returns.
_CHILD = r'''
import os, sys
from naeural_core import Logger
log = Logger("EXITLOG", base_folder=sys.argv[1], app_folder="_local_cache", TF_KERAS=False)
for index in range(20):
  log.P("closing line %d" % index)
log._shutdown_log_writer()
os._exit(0)
'''


def _run_main(orchestrator):
  """
  Run ``entrypoint.main()`` with a recording logger and the given orchestrator class, nothing else real.

  Parameters
  ----------
  orchestrator : callable
    What ``main()`` calls to create the Orchestrator: returns the engine or raises.

  Returns
  -------
  tuple
    ``(exit_code, logger)``: the exit code ``main()`` returned and the recording logger it used.

  Examples
  --------
  >>> exit_code, logger = _run_main(lambda log: engine)
  >>> logger._shutdown_log_writer.call_count
  1
  """
  # The logger main() creates: every call on it is recorded in order.
  logger = mock.Mock()
  # A usable local cache and a startup config that is not empty, so main() goes on to the Orchestrator.
  logger.no_folders_no_save, logger.config_data = False, {"EE_ID": "node"}
  # The package list main() prints.
  logger.get_packages.return_value = "packages"
  # main() without a signal handler, a .env file, a config file on disk or a host name; one process lock.
  with mock.patch.object(entrypoint, "stop_on_sigterm"), mock.patch.object(entrypoint, "load_dotenv"), \
       mock.patch.object(entrypoint, "get_config", return_value="config_startup.json"), \
       mock.patch.object(entrypoint, "running_with_hostname", return_value=None), \
       mock.patch.object(entrypoint, "get_id", return_value="node"), \
       mock.patch.object(entrypoint, "Logger", return_value=logger), \
       mock.patch.object(entrypoint, "Orchestrator", side_effect=orchestrator):
    # The node's entry point.
    exit_code, _ = entrypoint.main()
  # The code and the logger with its recorded calls.
  return exit_code, logger


class TestEntrypointLogDrain(unittest.TestCase):

  def test_main_drains_the_log_writer_last_after_an_orderly_main_loop(self):
    """After the main loop returned, the last thing main() does with the logger is to write out what it holds."""
    # An engine whose main loop ends with the shutdown code.
    engine = mock.Mock(__version__="1.0")
    # The orderly end of the main loop.
    engine.main_loop.return_value = ct.CODE_SHUTDOWN
    # main() with that engine.
    exit_code, logger = _run_main(lambda log: engine)
    # The main loop's code is returned.
    self.assertEqual(exit_code, ct.CODE_SHUTDOWN)
    # The drain is the last call on the logger, after the "main loop exits" line; before, there was none.
    self.assertEqual(logger.mock_calls[-1], mock.call._shutdown_log_writer())
    # Once.
    self.assertEqual(logger._shutdown_log_writer.call_count, 1)

  def test_main_drains_the_log_writer_after_a_start_that_raised(self):
    """When creating the Orchestrator raises, the traceback lines are written out too before main() returns."""

    def failing(log):
      """
      Stand in for an Orchestrator whose constructor fails.

      Parameters
      ----------
      log : object
        The logger main() passes.

      Returns
      -------
      None
        Never returns: it raises.
      """
      # A start-up failure.
      raise RuntimeError("start failed")

    # main() with the failing constructor.
    exit_code, logger = _run_main(failing)
    # The exception code.
    self.assertEqual(exit_code, ct.CODE_EXCEPTION)
    # The drain is again the last call on the logger, after the lines about the exception.
    self.assertEqual(logger.mock_calls[-1], mock.call._shutdown_log_writer())

  def test_the_drain_writes_ordinary_lines_of_a_process_that_ends_without_exit_hooks(self):
    """With the real logger: 20 ordinary lines logged in a burst are in the log file after the drain and os._exit."""
    # A folder for the child's log.
    folder = tempfile.TemporaryDirectory()
    # Removed at the end of the test.
    self.addCleanup(folder.cleanup)
    # The child logs, drains and ends without exit hooks.
    result = subprocess.run([sys.executable, "-c", _CHILD, folder.name], capture_output=True, text=True)
    # The child ran to its end.
    self.assertEqual(result.returncode, 0, result.stderr[-2000:])
    # Every text file the logger wrote.
    files = glob.glob(os.path.join(folder.name, "**", "*.txt"), recursive=True)
    # Their content.
    written = "".join(open(path, encoding="utf-8", errors="replace").read() for path in files)
    # The first and the last line of the burst are on disk; without the drain none of the 20 is.
    self.assertTrue("closing line 0" in written and "closing line 19" in written)


if __name__ == "__main__":
  # Run this file's tests when it is started directly.
  unittest.main()

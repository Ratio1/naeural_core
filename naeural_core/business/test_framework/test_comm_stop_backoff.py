"""A stopping MQTT communicator leaves its reconnect back-off at once and opens no new broker connection."""

# The helper thread that calls stop(), and the stand-in's signal.
import threading
# Timing of the stop and of the back-off.
import time
# The test framework.
import unittest
# The wrapper factory is replaced, and the managers are stand-ins.
from unittest import mock

# The node logger the real constructor needs, and the status and field names.
from naeural_core import Logger, constants as ct
# The MQTT communicator under test.
from naeural_core.comm.default import mqtt as mqtt_comm


class _RefusingController:
  """
  Stand-in for the MQTT wrapper of a communicator whose broker refuses every connection.

  Parameters
  ----------
  None

  Returns
  -------
  None

  Examples
  --------
  >>> controller = _RefusingController()
  >>> controller.server_connect(max_retries=2)['has_connection']
  False
  >>> controller.calls
  1
  """

  def __init__(self):
    """
    Start with no client, no connect attempt counted and the "burst failed" signal clear.

    Returns
    -------
    None

    Examples
    --------
    >>> _RefusingController().connection is None
    True
    """
    # No client object, as after a disconnect: the communicator must connect before it can send.
    self.connection = None
    # Number of connect bursts the communicator asked for.
    self.calls = 0
    # Set at each failed burst, so a test knows the communicator is about to wait out its back-off.
    self.burst_failed = threading.Event()
    return

  def server_connect(self, max_retries=5):
    """
    Count one connect burst and answer that the broker refused it.

    Parameters
    ----------
    max_retries : int, optional
      Attempts of the burst; not used, every burst fails at once.

    Returns
    -------
    dict
      The wrapper's answer shape: ``has_connection`` False, a message and its notification type.

    Examples
    --------
    >>> _RefusingController().server_connect(max_retries=2)['msg']
    'refused'
    """
    # One more burst asked by the communicator.
    self.calls += 1
    # Tell the waiting test that the back-off starts now.
    self.burst_failed.set()
    # Refused, in the wrapper's answer shape.
    return {'has_connection': False, 'msg': 'refused', 'msg_type': ct.STATUS_TYPE.STATUS_EXCEPTION}

  def release(self):
    """
    Answer the communicator's release at the end of its thread.

    Returns
    -------
    dict
      The wrapper's answer shape with no messages.

    Examples
    --------
    >>> _RefusingController().release()
    {'msgs': []}
    """
    # Nothing to report at release.
    return {'msgs': []}


def _make_comm(backoff):
  """
  Build a notifications communicator through the real constructor, with a broker that refuses every connection.

  Parameters
  ----------
  backoff : int or float
    Seconds the communicator waits after a failed connect burst (``CONNECTION_FAIL_SLEEP_TIME``).

  Returns
  -------
  tuple
    ``(comm, controller)``: the ``MQTTCommThread`` and the ``_RefusingController`` it will use.

  Examples
  --------
  >>> comm, controller = _make_comm(backoff=50)
  >>> comm.cfg_connection_fail_sleep_time
  50
  """
  # A logger that writes no file: the test tree is mounted read-only.
  logger = Logger("COMMSTOP", base_folder=".", app_folder="_local_cache", no_folders_no_save=True, DEBUG=False)
  # The constructor reads the node configuration from the logger.
  logger.config_data = {}
  # The constructor asks the formatter manager for the node's formatter; none is needed here.
  formatter = mock.Mock()
  # No formatter and no formatter name.
  formatter.get_formatter.return_value = (None, None)
  # The real constructor: an attribute it does not create is missing in every test below, as it would be in the node.
  comm = mqtt_comm.MQTTCommThread(
    log=logger,
    shmem={"heavy_ops_manager": mock.Mock(), "network_monitor": mock.Mock(), "io_formatter_manager": formatter},
    signature="TEST_MQTT", comm_type="NOTIFICATIONS",
    default_config={**mqtt_comm.MQTTCommThread.CONFIG, "CONNECTION_FAIL_SLEEP_TIME": backoff},
    upstream_config={ct.EE_ID: "stop_node"},
    # A receive channel, so the receive half of a lap reconnects too (without one it only marks itself ready).
    recv_channel_name="CONFIG_CHANNEL",
  )
  # Notifications go to the node's notification queue; the tests only need the calls not to fail.
  comm._create_notification = mock.Mock()
  # The broker of this communicator refuses every connection.
  controller = _RefusingController()
  # Both, so a test can install the controller and read its counters.
  return comm, controller


class TestCommStopDuringBackoff(unittest.TestCase):

  def test_stop_ends_a_50_second_backoff_at_once(self):
    """stop() returns within 3 s while the comm thread waits out a 50 s back-off after a refused connect burst."""
    # The released back-off length: 50 s.
    comm, controller = _make_comm(backoff=50)
    # The comm thread builds its wrapper in _init(); it gets the refusing one.
    with mock.patch.object(mqtt_comm, "MQTTWrapper", return_value=controller):
      # The real comm thread: _init() makes the first connect burst, which fails, and the back-off starts.
      comm.start()
      # The burst has failed: the thread is at, or just before, its back-off.
      self.assertTrue(controller.burst_failed.wait(5))
      # Let the thread enter the back-off itself.
      time.sleep(0.3)
      # stop() joins the comm thread; it runs on a helper so a stop that blocks fails the test instead of hanging it.
      stopper = threading.Thread(target=comm.stop, daemon=True)
      # Time the stop.
      started = time.time()
      # Ask the communicator to stop, as CommunicationManager.close() does.
      stopper.start()
      # Far less than the 50 s back-off, with room for a slow host.
      stopper.join(3)
      # Seconds the stop needed (3 when it is still blocked).
      elapsed = time.time() - started
    # The stop returned: the back-off did not hold the join.
    self.assertFalse(stopper.is_alive(), "stop() still blocked after {:.1f}s of a 50 s back-off".format(elapsed))
    # The comm thread ended.
    self.assertFalse(comm._thread.is_alive())
    # One burst only: the stopping thread did not try the broker again.
    self.assertEqual(controller.calls, 1)

  def test_no_connect_and_no_stale_connection_flag_once_stopping(self):
    """After stop was asked, the send and receive reconnects make no connect burst and report no connection."""
    # A short back-off, so the released code (which connects and waits here) fails this test quickly.
    comm, controller = _make_comm(backoff=0.3)
    # The wrapper of a communicator whose client is gone (the disconnect was seen after the stop).
    comm._controller = controller
    # The flag still says "connected": the stale state a late disconnect leaves behind.
    comm.has_server_conn = True
    # What stop() sets before it joins the thread.
    comm._stop = True
    # Time both reconnect calls of one loop lap.
    started = time.time()
    # The send half of the lap.
    comm._maybe_reconnect_send()
    # The receive half of the lap (the heartbeat and command loops call both).
    comm._maybe_reconnect_recv()
    # Seconds both calls needed.
    elapsed = time.time() - started
    # No new broker connection once stopping.
    self.assertEqual(controller.calls, 0)
    # No flag says "connected", so the loop sends nothing to a client that is gone and does not subscribe (the
    # stand-in has no subscribe method: a subscribe attempt would have raised above).
    self.assertEqual((comm.has_server_conn, comm.has_send_conn, comm.has_recv_conn), (False, False, False))
    # No back-off either.
    self.assertLess(elapsed, 0.2)

  def test_running_backoff_keeps_its_length(self):
    """Without a stop, a failed connect burst is still followed by the whole configured back-off."""
    # A back-off short enough for a test and long enough to measure.
    comm, controller = _make_comm(backoff=0.4)
    # The wrapper with no client: a connect burst is due.
    comm._controller = controller
    # Time the reconnect call.
    started = time.time()
    # One burst, refused, then the back-off.
    comm._maybe_reconnect_to_server()
    # Seconds the call needed.
    elapsed = time.time() - started
    # The burst was made.
    self.assertEqual(controller.calls, 1)
    # The whole back-off was waited: a running node does not retry a refusing broker in a tight loop.
    self.assertGreaterEqual(elapsed, 0.4)
    # The failure is still counted for the node's status.
    self.assertEqual(comm._total_conn_fails, 1)


if __name__ == "__main__":
  # Run this file's tests when it is started directly.
  unittest.main()

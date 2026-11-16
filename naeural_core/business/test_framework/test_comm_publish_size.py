"""A message is published only when its UTF-8 text fits the brokers' packet limit; a larger one is dropped loudly."""

# The test framework.
import unittest
# Stand-ins for the node's managers and for the publish step.
from unittest import mock

# The node logger the real constructor needs, and the field names.
from naeural_core import Logger, constants as ct
# The module of the limit and the MQTT communicator.
from naeural_core.comm.base import base_comm_thread
# The communicator class under test.
from naeural_core.comm.default import mqtt as mqtt_comm

# The limit under test, in bytes.
LIMIT = base_comm_thread.MAX_MESSAGE_LEN


def _make_comm():
  """
  Build a default-type communicator through the real constructor, with the publish step replaced by a recorder.

  Returns
  -------
  tuple
    ``(comm, published, lines)``: the communicator, the list of message texts handed to the publish step and
    the list of ``(text, color)`` lines it logged.

  Examples
  --------
  >>> comm, published, lines = _make_comm()
  >>> comm.send_wrapper({'TEXT': 'x'}) > 0, len(published)
  (True, 1)
  """
  # A logger that writes no file: the test tree is mounted read-only.
  logger = Logger("COMMSIZE", base_folder=".", app_folder="_local_cache", no_folders_no_save=True, DEBUG=False)
  # The constructor reads the node configuration from the logger.
  logger.config_data = {}
  # The constructor asks the formatter manager for the node's formatter; none is needed here.
  formatter = mock.Mock()
  # No formatter and no formatter name.
  formatter.get_formatter.return_value = (None, None)
  # The real constructor; no block engine, so a message is published as it is given, unsigned.
  comm = mqtt_comm.MQTTCommThread(
    log=logger,
    shmem={
      "heavy_ops_manager": mock.Mock(), "network_monitor": mock.Mock(), "io_formatter_manager": formatter,
      ct.BLOCKCHAIN_MANAGER: None,
    },
    signature="TEST_MQTT", comm_type="DEFAULT",
    default_config=mqtt_comm.MQTTCommThread.CONFIG, upstream_config={ct.EE_ID: "size_node"},
  )
  # The message texts that reached the publish step, and the lines the communicator logged.
  published, lines = [], []

  def publish(message, data, send_to=None):
    """
    Record a message handed to the transport and answer that it was published to one target.

    Parameters
    ----------
    message : str
      The serialized message.
    data : dict
      The message before serialization; unused.
    send_to : str, optional
      The target; unused.

    Returns
    -------
    dict
      The publish result shape of the communicator, for one successful target.
    """
    # One more message handed over.
    published.append(message)
    # Published to one target, none failed.
    return {
      'attempted_targets': ['t'], 'successful_targets': ['t'], 'failed_targets': [], 'failed_errors': [],
      'published_bytes': len(message),
    }

  # The transport is replaced: nothing is sent to a broker.
  comm._publish_serialized_message = publish
  # Log lines are kept with their color.
  comm.P = lambda text, color=None, **kwargs: lines.append((text, color))
  # Notifications are not read here.
  comm._create_notification = mock.Mock()
  # All three, so a test can send and read what happened.
  return comm, published, lines


class TestPublishSizeGuard(unittest.TestCase):

  def test_the_limit_is_under_the_brokers_one_megabyte_packet_size(self):
    """The limit is 1,000,000 bytes: below the 1,048,576-byte packet a broker accepts, with room for the topic."""
    # The value, and its distance to the broker's packet limit.
    self.assertEqual((LIMIT, 1024 * 1024 - LIMIT), (1_000_000, 48_576))

  def test_the_check_counts_bytes_at_the_limit(self):
    """A text of exactly the limit passes and one byte more does not (before, anything under 2 MiB of characters passed)."""
    # Only the check is called.
    comm, _, _ = _make_comm()
    # Exactly the limit, in one-byte characters.
    self.assertTrue(comm._check_send_message("a" * LIMIT))
    # One byte over.
    self.assertFalse(comm._check_send_message("a" * (LIMIT + 1)))

  def test_the_check_counts_bytes_not_characters(self):
    """Characters of three bytes each are counted as three: 400,000 of them are over the limit, 300,000 are under."""
    # Only the check is called.
    comm, _, _ = _make_comm()
    # 1.2 MB on the wire in 400,000 characters; the character count alone is far under the limit.
    self.assertFalse(comm._check_send_message("€" * 400_000))
    # 0.9 MB on the wire.
    self.assertTrue(comm._check_send_message("€" * 300_000))

  def test_an_oversize_message_is_dropped_for_good_with_one_red_line(self):
    """A message over the limit is not handed to the transport; it is reported in red with its size in bytes."""
    # The communicator with the recording transport.
    comm, published, lines = _make_comm()
    # About 1.2 MB of text once serialized.
    result = comm.send_wrapper({"TEXT": "€" * 400_000})
    # Not handed to the transport.
    self.assertEqual(published, [])
    # Dropped for good: the loops take 1 as "do not keep this message" and read this outcome.
    self.assertEqual((result, comm._last_send_outcome), (1, 'terminal_dropped_oversize'))
    # The red lines.
    red = [text for text, color in lines if color == 'r']
    # One, for this message.
    self.assertEqual(len(red), 1)
    # It gives the size as the broker counts it (bytes: about 1,172 KB, not the 390 KB of its characters).
    self.assertIn("1,171.9 KB", red[0])

  def test_a_message_under_the_limit_is_published(self):
    """A message under the limit is handed to the transport as before."""
    # The communicator with the recording transport.
    comm, published, lines = _make_comm()
    # About 0.9 MB of text once serialized.
    result = comm.send_wrapper({"TEXT": "€" * 300_000})
    # Handed over once.
    self.assertEqual(len(published), 1)
    # Reported as published, with no red line.
    self.assertTrue(result > 0 and comm._last_send_outcome == 'paho_handoff')
    # Nothing in red.
    self.assertEqual([text for text, color in lines if color == 'r'], [])


if __name__ == "__main__":
  # Run this file's tests when it is started directly.
  unittest.main()

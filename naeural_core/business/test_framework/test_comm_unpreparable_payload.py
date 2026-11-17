"""
Offline tests of the comm threads' send loops: a payload or command that cannot be prepared is dropped with one red
line, and the ones queued behind it still leave.
"""
# The send queue.
import collections
# The payload's JSON text, as the preparation encodes it before encrypting it.
import json
# The test framework.
import unittest
# The heavy-ops manager stand-in.
from types import SimpleNamespace
# The loops' sleeps, made instant.
from unittest import mock

# The payload field names.
from naeural_core import constants as ct
# The command-and-control loop.
from naeural_core.comm.mixins.commandcontrol_comm_mixin import _CommandControlCommMixin
# The default send loop.
from naeural_core.comm.mixins.default_comm_mixin import _DefaultCommMixin
# The heartbeats loop.
from naeural_core.comm.mixins.heartbeats_comm_mixin import _HeartbeatsCommMixin
# The notifications loop.
from naeural_core.comm.mixins.notifications_comm_mixin import _NotificationsCommMixin

# A payload holding a lone surrogate (as a JSON escape decodes), which UTF-8 cannot encode, and one that is fine.
BAD = {ct.EE_ID: "node", ct.PAYLOAD_DATA.STREAM_NAME: "stream", ct.PAYLOAD_DATA.SIGNATURE: "SIG",
       ct.PAYLOAD_DATA.INSTANCE_ID: "inst", "data": json.loads('"k\\ud800"')}
# The payload queued behind it.
GOOD = {ct.EE_ID: "node", "data": "ok"}
# A command whose payload holds a lone surrogate, and the command queued behind it.
BAD_COMMAND = {ct.COMMS.COMM_SEND_MESSAGE.K_ACTION: "UPDATE_CONFIG",
               ct.COMMS.COMM_SEND_MESSAGE.K_PAYLOAD: {"NAME": json.loads('"p\\ud800"')}}
# The good command.
GOOD_COMMAND = {ct.COMMS.COMM_SEND_MESSAGE.K_ACTION: "UPDATE_CONFIG", ct.COMMS.COMM_SEND_MESSAGE.K_PAYLOAD: "ok"}


class _Comm:
  """A comm thread's state and stubs around one send loop: its queue holds an unpreparable payload, then a good one."""

  def __init__(self):
    """
    Build the state a send loop reads, with the two payloads queued and a connection up.

    Returns
    -------
    None
    """
    # Queued: the bad payload first, then the good one (id, payload, time queued, retry targets).
    self._send_buff = collections.deque([(1, BAD, 0, None), (2, GOOD, 0, None)])
    # Running, connected, no receive connection.
    self._stop, self.has_send_conn, self.has_recv_conn, self._last_read = False, True, False, 0
    # The loop's rate and timings, and the retry targets of a partial send (none).
    self.loop_resolution, self.loop_timings, self._last_send_retry_targets = 100, [], None
    # The heavy operations run on the default loop's thread: none here.
    self._heavy_ops_manager = SimpleNamespace(run_all_on_comm_thread=lambda msg: None)
    # What was sent and logged, and the turns of the loop.
    self.sent, self.logged, self.turns = [], [], 0

  def _maybe_reconnect_send(self):
    """
    Count one turn of the loop, and stop the loop after 20 turns.

    Returns
    -------
    None
    """
    # One more turn.
    self.turns += 1
    # 20 turns are enough for both payloads.
    self._stop = self.turns > 20

  def _prepare_message(self, msg, msg_id):
    """
    Prepare a payload as the comm thread does before encrypting it: its JSON text, encoded as UTF-8.

    Parameters
    ----------
    msg : dict
      The payload.
    msg_id : int
      Its sequence number.

    Returns
    -------
    dict
      The payload, as sent.

    Raises
    ------
    UnicodeEncodeError
      For a payload whose text UTF-8 cannot encode.
    """
    # The encoding the encryption makes, which raises for a lone surrogate.
    json.dumps(msg, ensure_ascii=False).encode()
    # Sent as it is.
    return msg

  def send_wrapper(self, msg, send_to=None):
    """
    Record a sent payload.

    Parameters
    ----------
    msg : dict
      The prepared payload.
    send_to : list, optional
      The retry targets; unused.

    Returns
    -------
    int
      The bytes delivered.
    """
    # Sent.
    self.sent.append(msg)
    # Delivered.
    return 100

  def P(self, msg, color=None, **kwargs):
    """
    Record a log line.

    Parameters
    ----------
    msg : str
      The line.
    color : str, optional
      Its color.
    **kwargs
      Unused.

    Returns
    -------
    None
    """
    # Logged.
    self.logged.append((msg, color))

  def _noop(self, *args, **kwargs):
    """
    Stand for the comm thread's other steps, which do nothing here.

    Parameters
    ----------
    *args
      Unused positional arguments.
    **kwargs
      Unused keyword arguments.

    Returns
    -------
    bool
      True (a worker stopped in time).
    """
    # Nothing to do.
    return True

  # Start, end, telemetry, notifications and the heartbeat steps of the loops.
  _init = _release = telemetry_maybe_add_message = _create_notification = _noop
  # The heartbeat loop's own steps.
  _start_heartbeat_ingress_worker = _stop_heartbeat_ingress_worker = _process_next_heartbeat_synchronously = _noop
  # Its receive steps.
  _maybe_reconnect_recv = maybe_fill_recv_buffer_wrapper = _noop


class _CommandComm(_Comm):
  """The command-and-control thread's state and stubs: its queue holds a command that cannot be prepared, then a good
  one."""

  def __init__(self):
    """
    Build the state the command loop reads, with the two commands queued and a connection up.

    Returns
    -------
    None
    """
    # The state of the send loops.
    super().__init__()
    # Queued: the bad command first, then the good one (id, (receiver id, address, command), time queued, retries).
    self._send_buff = collections.deque([(1, ("peer", "0xai_peer", BAD_COMMAND), 0, None),
                                         (2, ("peer", "0xai_peer", GOOD_COMMAND), 0, None)])
    # Commands logged in short, no timing debug, and the last send's outcome (handed to the transport).
    self.cfg_log_send_commands, self._debug_comm_loop_timings_enabled, self._last_send_outcome = False, False, "sent"
    # The logger the loop's error handler reads.
    self.log = SimpleNamespace(get_error_info=lambda return_err_val=False: "error")
    # The command being sent (its id), and how each command left the queue: "discard" or "handoff".
    self.current, self.finished = None, []

  def _mark_outbound_dequeued(self, msg_id, age_at_dequeue_seconds=0.0):
    """
    Note the command that left the queue as the current one.

    Parameters
    ----------
    msg_id : int
      Its id.
    age_at_dequeue_seconds : float, optional
      How long it waited; unused.

    Returns
    -------
    None
    """
    # The current command.
    self.current = msg_id

  def _prepare_command(self, command, receiver_address=None):
    """
    Prepare a command as the comm thread does before encrypting its payload: its JSON text, encoded as UTF-8.

    Parameters
    ----------
    command : dict
      The command.
    receiver_address : str, optional
      Its receiver; unused.

    Returns
    -------
    dict
      The command, as sent.

    Raises
    ------
    UnicodeEncodeError
      For a command whose text UTF-8 cannot encode.
    """
    # The encoding the encryption makes, which raises for a lone surrogate.
    json.dumps(command, ensure_ascii=False).encode()
    # Sent as it is.
    return command

  def _finish_outbound_current(self, transport_handoff=False, terminal_discard=False):
    """
    Record how the current command left the queue, when there is one.

    Parameters
    ----------
    transport_handoff : bool, optional
      True when it was handed to the transport.
    terminal_discard : bool, optional
      True when it was discarded for good.

    Returns
    -------
    None
    """
    if self.current is not None:
      # Discarded for good, or handed over; then no command is current, as the comm thread's accounting does.
      self.finished.append("discard" if terminal_discard else "handoff")
      # None current.
      self.current = None

  # The other step of the outbound accounting.
  _set_outbound_current_state = _Comm._noop


class TestUnpreparablePayload(unittest.TestCase):
  """Each send loop drops a payload it cannot prepare and goes on with the next one."""

  def test_each_loop_drops_a_payload_it_cannot_prepare_and_sends_the_next(self):
    """In the default, notifications and heartbeats loops, a payload whose preparation raises is dropped with one red
    line that names its path and the error, and the payload queued behind it is sent."""
    # Each loop: its mixin, its run method and the module whose sleep is made instant.
    loops = ((_DefaultCommMixin, "_run_thread_default", "default"),
             (_NotificationsCommMixin, "_run_thread_notifications", "notifications"),
             (_HeartbeatsCommMixin, "_run_thread_heartbeats", "heartbeats"))
    # Each loop.
    for mixin, run, module in loops:
      # One sub-test each.
      with self.subTest(loop=run):
        # The loop on the stub comm thread.
        comm = type("Comm", (mixin, _Comm), {})()
        # The loop's sleeps (its pace, and 5 s after an error) made instant.
        with mock.patch("naeural_core.comm.mixins.{}_comm_mixin.sleep".format(module)):
          # The loop, until 20 turns.
          getattr(comm, run)()
        # The red lines about the dropped payload.
        dropped = [msg for msg, color in comm.logged if color == "r" and "cannot be prepared" in msg]
        # The good payload sent once; one red line naming the bad one's path and the error.
        self.assertEqual(([msg["data"] for msg in comm.sent], len(dropped), any(
          "['node', 'stream', 'SIG', 'inst']" in msg and "surrogates not allowed" in msg for msg in dropped)),
                         (["ok"], 1, True))

  def test_the_command_loop_drops_a_command_it_cannot_prepare_and_sends_the_next(self):
    """In the command-and-control loop, a command whose preparation raises is dropped with one red line that names
    its action, its receiver and the error (not its payload) and is accounted as discarded; the command queued behind
    it is sent."""
    # The loop on the stub comm thread.
    comm = type("Comm", (_CommandControlCommMixin, _CommandComm), {})()
    # The loop's sleeps (its pace, and 5 s after an error) made instant.
    with mock.patch("naeural_core.comm.mixins.commandcontrol_comm_mixin.sleep"):
      # The loop, until 20 turns.
      comm._run_thread_command_and_control()
    # The red lines about the dropped command.
    dropped = [msg for msg, color in comm.logged if color == "r" and "cannot be prepared" in msg]
    # The good command sent once, the bad one discarded then the good one handed over; one red line naming the action,
    # the receiver and the error, and not the payload.
    self.assertEqual((comm.sent, comm.finished, len(dropped), any(
      "UPDATE_CONFIG" in msg and "<0xai_peer>" in msg and "surrogates not allowed" in msg and "NAME" not in msg
      for msg in dropped)), ([GOOD_COMMAND], ["discard", "handoff"], 1, True))


if __name__ == "__main__":
  # Run this module alone.
  unittest.main()

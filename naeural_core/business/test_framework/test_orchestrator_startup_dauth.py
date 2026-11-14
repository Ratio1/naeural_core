"""Start-up dAuth: rounds of requests until dAuth answers, gives up, the time limit passes or a stop is asked."""
import unittest
from types import SimpleNamespace
from unittest import mock

from naeural_core import constants as ct
from naeural_core.main import orchestrator as orchestrator_module
from naeural_core.main.orchestrator import Orchestrator


class _Dauth:
  """Stands for the blockchain manager: hands out the given answers, one per round, and records each call."""

  def __init__(self, answers, on_call=None):
    self.answers = list(answers)
    self.on_call = on_call
    self.max_tries = []
    self.eth_address = "0xnode"
    # What the blockchain manager says after a request when the answer did not report the node key as revoked.
    self.dauth_revoked_by = None

  def pending_node_key_announcement(self):
    """No new-key announcement waits for dAuth in these tests, so a round sends only the dAuth request."""
    return None

  def dauth_autocomplete(self, max_tries, **kwargs):  # pylint: disable=unused-argument
    self.max_tries.append(max_tries)
    if self.on_call is not None:
      self.on_call()
    return self.answers.pop(0)


class TestStartupDauth(unittest.TestCase):

  def _run(self, answers, on_call=None, stop_before=False):
    eng = Orchestrator.__new__(Orchestrator)  # the method needs only the fields set here
    eng.config_data = {ct.CONFIG_STARTUP_v2.K_EE_ID: "node-1"}
    eng._Orchestrator__evm_network = "mainnet"
    eng._return_code = ct.CODE_SHUTDOWN if stop_before else None
    eng._blockchain_manager = _Dauth(answers, on_call=(lambda: on_call(eng)) if on_call else None)
    lines, reloads = [], []
    eng.P = lambda msg, **kwargs: lines.append(msg)
    eng.log = SimpleNamespace(reload_config=lambda: reloads.append(1))
    # No 16-29 s pause between rounds in a test.
    with mock.patch.object(orchestrator_module, "sleep", lambda _seconds: None):
      eng._check_and_complete_environment_variables()
    warned = any("could not retrieve dAuth" in line for line in lines)
    return eng._blockchain_manager.max_tries, warned, len(reloads)

  def test_five_tries_in_the_first_round_and_two_in_each_later_one(self):
    # Two tries per later round: one failure that is not "busy" is retried at once instead of ending dAuth.
    max_tries, warned, reloads = self._run([{}, {}, {"EE_SUPERVISOR": "false"}])
    self.assertEqual(max_tries, [5, 2, 2])
    self.assertEqual((warned, reloads), (False, 1))

  def test_a_later_round_that_ends_on_none_is_reported(self):
    # None after a busy round: dAuth gave up on a failure that is not "busy"; the node runs on its local environment.
    max_tries, warned, reloads = self._run([{}, None])
    self.assertEqual(max_tries, [5, 2])
    self.assertEqual((warned, reloads), (True, 0))

  def test_none_in_the_first_round_is_not_reported(self):
    # What a VPN deployment gets from the SDK, without a request: nothing went wrong, so no warning at every start.
    max_tries, warned, reloads = self._run([None])
    self.assertEqual(max_tries, [5])
    self.assertEqual((warned, reloads), (False, 0))

  def test_a_stop_request_ends_the_wait_after_the_round_in_progress(self):
    # SIGTERM during start-up calls request_stop(); no new round starts after it.
    max_tries, warned, reloads = self._run([{}, {}, {"EE_SUPERVISOR": "false"}], on_call=Orchestrator.request_stop)
    self.assertEqual(max_tries, [5])
    self.assertEqual((warned, reloads), (True, 0))

  def test_a_revoked_key_answer_switches_the_key_once_and_the_next_round_asks_again(self):
    # Round 1: the oracle reports the key as revoked and grants nothing. The node switches key, and round 2,
    # a later round with two tries, asks with the new key and gets its variables.
    rounds = []

    def revoked_in_the_first_round(eng):
      eng._blockchain_manager.dauth_revoked_by = None if rounds else "0xoracle"
      rounds.append(1)

    with mock.patch.object(Orchestrator, "_rotate_revoked_node_key") as rotate:
      max_tries, warned, reloads = self._run([{}, {"EE_SUPERVISOR": "false"}], on_call=revoked_in_the_first_round)
    self.assertEqual(rotate.call_count, 1)
    self.assertEqual(max_tries, [5, 2])
    self.assertEqual((warned, reloads), (False, 1))

  def test_a_stop_requested_before_the_first_round_sends_no_request(self):
    max_tries, warned, reloads = self._run([{"EE_SUPERVISOR": "false"}], stop_before=True)
    self.assertEqual(max_tries, [])
    self.assertEqual((warned, reloads), (True, 0))


class TestStartupDauthBackoff(unittest.TestCase):
  """The pause after a round that brought no variables grows from round to round, and a stop request ends it."""

  def _run(self, answers, stop_after_slices=None):
    """
    Run the start-up dAuth rounds with the given answers and record the pause after each round.

    Parameters
    ----------
    answers : list
      What ``dauth_autocomplete`` returns, one per round: ``{}`` for no variables (a busy oracle or a refusal).
    stop_after_slices : int, optional
      Ask the node to stop once this many seconds of pause have been slept. Default None: no stop.

    Returns
    -------
    tuple
      ``(pauses, lines)``: the seconds slept after each round that was followed by a pause, and the lines logged.
    """
    # The method needs only the fields set here.
    eng = Orchestrator.__new__(Orchestrator)
    # The node's name, sent with the request.
    eng.config_data = {ct.CONFIG_STARTUP_v2.K_EE_ID: "node-1"}
    # The network name of the first log line.
    eng._Orchestrator__evm_network = "mainnet"
    # No stop requested yet.
    eng._return_code = None
    # The seconds slept since the last request, and the pauses of the finished rounds.
    slept, pauses = [], []
    # Each request closes the pause that preceded it.
    eng._blockchain_manager = _Dauth(answers, on_call=lambda: pauses.append(sum(slept)) or slept.clear())
    # The lines the method logs.
    lines = []
    # Record each line.
    eng.P = lambda msg, **kwargs: lines.append(msg)
    # The config reload after a granted answer is not read here.
    eng.log = SimpleNamespace(reload_config=lambda: None)

    def fake_sleep(seconds):
      """
      Record a sleep instead of waiting, and ask for the stop when the test wants one.

      Parameters
      ----------
      seconds : int or float
        The length of the sleep.

      Returns
      -------
      None
      """
      # One more piece of the current pause.
      slept.append(seconds)
      if stop_after_slices is not None and len(slept) == stop_after_slices:
        # What the SIGTERM handler calls.
        eng.request_stop()

    # No real waiting, and the random part of every pause fixed at 5 s: the first pause is 15 + 5 = 20 s.
    with mock.patch.object(orchestrator_module, "sleep", fake_sleep), \
         mock.patch.object(orchestrator_module.np.random, "randint", lambda low, high: 5):
      # The start-up dAuth rounds.
      eng._check_and_complete_environment_variables()
    # The pause after the last round, when one was slept and no request followed it.
    pauses.append(sum(slept))
    # The first entry is the time before the first request, always 0: dropped.
    return pauses[1:], lines

  def test_the_pause_doubles_after_each_empty_round_up_to_300_seconds(self):
    """Six rounds without variables are followed by pauses of 20, 40, 80, 160, 300 and 300 s, not six of 20 s."""
    # Six empty answers, then the variables.
    pauses, lines = self._run([{}] * 6 + [{"EE_SUPERVISOR": "false"}])
    # Doubling from the first pause, capped at 300 s; the last entry is 0, as nothing follows the granted round.
    self.assertEqual(pauses, [20, 40, 80, 160, 300, 300, 0])
    # Each retry line says when the next request comes.
    self.assertIn("next try in 160s", [line for line in lines if "Retrying dAuth" in line][3])

  def test_a_stop_request_ends_the_pause_in_progress(self):
    """A stop asked 3 s into a 20 s pause ends the pause there, and no further request is sent."""
    # Answers for three rounds; only the first is asked.
    pauses, lines = self._run([{}, {}, {"EE_SUPERVISOR": "false"}], stop_after_slices=3)
    # One pause, cut at the third second.
    self.assertEqual(pauses, [3])
    # The node goes on without dAuth variables and says so.
    self.assertTrue(any("could not retrieve dAuth" in line for line in lines))


if __name__ == "__main__":
  unittest.main()

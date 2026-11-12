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

  def test_a_stop_requested_before_the_first_round_sends_no_request(self):
    max_tries, warned, reloads = self._run([{"EE_SUPERVISOR": "false"}], stop_before=True)
    self.assertEqual(max_tries, [])
    self.assertEqual((warned, reloads), (True, 0))


if __name__ == "__main__":
  unittest.main()

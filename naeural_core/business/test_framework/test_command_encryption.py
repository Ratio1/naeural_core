"""A command a node sends always leaves encrypted for its receiver, and a receiver reads it as it reads any other."""

# A copy of the command, to show the caller's object is not changed.
import copy
# The decrypted body is JSON text.
import json
# The key files of the test identities.
import os
# A folder for the key files.
import tempfile
# The test framework.
import unittest
# Stand-ins for the node configuration and the block engine view of a manager.
from types import SimpleNamespace
# Stubs for what the receiver does after the command is accepted.
from unittest import mock

# The node logger the block engines need, and the field and command names.
from naeural_core import Logger, constants as ct
# The communicator whose command preparation is under test.
from naeural_core.comm.base.base_comm_thread import BaseCommThread
# The receiver of commands.
from naeural_core.comm.communication_manager import CommunicationManager
# Real identities: keys, signing, encryption.
from ratio1.bc import DefaultBlockEngine

# The command field names, as the sender writes and the receiver reads them.
SEND = ct.COMMS.COMM_SEND_MESSAGE


class _Sender:
  """
  The parts of a command communicator that prepare a command, around a real block engine.

  Parameters
  ----------
  engine : DefaultBlockEngine
    The sending node's identity.
  log : Logger
    The node logger (JSON helpers).

  Returns
  -------
  None

  Examples
  --------
  >>> sender = _Sender(engine, log)
  >>> sender._prepare_command(command, receiver.address)[SEND.K_EE_IS_ENCRYPTED]
  True
  """
  # The real preparation of a command.
  _prepare_command = BaseCommThread._prepare_command
  # The real debug print of a published message.
  payload_debugger = BaseCommThread.payload_debugger

  def __init__(self, engine, log):
    """
    Keep the identity and the logger, with the configuration a released node has.

    Parameters
    ----------
    engine : DefaultBlockEngine
      The sending node's identity.
    log : Logger
      The node logger.

    Returns
    -------
    None

    Examples
    --------
    >>> _Sender(engine, log).cfg_encrypted_comms
    False
    """
    # The identity that encrypts and signs.
    self.bc_engine = engine
    # The logger whose JSON helpers the preparation uses.
    self.log = log
    # The default value of the setting: payload encryption off.
    self.cfg_encrypted_comms = False
    # No filter of the debug print.
    self.cfg_debug_log_payloads_pipelines, self.cfg_debug_log_payloads_signatures = [], []
    # No message is saved by the debug print.
    self.cfg_debug_log_payloads_save_filter = None
    # The lines the communicator logs, with their color.
    self.lines = []
    return

  def P(self, text, color=None, **kwargs):
    """
    Record a log line.

    Parameters
    ----------
    text : str
      The line.
    color : str, optional
      Its color.
    **kwargs
      Unused.

    Returns
    -------
    None

    Examples
    --------
    >>> sender.P('line', color='r')
    """
    # Kept with its color, so a test can find a red line.
    self.lines.append((text, color))
    return

  def json_dumps(self, value, **kwargs):
    """
    JSON text of a value, as the communicator's helper gives it.

    Parameters
    ----------
    value : object
      What to encode.
    **kwargs
      Passed to ``json.dumps``.

    Returns
    -------
    str
      The JSON text.

    Examples
    --------
    >>> sender.json_dumps({'a': 1})
    '{"a": 1}'
    """
    # The standard encoder is enough for the commands of these tests.
    return json.dumps(value, **kwargs)


class TestNodeCommandsLeaveEncrypted(unittest.TestCase):

  @classmethod
  def setUpClass(cls):
    """
    Create a sending node and a receiving node with real keys, in a temporary folder.

    Returns
    -------
    None
    """
    # The key files live here for the length of the class.
    cls._tmp = tempfile.TemporaryDirectory(prefix="cmd-encryption-")
    # A logger that writes no file.
    cls.log = Logger(
      "CMDENC", base_folder=cls._tmp.name, app_folder="_local_cache", no_folders_no_save=True, DEBUG=False,
    )

    def engine(name):
      """
      Create an identity with its own key file.

      Parameters
      ----------
      name : str
        Name of the identity and of its key file.

      Returns
      -------
      DefaultBlockEngine
        The identity.
      """
      # A new key pair in the temporary folder.
      return DefaultBlockEngine(
        name=name, log=cls.log, eth_enabled=False, verbosity=0,
        config={"PEM_FILE": os.path.join(cls._tmp.name, name + ".pem")},
      )

    # The node that sends the command (an oracle that deploys, for example).
    cls.sender = engine("sender")
    # The node the command is for.
    cls.receiver = engine("receiver")

  @classmethod
  def tearDownClass(cls):
    """
    Remove the key files.

    Returns
    -------
    None
    """
    # The folder and everything in it.
    cls._tmp.cleanup()

  @staticmethod
  def command():
    """
    A command as the communication manager queues it for the command communicator.

    Returns
    -------
    dict
      An UPDATE_CONFIG command whose payload holds a value that must not be readable on the wire.
    """
    # The action and payload are the two fields the receiver acts on.
    return {
      ct.EE_ID: "receiver",
      SEND.K_ACTION: ct.COMMANDS.UPDATE_CONFIG,
      SEND.K_PAYLOAD: {"NAME": "zz-test", "TYPE": "Void", "PLUGINS": [], "ENV": {"APP_PASSWORD": "not-for-the-wire"}},
      SEND.K_INITIATOR_ID: "sender",
      SEND.K_SESSION_ID: "s1",
    }

  def receiving_manager(self, engine, allowed):
    """
    Build the communication manager of a receiving node that allows the given senders.

    Parameters
    ----------
    engine : DefaultBlockEngine
      The receiving node's identity.
    allowed : list of DefaultBlockEngine
      The identities whose commands the node accepts.

    Returns
    -------
    CommunicationManager
      The manager, with ``process_decrypted_command`` replaced by a recorder.
    """
    # The addresses on the node's allowed list, without their prefix.
    allowed_addresses = {engine._remove_prefix(one.address) for one in allowed}
    # No constructor: the receive path needs only the members set below.
    manager = CommunicationManager.__new__(CommunicationManager)
    # A secured node named "receiver".
    manager.log = SimpleNamespace(
      config_data={ct.CONFIG_STARTUP_v2.K_SECURED: True, ct.CONFIG_STARTUP_v2.K_EE_ID: "receiver"},
      match_template=self.log.match_template, version="test",
    )
    # The node's identity as the manager uses it: verify, decrypt and the allowed list.
    manager.shmem = {ct.BLOCKCHAIN_MANAGER: SimpleNamespace(
      address=engine.address, eth_enabled=False, eth_address=None, verify=engine.verify, decrypt=engine.decrypt,
      is_allowed=lambda addr: engine._remove_prefix(addr) in allowed_addresses,
    )}
    # No environment overrides: unencrypted commands are still accepted, as on every released node.
    manager._environment_variables = {}
    # The shipped command whitelist.
    manager._load_whitelist_commands = lambda: ct.TEMPLATE_WHITELIST_COMMANDS
    # Notifications and log lines are not read here.
    manager._create_notification, manager.P = mock.Mock(), mock.Mock()
    # What the manager hands to the node once the command is decrypted and allowed.
    manager.process_decrypted_command = mock.Mock()
    # The receiving manager.
    return manager

  def test_a_command_is_encrypted_whatever_the_payload_encryption_setting_is(self):
    """With ENCRYPTED_COMMS off (the default) a prepared command has no readable ACTION or PAYLOAD."""
    # A communicator of the sending node; its payload encryption setting is off.
    sender = _Sender(self.sender, self.log)
    # The command, and a copy to compare with afterwards.
    original = self.command()
    # What the caller handed over.
    expected = copy.deepcopy(original)
    # The command as it goes to the wire.
    prepared = sender._prepare_command(original, self.receiver.address)
    # The caller's command is untouched.
    self.assertEqual(original, expected)
    # Marked encrypted, with the body in the encrypted field.
    self.assertIs(prepared[SEND.K_EE_IS_ENCRYPTED], True)
    # Neither field the receiver acts on is readable.
    self.assertNotIn(SEND.K_ACTION, prepared)
    # The payload neither.
    self.assertNotIn(SEND.K_PAYLOAD, prepared)
    # And the value inside the payload appears nowhere in the outgoing text.
    self.assertNotIn("not-for-the-wire", json.dumps(prepared))
    # The routing fields stay readable: the receiver and the sender.
    self.assertEqual((prepared[ct.EE_ID], prepared[SEND.K_SENDER_ADDR]), ("receiver", self.sender.address))

  def test_the_receiver_reads_the_encrypted_command(self):
    """A signed, encrypted command is decrypted by its receiver and handed on with the original action and payload."""
    # The same path for a command to another node and for one a node sends to itself.
    for label, target in (("other node", self.receiver), ("itself", self.sender)):
      # Each target is reported on its own.
      with self.subTest(target=label):
        # A communicator of the sending node.
        sender = _Sender(self.sender, self.log)
        # The command, addressed to the target.
        prepared = sender._prepare_command(self.command(), target.address)
        # Signed by the sender, as the communicator does before it publishes.
        self.sender.sign(prepared)
        # What arrives: the JSON text decoded again.
        received = json.loads(json.dumps(prepared))
        # The target's manager, which allows the sender.
        manager = self.receiving_manager(target, allowed=[self.sender])
        # The receive path of a node.
        manager.process_command_message(received)
        # The command was accepted once.
        manager.process_decrypted_command.assert_called_once()
        # What was handed to the node.
        handed = manager.process_decrypted_command.call_args.kwargs
        # The original action.
        self.assertEqual(handed["action"], ct.COMMANDS.UPDATE_CONFIG)
        # The original payload, value included.
        self.assertEqual(handed["payload"]["ENV"], {"APP_PASSWORD": "not-for-the-wire"})
        # From the signer, with a valid signature.
        self.assertEqual((handed["sender_addr"], handed["validated_command"]), (self.sender.address, True))

  def test_a_receiver_that_is_not_the_addressee_cannot_read_it(self):
    """Another node that sees the same message cannot decrypt it and hands nothing on."""
    # A communicator of the sending node.
    sender = _Sender(self.sender, self.log)
    # A command for the receiver.
    prepared = sender._prepare_command(self.command(), self.receiver.address)
    # Signed by the sender.
    self.sender.sign(prepared)
    # A third identity that also allows the sender.
    other = DefaultBlockEngine(
      name="other", log=self.log, eth_enabled=False, verbosity=0,
      config={"PEM_FILE": os.path.join(self._tmp.name, "other.pem")},
    )
    # Its manager; the node name matches so only the decryption decides.
    manager = self.receiving_manager(other, allowed=[self.sender])
    # The receive path of that node.
    manager.process_command_message(json.loads(json.dumps(prepared)))
    # Nothing readable came out: no action was handed on.
    for call in manager.process_decrypted_command.call_args_list:
      # A call without an action is not a processed command.
      self.assertIsNone(call.kwargs["action"])

  def test_a_command_that_cannot_be_encrypted_does_not_leave_in_plaintext(self):
    """Without a usable receiver address the preparation raises; it never returns a readable command."""
    # A communicator of the sending node.
    sender = _Sender(self.sender, self.log)
    # No address, and an address no key can be read from.
    for bad in (None, "0xai_not-an-address"):
      # Each address is reported on its own.
      with self.subTest(receiver=bad):
        # The preparation fails loudly; the command loop drops such a command with one red line.
        with self.assertRaises(Exception):
          # No return value to inspect: a readable command would have been returned here before.
          sender._prepare_command(self.command(), bad)

  def test_the_debug_print_takes_an_encrypted_command_for_a_command(self):
    """With DEBUG_LOG_PAYLOADS on, a published encrypted command prints no 'Failed debug' line; a payload prints its path."""
    # A communicator of the sending node.
    sender = _Sender(self.sender, self.log)
    # The command as published: encrypted, so without an ACTION field.
    prepared = sender._prepare_command(self.command(), self.receiver.address)
    # The debug print of a published command.
    sender.payload_debugger(json.dumps(prepared))
    # Nothing printed: a command is recognised and skipped, as a readable one was.
    self.assertEqual(sender.lines, [])
    # A payload, which carries its path.
    payload = {ct.PAYLOAD_DATA.EE_PAYLOAD_PATH: ["node", "pipeline", "SIGNATURE", "instance"]}
    # The debug print of a published payload.
    sender.payload_debugger(json.dumps(payload))
    # One line with the payload's path, not in an error color.
    self.assertEqual(len(sender.lines), 1)
    # The path is in it.
    self.assertIn("pipeline", sender.lines[0][0])
    # Not an error.
    self.assertIsNone(sender.lines[0][1])


if __name__ == "__main__":
  # Run this file's tests when it is started directly.
  unittest.main()

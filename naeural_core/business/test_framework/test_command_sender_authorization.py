"""Command authorization must use the address that signed the command envelope."""

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from naeural_core import Logger, constants as ct
from naeural_core.comm import communication_manager
from naeural_core.comm.communication_manager import CommunicationManager, node_is_secured
from ratio1.bc import DefaultBlockEngine


RECV = ct.COMMS.COMM_RECV_MESSAGE


class TestCommandSenderAuthorization(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls._tmp = tempfile.TemporaryDirectory(prefix="cmd-sender-auth-")
    cls.log = Logger(
      "CMDAUTH", base_folder=cls._tmp.name, app_folder="_local_cache",
      no_folders_no_save=True, DEBUG=False,
    )

    def engine(name):
      return DefaultBlockEngine(
        name=name, log=cls.log, eth_enabled=False, verbosity=0,
        config={"PEM_FILE": os.path.join(cls._tmp.name, name + ".pem")},
      )

    cls.node = engine("node")
    cls.owner = engine("owner")
    cls.attacker = engine("attacker")

  @classmethod
  def tearDownClass(cls):
    cls._tmp.cleanup()

  def manager(self, secured=True):
    """Builds a manager whose allowed list holds only the owner (and the node itself)."""
    node = self.node
    allowed = {node._remove_prefix(self.owner.address), node._remove_prefix(node.address)}
    manager = CommunicationManager.__new__(CommunicationManager)
    manager.log = SimpleNamespace(
      config_data={ct.CONFIG_STARTUP_v2.K_SECURED: secured, ct.CONFIG_STARTUP_v2.K_EE_ID: "node"},
      match_template=self.log.match_template,
      version="test",
    )
    manager.shmem = {ct.BLOCKCHAIN_MANAGER: SimpleNamespace(
      address=node.address,
      eth_enabled=False,
      eth_address=None,
      verify=node.verify,
      decrypt=node.decrypt,
      is_allowed=lambda addr: node._remove_prefix(addr) in allowed,
    )}
    manager._environment_variables = {}
    manager._load_whitelist_commands = lambda: ct.TEMPLATE_WHITELIST_COMMANDS
    manager._create_notification = mock.Mock()
    manager.P = mock.Mock()
    manager.process_decrypted_command = mock.Mock()
    return manager

  def command(self, signer, body, encrypted=True):
    """Builds a signed command as the SDK sends it, with `body` as the encrypted part."""
    if encrypted:
      critical = {
        RECV.K_EE_IS_ENCRYPTED: True,
        RECV.K_EE_ENCRYPTED_DATA: signer.encrypt(
          plaintext=json.dumps(body), receiver_address=self.node.address,
        ),
      }
    else:
      critical = {RECV.K_EE_IS_ENCRYPTED: False, **body}
    msg = {
      **critical,
      ct.PAYLOAD_DATA.EE_DESTINATION: self.node.address,
      RECV.K_EE_ID: "node",
      RECV.K_SESSION_ID: "s1",
      RECV.K_INITIATOR_ID: "i1",
      RECV.K_SENDER_ADDR: signer.address,
    }
    signer.sign(msg)
    return msg

  @staticmethod
  def deploy_body(**extra):
    return {
      RECV.K_ACTION: ct.COMMANDS.UPDATE_CONFIG,
      RECV.K_PAYLOAD: {"NAME": "zz-test", "TYPE": "Void", "PLUGINS": []},
      **extra,
    }

  def test_allowed_signer_command_is_processed(self):
    manager = self.manager()
    manager.process_command_message(self.command(self.owner, self.deploy_body()))
    manager.process_decrypted_command.assert_called_once()
    kwargs = manager.process_decrypted_command.call_args.kwargs
    self.assertEqual(kwargs["sender_addr"], self.owner.address)
    self.assertTrue(kwargs["validated_command"])
    self.assertEqual(kwargs["action"], ct.COMMANDS.UPDATE_CONFIG)

  def test_unknown_signer_command_is_dropped(self):
    manager = self.manager()
    manager.process_command_message(self.command(self.attacker, self.deploy_body()))
    manager.process_decrypted_command.assert_not_called()

  def test_encrypted_body_cannot_override_sender(self):
    for claimed in (self.owner.address, self.node.address):
      with self.subTest(claimed=claimed):
        manager = self.manager()
        body = self.deploy_body(**{RECV.K_SENDER_ADDR: claimed, RECV.K_EE_ID: "other"})
        msg = self.command(self.attacker, body)
        manager.process_command_message(msg)
        manager.process_decrypted_command.assert_not_called()
        self.assertEqual(msg[RECV.K_SENDER_ADDR], self.attacker.address)
        self.assertEqual(msg[RECV.K_EE_ID], "node")

  def test_secured_is_forced_outside_vpn(self):
    with mock.patch.object(communication_manager, "EE_VPN_IMPL", False):
      self.assertTrue(node_is_secured({ct.CONFIG_STARTUP_v2.K_SECURED: False}))
      self.assertTrue(node_is_secured({}))
    with mock.patch.object(communication_manager, "EE_VPN_IMPL", True):
      self.assertFalse(node_is_secured({ct.CONFIG_STARTUP_v2.K_SECURED: False}))
      self.assertFalse(node_is_secured({}))
      self.assertTrue(node_is_secured({ct.CONFIG_STARTUP_v2.K_SECURED: True}))

  def test_non_vpn_node_drops_invalid_signature_despite_unsecured_config(self):
    body = {RECV.K_ACTION: ct.COMMANDS.FULL_HEARTBEAT, RECV.K_PAYLOAD: {}}
    msg = self.command(self.attacker, body, encrypted=False)
    msg[RECV.K_SENDER_ADDR] = self.owner.address
    manager = self.manager(secured=False)
    with mock.patch.object(communication_manager, "EE_VPN_IMPL", False):
      manager.process_command_message(msg)
    manager.process_decrypted_command.assert_not_called()

  def test_unsecured_vpn_node_does_not_authorize_invalid_signature(self):
    msg = self.command(self.attacker, self.deploy_body(), encrypted=False)
    msg[RECV.K_SENDER_ADDR] = self.owner.address
    manager = self.manager(secured=False)
    with mock.patch.object(communication_manager, "EE_VPN_IMPL", True):
      manager.process_command_message(msg)
    manager.process_decrypted_command.assert_not_called()

  def test_non_vpn_full_heartbeat_requires_authorized_sender(self):
    body = {RECV.K_ACTION: ct.COMMANDS.FULL_HEARTBEAT, RECV.K_PAYLOAD: {}}
    with mock.patch.object(communication_manager, "EE_VPN_IMPL", False):
      manager = self.manager()
      manager.process_command_message(self.command(self.attacker, body))
      manager.process_decrypted_command.assert_not_called()

      manager = self.manager()
      manager.process_command_message(self.command(self.owner, body))
      manager.process_decrypted_command.assert_called_once()

  def test_non_vpn_timers_heartbeat_stays_whitelisted(self):
    body = {RECV.K_ACTION: ct.COMMANDS.TIMERS_ONLY_HEARTBEAT, RECV.K_PAYLOAD: {}}
    manager = self.manager()
    with mock.patch.object(communication_manager, "EE_VPN_IMPL", False):
      manager.process_command_message(self.command(self.attacker, body))
    manager.process_decrypted_command.assert_called_once()

  def test_vpn_full_heartbeat_stays_whitelisted(self):
    body = {RECV.K_ACTION: ct.COMMANDS.FULL_HEARTBEAT, RECV.K_PAYLOAD: {}}
    manager = self.manager()
    with mock.patch.object(communication_manager, "EE_VPN_IMPL", True):
      manager.process_command_message(self.command(self.attacker, body))
    manager.process_decrypted_command.assert_called_once()

  def test_unsecured_vpn_node_keeps_whitelisted_commands_without_valid_signature(self):
    body = {RECV.K_ACTION: ct.COMMANDS.FULL_HEARTBEAT, RECV.K_PAYLOAD: {}}
    msg = self.command(self.attacker, body, encrypted=False)
    msg[RECV.K_SENDER_ADDR] = self.owner.address
    manager = self.manager(secured=False)
    with mock.patch.object(communication_manager, "EE_VPN_IMPL", True):
      manager.process_command_message(msg)
    manager.process_decrypted_command.assert_called_once()
    self.assertFalse(manager.process_decrypted_command.call_args.kwargs["validated_command"])

if __name__ == "__main__":
  unittest.main()

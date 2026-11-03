"""Non-VPN, non-seed nodes must start every run with an empty `authorized_addrs`."""

import os
import tempfile
import unittest
from unittest import mock

from naeural_core import Logger
from naeural_core.main.orchestrator_mixins import managers_init
from naeural_core.main.orchestrator_mixins.managers_init import _ManagersInitMixin
from ratio1.bc import DefaultBlockEngine


DAUTH_URL_KEY = "EE_DAUTH_URL"


class TestAuthorizedAddrsReset(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls._tmp = tempfile.TemporaryDirectory(prefix="authorized-addrs-reset-")
    cls.log = Logger(
      "ADDRRST", base_folder=cls._tmp.name, app_folder="_local_cache",
      no_folders_no_save=True, DEBUG=False,
    )

    def engine(name):
      return DefaultBlockEngine(
        name=name, log=cls.log, eth_enabled=False, verbosity=0,
        config={"PEM_FILE": os.path.join(cls._tmp.name, name + ".pem")},
      )

    cls.bc = engine("node")
    cls.planted = engine("planted")

  @classmethod
  def tearDownClass(cls):
    cls._tmp.cleanup()

  def setUp(self):
    path = self.bc._get_allowed_file()
    if os.path.exists(path + ".bak"):
      os.remove(path + ".bak")
    with open(path, "wt") as fh:
      fh.write("\n")
    self.bc.add_address_to_allowed(self.planted.address)
    self.assertIn(self.bc._remove_prefix(self.planted.address), self.bc.allowed_list)

    self.mixin = _ManagersInitMixin.__new__(_ManagersInitMixin)
    self.mixin._blockchain_manager = self.bc
    self.mixin.P = lambda *args, **kwargs: None

  def reset(self, dauth_url=None, vpn=False):
    with mock.patch.dict(os.environ), mock.patch.object(managers_init, "EE_VPN_IMPL", vpn):
      os.environ.pop(DAUTH_URL_KEY, None)
      if dauth_url is not None:
        os.environ[DAUTH_URL_KEY] = dauth_url
      self.mixin._maybe_reset_authorized_addrs()

  def test_regular_node_drops_all_entries_and_keeps_backup(self):
    for dauth_url in [None, "https://dauth.ratio1.ai/get_auth_data"]:
      with self.subTest(dauth_url=dauth_url):
        self.bc.add_address_to_allowed(self.planted.address)
        self.reset(dauth_url=dauth_url)
        self.assertEqual(self.bc.allowed_list, [])
        with open(self.bc._get_allowed_file() + ".bak", "rt") as fh:
          self.assertIn(self.bc._remove_prefix(self.planted.address), fh.read())

  def test_dauth_entries_can_be_added_after_reset(self):
    self.reset()
    self.bc.add_address_to_allowed(self.planted.address)
    self.assertEqual(self.bc.allowed_list, [self.bc._remove_prefix(self.planted.address)])

  def test_seed_node_keeps_entries(self):
    self.reset(dauth_url="seed_node")
    self.assertIn(self.bc._remove_prefix(self.planted.address), self.bc.allowed_list)
    self.assertFalse(os.path.exists(self.bc._get_allowed_file() + ".bak"))

  def test_vpn_node_keeps_entries(self):
    self.reset(vpn=True)
    self.assertIn(self.bc._remove_prefix(self.planted.address), self.bc.allowed_list)
    self.assertFalse(os.path.exists(self.bc._get_allowed_file() + ".bak"))


if __name__ == "__main__":
  unittest.main()

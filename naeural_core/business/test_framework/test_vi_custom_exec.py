import unittest

from naeural_core import constants as ct


class TestViCustomExec(unittest.TestCase):
  def test_signature_is_not_blocked(self):
    self.assertNotIn("VI_CUSTOM_EXEC", ct.PLUGIN_SEARCH.BLOCKED_BIZ_PLUGINS)

  def test_plugin_class_keeps_custom_exec_01_contract(self):
    from naeural_core.business.default.custom_exec_01 import CustomExec01Plugin
    from naeural_core.business.default.vi_custom_exec import ViCustomExecPlugin
    self.assertTrue(issubclass(ViCustomExecPlugin, CustomExec01Plugin))
    self.assertEqual(ViCustomExecPlugin.__name__, "ViCustomExec" + ct.PLUGIN_SEARCH.SUFFIX_BIZ_PLUGINS)
    for key in ("CODE", "DEBUG_EXEC", "RESULT_KEY", "ERROR_KEY", "WARNING_KEY"):
      self.assertIn(key, ViCustomExecPlugin.CONFIG)
    self.assertEqual(ViCustomExecPlugin.CONFIG["VALIDATION_RULES"]["CODE"], {"TYPE": "str"})


if __name__ == "__main__":
  unittest.main()

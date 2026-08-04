import ast
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


_ENTRYPOINT_PATH = pathlib.Path(__file__).with_name("entrypoint.py")


class _LoggerStub:

  def __init__(self):
    self.messages = []

  def P(self, message, **kwargs):
    self.messages.append((message, kwargs))


def _load_install_function():
  source = _ENTRYPOINT_PATH.read_text()
  module = ast.parse(source)
  function_node = next(
    node for node in module.body
    if isinstance(node, ast.FunctionDef)
    and node.name == "install_package_with_constraints_to_target"
  )
  isolated_module = ast.Module(body=[function_node], type_ignores=[])
  ast.fix_missing_locations(isolated_module)
  namespace = {
    "Logger": object,
    "os": os,
    "subprocess": subprocess,
    "sys": sys,
    "tempfile": tempfile,
  }
  exec(compile(isolated_module, str(_ENTRYPOINT_PATH), "exec"), namespace)
  return namespace["install_package_with_constraints_to_target"]


class TestEntrypointPackageInstall(unittest.TestCase):

  def test_editable_packages_are_excluded_from_generated_constraints(self):
    install_package = _load_install_function()
    logger = _LoggerStub()

    with tempfile.TemporaryDirectory() as destination:
      with mock.patch.object(subprocess, "run") as run:
        success = install_package(
          l=logger,
          package_name="example-package",
          destination=destination,
        )

    self.assertTrue(success)
    self.assertEqual(run.call_count, 2)
    freeze_command = run.call_args_list[0].args[0]
    self.assertEqual(
      freeze_command,
      [sys.executable, "-m", "pip", "freeze", "--exclude-editable"],
    )


if __name__ == "__main__":
  unittest.main()

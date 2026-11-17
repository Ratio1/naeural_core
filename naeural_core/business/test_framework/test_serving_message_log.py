"""The serving manager logs which message a serving process sent, never the message's content."""

# The manager's method is taken from its source, without importing the serving runtime.
import ast
# The clock the method reads.
import time
# The test framework.
import unittest
# The source file of the manager.
from pathlib import Path
# The stand-in for the manager object.
from types import SimpleNamespace

# The serving manager's source.
_SOURCE = Path(__file__).resolve().parents[2] / "serving" / "serving_manager.py"


def _wait_for_result():
  """
  Compile ``ServingManager._wait_for_result`` and the constants it reads from the manager's source.

  Returns
  -------
  function
    The production method as a plain function, to call with a stand-in manager as ``self``.

  Examples
  --------
  >>> wait = _wait_for_result()
  >>> wait(manager, "SERVER")
  ('GET_CONFIG', {...})
  """
  # The parsed source of the module.
  tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
  # The class of message field and type names the method reads.
  constants = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SMConst")
  # The manager class.
  manager = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ServingManager")
  # The method under test, exactly as it is in the source.
  method = next(node for node in manager.body if isinstance(node, ast.FunctionDef) and node.name == "_wait_for_result")
  # The names the method needs at module level: only the clock.
  namespace = {"time": time.time}
  # Compile the constants and the method alone: importing the module would import the serving runtime.
  exec(compile(ast.Module(body=[constants, method], type_ignores=[]), str(_SOURCE), "exec"), namespace)  # noqa: S102
  # The method as a function.
  return namespace["_wait_for_result"]


class TestServingMessageLog(unittest.TestCase):

  def test_a_configuration_answer_is_logged_by_type_only(self):
    """The answer to GET_CONFIG reaches the caller whole, and the log line names its type and server, no value."""
    # The configuration a serving process answers with; it can hold credentials.
    config = {"HF_TOKEN": "hf_canary_123456", "MODEL_NAME": "canary-model", "SKIP_ERRORS": True}
    # The lines the manager logs.
    lines = []
    # A stand-in manager whose serving process answers the configuration request.
    manager = SimpleNamespace(
      get_server_name=lambda name: name, full_debug=False, _server_wait_time=lambda name: 1,
      _wait_for_comm_message=lambda **kwargs: {"TYPE": "GET_CONFIG", "DATA": config},
      P=lambda text, **kwargs: lines.append(text),
    )
    # The production method: one message that is not a RESULT, and no callback for it.
    result = _wait_for_result()(manager, "LLM_SERVER")
    # The caller gets the type and the whole configuration, as before.
    self.assertEqual(result, ("GET_CONFIG", config))
    # One line for the message.
    self.assertEqual(len(lines), 1)
    # It names the message type and the serving process.
    self.assertTrue("GET_CONFIG" in lines[0] and "LLM_SERVER" in lines[0])
    # Two values and two keys of the configuration.
    for value in ("hf_canary_123456", "canary-model", "HF_TOKEN", "SKIP_ERRORS"):
      # No value of the configuration, and no key either, is in the log.
      self.assertNotIn(value, lines[0])


if __name__ == "__main__":
  # Run this file's tests when it is started directly.
  unittest.main()

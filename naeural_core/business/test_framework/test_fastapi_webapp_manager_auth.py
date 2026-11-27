"""A web app's local process manager gets a key of its own at every start, and the key is not logged."""

# The plugin's methods are taken from its source, without importing the plugin stack.
import ast
# The error a manager answers a wrong key with.
import multiprocessing
# The folder the asset step writes its environment files to, and the name helpers the methods use.
import os
# The asset step's module-level names.
import shutil
# A folder for the environment files.
import tempfile
# The methods are indented into a harness class.
import textwrap
# The plugin's start uses a lock and an event.
import threading
# The test framework.
import unittest
# The source files of the plugin and of its base.
from pathlib import Path
# The stand-in manager object.
from types import SimpleNamespace

# The template engine, to render the key as the server template does.
from jinja2 import Template

# The real manager helpers: start one, connect to one.
from naeural_core.utils.uvicorn_fast_api_ipc_manager import get_client_manager, get_server_manager
# The real environment selector used when the extracted asset method writes its snapshots.
from naeural_core.utils.process_credentials import credential_env_names

# The package folder.
_PACKAGE = Path(__file__).resolve().parents[2]
# The FastAPI web app plugin.
_PLUGIN = _PACKAGE / "business" / "default" / "web_app" / "fast_api_web_app.py"
# Its base, which holds the asset step.
_BASE = _PACKAGE / "business" / "base" / "web_app" / "base_web_app_plugin.py"


def _method_source(path, class_name, method_name):
  """
  The source text of one method of a class, as it is in the file.

  Parameters
  ----------
  path : pathlib.Path
    The source file.
  class_name : str
    The class that holds the method.
  method_name : str
    The method.

  Returns
  -------
  str
    The method's source, indented for a class body.

  Examples
  --------
  >>> _method_source(_PLUGIN, "FastApiWebAppPlugin", "on_init").lstrip().startswith("def on_init")
  True
  """
  # The file's text.
  source = path.read_text(encoding="utf-8")
  # The class in the parsed file.
  class_def = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == class_name)
  # The method in the class.
  method = next(node for node in class_def.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
  # Its text, indented as a class member.
  return textwrap.indent(textwrap.dedent(ast.get_source_segment(source, method, padded=True)), "  ")


def _started_plugin():
  """
  Run the real ``FastApiWebAppPlugin.on_init`` on a harness whose manager is a stand-in.

  Returns
  -------
  tuple
    ``(plugin, keys)``: the harness after its start, and the list of keys the manager was started with.

  Examples
  --------
  >>> plugin, keys = _started_plugin()
  >>> keys == [plugin.manager_auth]
  True
  """
  # The keys handed to the manager helper.
  keys = []

  def fake_manager(auth):
    """
    Stand in for ``get_server_manager``: record the key and return a manager with two queues and an address.

    Parameters
    ----------
    auth : bytes
      The key the plugin starts its manager with.

    Returns
    -------
    types.SimpleNamespace
      The stand-in manager.
    """
    # The key of this start.
    keys.append(auth)
    # What on_init reads from a manager.
    return SimpleNamespace(get_server_queue=lambda: "server", get_client_queue=lambda: "client",
                           address=("127.0.0.1", 12345))

  # A base whose start does nothing, then the real on_init in a class named as the plugin (its super() call).
  class_source = (
    "class Base:\n"
    "  def on_init(self):\n"
    "    return\n\n"
    "class FastApiWebAppPlugin(Base):\n" + _method_source(_PLUGIN, "FastApiWebAppPlugin", "on_init")
  )
  # The module-level names on_init uses.
  namespace = {"os": os, "threading": threading, "get_server_manager": fake_manager}
  # Compile the two classes.
  exec(class_source, namespace)  # noqa: S102
  # An instance without the plugin stack.
  plugin = namespace["FastApiWebAppPlugin"]()
  # What on_init calls on the plugin besides the manager.
  plugin._init_endpoints, plugin.deque, plugin.time = (lambda: None), list, (lambda: 0.0)
  # The request monitor thread is not started in these tests.
  plugin._start_request_monitor_thread = lambda: None
  # Log lines are not read here.
  plugin.P = lambda *args, **kwargs: None
  # The plugin's start.
  plugin.on_init()
  # The started harness and the keys.
  return plugin, keys


class TestManagerKey(unittest.TestCase):

  def test_each_start_gets_a_key_of_its_own(self):
    """on_init starts the manager with 32 random bytes as hex text, different at every start, never the old constant."""
    # Two starts, as two web apps of a node or two starts of one.
    (first, first_keys), (second, second_keys) = _started_plugin(), _started_plugin()
    # The manager was started with the key the plugin keeps for its server template.
    self.assertEqual((first_keys, second_keys), ([first.manager_auth], [second.manager_auth]))
    # Both keys.
    for key in (first.manager_auth, second.manager_auth):
      # Bytes, as the manager needs; 64 hex digits, so 32 random bytes.
      self.assertTrue(isinstance(key, bytes) and len(key) == 64 and int(key, 16) >= 0)
      # Not the constant every node shared.
      self.assertNotEqual(key, b'abc')
    # Two starts, two keys.
    self.assertNotEqual(first.manager_auth, second.manager_auth)

  def test_the_key_survives_the_server_template_and_the_old_constant_is_refused(self):
    """Rendered as the template renders it, the key reads back unchanged; a manager started with it refuses b'abc'."""
    # A key from a real start.
    plugin, _ = _started_plugin()
    # The template writes "auth={{ manager_auth }}" into the server's Python source.
    rendered = Template("{{ manager_auth }}").render(manager_auth=plugin.manager_auth)
    # That text is a Python bytes literal of the same key.
    self.assertEqual(ast.literal_eval(rendered), plugin.manager_auth)
    # A real manager process with this key.
    manager = get_server_manager(plugin.manager_auth)
    # Stopped at the end of the test.
    self.addCleanup(manager.shutdown)
    # Its port on the loopback interface.
    port = manager.address[1]
    # The constant that every node used is not accepted any more.
    with self.assertRaises(multiprocessing.AuthenticationError):
      # A client that only knows the old constant.
      get_client_manager(port, b'abc')
    # The web server, which got the key through its template, connects.
    client = get_client_manager(port, ast.literal_eval(rendered))
    # And reaches the plugin's queue.
    self.assertIsNotNone(client.get_server_queue())


class TestAssetLogLine(unittest.TestCase):

  def test_the_asset_line_names_the_template_keys_and_prints_no_value(self):
    """initialize_assets logs the names of the template arguments; the manager key and the other values are not in the line."""
    # The real method on a harness class.
    class_source = "class Harness:\n" + _method_source(_BASE, "BaseWebAppPlugin", "initialize_assets")
    # The module-level names the method uses.
    namespace = {"os": os, "shutil": shutil, "credential_env_names": credential_env_names}
    # Compile the harness.
    exec(class_source, namespace)  # noqa: S102
    # An instance without the plugin stack.
    plugin = namespace["Harness"]()
    # The lines the plugin logs.
    lines = []
    # Record each line.
    plugin.P = lambda text, **kwargs: lines.append(text)
    # A folder for the environment files the method writes.
    folder = tempfile.TemporaryDirectory()
    # Removed at the end of the test.
    self.addCleanup(folder.cleanup)
    # What the method reads from the plugin: path helpers, the folder, and two empty environments.
    plugin.os_path, plugin.script_temp_dir, plugin.prepared_env, plugin.base_env = os.path, folder.name, {}, {}
    # Where downloaded assets would be (none here), and the plugin's name in that folder.
    plugin.get_output_folder, plugin.plugin_id = (lambda: folder.name), "plugin"
    # Template arguments as a FastAPI web app passes them, with the manager key and a deployer's own value.
    jinja_args = {"manager_port": 12345, "manager_auth": b"0123abcd-canary-key", "custom_token": "canary-value"}
    # No source folder: only the log line and the environment files.
    plugin.initialize_assets(None, folder.name, jinja_args)
    # The line about the copy, and the line that says it is done.
    self.assertEqual(len(lines), 2)
    # Every argument.
    for name in jinja_args:
      # Each is named, so a missing one can still be seen.
      self.assertIn(name, lines[0])
    # The key, a deployer's value and the port.
    for value in ("0123abcd-canary-key", "canary-value", "12345"):
      # No value is printed in either line.
      self.assertNotIn(value, lines[0] + lines[1])


if __name__ == "__main__":
  # Run this file's tests when it is started directly.
  unittest.main()

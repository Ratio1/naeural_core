import ast
import pathlib
import unittest


_ENTRYPOINT_PATH = pathlib.Path(__file__).with_name("entrypoint.py")


class TestEntrypointEnvironmentLog(unittest.TestCase):

  def test_main_never_walks_the_environment(self):
    """Startup must not log env values: they hold broker, storage and tunnel secrets."""
    module = ast.parse(_ENTRYPOINT_PATH.read_text())
    main = next(
      node for node in module.body
      if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    environ_loops = [
      ast.unparse(node.iter) for node in ast.walk(main)
      if isinstance(node, (ast.For, ast.comprehension))
      and ast.unparse(node.iter).startswith("os.environ")
    ]
    self.assertEqual(environ_loops, [])


if __name__ == "__main__":
  unittest.main()

import os
import pathlib
import subprocess
import sys
import unittest


_REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[2]


class TestPluginBaseUtilsImport(unittest.TestCase):

  def test_plugin_base_utils_imports_in_fresh_interpreter(self):
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(_REPOSITORY_ROOT)
    if existing_pythonpath:
      env["PYTHONPATH"] += os.pathsep + existing_pythonpath

    result = subprocess.run(
      [
        sys.executable,
        "-c",
        (
          "import pathlib; "
          "import naeural_core.utils.plugins_base.plugin_base_utils as module; "
          "assert pathlib.Path(module.__file__).resolve().is_relative_to(pathlib.Path.cwd().resolve())"
        ),
      ],
      cwd=_REPOSITORY_ROOT,
      env=env,
      capture_output=True,
      text=True,
      timeout=30,
    )

    self.assertEqual(
      result.returncode,
      0,
      msg=f"fresh import failed:\n{result.stderr}",
    )


if __name__ == "__main__":
  unittest.main()

"""The publish step (naeural_core/main/ver.py) rewrites every pyproject.toml line that contains "version"."""
import os
import unittest


class TestPyprojectPublishRewrite(unittest.TestCase):

  def test_only_the_version_line_contains_the_word_the_publish_step_replaces(self):
    # ver.py turns every line that holds the word into `version = "<ver>"`. A comment or a dependency that
    # holds it becomes a second such line; inside the dependencies list the file no longer parses, the build
    # fails and the package is not published.
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    with open(os.path.join(root, "pyproject.toml"), "rt") as fd:
      hits = [line for line in fd.readlines() if "version" in line]
    self.assertEqual(len(hits), 1, hits)
    self.assertTrue(hits[0].startswith("version = "), hits)


if __name__ == "__main__":
  unittest.main()

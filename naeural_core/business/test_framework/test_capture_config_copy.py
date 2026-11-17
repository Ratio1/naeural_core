"""Real-runtime capture configuration parity and fallback regressions."""

import os
import sys
import unittest
from copy import deepcopy
from unittest.mock import patch

from naeural_core.config import runtime_secret_resolution as secrets


def legacy_capture(config, environment=None):
  """Evaluate the pre-optimization capture builder as a comparison oracle.

  Parameters
  ----------
  config : dict
      Synthetic canonical pipeline.
  environment : Mapping or None, optional
      Synthetic capture environment.

  Returns
  -------
  dict
      Legacy detached configuration with canonical plugin references.
  """
  plugins = config.get("PLUGINS", secrets._MISSING)
  result = secrets.resolve_environment_references(config, environment=environment)
  if plugins is not secrets._MISSING:
    result["PLUGINS"] = deepcopy(plugins)
  return result


class CaptureConfigCopyTests(unittest.TestCase):
  """Pin canonical ownership, secret boundaries and unusual input semantics."""

  def test_json_shapes_match_legacy_and_preserve_order(self):
    """Cover absent, null, empty, nested and scalar plugin branches."""
    environment = {"EE_CAPTURE": "synthetic://capture", "EE_PLUGIN": "secret-value"}
    for plugins in (secrets._MISSING, None, [], {}, "literal", 3, [True, 1.5], [{
      "ENV": ["$EE_PLUGIN", secrets.DAUTH_SECRET_PLACEHOLDER, "$EE_MISSING"],
    }]):
      with self.subTest(plugins=plugins):
        canonical = {"NAME": "fixture", "URL": "$EE_CAPTURE"}
        if plugins is not secrets._MISSING:
          canonical["PLUGINS"] = plugins
        canonical["META"] = {"NESTED": ["$EE_CAPTURE", "$EE_MISSING", None]}
        before = deepcopy(canonical)
        actual = secrets.build_capture_pipeline_config(canonical, environment)
        self.assertEqual(actual, legacy_capture(canonical, environment))
        self.assertEqual(list(actual), list(canonical))
        self.assertEqual(canonical, before)
        self.assertNotIn("secret-value", repr(actual))

  def test_plugin_resolution_is_skipped_but_capture_resolution_remains_fresh(self):
    """Use real process-environment reads and count only resolver entry points."""
    canonical = {
      "URL": "$EE_CAPTURE_COPY_TEST", "PLUGINS": [{"TOKEN": "$EE_PLUGIN_COPY_TEST"}],
    }
    for capture_value in ("synthetic://first", "synthetic://second"):
      with patch.dict(os.environ, {
        "EE_CAPTURE_COPY_TEST": capture_value, "EE_PLUGIN_COPY_TEST": "secret-value",
      }):
        with patch.object(
          secrets, "resolve_environment_references",
          wraps=secrets.resolve_environment_references,
        ) as resolver:
          actual = secrets.build_capture_pipeline_config(canonical)
      self.assertEqual(actual["URL"], capture_value)
      self.assertEqual(actual["PLUGINS"], canonical["PLUGINS"])
      self.assertEqual(resolver.call_count, 1)
      resolver.assert_called_once_with("$EE_CAPTURE_COPY_TEST", environment=None)

  def test_outputs_are_detached_and_keep_internal_plugin_aliases(self):
    """Preserve deepcopy memo sharing inside PLUGINS, without cross-call sharing."""
    shared = {"VALUES": [1]}
    canonical = {"PLUGINS": [shared, shared], "META": {"VALUES": [2]}}
    first = secrets.build_capture_pipeline_config(canonical)
    second = secrets.build_capture_pipeline_config(canonical)
    self.assertIs(first["PLUGINS"][0], first["PLUGINS"][1])
    first["PLUGINS"][0]["VALUES"].append(3)
    first["META"]["VALUES"].append(4)
    self.assertEqual(second, canonical)
    self.assertEqual(canonical["PLUGINS"][0]["VALUES"], [1])

  def test_custom_environment_keeps_discarded_lookup_and_failure(self):
    """A custom mapping may observe or reject even a discarded plugin lookup."""
    class ObservedEnvironment(dict):
      """Reject a plugin lookup so the legacy exception is externally visible."""

      def get(self, key, default=None):
        """Return a capture value or raise for the synthetic plugin reference.

        Parameters
        ----------
        key : str
            Environment reference name.
        default : Any, optional
            Missing-key fallback.

        Returns
        -------
        Any
            Stored value for a non-plugin key.

        Raises
        ------
        LookupError
            When the legacy traversal visits the plugin key.
        """
        if key == "EE_PLUGIN":
          raise LookupError("synthetic plugin lookup")
        return super().get(key, default)

    canonical = {"URL": "$EE_CAPTURE", "PLUGINS": [{"TOKEN": "$EE_PLUGIN"}]}
    for builder in (legacy_capture, secrets.build_capture_pipeline_config):
      with self.assertRaisesRegex(LookupError, "synthetic plugin lookup"):
        builder(canonical, ObservedEnvironment(EE_CAPTURE="synthetic://capture"))

  def test_custom_leaf_retains_both_copy_hooks(self):
    """Do not remove observable first-pass copies for non-JSON leaves."""
    copies = []

    class CopyProbe:
      """Record copy-hook calls without modifying the configuration tree."""

      def __deepcopy__(self, memo):
        """Record one copy and return this immutable test sentinel.

        Parameters
        ----------
        memo : dict
            Memo supplied by the production copy operation.

        Returns
        -------
        CopyProbe
            The same sentinel, so only hook count matters in this test.
        """
        copies.append(1)
        return self

    canonical = {"PLUGINS": [CopyProbe()]}
    secrets.build_capture_pipeline_config(canonical)
    self.assertEqual(len(copies), 2)

  def test_custom_container_retains_iteration_behavior(self):
    """A dictionary subclass must keep legacy custom iteration failures."""
    class RejectItems(dict):
      """Fail if the resolver attempts to traverse this custom dictionary."""

      def items(self):
        """Raise the synthetic legacy traversal error.

        Raises
        ------
        RuntimeError
            Always, to expose accidental avoidance of the first traversal.
        """
        raise RuntimeError("synthetic items failure")

    for builder in (legacy_capture, secrets.build_capture_pipeline_config):
      with self.assertRaisesRegex(RuntimeError, "synthetic items failure"):
        builder({"PLUGINS": [RejectItems(TOKEN="value")]})

  def test_invalid_pipeline_retains_existing_failure(self):
    """Invalid pipeline containers still fail instead of becoming empty config."""
    for value in (None, [], "bad"):
      with self.subTest(value=value):
        with self.assertRaises(AttributeError):
          secrets.build_capture_pipeline_config(value)

  def test_non_ascii_environment_reference_retains_legacy_encoding_failure(self):
    """JSON-decoded surrogates must not hide a discarded os.environ error."""
    canonical = {"PLUGINS": [{"TOKEN": "$EE_\ud800"}]}
    with self.assertRaises(UnicodeEncodeError):
      legacy_capture(canonical)
    with self.assertRaises(UnicodeEncodeError):
      secrets.build_capture_pipeline_config(canonical)
    # A plain mapping does not encode keys; fallback must preserve that success
    # too, rather than rejecting the canonical value itself.
    self.assertEqual(
      secrets.build_capture_pipeline_config(canonical, {}),
      legacy_capture(canonical, {}),
    )

  def test_deep_capture_fields_use_legacy_recursion_capacity(self):
    """A shape probe must not lower the resolver's accepted nesting depth."""
    # Comprehensions inline from Python 3.12 onward; the real E2 image uses 3.13.
    # Older supported Pythons retain their smaller legacy recursion capacity.
    depth = 550 if sys.version_info >= (3, 12) else 200
    nested = 1
    for _ in range(depth):
      nested = [nested]
    canonical = {"META": nested}
    expected = legacy_capture(canonical)["META"]
    actual = secrets.build_capture_pipeline_config(canonical)["META"]
    for _ in range(depth):
      self.assertIsNot(actual, expected)
      actual, expected = actual[0], expected[0]
    self.assertEqual(actual, expected)


if __name__ == "__main__":
  unittest.main()

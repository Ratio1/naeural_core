import unittest
from copy import deepcopy
from threading import Lock
from types import SimpleNamespace

from naeural_core.business.base.base_plugin_biz import BasePluginExecutor
from naeural_core.utils.per_node_config import (
  deep_merge_config,
  get_structured_section,
  iter_overlays,
  normalize_config,
  overlay_for_node,
  validate_selectors,
)


class _StopAfterConfigPrepared(Exception):
  pass


class _PluginHarness(BasePluginExecutor):
  def __repr__(self):
    return "<_PluginHarness>"

  @property
  def ee_addr(self):
    return "0xai_node_b"

  def P(self, *_args, **_kwargs):
    return

  @property
  def cfg_instance_id(self):
    return "llm-api"

  def time(self):
    return 0


class PerNodeBusinessConfigTests(unittest.TestCase):
  def test_core_helpers_use_canonical_error_label_by_default(self):
    invalid_calls = (
      lambda: get_structured_section(
        {"default": []}, "default", ("default", "DEFAULT")
      ),
      lambda: normalize_config([]),
      lambda: list(iter_overlays([])),
      lambda: overlay_for_node([], "0xai_node_a", 0),
      lambda: validate_selectors([[]], ["0xai_node_a"]),
    )

    for invalid_call in invalid_calls:
      with self.subTest(call=invalid_call), self.assertRaisesRegex(
        ValueError, "^PER_NODE_CONFIG"
      ):
        invalid_call()

  def test_overlay_precedence_deep_merges_default_index_and_node(self):
    raw_config = {
      "default": {
        "STARTUP_AI_ENGINE_PARAMS": {
          "MODEL_NAME": "default-model",
          "MODEL_KWARGS": {"threads": 2, "context": 2048},
        },
      },
      "byIndex": {
        "1": {
          "AI_ENGINE": "llama_cpp_medium",
          "STARTUP_AI_ENGINE_PARAMS": {"MODEL_KWARGS": {"threads": 4}},
        },
      },
      "byNode": {
        "node_b": {
          "STARTUP_AI_ENGINE_PARAMS": {
            "MODEL_NAME": "node-b-model",
            "MODEL_KWARGS": {"context": 4096},
          },
        },
      },
    }

    overlay = overlay_for_node(raw_config, "0xai_node_b", 1)
    effective = deep_merge_config(
      {
        "AI_ENGINE": "llama_cpp_small",
        "STARTUP_AI_ENGINE_PARAMS": {"MODEL_KWARGS": {"batch": 128}},
      },
      overlay,
    )

    self.assertEqual(effective["AI_ENGINE"], "llama_cpp_medium")
    self.assertEqual(effective["STARTUP_AI_ENGINE_PARAMS"]["MODEL_NAME"], "node-b-model")
    self.assertEqual(
      effective["STARTUP_AI_ENGINE_PARAMS"]["MODEL_KWARGS"],
      {"batch": 128, "threads": 4, "context": 4096},
    )

  def test_normalize_rejects_nested_and_system_managed_overrides(self):
    with self.assertRaisesRegex(ValueError, "Nested .* overlays"):
      normalize_config({"byIndex": {"0": {"PER_NODE_CONFIG": {}}}})

    with self.assertRaisesRegex(ValueError, "system-managed"):
      normalize_config({"byNode": {"node-a": {"CHAINSTORE_PEERS": ["node-b"]}}})

  def test_base_plugin_materializes_ai_config_before_validation(self):
    plugin = object.__new__(_PluginHarness)
    plugin.log = SimpleNamespace(config_data={"PLUGINS_DEBUG_LOAD_TIMINGS": False})
    plugin._instance_config = None
    plugin._instance_config_unmaterialized = None
    plugin._environment_variables = None
    plugin._default_config = {
      "AI_ENGINE": "llama_cpp_small",
      "STARTUP_AI_ENGINE_PARAMS": {},
      "PER_NODE_TARGET_NODES": [],
    }
    plugin._upstream_config = {
      "INSTANCE_ID": "llm-api",
      "PER_NODE_TARGET_NODES": ["0xai_node_a", "0xai_node_b"],
      "PER_NODE_CONFIG": {
        "byIndex": {
          "1": {
            "AI_ENGINE": "llama_cpp_medium",
            "STARTUP_AI_ENGINE_PARAMS": {
              "MODEL_NAME": "model-b",
              "MODEL_INSTANCE_ID": "model-b-instance",
            },
          },
        },
      },
    }
    plugin._BasePluginExecutor__debug_config_changes = False
    plugin._BasePluginExecutor__set_loop_stage = lambda *_args, **_kwargs: None

    def capture_pre_validation_config(config, verbose=0):  # pylint: disable=unused-argument
      plugin.pre_validation_config = deepcopy(config)
      raise _StopAfterConfigPrepared()

    plugin.setup_config_and_validate = capture_pre_validation_config

    with self.assertRaises(_StopAfterConfigPrepared):
      plugin._update_instance_config()

    prepared = plugin.pre_validation_config
    self.assertEqual(prepared["AI_ENGINE"], "llama_cpp_medium")
    self.assertEqual(prepared["STARTUP_AI_ENGINE_PARAMS"]["MODEL_NAME"], "model-b")
    self.assertEqual(
      prepared["STARTUP_AI_ENGINE_PARAMS"]["MODEL_INSTANCE_ID"],
      "model-b-instance",
    )
    self.assertNotIn("PER_NODE_CONFIG", prepared)

  def test_base_plugin_rejects_camel_case_per_node_config_before_validation(self):
    plugin = object.__new__(_PluginHarness)
    plugin.log = SimpleNamespace(config_data={"PLUGINS_DEBUG_LOAD_TIMINGS": False})
    plugin._instance_config = None
    plugin._instance_config_unmaterialized = None
    plugin._environment_variables = None
    plugin._default_config = {
      "AI_ENGINE": "llama_cpp_small",
      "PER_NODE_TARGET_NODES": [],
    }
    plugin._upstream_config = {
      "PER_NODE_TARGET_NODES": ["0xai_node_a", "0xai_node_b"],
      "perNodeConfig": {
        "byIndex": {"1": {"AI_ENGINE": "llama_cpp_medium"}},
      },
    }
    plugin._BasePluginExecutor__debug_config_changes = False
    plugin._BasePluginExecutor__set_loop_stage = lambda *_args, **_kwargs: None

    validation_calls = []

    def capture_pre_validation_config(config, verbose=0):  # pylint: disable=unused-argument
      validation_calls.append(deepcopy(config))

    plugin.setup_config_and_validate = capture_pre_validation_config

    with self.assertRaisesRegex(ValueError, "only accepts 'PER_NODE_CONFIG'"):
      plugin._update_instance_config()

    self.assertEqual(validation_calls, [])

  def test_replacing_overlay_does_not_reuse_previous_materialized_ai_config(self):
    plugin = object.__new__(_PluginHarness)
    first = plugin._materialize_per_node_config({  # pylint: disable=protected-access
      "AI_ENGINE": "llama_cpp_small",
      "PER_NODE_TARGET_NODES": ["0xai_node_a", "0xai_node_b"],
      "PER_NODE_CONFIG": {
        "byIndex": {"1": {"AI_ENGINE": "llama_cpp_medium"}},
      },
    })
    second = plugin._materialize_per_node_config({  # pylint: disable=protected-access
      "AI_ENGINE": "llama_cpp_small",
      "PER_NODE_TARGET_NODES": ["0xai_node_a", "0xai_node_b"],
      "PER_NODE_CONFIG": {},
    })

    self.assertEqual(first["AI_ENGINE"], "llama_cpp_medium")
    self.assertEqual(second["AI_ENGINE"], "llama_cpp_small")
    self.assertNotIn("PER_NODE_CONFIG", second)

  def test_failed_reconfiguration_restores_upstream_state_for_retry(self):
    plugin = object.__new__(_PluginHarness)
    plugin._upstream_config = {"AI_ENGINE": "llama_cpp_small"}
    plugin._session_id = "session"
    plugin._plugin_loop_in_exec = False
    plugin._plugin_lifecycle_lock = Lock()
    plugin._instance_config = {"AI_ENGINE": "llama_cpp_small"}
    plugin._instance_config_unmaterialized = {"AI_ENGINE": "llama_cpp_small"}
    plugin.config_data = {"AI_ENGINE": "llama_cpp_small"}
    plugin._BasePluginExecutor__set_loop_stage = lambda *_args, **_kwargs: None
    plugin._BasePluginExecutor__save_config = lambda *_args, **_kwargs: None
    plugin._create_error_notification = lambda *_args, **_kwargs: None
    attempts = []

    def fail_update():
      attempts.append(True)
      raise RuntimeError("transient validation failure")

    plugin._update_instance_config = fail_update
    desired = {"AI_ENGINE": "llama_cpp_medium"}

    plugin.maybe_update_instance_config(deepcopy(desired))
    plugin.maybe_update_instance_config(deepcopy(desired))

    self.assertEqual(len(attempts), 2)
    self.assertEqual(plugin._upstream_config, {"AI_ENGINE": "llama_cpp_small"})


if __name__ == "__main__":
  unittest.main()

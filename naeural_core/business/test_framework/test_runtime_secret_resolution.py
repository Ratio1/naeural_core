import importlib.util
import unittest

from copy import deepcopy
from pathlib import Path
from threading import Event

from ratio1.const import BASE_CT

# The focused test environment may use the previous released SDK while Core
# main already consumes these additive network-policy keys.
for _key in (
  "ORACLE_SYNC_BLOCKCHAIN_PRESENCE_MIN_THRESHOLD",
  "ORACLE_SYNC_ONLINE_PRESENCE_MIN_THRESHOLD",
):
  if not hasattr(BASE_CT.EvmNetConstants, _key + "_KEY"):
    setattr(BASE_CT.EvmNetConstants, _key + "_KEY", _key)
  for _network in BASE_CT.EVM_NET_CONSTANTS.values():
    _network.setdefault(_key, 0)

from naeural_core import constants as ct
from naeural_core.config.config_manager import ConfigManager
from naeural_core.config.runtime_secret_resolution import (
  DAUTH_SECRET_PLACEHOLDER,
  RuntimeSecretResolutionError,
  build_capture_pipeline_config,
  build_runtime_pipeline_config,
  extract_dauth_plugins_secret_tree,
  get_dauth_pipeline_identity,
  overlay_canonical_secret_references,
  resolve_environment_references,
)
from naeural_core.main.orchestrator import Orchestrator


_CMDAPI_PATH = Path(__file__).resolve().parents[1] / "mixins_base" / "cmdapi.py"
_CMDAPI_SPEC = importlib.util.spec_from_file_location("runtime_secret_cmdapi", _CMDAPI_PATH)
_CMDAPI_MODULE = importlib.util.module_from_spec(_CMDAPI_SPEC)
_CMDAPI_SPEC.loader.exec_module(_CMDAPI_MODULE)
_CmdAPIMixin = _CMDAPI_MODULE._CmdAPIMixin
build_cmdapi_stream_config = _CMDAPI_MODULE.build_cmdapi_stream_config


def _pipeline(date_updated=1, token=DAUTH_SECRET_PLACEHOLDER):
  return {
    "NAME": "secret_pipeline",
    "TYPE": "Void",
    "DEEPLOY_SPECS": {
      "job_id": 7,
      "date_updated": date_updated,
    },
    "PLUGINS": [{
      "SIGNATURE": "TEST_PLUGIN",
      "INSTANCES": [{
        "INSTANCE_ID": "test_instance",
        "ENV": {
          "TOKEN": token,
          "LOCAL": "$EE_LOCAL_SECRET",
        },
        "ARGS": ["$EE_LIST_SECRET", "literal"],
      }],
    }],
  }


def _secret_plugins(token="dauth-secret"):
  return [{
    "INSTANCES": [{
      "ENV": {
        "TOKEN": token,
        "EXTRA": "ignored",
      },
      "UNUSED": "ignored",
    }],
  }]


def _bundle(token="dauth-secret"):
  return {
    "secret_bundle": {
      "job_id": "7",
      "job_secrets": {
        "PLUGINS": _secret_plugins(token=token),
        "EXTRA": "ignored",
      },
    },
  }


class _FakeBlockchainManager:
  def __init__(self, results):
    self.results = list(results)
    self.calls = []

  def get_dauth_job_secret_bundle(self, job_id, **kwargs):
    self.calls.append((job_id, kwargs))
    result = self.results.pop(0)
    if isinstance(result, Exception):
      raise result
    return result


class _FakeThread:
  def __init__(self):
    self.alive = True
    self.join_timeout = None

  def is_alive(self):
    return self.alive

  def join(self, timeout=None):
    self.join_timeout = timeout
    self.alive = False


class RuntimeSecretPureHelperTests(unittest.TestCase):
  def test_cache_identity_hashes_redacted_plugins_only(self):
    first = _pipeline()
    second = deepcopy(first)
    second["SESSION_ID"] = "new-session"

    self.assertEqual(
      get_dauth_pipeline_identity("secret_pipeline", first),
      get_dauth_pipeline_identity("secret_pipeline", second),
    )

    second["PLUGINS"][0]["INSTANCES"][0]["ENV"]["NEW"] = "value"
    self.assertNotEqual(
      get_dauth_pipeline_identity("secret_pipeline", first),
      get_dauth_pipeline_identity("secret_pipeline", second),
    )

  def test_environment_references_are_runtime_only_and_include_list_scalars(self):
    canonical = {
      "VALUE": "$EE_PRESENT",
      "NESTED": ["$EE_LIST_VALUE", "$EE_MISSING", "plain"],
    }

    runtime = resolve_environment_references(
      canonical,
      environment={"EE_PRESENT": "one", "EE_LIST_VALUE": "two"},
    )

    self.assertEqual(runtime["VALUE"], "one")
    self.assertEqual(runtime["NESTED"], ["two", None, "plain"])
    self.assertEqual(canonical["VALUE"], "$EE_PRESENT")
    self.assertEqual(canonical["NESTED"][0], "$EE_LIST_VALUE")

  def test_structural_resolution_replaces_exact_placeholders_and_ignores_extras(self):
    canonical = _pipeline(token=DAUTH_SECRET_PLACEHOLDER)
    canonical["PLUGINS"][0]["INSTANCES"][0]["NOT_EXACT"] = (
      "prefix" + DAUTH_SECRET_PLACEHOLDER
    )

    runtime = build_runtime_pipeline_config(
      canonical,
      secret_plugins=_secret_plugins(),
      environment={
        "EE_LOCAL_SECRET": "local-secret",
        "EE_LIST_SECRET": "list-secret",
      },
    )
    instance = runtime["PLUGINS"][0]["INSTANCES"][0]

    self.assertEqual(instance["ENV"]["TOKEN"], "dauth-secret")
    self.assertEqual(instance["ENV"]["LOCAL"], "local-secret")
    self.assertEqual(instance["ARGS"], ["list-secret", "literal"])
    self.assertEqual(
      instance["NOT_EXACT"],
      "prefix" + DAUTH_SECRET_PLACEHOLDER,
    )
    self.assertNotIn("EXTRA", instance["ENV"])

  def test_structural_resolution_rejects_missing_and_type_mismatch(self):
    canonical = _pipeline()
    missing = [{"INSTANCES": [{"ENV": {}}]}]
    mismatch = [{"INSTANCES": [{"ENV": {"TOKEN": {"bad": "type"}}}]}]

    with self.assertRaisesRegex(RuntimeSecretResolutionError, "missing"):
      build_runtime_pipeline_config(canonical, secret_plugins=missing)
    with self.assertRaisesRegex(RuntimeSecretResolutionError, "type mismatch"):
      build_runtime_pipeline_config(canonical, secret_plugins=mismatch)

  def test_secret_bundle_requires_matching_job_id(self):
    with self.assertRaisesRegex(RuntimeSecretResolutionError, "job_id mismatch"):
      extract_dauth_plugins_secret_tree(
        response={"job_secrets": {"PLUGINS": []}},
        expected_job_id="7",
      )

  def test_reference_overlay_preserves_only_canonical_secret_paths(self):
    canonical = {
      "ENV": {
        "LOCAL": "$EE_LOCAL_SECRET",
        "REMOTE": DAUTH_SECRET_PLACEHOLDER,
        "NORMAL": "old",
      },
    }
    proposed = {
      "ENV": {
        "LOCAL": "local-plaintext",
        "REMOTE": "remote-plaintext",
        "NORMAL": "new",
      },
    }

    overlaid = overlay_canonical_secret_references(proposed, canonical)

    self.assertEqual(overlaid["ENV"]["LOCAL"], "$EE_LOCAL_SECRET")
    self.assertEqual(overlaid["ENV"]["REMOTE"], DAUTH_SECRET_PLACEHOLDER)
    self.assertEqual(overlaid["ENV"]["NORMAL"], "new")

  def test_reference_overlay_rejects_identity_and_unkeyed_list_changes(self):
    canonical_plugins = [{
      "SIGNATURE": "ORIGINAL",
      "INSTANCES": [{
        "INSTANCE_ID": "instance",
        "ARGS": ["$EE_TOKEN", "literal"],
      }],
    }]
    renamed_plugins = deepcopy(canonical_plugins)
    renamed_plugins[0]["SIGNATURE"] = "RENAMED"
    renamed_plugins[0]["INSTANCES"][0]["ARGS"][0] = "resolved-secret"
    with self.assertRaisesRegex(RuntimeSecretResolutionError, "changed SIGNATURE"):
      overlay_canonical_secret_references(renamed_plugins, canonical_plugins)

    reordered_plugins = deepcopy(canonical_plugins)
    reordered_plugins[0]["INSTANCES"][0]["ARGS"] = ["literal", "resolved-secret"]
    with self.assertRaisesRegex(RuntimeSecretResolutionError, "protected list"):
      overlay_canonical_secret_references(reordered_plugins, canonical_plugins)

  def test_capture_config_resolves_capture_environment_only(self):
    canonical = _pipeline()
    canonical["URL"] = "$EE_CAPTURE_URL"

    capture = build_capture_pipeline_config(
      canonical,
      environment={"EE_CAPTURE_URL": "https://capture.invalid"},
    )

    self.assertEqual(capture["URL"], "https://capture.invalid")
    instance = capture["PLUGINS"][0]["INSTANCES"][0]
    self.assertEqual(instance["ENV"]["TOKEN"], DAUTH_SECRET_PLACEHOLDER)
    self.assertEqual(instance["ENV"]["LOCAL"], "$EE_LOCAL_SECRET")


class OrchestratorRuntimeSecretTests(unittest.TestCase):
  def _make_orchestrator(self, results):
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator._current_dct_config_streams = {}
    orchestrator._blockchain_manager = _FakeBlockchainManager(results)
    orchestrator._messages = []
    orchestrator.P = lambda message, **kwargs: orchestrator._messages.append(message)
    orchestrator._ensure_runtime_secret_resolution_state()
    return orchestrator

  def _set_canonical(self, orchestrator, streams):
    with orchestrator._runtime_config_lock:
      orchestrator._current_dct_config_streams = deepcopy(streams)
    orchestrator._reconcile_runtime_streams(streams)

  def test_retry_success_runtime_separation_and_redacted_view(self):
    orchestrator = self._make_orchestrator([
      RuntimeError("temporarily unavailable"),
      _bundle(),
    ])
    canonical = _pipeline()
    self._set_canonical(orchestrator, {"secret_pipeline": canonical})

    self.assertEqual(orchestrator._get_runtime_streams_snapshot(), {})
    self.assertEqual(orchestrator._resolve_runtime_streams_once(now=10), 0)
    self.assertEqual(orchestrator._resolve_runtime_streams_once(now=10.5), 0)
    self.assertEqual(
      orchestrator._blockchain_manager.calls,
      [("7", {"request_timeout": (5, 20)})],
    )
    self.assertEqual(orchestrator._resolve_runtime_streams_once(now=11), 1)

    runtime = orchestrator._get_runtime_streams_snapshot()["secret_pipeline"]
    runtime_token = runtime["PLUGINS"][0]["INSTANCES"][0]["ENV"]["TOKEN"]
    canonical_token = orchestrator.get_pipelines_view()[0]["PLUGINS"][0]["INSTANCES"][0]["ENV"]["TOKEN"]
    self.assertEqual(runtime_token, "dauth-secret")
    self.assertEqual(canonical_token, DAUTH_SECRET_PLACEHOLDER)
    self.assertEqual(len(orchestrator._runtime_secret_cache), 1)

  def test_mismatch_withholds_whole_pipeline(self):
    orchestrator = self._make_orchestrator([{
      "job_id": "7",
      "job_secrets": {
        "PLUGINS": [{"INSTANCES": "wrong-type"}],
      },
    }])
    self._set_canonical(orchestrator, {"secret_pipeline": _pipeline()})

    self.assertEqual(orchestrator._resolve_runtime_streams_once(now=1), 0)

    self.assertNotIn("secret_pipeline", orchestrator._get_runtime_streams_snapshot())
    self.assertEqual(orchestrator._runtime_secret_retry_state.popitem()[1]["next_retry"], 2)

  def test_retry_backoff_starts_after_request_failure(self):
    orchestrator = self._make_orchestrator([RuntimeError("timeout")])
    self._set_canonical(orchestrator, {"secret_pipeline": _pipeline()})

    with unittest.mock.patch(
      "naeural_core.main.orchestrator.time",
      side_effect=[100, 121],
    ):
      self.assertEqual(orchestrator._resolve_runtime_streams_once(), 0)

    retry = next(iter(orchestrator._runtime_secret_retry_state.values()))
    self.assertEqual(retry["next_retry"], 122)

  def test_canonical_update_clears_runtime_eligibility_before_reconcile(self):
    orchestrator = self._make_orchestrator([])
    old_pipeline = _pipeline(date_updated=1, token="literal")
    self._set_canonical(orchestrator, {"secret_pipeline": old_pipeline})
    self.assertIn("secret_pipeline", orchestrator._get_runtime_streams_snapshot())

    orchestrator._config_manager = type(
      "ConfigHarness",
      (),
      {"dct_config_streams": {"secret_pipeline": _pipeline(date_updated=2)}},
    )()
    orchestrator.log = type(
      "LogHarness",
      (),
      {"managed_lock_resource": lambda self, name: unittest.mock.MagicMock(
        __enter__=lambda self: None,
        __exit__=lambda self, *args: None,
      )},
    )()
    orchestrator.config_data = {"SEQUENTIAL_STREAMS": False}
    observed_runtime = []
    original_reconcile = orchestrator._reconcile_runtime_streams
    orchestrator._reconcile_runtime_streams = lambda streams: (
      observed_runtime.append(orchestrator._get_runtime_streams_snapshot()),
      original_reconcile(streams),
    )[-1]

    orchestrator.choose_current_running_streams()

    self.assertEqual(observed_runtime, [{}])
    self.assertEqual(orchestrator._get_runtime_streams_snapshot(), {})

  def test_cache_invalidates_on_update_and_is_removed_with_pipeline(self):
    orchestrator = self._make_orchestrator([_bundle("first"), _bundle("second")])
    self._set_canonical(orchestrator, {"secret_pipeline": _pipeline(date_updated=1)})
    self.assertEqual(orchestrator._resolve_runtime_streams_once(now=1), 1)

    updated = _pipeline(date_updated=2)
    self._set_canonical(orchestrator, {"secret_pipeline": updated})
    self.assertEqual(orchestrator._get_runtime_streams_snapshot(), {})
    self.assertEqual(orchestrator._runtime_secret_cache, {})
    self.assertEqual(orchestrator._resolve_runtime_streams_once(now=2), 1)
    token = orchestrator._get_runtime_streams_snapshot()["secret_pipeline"]["PLUGINS"][0]["INSTANCES"][0]["ENV"]["TOKEN"]
    self.assertEqual(token, "second")

    self._set_canonical(orchestrator, {})
    self.assertEqual(orchestrator._get_runtime_streams_snapshot(), {})
    self.assertEqual(orchestrator._runtime_secret_cache, {})

  def test_business_manager_receives_runtime_snapshot_only(self):
    orchestrator = self._make_orchestrator([_bundle()])
    self._set_canonical(orchestrator, {"secret_pipeline": _pipeline()})
    orchestrator._resolve_runtime_streams_once(now=1)
    received = []
    orchestrator._business_manager = type(
      "BusinessHarness",
      (),
      {"update_streams": lambda self, streams: received.append(streams) or set()},
    )()

    orchestrator.refresh_business_plugins()

    token = received[0]["secret_pipeline"]["PLUGINS"][0]["INSTANCES"][0]["ENV"]["TOKEN"]
    self.assertEqual(token, "dauth-secret")
    self.assertEqual(
      orchestrator.get_pipelines_view()[0]["PLUGINS"][0]["INSTANCES"][0]["ENV"]["TOKEN"],
      DAUTH_SECRET_PLACEHOLDER,
    )

  def test_capture_manager_withholds_unresolved_pipeline_and_never_gets_dauth_plaintext(self):
    orchestrator = self._make_orchestrator([_bundle()])
    canonical = _pipeline()
    canonical["URL"] = "$EE_CAPTURE_URL"
    self._set_canonical(orchestrator, {"secret_pipeline": canonical})

    self.assertEqual(orchestrator._get_capture_streams_snapshot(), {})

    with unittest.mock.patch.dict(
      "os.environ",
      {"EE_CAPTURE_URL": "https://capture.invalid"},
    ):
      orchestrator._resolve_runtime_streams_once(now=1)
      capture = orchestrator._get_capture_streams_snapshot()["secret_pipeline"]

    self.assertEqual(capture["URL"], "https://capture.invalid")
    self.assertEqual(
      capture["PLUGINS"][0]["INSTANCES"][0]["ENV"]["TOKEN"],
      DAUTH_SECRET_PLACEHOLDER,
    )

  def test_shutdown_joins_runtime_secret_resolver(self):
    orchestrator = self._make_orchestrator([])
    resolver_thread = _FakeThread()
    request_thread = _FakeThread()
    orchestrator._thread_runtime_secret_resolver = resolver_thread
    orchestrator._runtime_secret_request_worker = request_thread
    orchestrator._thread_async_comm = None
    orchestrator._thread_admin_pipeline_collect = None
    orchestrator._thread_node_oracle_refresh = None
    setattr(orchestrator, "_Orchestrator__done", False)

    orchestrator._stop()

    self.assertEqual(resolver_thread.join_timeout, 2)
    self.assertEqual(request_thread.join_timeout, 26)
    self.assertTrue(any("runtime secret resolver thread joined" in msg.lower() for msg in orchestrator._messages))
    self.assertTrue(any("runtime secret request worker joined" in msg.lower() for msg in orchestrator._messages))

  def test_dauth_fetch_has_hard_deadline(self):
    orchestrator = self._make_orchestrator([])
    release = Event()

    class BlockingBlockchainManager:
      def get_dauth_job_secret_bundle(self, **kwargs):
        release.wait(1)
        return _bundle()

    orchestrator._blockchain_manager = BlockingBlockchainManager()
    try:
      with unittest.mock.patch(
        "naeural_core.main.orchestrator.RUNTIME_SECRET_REQUEST_DEADLINE_SECONDS",
        0.01,
      ):
        with self.assertRaisesRegex(TimeoutError, "runtime deadline"):
          orchestrator._fetch_dauth_job_secret_bundle("7")
    finally:
      release.set()

  def test_timed_out_fetch_does_not_spawn_overlapping_request_workers(self):
    orchestrator = self._make_orchestrator([])
    release = Event()

    class BlockingBlockchainManager:
      def __init__(self):
        self.calls = 0

      def get_dauth_job_secret_bundle(self, **kwargs):
        self.calls += 1
        release.wait(1)
        return _bundle()

    manager = BlockingBlockchainManager()
    orchestrator._blockchain_manager = manager
    try:
      with unittest.mock.patch(
        "naeural_core.main.orchestrator.RUNTIME_SECRET_REQUEST_DEADLINE_SECONDS",
        0.01,
      ):
        with self.assertRaises(TimeoutError):
          orchestrator._fetch_dauth_job_secret_bundle("7")
        with self.assertRaisesRegex(TimeoutError, "still in progress"):
          orchestrator._fetch_dauth_job_secret_bundle("7")
      self.assertEqual(manager.calls, 1)
    finally:
      release.set()


class ConfigManagerSecretBarrierTests(unittest.TestCase):
  def _make_manager(self):
    manager = ConfigManager.__new__(ConfigManager)
    manager.dct_config_streams = {"secret_pipeline": _pipeline()}
    instance = manager.dct_config_streams["secret_pipeline"]["PLUGINS"][0]["INSTANCES"][0]
    manager._get_plugin_instance = lambda **kwargs: instance
    manager._saved = []
    manager._save_stream_config = lambda config, **kwargs: manager._saved.append(deepcopy(config))
    manager.log = type("LogHarness", (), {"P": lambda self, *args, **kwargs: None})()
    manager.P = lambda *args, **kwargs: None
    return manager

  def test_instance_save_barrier_prevents_runtime_plaintext_persistence(self):
    manager = self._make_manager()
    runtime_instance = deepcopy(
      manager.dct_config_streams["secret_pipeline"]["PLUGINS"][0]["INSTANCES"][0]
    )
    runtime_instance["ENV"]["TOKEN"] = "dauth-plaintext"
    runtime_instance["ENV"]["LOCAL"] = "env-plaintext"
    runtime_instance["NEW_VALUE"] = "persist-me"

    manager.save_instance_modifications(
      "secret_pipeline",
      "TEST_PLUGIN",
      "test_instance",
      runtime_instance,
    )

    saved_instance = manager.dct_config_streams["secret_pipeline"]["PLUGINS"][0]["INSTANCES"][0]
    self.assertEqual(saved_instance["ENV"]["TOKEN"], DAUTH_SECRET_PLACEHOLDER)
    self.assertEqual(saved_instance["ENV"]["LOCAL"], "$EE_LOCAL_SECRET")
    self.assertEqual(saved_instance["NEW_VALUE"], "persist-me")

  def test_pipeline_save_barrier_prevents_runtime_plaintext_persistence(self):
    manager = self._make_manager()
    runtime_pipeline = build_runtime_pipeline_config(
      manager.dct_config_streams["secret_pipeline"],
      secret_plugins=_secret_plugins(),
      environment={"EE_LOCAL_SECRET": "local", "EE_LIST_SECRET": "list"},
    )
    runtime_pipeline["NEW_VALUE"] = "persist-me"

    manager.save_pipeline_modifications("secret_pipeline", runtime_pipeline)

    saved = manager.dct_config_streams["secret_pipeline"]
    instance = saved["PLUGINS"][0]["INSTANCES"][0]
    self.assertEqual(instance["ENV"]["TOKEN"], DAUTH_SECRET_PLACEHOLDER)
    self.assertEqual(instance["ENV"]["LOCAL"], "$EE_LOCAL_SECRET")
    self.assertEqual(instance["ARGS"][0], "$EE_LIST_SECRET")
    self.assertEqual(saved["NEW_VALUE"], "persist-me")

  def test_pipeline_save_barrier_matches_plugins_and_instances_by_identity(self):
    manager = self._make_manager()
    canonical = manager.dct_config_streams["secret_pipeline"]
    second_plugin = deepcopy(canonical["PLUGINS"][0])
    second_plugin["SIGNATURE"] = "SECOND_PLUGIN"
    second_plugin["INSTANCES"][0]["INSTANCE_ID"] = "second_instance"
    second_plugin["INSTANCES"][0]["ENV"]["TOKEN"] = "$EE_SECOND_TOKEN"
    canonical["PLUGINS"].append(second_plugin)

    runtime_pipeline = deepcopy(canonical)
    runtime_pipeline["PLUGINS"].reverse()
    for plugin in runtime_pipeline["PLUGINS"]:
      plugin["INSTANCES"][0]["ENV"]["TOKEN"] = "plaintext"

    manager.save_pipeline_modifications("secret_pipeline", runtime_pipeline)

    saved_plugins = manager.dct_config_streams["secret_pipeline"]["PLUGINS"]
    self.assertEqual(saved_plugins[0]["SIGNATURE"], "SECOND_PLUGIN")
    self.assertEqual(
      saved_plugins[0]["INSTANCES"][0]["ENV"]["TOKEN"],
      "$EE_SECOND_TOKEN",
    )
    self.assertEqual(
      saved_plugins[1]["INSTANCES"][0]["ENV"]["TOKEN"],
      DAUTH_SECRET_PLACEHOLDER,
    )


class CmdAPIBuilderTests(unittest.TestCase):
  def test_builder_is_pure_and_start_helper_dispatches_its_result(self):
    plugins = [{"SIGNATURE": "TEST", "INSTANCES": []}]
    expected = build_cmdapi_stream_config(
      name="pipeline",
      stream_type="Void",
      url="https://example.invalid",
      reconnectable="YES",
      plugins=plugins,
      stream_config_metadata={"source": "test"},
      cap_resolution=2,
      custom_value=3,
    )
    self.assertEqual(expected["NAME"], "pipeline")
    self.assertEqual(expected["TYPE"], "Void")
    self.assertEqual(expected["CUSTOM_VALUE"], 3)
    self.assertIs(expected["PLUGINS"], plugins)

    mixin = _CmdAPIMixin.__new__(_CmdAPIMixin)
    dispatched = []
    mixin._cmdapi_start_stream_by_config = lambda **kwargs: dispatched.append(kwargs)
    result = mixin._cmdapi_start_stream_by_params(
      name="pipeline",
      stream_type="Void",
      url="https://example.invalid",
      reconnectable="YES",
      plugins=plugins,
      stream_config_metadata={"source": "test"},
      cap_resolution=2,
      custom_value=3,
    )

    self.assertEqual(result, expected)
    self.assertEqual(dispatched[0]["config_stream"], expected)
    self.assertTrue(dispatched[0]["send_immediately"])


if __name__ == "__main__":
  unittest.main()

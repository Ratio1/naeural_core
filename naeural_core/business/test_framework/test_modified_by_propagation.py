import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest


_ROOT = Path(__file__).resolve().parents[2]


def _make_mixin(name):
  return type(name, (), {})


class _Constants(ModuleType):
  def __init__(self):
    super().__init__("naeural_core.constants")
    self.BLOCKCHAIN_MANAGER = "BLOCKCHAIN_MANAGER"
    self.R1FS_ENGINE = "R1FS_ENGINE"
    self.CONST_ADMIN_PIPELINE_NAME = "admin_pipeline"
    self.ADMIN_PIPELINE_NETMON = "NET_MON_01"
    self.ADMIN_PIPELINE_DAUTH = "DAUTH"
    self.COLORS = SimpleNamespace(BIZ="biz", MAIN="main")
    self.CONFIG_STREAM = SimpleNamespace(
      K_INITIATOR_ADDR="INITIATOR_ADDR",
      K_INITIATOR_ID="INITIATOR_ID",
      K_MODIFIED_BY_ADDR="MODIFIED_BY_ADDR",
      K_MODIFIED_BY_ID="MODIFIED_BY_ID",
      K_PLUGINS="PLUGINS",
      K_SESSION_ID="SESSION_ID",
      K_USE_LOCAL_COMMS_ONLY="USE_LOCAL_COMMS_ONLY",
    )
    self.CONFIG_PLUGIN = SimpleNamespace(
      K_SIGNATURE="SIGNATURE",
      K_INSTANCES="INSTANCES",
    )
    self.CONFIG_INSTANCE = SimpleNamespace(
      K_INSTANCE_ID="INSTANCE_ID",
    )
    self.PAYLOAD_DATA = SimpleNamespace(
      INITIATOR_ID="INITIATOR_ID",
      SESSION_ID="SESSION_ID",
      SIGNATURE="SIGNATURE",
      STREAM_NAME="STREAM_NAME",
      INSTANCE_ID="INSTANCE_ID",
    )
    self.PLUGIN_SEARCH = SimpleNamespace(
      LOC_BIZ_PLUGINS="business",
      SUFFIX_BIZ_PLUGINS=".py",
      SAFE_BIZ_PLUGINS=[],
      SAFE_BIZ_IMPORTS=[],
    )
    self.STATUS_TYPE = SimpleNamespace(
      STATUS_EXCEPTION="STATUS_EXCEPTION",
    )
    self.NOTIFICATION_CODES = SimpleNamespace()
    self.CALLBACKS = SimpleNamespace()
    self.PAYLOAD_CT = SimpleNamespace(
      STATUS_TYPE=SimpleNamespace(STATUS_NORMAL="STATUS_NORMAL"),
    )


class _DecentrAIObject:
  def __init__(self, log=None, **_kwargs):
    self.log = log
    self.config_data = getattr(log, "config_data", {})

  def P(self, *_args, **_kwargs):
    return None


class _Manager(_DecentrAIObject):
  pass


class _BCWrapper:
  def __init__(self, blockchain_manager, owner):
    self.blockchain_manager = blockchain_manager
    self.owner = owner


class _FakeLog:
  config_data = {}

  def __init__(self):
    self.messages = []

  def P(self, message, **kwargs):
    self.messages.append((message, kwargs))

  def now_str(self, **_kwargs):
    return "2026-05-29T17:00:00"

  def hash_object(self, obj, size=None):
    return "hash-{}".format("-".join(str(part) for part in obj))


def _module(name, **attrs):
  module = ModuleType(name)
  for key, value in attrs.items():
    setattr(module, key, value)
  return module


def _load_module(module_name, source_path, stubs):
  previous = {name: sys.modules.get(name) for name in stubs}
  sys.modules.update(stubs)
  try:
    spec = importlib.util.spec_from_file_location(module_name, source_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
  finally:
    for name in stubs:
      if previous[name] is None:
        sys.modules.pop(name, None)
      else:
        sys.modules[name] = previous[name]


def _make_common_stubs():
  constants = _Constants()
  naeural_core = _module(
    "naeural_core",
    constants=constants,
    Logger=type("Logger", (), {}),
    DecentrAIObject=_DecentrAIObject,
  )
  business = _module("naeural_core.business")
  business_base = _module("naeural_core.business.base")
  mixins_base = _module(
    "naeural_core.business.mixins_base",
    _AlerterMixin=_make_mixin("_AlerterMixin"),
    _ShmMixin=_make_mixin("_ShmMixin"),
    _EmailerMixin=_make_mixin("_EmailerMixin"),
    _DailyIntervalMixin=_make_mixin("_DailyIntervalMixin"),
    _DataAPIMixin=_make_mixin("_DataAPIMixin"),
    _WorkingHoursMixin=_make_mixin("_WorkingHoursMixin"),
    _IntervalAggregationMixin=_make_mixin("_IntervalAggregationMixin"),
    _UploadMixin=_make_mixin("_UploadMixin"),
    _CmdAPIMixin=_make_mixin("_CmdAPIMixin"),
    _ExecutionAuditMixin=_make_mixin("_ExecutionAuditMixin"),
    _GenericUtilsApiMixin=_make_mixin("_GenericUtilsApiMixin"),
    _DiskAPIMixin=_make_mixin("_DiskAPIMixin"),
    _DeAPIMixin=_make_mixin("_DeAPIMixin"),
    _DatasetBuilderMixin=_make_mixin("_DatasetBuilderMixin"),
    _StateMachineAPIMixin=_make_mixin("_StateMachineAPIMixin"),
  )
  return constants, {
    "naeural_core": naeural_core,
    "naeural_core.constants": constants,
    "naeural_core.manager": _module("naeural_core.manager", Manager=_Manager),
    "naeural_core.business": business,
    "naeural_core.business.base": business_base,
    "naeural_core.business.mixins_base": mixins_base,
    "naeural_core.business.mixins_base.threading": _module(
      "naeural_core.business.mixins_base.threading",
      _ThreadingAPIMixin=_make_mixin("_ThreadingAPIMixin"),
    ),
    "naeural_core.main": _module("naeural_core.main"),
    "naeural_core.main.net_mon": _module(
      "naeural_core.main.net_mon",
      NetworkMonitor=type("NetworkMonitor", (), {}),
    ),
    "naeural_core.business.base.base_plugin_biz_loop": _module(
      "naeural_core.business.base.base_plugin_biz_loop",
      _BasePluginLoopMixin=_make_mixin("_BasePluginLoopMixin"),
    ),
    "naeural_core.business.base.base_plugin_biz_api": _module(
      "naeural_core.business.base.base_plugin_biz_api",
      _BasePluginAPIMixin=_make_mixin("_BasePluginAPIMixin"),
    ),
    "naeural_core.business.mixins_libs": _module(
      "naeural_core.business.mixins_libs",
      _TimeBinsMixin=_make_mixin("_TimeBinsMixin"),
      _PoseAPIMixin=_make_mixin("_PoseAPIMixin"),
      _AlertTrackerMixin=_make_mixin("_AlertTrackerMixin"),
    ),
    "naeural_core.local_libraries": _module(
      "naeural_core.local_libraries",
      _ConfigHandlerMixin=_make_mixin("_ConfigHandlerMixin"),
    ),
    "naeural_core.business.mixins_base.plugin_readiness_mixin": _module(
      "naeural_core.business.mixins_base.plugin_readiness_mixin",
      _PluginReadinessMixin=_make_mixin("_PluginReadinessMixin"),
    ),
    "naeural_core.business.mixins_base.semaphored_paired_plugin_mixin": _module(
      "naeural_core.business.mixins_base.semaphored_paired_plugin_mixin",
      _SemaphoredPairedPluginMixin=_make_mixin("_SemaphoredPairedPluginMixin"),
    ),
    "naeural_core.business.mixins_base.deeploy_chainstore_response_mixin": _module(
      "naeural_core.business.mixins_base.deeploy_chainstore_response_mixin",
      _DeeployChainstoreResponseMixin=_make_mixin("_DeeployChainstoreResponseMixin"),
    ),
    "naeural_core.utils": _module("naeural_core.utils"),
    "naeural_core.utils.mixins": _module("naeural_core.utils.mixins"),
    "naeural_core.utils.mixins.code_executor": _module(
      "naeural_core.utils.mixins.code_executor",
      _CodeExecutorMixin=_make_mixin("_CodeExecutorMixin"),
    ),
    "naeural_core.data_structures": _module(
      "naeural_core.data_structures",
      GeneralPayload=type("GeneralPayload", (), {}),
    ),
    "naeural_core.utils.config_utils": _module(
      "naeural_core.utils.config_utils",
      get_now_value_from_time_dict=lambda *_args, **_kwargs: None,
    ),
    "naeural_core.business.test_framework": _module("naeural_core.business.test_framework"),
    "naeural_core.business.test_framework.testing_manager": _module(
      "naeural_core.business.test_framework.testing_manager",
      TestingManager=type("TestingManager", (), {}),
    ),
    "naeural_core.business.test_framework.scoring_manager": _module(
      "naeural_core.business.test_framework.scoring_manager",
      ScoringManager=type("ScoringManager", (), {}),
    ),
    "naeural_core.utils.plugins_base": _module("naeural_core.utils.plugins_base"),
    "naeural_core.utils.plugins_base.bc_wrapper": _module(
      "naeural_core.utils.plugins_base.bc_wrapper",
      BCWrapper=_BCWrapper,
    ),
    "naeural_core.ipfs": _module(
      "naeural_core.ipfs",
      R1FSEngine=type("R1FSEngine", (), {}),
    ),
  }


def _load_base_plugin_biz_module():
  _constants, stubs = _make_common_stubs()
  return _load_module(
    "base_plugin_biz_under_test",
    _ROOT / "business" / "base" / "base_plugin_biz.py",
    stubs,
  )


def _load_business_manager_module():
  _constants, stubs = _make_common_stubs()
  return _load_module(
    "business_manager_under_test",
    _ROOT / "business" / "business_manager.py",
    stubs,
  )


def _make_base_plugin(module, **kwargs):
  ct = module.ct
  base_kwargs = dict(
    log=_FakeLog(),
    global_shmem={
      ct.BLOCKCHAIN_MANAGER: object(),
      ct.R1FS_ENGINE: object(),
      "is_supervisor_node": False,
    },
    plugins_shmem={},
    stream_id="pipeline-1",
    signature="FAKE_PLUGIN",
    default_config={},
    upstream_config={},
    initiator_id="creator-id",
    initiator_addr="creator-addr",
  )
  base_kwargs.update(kwargs)
  return module.BasePluginExecutor(**base_kwargs)


class ModifiedByPropagationTests(unittest.TestCase):
  def test_base_plugin_executor_uses_explicit_modified_by_constructor_values(self):
    module = _load_base_plugin_biz_module()

    plugin = _make_base_plugin(
      module,
      modified_by_id="updater-id",
      modified_by_addr="updater-addr",
    )

    self.assertEqual(plugin.modified_by_id, "updater-id")
    self.assertEqual(plugin.modified_by_addr, "updater-addr")

  def test_base_plugin_executor_defaults_modified_by_to_initiator(self):
    module = _load_base_plugin_biz_module()

    plugin = _make_base_plugin(module)

    self.assertEqual(plugin.modified_by_id, "creator-id")
    self.assertEqual(plugin.modified_by_addr, "creator-addr")

  def test_business_manager_forwards_modified_by_to_new_plugin_constructor(self):
    module = _load_business_manager_module()
    ct = module.ct
    captured_kwargs = []

    class _FakePlugin:
      cfg_runs_only_on_supervisor_node = False

      def __init__(self, **kwargs):
        captured_kwargs.append(kwargs)

      def start_thread(self):
        raise AssertionError("thread startup should be disabled in this focused test")

    manager = module.BusinessManager.__new__(module.BusinessManager)
    manager.log = _FakeLog()
    manager.owner = SimpleNamespace(
      __version__="edge-version",
      runs_in_docker=False,
      docker_source="main",
      get_pipelines_view=lambda: {},
      is_secured=True,
    )
    manager.P = lambda *_args, **_kwargs: None
    manager.set_loop_stage = lambda *_args, **_kwargs: None
    manager._create_notification = lambda *_args, **_kwargs: None
    manager.config_data = {
      "PLUGINS_DEBUG_LOAD_TIMINGS": False,
      "PLUGINS_DEBUG_CONFIG_CHANGES": False,
    }
    manager.shmem = {}
    manager.plugins_shmem = {}
    manager.comm_shared_memory = {"payloads": {}, "commands": {}}
    manager._run_on_threads = False
    manager._environment_variables = {"ENV": "value"}
    manager._dct_current_instances = {}
    manager._dct_hash_mappings = {}
    manager._dct_instance_hash_log = {}
    manager.is_supervisor_node = True
    manager._BusinessManager__dauth_hash = None
    manager._dct_config_streams = {
      "pipeline-1": {
        ct.CONFIG_STREAM.K_INITIATOR_ADDR: "creator-addr",
        ct.CONFIG_STREAM.K_INITIATOR_ID: "creator-id",
        ct.CONFIG_STREAM.K_MODIFIED_BY_ADDR: "updater-addr",
        ct.CONFIG_STREAM.K_MODIFIED_BY_ID: "updater-id",
        ct.CONFIG_STREAM.K_SESSION_ID: "session-1",
        ct.CONFIG_STREAM.K_PLUGINS: [
          {
            ct.CONFIG_PLUGIN.K_SIGNATURE: "FAKE_PLUGIN",
            ct.CONFIG_PLUGIN.K_INSTANCES: [
              {ct.CONFIG_INSTANCE.K_INSTANCE_ID: "instance-1"},
            ],
          },
        ],
      },
    }
    manager._get_module_name_and_class = lambda **_kwargs: (
      "fake_module",
      "FakePlugin",
      _FakePlugin,
      {"MODULE_VERSION": "1.2.3"},
    )

    active_instances = manager._check_instances()

    self.assertEqual(active_instances, ["hash-pipeline-1-FAKE_PLUGIN-instance-1"])
    self.assertEqual(len(captured_kwargs), 1)
    self.assertEqual(captured_kwargs[0]["initiator_id"], "creator-id")
    self.assertEqual(captured_kwargs[0]["initiator_addr"], "creator-addr")
    self.assertEqual(captured_kwargs[0]["modified_by_id"], "updater-id")
    self.assertEqual(captured_kwargs[0]["modified_by_addr"], "updater-addr")


if __name__ == "__main__":
  unittest.main()

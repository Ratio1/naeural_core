"""Real-import heartbeat tests with explicit synthetic hardware observations.

The real monitor and serving-manager methods run end to end. Only unrelated
system/service observations and the GPU hardware boundary are replaced. These
tests prove call count, selection and formatting, not live NVML/CUDA behavior.
"""

import unittest
from collections import defaultdict, deque
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, call

from naeural_core import constants as ct
from naeural_core.main.app_monitor import ApplicationMonitor
from naeural_core.serving.serving_manager import ServingManager, SMConst


def gpu_snapshot(load=25):
  """Build a synthetic two-device GB snapshot without filtering process rows.

  Parameters
  ----------
  load : int, optional
    First device's load, used to prove freshness on subsequent transactions.

  Returns
  -------
  list of dict
    GPU telemetry in the production collector's existing schema.
  """
  return [{
    'NAME': 'Synthetic GPU {}'.format(index), 'TOTAL_MEM': 8.0 + index,
    'ALLOCATED_MEM': 2.0 + index, 'FREE_MEM': 6.0, 'MEM_UNIT': 'GB',
    'GPU_USED': load + index, 'GPU_TEMP': 40 + index, 'GPU_TEMP_MAX': 90,
    'GPU_FAN_SPEED': 30 + index, 'GPU_FAN_SPEED_UNIT': '%',
    'PROCESSES': [{'PID': 123, 'ALLOCATED_MEM': 0.5}],
    'USED_BY_PROCESS': index == 1,
  } for index in range(2)]


class StatusGpuSnapshotTests(unittest.TestCase):
  """Keep fresh status transactions and backward-compatible standalone reads."""

  def setUp(self):
    """Prepare real classes with synthetic non-GPU subsystems, without startup."""
    self.snapshot = gpu_snapshot()
    self.log = SimpleNamespace(
      config_data={SMConst.SERVING_ENVIRONMENT: {ct.DEFAULT_CUDA: 'cuda:1'}},
      gpu_info=Mock(return_value=self.snapshot), total_memory=16.0, total_disk=100.0,
      get_localhost_ip=Mock(return_value='127.0.0.1'),
      get_cpu_usage=Mock(return_value=12.0), get_avail_disk=Mock(return_value=50.0),
      get_processor_platform=Mock(return_value='Synthetic CPU'),
      get_processor_cores=Mock(return_value=4), now_str=Mock(return_value='fixed-time'),
      python_version='synthetic', version='synthetic', conda_env='synthetic',
      format_timers=Mock(return_value=[]), get_packages=Mock(return_value=''),
    )
    self.manager = object.__new__(ServingManager)
    self.manager.log = self.log
    self.manager._servers = {}
    self.manager.get_active_servers = Mock(return_value=[])
    self.owner = SimpleNamespace(
      serving_manager=self.manager, runs_in_docker=False, docker_env='', docker_source='',
      cfg_min_avail_disk_thr=1, cfg_min_avail_mem_thr=0.1, running_time=123,
      comm_manager=None, in_process_serving=False, capture_manager=None,
      business_manager=SimpleNamespace(get_active_plugins_instances=Mock(return_value=[])),
      cfg_hb_contains_pipelines=False, cfg_hb_contains_active_plugins=False,
      config_manager=None, e2_addr='synthetic-address', is_supervisor_node=False,
      whitelist=[], cfg_io_formatter=None, is_secured=False, cfg_app_seconds_heartbeat=10,
      main_loop_stop_log=[], r1fs_id=None, r1fs_started=False, r1fs_relay=None,
      cfg_compress_heartbeat=False,
    )
    self.monitor = object.__new__(ApplicationMonitor)
    self.monitor.owner = self.owner
    self.monitor.log = self.log
    self.monitor.cpu_log = deque()
    self.monitor.gpu_log = {}
    self.monitor.process_memory_log = deque([1.0])
    self.monitor.avail_memory_log = deque([8.0])
    self.monitor.alert_avail = 0
    self.monitor.alert_process = False
    self.monitor.dct_curr_nr = defaultdict(int)
    self.monitor.log_avail_memory = Mock(return_value=8.0)
    self.monitor.log_process_memory = Mock(return_value=1.0)
    self.monitor.get_owner_version = Mock(return_value='synthetic')
    self.monitor.get_opencv_info = Mock(return_value='')
    self.monitor.sys_mon = SimpleNamespace(
      git_branch='synthetic', get_status_log=Mock(return_value=[]),
    )
    self.monitor.location_data = {}
    self.monitor._ApplicationMonitor__logged_hb_with_pipelines_status = True
    self.monitor._ApplicationMonitor__logged_hb_with_active_plugins_status = True
    self.monitor._ApplicationMonitor__last_temperature_info = None
    self.monitor._ApplicationMonitor__first_payload_prepared = True

  def test_status_collects_once_and_preserves_snapshot_schema_and_units(self):
    """One observation supplies payload, configured GPU and performance history."""
    expected = deepcopy(self.snapshot)
    for full in (False, True):
      with self.subTest(full=full):
        self.log.gpu_info.reset_mock()
        status = self.monitor.get_status(full=full)
        self.log.gpu_info.assert_called_once_with(mb=False)
        self.assertIs(status[ct.HB.GPUS], self.snapshot)
        self.assertEqual(status[ct.HB.GPUS], expected)
        self.assertEqual(status[ct.HB.DEFAULT_CUDA], 'cuda:1')
        self.assertIn("Used GPU: [1] 'Synthetic GPU 1'", status[ct.HB.GPU_INFO])
        self.assertIn('3.0 GB', status[ct.HB.GPU_INFO])
        self.assertEqual(status[ct.HB.HEARTBEAT_VERSION], ct.HB.V1)
    self.assertEqual(list(self.monitor.gpu_log[1][ct.GPU_INFO.GPU_USED]), [26, 26])

  def test_each_status_is_fresh_and_configuration_changes_are_visible(self):
    """No GPU observation or resolved CUDA choice is retained for a later call."""
    second = gpu_snapshot(load=60)
    self.log.gpu_info.side_effect = [self.snapshot, second]
    first_status = self.monitor.get_status(full=False)
    self.log.config_data[SMConst.SERVING_ENVIRONMENT][ct.DEFAULT_CUDA] = 'cuda:0'
    second_status = self.monitor.get_status(full=False)
    self.assertEqual(self.log.gpu_info.call_args_list, [call(mb=False), call(mb=False)])
    self.assertIs(first_status[ct.HB.GPUS], self.snapshot)
    self.assertIs(second_status[ct.HB.GPUS], second)
    self.assertEqual(second_status[ct.HB.DEFAULT_CUDA], 'cuda:0')
    self.assertIn("Used GPU: [0] 'Synthetic GPU 0'", second_status[ct.HB.GPU_INFO])
    self.assertEqual(list(self.monitor.gpu_log[0][ct.GPU_INFO.GPU_USED]), [25, 60])

  def test_empty_or_failed_observation_is_not_retried(self):
    """No-GPU and collector failure retain the error string and CPU selection."""
    for observation in ([], None):
      with self.subTest(observation=observation):
        self.log.gpu_info.reset_mock()
        self.log.gpu_info.return_value = observation
        status = self.monitor.get_status(full=False)
        self.log.gpu_info.assert_called_once_with(mb=False)
        self.assertEqual(status[ct.HB.GPUS], 'Pytorch or NVidia-SMI issue')
        self.assertEqual(status[ct.HB.DEFAULT_CUDA], 'cpu')
        self.assertNotIn('Used GPU:', status[ct.HB.GPU_INFO])

  def test_cpu_invalid_index_and_nonstring_configuration_remain_unchanged(self):
    """Keep availability-only selection and the formatter's existing fallback."""
    for configured, expected_index in [('cpu', 0), ('CUDA:1', 1), ('cuda:9', None),
                                       ('invalid', 0), (1, 1), (None, None)]:
      with self.subTest(configured=configured):
        self.log.config_data[SMConst.SERVING_ENVIRONMENT][ct.DEFAULT_CUDA] = configured
        self.log.gpu_info.reset_mock()
        status = self.monitor.get_status(full=False)
        self.log.gpu_info.assert_called_once_with(mb=False)
        self.assertEqual(status[ct.HB.DEFAULT_CUDA], configured)
        if expected_index is None:
          self.assertNotIn('Used GPU:', status[ct.HB.GPU_INFO])
        else:
          self.assertIn('Used GPU: [{}]'.format(expected_index), status[ct.HB.GPU_INFO])

  def test_missing_configuration_and_later_gpu_failure_keep_legacy_fallbacks(self):
    """An absent config uses CUDA-0; a later failure retains historical text."""
    self.log.config_data[SMConst.SERVING_ENVIRONMENT].clear()
    first = self.monitor.get_status(full=False)
    self.assertEqual(first[ct.HB.DEFAULT_CUDA], 'cuda:0')
    self.log.gpu_info.reset_mock()
    self.log.gpu_info.return_value = None
    failed = self.monitor.get_status(full=False)
    self.log.gpu_info.assert_called_once_with(mb=False)
    self.assertEqual(failed[ct.HB.DEFAULT_CUDA], 'cpu')
    self.assertEqual(failed[ct.HB.GPUS], 'Pytorch or NVidia-SMI issue')
    # The original formatter maps 'cpu' to zero, so prior GPU-0 history remains
    # visible. This transaction-local reuse must not silently rewrite that policy.
    self.assertIn("Used GPU: [0] 'Synthetic GPU 0'", failed[ct.HB.GPU_INFO])
    self.assertEqual(list(self.monitor.gpu_log[0][ct.GPU_INFO.GPU_USED]), [25])

  def test_missing_manager_keeps_empty_payload_device_and_cuda_zero_text(self):
    """Preserve the distinct historical fallbacks when serving is not ready."""
    self.owner.serving_manager = None
    status = self.monitor.get_status(full=False)
    self.log.gpu_info.assert_called_once_with(mb=False)
    self.assertEqual(status[ct.HB.DEFAULT_CUDA], '')
    self.assertIn("Used GPU: [0] 'Synthetic GPU 0'", status[ct.HB.GPU_INFO])

  def test_standalone_property_and_formatter_still_collect_fresh_data(self):
    """The reuse path is opt-in; existing standalone callers do not gain a cache."""
    self.log.gpu_info.side_effect = [self.snapshot, []]
    self.assertEqual(self.manager.default_cuda, 'cuda:1')
    self.assertEqual(self.manager.default_cuda, 'cpu')
    self.assertEqual(self.log.gpu_info.call_args_list, [call(), call()])
    self.log.gpu_info.reset_mock()
    self.log.gpu_info.side_effect = None
    self.monitor.cpu_log.append(12)
    self.monitor._add_gpu_data(self.snapshot)
    self.assertIn('Used GPU: [1]', self.monitor.get_basic_perf_info())
    self.log.gpu_info.assert_called_once_with()
    self.log.gpu_info.reset_mock()
    self.assertEqual(self.manager.get_default_cuda([]), 'cpu')
    self.assertEqual(self.manager.get_default_cuda(None), 'cpu')
    self.monitor.get_basic_perf_info(default_cuda=None)
    self.log.gpu_info.assert_not_called()

  def test_standalone_property_reads_configuration_before_observation(self):
    """Retain the existing order even if configuration changes during telemetry."""
    def collect():
      self.log.config_data[SMConst.SERVING_ENVIRONMENT][ct.DEFAULT_CUDA] = 'cuda:0'
      return self.snapshot

    self.log.gpu_info.side_effect = collect
    self.assertEqual(self.manager.default_cuda, 'cuda:1')
    self.assertEqual(self.manager.default_cuda, 'cuda:0')


if __name__ == '__main__':
  unittest.main()

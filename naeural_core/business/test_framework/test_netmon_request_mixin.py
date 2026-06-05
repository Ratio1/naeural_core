import unittest

from naeural_core.business.mixins_admin.network_monitor_mixin import (
  NMonConst,
  _NetworkMonitorMixin,
)


class _StubBc:
  def maybe_remove_prefix(self, addr):
    return addr.replace("0xai_", "", 1)


class _StubNetmon:
  all_nodes = ["REMOTE"]

  def __init__(self):
    self.history_calls = []
    self.pipeline_calls = []

  def network_node_eeid(self, addr):
    return "remote-eeid"

  def network_node_history(self, addr, hb_step, minutes, reverse_order):
    self.history_calls.append({
      "addr": addr,
      "hb_step": hb_step,
      "minutes": minutes,
      "reverse_order": reverse_order,
    })
    return {"timestamps": ["2026-05-28 10:00:00"]}

  def network_node_pipelines(self, addr):
    self.pipeline_calls.append(addr)
    return []


class _Requester(_NetworkMonitorMixin):
  def __init__(self):
    self.netmon = _StubNetmon()
    self.bc = _StubBc()
    self.e2_addr = "0xai_SELF"
    self.payloads = []
    self.messages = []

  def P(self, message, **kwargs):
    self.messages.append(message)

  def time(self):
    return 10.0 + len(self.payloads)

  def add_payload_by_fields(self, **kwargs):
    self.payloads.append(kwargs)


class TestNetworkMonitorRequestMixin(unittest.TestCase):

  def test_exec_netmon_request_accepts_prefixed_target_addresses(self):
    requester = _Requester()

    requester._exec_netmon_request(
      target_addr="0xai_REMOTE",
      request_type=NMonConst.NMON_CMD_HISTORY,
      request_options={"step": 2, "time_window_hours": 1},
      data={"SDK_REQUEST": "req-1"},
    )

    self.assertEqual(len(requester.payloads), 1)
    self.assertEqual(requester.netmon.history_calls[0]["addr"], "REMOTE")
    self.assertEqual(requester.netmon.history_calls[0]["hb_step"], 2)
    self.assertEqual(requester.payloads[0][NMonConst.NMON_RES_E2_TARGET_ADDR], "REMOTE")
    self.assertEqual(requester.payloads[0][NMonConst.NMON_RES_E2_TARGET_ID], "remote-eeid")
    self.assertEqual(requester.payloads[0]["command_params"]["SDK_REQUEST"], "req-1")

  def test_exec_netmon_request_does_not_respond_for_unknown_nodes(self):
    requester = _Requester()

    requester._exec_netmon_request(
      target_addr="0xai_MISSING",
      request_type=NMonConst.NMON_CMD_LAST_CONFIG,
      data={},
    )

    self.assertEqual(requester.payloads, [])
    self.assertEqual(requester.netmon.pipeline_calls, [])


if __name__ == "__main__":
  unittest.main()

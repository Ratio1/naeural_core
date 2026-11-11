"""A network processor takes one queued input per loop lap unless it opts in to more (the chain store does)."""
import importlib.util
import unittest
from collections import defaultdict, deque
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[2]


def _load_mixin():
  """The mixin alone (it has no imports): importing the plugin base would import the whole node."""
  spec = importlib.util.spec_from_file_location("_np_mixin", _ROOT / "business" / "mixins_libs" / "network_processor_mixin.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module._NetworkProcessorMixin


_NetworkProcessorMixin = _load_mixin()
_CONST = SimpleNamespace(PAYLOAD_DATA=SimpleNamespace(EE_PAYLOAD_PATH="EE_PAYLOAD_PATH", EE_SENDER="EE_SENDER"))


class _Plugin(_NetworkProcessorMixin):
  """Just what network_processor_loop and the base plugin's execute() use."""

  def __init__(self, n_inputs, seconds_per_input=0.0, fail_on=None, **cfg):
    self.upstream_inputs_deque = deque(
      {"EE_PAYLOAD_PATH": ["0xai_peer", "admin", "TEST_NP", "inst"], "EE_SENDER": "0xai_peer", "n": n}
      for n in range(n_inputs)
    )
    self.inputs, self.handled, self.now = None, [], 0.0
    self.seconds_per_input, self.fail_on = seconds_per_input, fail_on
    self.const, self.defaultdict = _CONST, defaultdict
    self.ee_addr, self._stream_id, self._signature, self.cfg_instance_id = "0xai_self", "admin", "TEST_NP", "inst"
    self.cfg_skip_message_verify, self.cfg_accept_self, self.cfg_full_debug_payloads = True, False, False
    self.cfg_max_inputs_per_iteration = 1  # the base plugin config's default
    for key, value in cfg.items():
      setattr(self, key, value)
    self.network_processor_init()

  def P(self, *args, **kwargs):
    return

  def time(self):
    return self.now

  @property
  def input_queue_size(self):
    return len(self.upstream_inputs_deque)

  def get_next_avail_input(self):
    return self.upstream_inputs_deque.popleft()

  def dataapi_struct_datas(self, full=False, as_list=True):
    return [] if self.inputs is None else [self.inputs]

  def on_payload_test_np(self, data):
    if data["n"] == self.fail_on:
      raise RuntimeError("handler failed")
    self.handled.append(data["n"])
    self.now += self.seconds_per_input

  def lap(self):
    # What execute() does around the plugin's process(): one input in, then consumed.
    self.inputs = self.get_next_avail_input() if self.input_queue_size > 0 else None
    try:
      self.network_processor_loop()
    finally:
      self.inputs = None


class TestInputsPerLap(unittest.TestCase):

  def test_default_is_one_input_per_lap(self):
    plugin = _Plugin(5)
    plugin.lap()
    self.assertEqual((plugin.handled, plugin.input_queue_size), ([0], 4))

  def test_every_plugin_gets_the_default_of_one(self):
    # The loop reads the key directly, and plugins that use the mixin with another base class run it too.
    source = (_ROOT / "business" / "base" / "base_plugin_biz.py").read_text(encoding="utf-8")
    self.assertIn("'MAX_INPUTS_PER_ITERATION': 1,", source)

  def test_opted_in_plugin_drains_the_queue_in_order(self):
    plugin = _Plugin(5, cfg_max_inputs_per_iteration=32)
    plugin.lap()
    self.assertEqual((plugin.handled, plugin.input_queue_size), ([0, 1, 2, 3, 4], 0))

  def test_at_most_the_configured_number_per_lap(self):
    plugin = _Plugin(5, cfg_max_inputs_per_iteration=3)
    plugin.lap()
    self.assertEqual((plugin.handled, plugin.input_queue_size), ([0, 1, 2], 2))

  def test_extra_inputs_stop_after_half_a_second(self):
    plugin = _Plugin(10, seconds_per_input=0.3, cfg_max_inputs_per_iteration=32)
    plugin.lap()  # deadline 0.8 s, set after the first input: two more fit
    self.assertEqual((plugin.handled, plugin.input_queue_size), ([0, 1, 2], 7))

  def test_a_failing_input_leaves_the_rest_queued(self):
    plugin = _Plugin(5, fail_on=1, cfg_max_inputs_per_iteration=32)
    with self.assertRaises(RuntimeError):
      plugin.lap()
    self.assertEqual((plugin.handled, plugin.input_queue_size), ([0], 3))

  def test_the_chain_store_opts_in(self):
    source = (_ROOT / "business" / "default" / "admin" / "chain_store_base.py").read_text(encoding="utf-8")
    source = source.replace("from naeural_core.business.base.network_processor import NetworkProcessorPlugin\n", "")
    class _Base:
      CONFIG = {"VALIDATION_RULES": {}}
      payload_handler = staticmethod(lambda signature="DEFAULT": (lambda func: func))

    space = {"NetworkProcessorPlugin": _Base}
    exec(compile(source, "chain_store_base.py", "exec"), space)  # noqa: S102
    self.assertEqual(space["_CONFIG"]["MAX_INPUTS_PER_ITERATION"], 32)


if __name__ == "__main__":
  unittest.main()

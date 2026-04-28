import unittest

from naeural_core.business.mixins_base.dataapi import _DataAPIMixin


class _DataAPIProbe(_DataAPIMixin):
  def __init__(self, inputs):
    self.inputs = inputs
    super().__init__()


class TestDataAPIMixinInputNormalization(unittest.TestCase):
  def test_dataapi_inputs_returns_empty_list_for_non_list_inputs(self):
    probe = _DataAPIProbe({
      "STREAM_NAME": "admin_pipeline",
      "INPUTS": {"bad": "shape"},
    })

    self.assertEqual(probe.dataapi_inputs(), [])
    self.assertEqual(probe.dataapi_struct_datas(full=False, as_list=True), [])

  def test_dataapi_inputs_drops_non_dict_entries(self):
    probe = _DataAPIProbe({
      "STREAM_NAME": "admin_pipeline",
      "INPUTS": [
        "bad-entry",
        {
          "TYPE": "STRUCT_DATA",
          "STRUCT_DATA": {"payload": 1},
          "IMG": None,
          "INIT_DATA": None,
          "METADATA": {},
        },
        42,
      ],
    })

    self.assertEqual(len(probe.dataapi_inputs()), 1)
    self.assertEqual(
      probe.dataapi_struct_datas(full=False, as_list=True),
      [{"payload": 1}],
    )

from naeural_core.data.base import AbstractMapReduceDataCapture

_CONFIG = {
  **AbstractMapReduceDataCapture.CONFIG,
  
  'VALIDATION_RULES' : {
    **AbstractMapReduceDataCapture.CONFIG['VALIDATION_RULES'],
  },
}

class BasicMapReduceDataCapture(AbstractMapReduceDataCapture):
  CONFIG = _CONFIG
  def __init__(self, **kwargs):
    super(BasicMapReduceDataCapture, self).__init__(**kwargs)
    return

  def _init_map(self):
    return

  def _release_map(self):
    return

  def _maybe_reconnect_map(self):
    return

  def _run_data_aquisition_step_map(self):
    workers = self._workers[:len(self.cfg_url)]
    self._add_inputs(
      [
        self._new_input(struct_data=dict(url_map=self.cfg_url, workers=workers))
      ]
    )

    self._finished_map = True
    return

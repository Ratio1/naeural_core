"""


"""

from naeural_core.business.base import BasePluginExecutor as BaseClass
from naeural_core.business.mixins_libs.network_processor_mixin import _NetworkProcessorMixin, __VER__

_CONFIG = {
  **BaseClass.CONFIG,
  
  'ALLOW_EMPTY_INPUTS' : False,
  
  'VALIDATION_RULES' : {
    **BaseClass.CONFIG['VALIDATION_RULES'],
  },  
}

class NetworkProcessorPlugin(
  BaseClass,
  _NetworkProcessorMixin
):
  CONFIG = _CONFIG

  def _on_init(self):
    self.network_processor_init()
    self.P(
      "NetworkProcessorPlugin v{} base initialization completed. Proceeding to custom init...".format(__VER__),
      color="green"
    )
    self.on_init()
    return


  def _process(self):
    """
    This method must be protected while the child plugins should have normal `process`
    """
    self.network_processor_loop()
    return self.process()

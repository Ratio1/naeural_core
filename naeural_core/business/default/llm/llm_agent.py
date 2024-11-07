from naeural_core.business.base import BasePluginExecutor as BasePlugin
from naeural_core.business.mixins_libs.nlp_agent_mixin import _NlpAgentMixin, NLP_AGENT_MIXIN_CONFIG

__VER__ = '0.1.0.0'

_CONFIG = {
  # mandatory area
  **BasePlugin.CONFIG,
  **NLP_AGENT_MIXIN_CONFIG,

  # our overwritten props
  'AI_ENGINE': "llm",

  'VALIDATION_RULES': {
    **BasePlugin.CONFIG['VALIDATION_RULES'],
    **NLP_AGENT_MIXIN_CONFIG['VALIDATION_RULES'],
  },
}


class LlmAgentPlugin(BasePlugin, _NlpAgentMixin):
  CONFIG = _CONFIG

  def _process(self):
    # we always receive input from the upstream due to the fact that _process
    # is called only when we have input based on ALLOW_EMPTY_INPUTS=False (from NLP_AGENT_MIXIN_CONFIG)
    data = self.dataapi_struct_data()
    self.P(f"Received request:\n{self.json_dumps(self.shorten_str(data), indent=2)}")
    inferences = self.dataapi_struct_data_inferences()
    return self.compute_response(data, inferences)

from naeural_core.business.base import BasePluginExecutor as BasePlugin

__VER__ = '0.1.0.0'

_CONFIG = {
  # mandatory area
  **BasePlugin.CONFIG,

  # our overwritten props
  'AI_ENGINE': "doc_embed",
  'ALLOW_EMPTY_INPUTS': False,  # if this is set to true the on-idle will continuously trigger the process

  'VALIDATION_RULES': {
    **BasePlugin.CONFIG['VALIDATION_RULES'],
  },
}


class DocEmbeddingAgentPlugin(BasePlugin):
  CONFIG = _CONFIG

  def _process(self):
    data = self.dataapi_struct_data()
    self.P(f"Received requests:\n{self.json_dumps(data, indent=2)}")
    inferences = self.dataapi_struct_data_inferences()
    self.P(f'Received following inferences: {inferences}')
    for inf in inferences:
      self.P(inf)
      # For each inference a response payload will be created
      self.add_payload_by_fields(**inf)
    # endfor inferences
    return

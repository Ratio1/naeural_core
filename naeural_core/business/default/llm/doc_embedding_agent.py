from naeural_core.business.base import BasePluginExecutor as BasePlugin

__VER__ = '0.1.0.0'

_CONFIG = {
  # mandatory area
  **BasePlugin.CONFIG,

  # our overwritten props
  'AI_ENGINE': "doc_embed",
  "DOC_EMBED_STATUS_PERIOD": 20,
  'ALLOW_EMPTY_INPUTS': True,  # if this is set to true the on-idle will continuously trigger the process

  'VALIDATION_RULES': {
    **BasePlugin.CONFIG['VALIDATION_RULES'],
  },
}


class DocEmbeddingAgentPlugin(BasePlugin):
  CONFIG = _CONFIG

  def on_init(self):
    self.__last_status_time = None
    super(DocEmbeddingAgentPlugin, self).on_init()
    return

  def send_status(self, inf_meta):
    self.add_payload_by_fields(
      contexts=inf_meta.get('contexts', []),
      model_name=inf_meta.get('model_name', ''),
      doc_embed_is_status=True,
    )
    return

  def maybe_send_status(self, inf_meta):
    if self.__last_status_time is None or self.time() - self.__last_status_time > self.cfg_doc_embed_status_period:
      self.send_status(inf_meta)
      self.__last_status_time = self.time()
    # endif
    return

  def _process(self):
    data = self.dataapi_struct_data()
    inf_meta = self.dataapi_inferences_meta().get(self.cfg_ai_engine)
    if inf_meta is None:
      return
    self.maybe_send_status(inf_meta)
    if data is None or len(data) == 0:
      return
    inferences = self.dataapi_struct_data_inferences()
    for inf in inferences:
      # For each inference a response payload will be created
      self.add_payload_by_fields(**inf)
    # endfor inferences
    return

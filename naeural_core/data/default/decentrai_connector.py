from naeural_core.data.base.base_decentrai_connector import BaseDecentraiConnectorDataCapture

_CONFIG = {
  **BaseDecentraiConnectorDataCapture.CONFIG,

  'VALIDATION_RULES': {
    **BaseDecentraiConnectorDataCapture.CONFIG['VALIDATION_RULES'],
  },
}


class DecentraiConnectorDataCapture(BaseDecentraiConnectorDataCapture):
  CONFIG = _CONFIG

  def interpret_message(self, message):
    return message

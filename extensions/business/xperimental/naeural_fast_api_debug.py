from naeural_core.business.default.web_app.naeural_fast_api_web_app import NaeuralFastApiWebApp

_CONFIG = {
  **NaeuralFastApiWebApp.CONFIG,

  # Only for the debug plugin
  "ASSETS": "extensions/business/xperimental",
  "DEBUG_MODE": True,

  'VALIDATION_RULES': {
    **NaeuralFastApiWebApp.CONFIG['VALIDATION_RULES'],
  },
}


class NaeuralFastApiDebugPlugin(NaeuralFastApiWebApp):
  """
  Debug plugin class for the Naeural Fast API Web App interface.
  """
  CONFIG = _CONFIG



from naeural_core.business.default.web_app.naeural_fast_api_web_app import NaeuralFastApiWebApp

_CONFIG = {
  **NaeuralFastApiWebApp.CONFIG,

  # Only for the debug plugin
  "ASSETS": "extensions/business/xperimental",
  "DEBUG_MODE": False,

  'VALIDATION_RULES': {
    **NaeuralFastApiWebApp.CONFIG['VALIDATION_RULES'],
  },
}


class NaeuralFastApiDebugPlugin(NaeuralFastApiWebApp):
  """
  Debug plugin class for the Naeural Fast API Web App interface.
  """
  CONFIG = _CONFIG

  @NaeuralFastApiWebApp.endpoint(method='get', require_token=True)
  def get_stuff_with_token(self, token: str, stuff: str = ""):
    if token not in ['123', 'alabala']:
      return "Unauthorized token"
    stuff = stuff or {"empty": True}
    return {
      'Status': 'Success',
      'Stuff': stuff
    }

  @NaeuralFastApiWebApp.endpoint(method='get')
  def get_stuff_without_token(self, stuff: str = ""):
    stuff = stuff or {"empty": True}
    return {
      'Status': 'Success',
      'Stuff': stuff
    }





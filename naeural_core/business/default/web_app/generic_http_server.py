from naeural_core.business.default.web_app.fast_api_web_app import FastApiWebAppPlugin as BasePlugin

__VER__ = '0.0.0.0'

_CONFIG = {
  **BasePlugin.CONFIG,
  'PROCESS_DELAY': 5,

  'NGROK_DOMAIN': None,
  'NGROK_EDGE_LABEL': None,

  'PORT': None,
  'ASSETS': None,
  'STATIC_DIRECTORY': '.',
  'DEFAULT_ROUTE': None,

  'VALIDATION_RULES': {
    **BasePlugin.CONFIG['VALIDATION_RULES']
  },
}


class GenericHttpServerPlugin(BasePlugin):
  """
  A plugin which handles a Basic http server web app hosted through FastAPI.
  """

  CONFIG = _CONFIG


from naeural_core.business.base.web_app.base_web_app_plugin import BaseWebAppPlugin as BasePlugin

__VER__ = '0.0.0.0'

_CONFIG = {
  **BasePlugin.CONFIG,
  'PROCESS_DELAY': 5,

  'NGROK_ENABLED': True,
  'NGROK_DOMAIN': None,
  'NGROK_EDGE_LABEL': None,

  'PORT': None,
  'ASSETS': None,
  'SETUP_COMMANDS': [],
  'START_COMMANDS': [],
  'AUTO_START': True,

  'RUN_COMMAND': "",

  'VALIDATION_RULES': {
    **BasePlugin.CONFIG['VALIDATION_RULES']
  },
}


class GenericWebAppPlugin(BasePlugin):
  """
  A plugin which handles a NodeJS web app.

  Assets must be a path to a directory containing the NodeJS app.
  """

  CONFIG = _CONFIG

  def get_start_commands(self):
    super_start_commands = super(GenericWebAppPlugin, self).get_start_commands()
    if len(self.cfg_run_command) > 0:
      return super_start_commands + [self.cfg_run_command]
    else:
      return super_start_commands

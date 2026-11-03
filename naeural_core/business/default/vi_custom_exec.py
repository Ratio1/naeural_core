"""
VI_CUSTOM_EXEC: debugging variant of CUSTOM_EXEC_01.

CUSTOM_EXEC_01 is listed in `ct.PLUGIN_SEARCH.BLOCKED_BIZ_PLUGINS` and is never
instantiated by the BusinessManager. This signature keeps the same behaviour under a
name that is not blocked, for debugging on nodes the operator controls. It executes
arbitrary code supplied in its `CODE` config, so treat it like CUSTOM_EXEC_01.
"""
from naeural_core.business.default.custom_exec_01 import CustomExec01Plugin

__VER__ = '0.1.0'

_CONFIG = {
  **CustomExec01Plugin.CONFIG,

  'VALIDATION_RULES' : {
    **CustomExec01Plugin.CONFIG['VALIDATION_RULES'],
  },
}


class ViCustomExecPlugin(CustomExec01Plugin):
  CONFIG = _CONFIG

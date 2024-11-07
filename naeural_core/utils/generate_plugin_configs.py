#global dependencies
import os

#local dependencies
from naeural_core import constants as ct

from naeural_core import Logger
from naeural_core.utils.config_utils import get_config_from_code

if __name__ == '__main__':
  log = Logger(
    lib_name='GPC', 
    base_folder='.',
    app_folder='_local_cache',
    config_file='config_startup.txt', 
    TF_KERAS=False
    )  
  
  path = ct.PLUGIN_SEARCH.LOC_BIZ_PLUGINS.replace('.', '/')
  files = [x for x in os.listdir(path) if x.endswith('.py')]
  files = [os.path.join(path, x) for x in files]
  dct = {}
  for fn in files:
    cfg = get_config_from_code(fn, log)
    if cfg is not None:
      signature = os.path.basename(fn).split('.')[0].upper()
      cfg[ct.SIGNATURE] = signature
      dct[signature] = cfg

  fn_out = '{}_all_plugins_config.txt'.format(log.now_str())
  log.save_output_json(
    data_json=dct, 
    fname=fn_out
    )

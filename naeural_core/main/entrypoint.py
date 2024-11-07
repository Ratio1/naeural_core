
import json
import multiprocessing as mp
import os
import shutil
import sys
import traceback
import warnings

from uuid import uuid4

warnings.filterwarnings("ignore")

#local dependencies
from naeural_core import constants as ct
from naeural_core.main.orchestrator import Orchestrator
from naeural_core import Logger

from naeural_core.main.ver import __VER__

# TODO: change to `from PyE2 import`
from PyE2.utils import load_dotenv

MANDATORY_PACKAGES = {
  'torch'           : '2.0',
  'accelerate'      : '0.2',
  'transformers'    : '4.43',
  'tokenizers'      : '0.14',
}

def maybe_replace_txt(fn):
  result = fn
  if fn.endswith('.txt'):
    fn_dest = fn.replace('.txt', '.json')
    print("Found '{}' as base startup config file. Converting to '{}'...".format(fn, fn_dest), flush=True)
    # duplicate .txt to .json    
    shutil.copy(fn, fn_dest)
    # delete old .txt
    os.remove(fn)
    result = fn_dest
  return result

def running_with_hostname(config_file):
  result = None
  custom_ee_id = False
  ee_id = os.environ.get(ct.CONFIG_STARTUP_v2.K_EE_ID, '')
  if len(ee_id) > 0:
    custom_ee_id = ee_id.upper().replace('X','') not in ['HOSTNAME', '']
    print("Found {} in env EE_ID: '{}' ".format("custom" if custom_ee_id else "default", ee_id), flush=True)    
  else:
    print("No EE_ID found in env", flush=True)
  is_hostname_config = False
  is_hostname_env = ee_id in ['HOSTNAME'] # if explicitly set to HOSTNAME in environment
  with open(config_file, 'r') as fh:
    config_data = json.load(fh)
    config_ee_id = config_data.get(ct.CONFIG_STARTUP_v2.K_EE_ID, '')
    print("Found EE_ID in config: '{}'".format(config_ee_id), flush=True)
    str_simple = config_ee_id.upper().replace('X','')
    is_hostname_config = str_simple in ['HOSTNAME', ''] # if explicitly set to HOSTNAME in config or first run with no config
    if str_simple == 'HOSTNAME':
      custom_ee_id = False # if config is set to HOSTNAME, then we ignore the env EE_ID
  #endwith config
  if (is_hostname_env or is_hostname_config) and not custom_ee_id:
    default_uuid = str(uuid4())[:8]
    result = os.environ.get('HOSTNAME', default_uuid)
  return result
  
def get_id(log):
  config_box_id = log.config_data.get(ct.CONFIG_STARTUP_v2.K_EE_ID, '')
  log.P("Found EE_ID '{}'".format(config_box_id))
  if config_box_id.upper().replace('X','').upper() in ['', 'HOSTNAME']:
    config_box_id_env = os.environ.get('EE_ID')
    log.P("Changing default node id '{}'...".format(config_box_id))  
    if config_box_id_env is not None and config_box_id != config_box_id_env:
      log.P("E2 configured from env '{}'".format(config_box_id_env), color='m')
      if config_box_id_env.upper() in ['E2DKR','HOSTNAME']: # same as in Dockerfile
        hostname = os.environ.get('HOSTNAME', '') 
        if hostname:
          config_box_id = hostname
        else:
          config_box_id = config_box_id_env + '-' + log.get_uid(size=4)
        #endif hostname
      else:
        config_box_id = config_box_id_env
      #endif E2dkr
    else:
      config_box_id = 'ee-' + log.get_uid(size=4)
      log.P("E2 is not manually configured nor from env. Assuming a random id '{}'".format(config_box_id), color='r')
    #endif no name
    log.config_data[ct.CONFIG_STARTUP_v2.K_EE_ID] = config_box_id
    log.P("  Saving/updating config with new EE_ID '{}'...".format(config_box_id))
    log.update_config_values({ct.CONFIG_STARTUP_v2.K_EE_ID: config_box_id})
  #endif config not ok
  return config_box_id
  
  
def get_config(config_fn):  
  fn = None
  extensions = ['.json', '.txt',]
  for loc in ['.', ct.LOCAL_CACHE]:
    for ext in extensions:
      test_fn = os.path.join(loc, config_fn + ext)
      print("Checking '{}'...".format(test_fn), flush=True)     
      if os.path.isfile(test_fn):
        fn = maybe_replace_txt(test_fn)
        break
      #endif file exists
    #endfor extensions
    if fn is not None:
      break
    #endif found
  #endfor locations
  
  if fn is not None:
    print("Found '{}' as base startup config file.".format(fn), flush=True)
  else:
    fn = "{}.json".format(config_fn)
    print("No startup config file found in base folder", flush=True)
    os.makedirs(ct.LOCAL_CACHE, exist_ok=True)
    fn = os.path.join(ct.LOCAL_CACHE, fn)
    is_config_in_local_cache = os.path.isfile(fn)
    print("Using {}: {}".format(fn, is_config_in_local_cache), flush=True)
    config_string= os.environ.get('EE_CONFIG', './.{}.json'.format(config_fn)) # default to local .config_startup.json
    is_config_string_a_file = os.path.isfile(config_string)
    
    if is_config_in_local_cache:
      print("Found '{}' config file in local cache.".format(fn), flush=True)
    #endif local cache
    elif is_config_string_a_file:
      shutil.copy(config_string, fn)
      print("Using '{}' -> '{} as base startup config file.".format(config_string, fn), flush=True)
    #endif config string is a file
    elif len(config_string) > 3:
      # assume input is json config and we will overwrite the local cache even if it exists
      print("Attempting to process JSON '{}'...".format(config_string), flush=True)      
      config_data = json.loads(config_string)
      if isinstance(config_data, dict):
        with open(fn, 'w') as fh:
          json.dump(config_data, fh)
        print("Saved config JSON to {}".format(fn), flush=True)
      else:
        print("ERROR: EE_CONFIG '{}' is neither config file nor valid json data".format(config_string), flush=True)
        sys.exit(ct.CODE_CONFIG_ERROR)
    else:
      print("ERROR: EE_CONFIG '{}' cannot be used for startup configuration".format(config_string), flush=True)
      sys.exit(ct.CODE_CONFIG_ERROR)
      #endif cache or copy
    #endif JSON or file
  #endif default config exists or not
  return fn


def main():
  app_base_folder = os.getcwd()
  print("Core Edge Node v{} starting in {}".format(__VER__, app_base_folder), flush=True)
  
  CONFIG_FILE = 'config_startup'
  is_docker = str(os.environ.get('AINODE_DOCKER')).lower() in ["yes", "true"]
  if not is_docker:
    load_dotenv()
      
  config_file = get_config(config_fn=CONFIG_FILE)
  hostname = running_with_hostname(config_file)
  
  if hostname is not None:
    print("Hostname is '{}' - changing to ./_local_cache/{}/ cache structure".format(hostname, hostname), flush=True)
    base_folder = '_local_cache'
    app_folder = hostname
  else:
    base_folder = '.'
    app_folder = ct.LOCAL_CACHE

  l = Logger(
    lib_name='EE',
    base_folder=base_folder,
    app_folder=app_folder,
    config_file=config_file,
    max_lines=1000, 
    TF_KERAS=False
  )
  
  if l.no_folders_no_save:
    l.P("ERROR: local cache not properly configured. Note: This version is not able to use read-only systems...", color='r', boxed=True)
    sys.exit(ct.CODE_CONFIG_ERROR)
  #endif no folders
  
  if l.config_data is None or len(l.config_data) == 0:
    l.P("ERROR: config_startup.txt is not a valid json file", color='r', boxed=True)
    sys.exit(ct.CODE_CONFIG_ERROR)
  else:
    l.P("Running with config:\n{}".format(json.dumps(l.config_data, indent=4)), color='n')

  packs = l.get_packages(as_text=True, indent=4, mandatory=MANDATORY_PACKAGES)
  l.P("Current build installed packages:\n{}".format(packs))

  # DEBUG log environment
  l.P("Environment:")
  for k in os.environ:
    if k.startswith('EE_') or k.startswith('AIXP'):
      l.P("  {}={}".format(k, os.environ[k]))
  # DEBUG end log environment

  if is_docker:
    # post config setup
    docker_env = os.environ.get('AINODE_ENV')
    l.P("Docker base layer environment {}".format(docker_env))
    # test GPU overwrite
  #endif docker post config  
  
  config_box_id = get_id(log=l)
  
  try:    
    lock = l.lock_process(config_box_id)

    l.P("Starting Execution Engine Main Loop...\n\n\n.", color=ct.COLORS.MAIN)
    eng = Orchestrator(log=l)
        
    if lock is None:
      msg = "Shutdown forced due to already started local processing node with id '{}'!".format(eng.cfg_eeid)
      eng.P(msg, color='error')
      return_code = eng.forced_shutdown()
    else:
      return_code = eng.main_loop()
      
    l.p('Execution Engine v{} main loop exits with return_code: {}'.format(
      eng.__version__, return_code), color=ct.COLORS.MAIN
    )
    l.maybe_unlock_windows_file_lock(lock)
    exit_code = return_code
      
  except Exception as e:
    l.p('Execution Engine encountered an error: {}'.format(str(e)), color=ct.COLORS.MAIN)
    l.p(traceback.format_exc())
    l.maybe_unlock_windows_file_lock(lock)    
    l.P("Execution Engine exiting with post-exception code: {}".format(ct.CODE_EXCEPTION), color='r')
    exit_code = ct.CODE_EXCEPTION
  #endtry main loop startup
  return exit_code, eng
  

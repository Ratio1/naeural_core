
import json
import multiprocessing as mp
import os
import subprocess
import tempfile
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

# TODO: change to `from ratio1 import`
from ratio1.utils import load_dotenv

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
  ee_id = os.environ.get(ct.CONFIG_STARTUP_v2.K_EE_ID, '')[:ct.EE_ALIAS_MAX_SIZE]
  if len(ee_id) > 0:
    custom_ee_id = ee_id.upper().replace('X','') not in ['HOSTNAME', '']
    print("Found {} in env EE_ID: '{}' ".format("custom" if custom_ee_id else "default", ee_id), flush=True)    
  else:
    print("No EE_ID found in env", flush=True)
  is_hostname_config = False
  is_hostname_env = ee_id in ['HOSTNAME'] # if explicitly set to HOSTNAME in environment
  with open(config_file, 'r') as fh:
    config_data = json.load(fh)
    config_ee_id = config_data.get(ct.CONFIG_STARTUP_v2.K_EE_ID, '')[:ct.EE_ALIAS_MAX_SIZE]
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
  
def get_id(log : Logger):
  config_box_id = log.config_data.get(ct.CONFIG_STARTUP_v2.K_EE_ID, '')[:ct.EE_ALIAS_MAX_SIZE]
  log.P("Found EE_ID '{}'".format(config_box_id))
  if config_box_id.upper().replace('X','').upper() in [None, '', 'HOSTNAME', 'E2DKR']:
    config_box_id_env = os.environ.get('EE_ID', '')
    if isinstance(config_box_id_env, str) and config_box_id_env.upper() not in ['', 'E2DKR','HOSTNAME']:
      config_box_id = config_box_id_env
    else:
      config_box_id = log.get_random_name(2)
      log.P("E2 is not manually configured nor from env. Assuming a random id '{}'".format(config_box_id), color='r')
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


def install_package_with_constraints_to_target(
  l: Logger,
  package_name : str,
  destination: str,
) -> bool:
  """
  Install a Python package into a specified target directory using pip,
  while respecting the current environment's package versions via a constraints
  file generated from `pip freeze`.

  Parameters
  ----------
  l : Logger
    The logger instance for logging messages.
  package_name : str
    The name of the package to install.
  destination
    The target directory where the package should be installed.

  Returns
  -------
  True if the installation was successful, False otherwise.
  """
  destination = os.path.abspath(destination)
  os.makedirs(destination, exist_ok=True)
  success = True

  try:
    with tempfile.TemporaryDirectory() as tmpdir:
      constraints_path = os.path.join(tmpdir, "constraints.txt")

      # 1) Snapshot current environment into constraints file
      with open(constraints_path, "w", encoding="utf-8") as f:
        subprocess.run(
          [sys.executable, "-m", "pip", "freeze"],
          stdout=f,
          stderr=subprocess.PIPE,
          check=True,  # raises CalledProcessError on non-zero exit
        )

      # 2) Install the requested package into the target directory
      cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        package_name,
        "-t",
        destination,
        "-c",
        constraints_path,
      ]
      subprocess.run(cmd, check=True)
    # endwith tempdir
  except Exception as e:
    l.P(f"ERROR: cannot install package '{package_name}' to '{destination}': {str(e)}", color='r', boxed=True)
    success = False
  return success


def check_installed_package_in_target(
  package_name: str,
  target_folder: str,
):
  """
  Check if a Python package is installed in a specified target directory.
  Very simple check: does `target_folder` contain a dist-info/egg-info directory
  for this package?

  This is a shallow presence check only. It does not validate package version,
  dependency health, or ABI compatibility.

  Parameters
  ----------
  package_name : str
    The name of the package to check.
  target_folder : str
    The target directory to check for the package.

  Returns
  -------
  True if the package is installed, False otherwise.
  """
  dest = os.path.abspath(target_folder)
  if not os.path.isdir(dest):
    return False

  # normalize like pip/importlib: lower + '-' -> '_'
  norm = package_name.lower().replace("-", "_")

  for entry in os.listdir(dest):
    entry_lower = entry.lower()

    # we only care about metadata dirs
    if not (entry_lower.endswith(".dist-info") or entry_lower.endswith(".egg-info")):
      continue

    # strip suffix and get the "name-version" part
    base = entry_lower.rsplit(".", 1)[0]  # remove .dist-info / .egg-info

    # split "name-version" -> "name"
    name_part = base.split("-", 1)[0].replace("-", "_")

    if name_part == norm:
      return True

  return False


def add_target_path_to_sys_path(target_folder: str):
  """
  Add the target folder to sys.path if not already present.

  Parameters
  ----------
  target_folder : str
    The target directory to add to sys.path.
  """
  target_folder = os.path.abspath(target_folder)
  if target_folder not in sys.path:
    sys.path.insert(0, target_folder)
  return


def get_additional_packages_cache_folder(base_folder: str) -> str:
  """
  Return the interpreter-specific cache folder used for additional packages.

  Notes
  -----
  Legacy flat `_bin` installs are intentionally ignored. New installs are scoped
  by interpreter cache tag to avoid reusing packages across Python upgrades.
  """
  cache_tag = getattr(sys.implementation, "cache_tag", None)
  if not cache_tag:
    cache_tag = "py{}{}".format(sys.version_info.major, sys.version_info.minor)
  return os.path.join(os.path.abspath(base_folder), "_bin", cache_tag)


def get_additional_packages_manifest_path(target_folder: str) -> str:
  """
  Return the manifest path used to track an interpreter-specific package cache.
  """
  return os.path.join(os.path.abspath(target_folder), "install_manifest.json")


def build_additional_packages_manifest(additional_packages: list) -> dict:
  """
  Build a normalized manifest for the current runtime and requested packages.

  Package order is normalized so equivalent requests do not trigger rebuilds.
  """
  normalized_packages = sorted(set(additional_packages or []))
  return {
    "python_version": "{}.{}.{}".format(
      sys.version_info.major,
      sys.version_info.minor,
      sys.version_info.micro,
    ),
    "cache_tag": getattr(sys.implementation, "cache_tag", None),
    "executable": sys.executable,
    "packages": normalized_packages,
  }


def load_additional_packages_manifest(target_folder: str):
  """
  Load the package-cache manifest for a target folder, if present.
  """
  manifest_path = get_additional_packages_manifest_path(target_folder)
  if not os.path.isfile(manifest_path):
    return None
  with open(manifest_path, "r", encoding="utf-8") as fh:
    return json.load(fh)


def save_additional_packages_manifest(target_folder: str, manifest: dict):
  """
  Persist the package-cache manifest for a target folder.
  """
  manifest_path = get_additional_packages_manifest_path(target_folder)
  with open(manifest_path, "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, indent=2, sort_keys=True)
  return


def additional_packages_manifest_matches(target_folder: str, additional_packages: list):
  """
  Compare the existing target-folder manifest with the current runtime manifest.

  Returns
  -------
  tuple[bool, dict, dict | None]
    A match flag, the current manifest, and the existing manifest if it could be
    loaded.
  """
  current_manifest = build_additional_packages_manifest(additional_packages=additional_packages)
  try:
    existing_manifest = load_additional_packages_manifest(target_folder=target_folder)
  except Exception:
    return False, current_manifest, None
  if existing_manifest is None:
    return False, current_manifest, None
  return existing_manifest == current_manifest, current_manifest, existing_manifest


def maybe_install_additional_packages(
  l: Logger,
  target_folder: str,
  additional_packages: list
):
  """
  Install additional Python packages into a specified target folder.

  The target folder is an interpreter-scoped cache. If its manifest is missing,
  unreadable, or mismatched with the current runtime and requested packages, the
  cache is rebuilt before package checks/installations proceed. A fresh manifest
  is written only after all requested packages are available.

  Parameters
  ----------
  l : Logger
    The logger instance for logging messages.
  target_folder : str
    The target directory where packages should be installed.
  additional_packages : list
    A list of package names to install.
  """
  if not additional_packages or len(additional_packages) == 0:
    return
  # endif no additional packages
  target_folder = os.path.abspath(target_folder)
  l.P(f"Checking additional packages in '{target_folder}': {additional_packages}")
  manifest_ok, current_manifest, existing_manifest = additional_packages_manifest_matches(
    target_folder=target_folder,
    additional_packages=additional_packages,
  )
  if not manifest_ok:
    if existing_manifest is None:
      l.P(
        "Additional package cache manifest missing or unreadable. Rebuilding interpreter cache...",
        color='y'
      )
    else:
      l.P(
        "Additional package cache manifest mismatch detected. Rebuilding interpreter cache...",
        color='y'
      )
      l.P(f"Existing manifest: {json.dumps(existing_manifest, indent=2, sort_keys=True)}")
      l.P(f"Current manifest: {json.dumps(current_manifest, indent=2, sort_keys=True)}")
    shutil.rmtree(target_folder, ignore_errors=True)
  os.makedirs(target_folder, exist_ok=True)
  all_packages_ok = True
  for pkg in additional_packages:
    if not check_installed_package_in_target(package_name=pkg, target_folder=target_folder):
      l.P(f"Package '{pkg}' not found in target folder. Installing...")
      success = install_package_with_constraints_to_target(
        l=l,
        package_name=pkg,
        destination=target_folder,
      )
      if success:
        l.P(f"Package '{pkg}' installed successfully.", color='g')
      else:
        l.P(f"Failed to install package '{pkg}'.", color='r', boxed=True)
        all_packages_ok = False
    else:
      l.P(f"Package '{pkg}' already installed.", color='g')
    # endif package not installed
  # endfor additional packages
  if all_packages_ok:
    save_additional_packages_manifest(
      target_folder=target_folder,
      manifest=current_manifest,
    )
  add_target_path_to_sys_path(target_folder)
  return


def main(additional_packages: list = None):
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

  if isinstance(additional_packages, list) and len(additional_packages) > 0:
    additional_packages_cache_folder = get_additional_packages_cache_folder(
      base_folder=l.app_folder
    )
    maybe_install_additional_packages(
      l=l,
      target_folder=additional_packages_cache_folder,
      additional_packages=additional_packages
    )
  # endif additional packages

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
  lock = None
  eng = None
  
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
  

"""
This plugin handles the following base functionality:
 - checks for new versions on the git server and restarts the EE if a new version is available
 - sends the config_startup.json file to the git server on request
 - receives the config_startup.json file from the git server on request
 - restarts the EE if the running time is above a certain threshold (default 2.5 days)
 
 
 
Examples:

{
  "EE_ID": "aid_hpc",
  "SB_ID": "aid_hpc",
  "ACTION": "UPDATE_PIPELINE_INSTANCE",
  "PAYLOAD": {
    "NAME": "admin_pipeline",
    "SIGNATURE": "UPDATE_MONITOR_01",
    "INSTANCE_ID": "UPDATE_MONITOR_01_INST",
    "INSTANCE_CONFIG": {
      "INSTANCE_COMMAND": {
        "COMMAND": "GET_CONFIG"
      }
    }
  },
  "INITIATOR_ID": "Explorer_xxxx",
  "EE_SIGN": "MEQCIEN1AH1NJ5A60L9xeKAa_EBliFDiBuR5-dWsqtjI6hz4AiB-zBSnyxQGRqmTud6QHGjquTDEcEQd2h4f2i1ZaKPzRQ==",   
  "EE_SENDER": "0xai_A6IrUO8pNoZrezX7UhYSjD7mAhpqt-p8wTVNHfuTzg-G",
  "EE_HASH": "47a9913c1f0b1cb5a4b29147cdcd9fd34c4235c9476c66521bbbd4ca1c84b69e"
}


{
  "EE_ID": "aid_hpc",
  "SB_ID": "aid_hpc",
  "ACTION": "UPDATE_PIPELINE_INSTANCE",
  "PAYLOAD": {
    "NAME": "admin_pipeline",
    "SIGNATURE": "UPDATE_MONITOR_01",
    "INSTANCE_ID": "UPDATE_MONITOR_01_INST",
    "INSTANCE_CONFIG": {
      "INSTANCE_COMMAND": {
        "COMMAND": "SAVE_CONFIG",
        "DATA": "eNqNVttu4zYQfa6/QlX7mPiSIrttgKJlJNrmRiJViUrWLQJCrp6elpuMqz7V32tVJQMTr7uP32WI7TeHz/27/R+Ug/VUS03Yp1WYiijNZxuh6WX8s/VsnvE7MOqAR1O3j9D+cb42E="   
      }
    }
  },
  "INITIATOR_ID": "Explorer_xxxx",
  "EE_SIGN": "MEUCIQC2B_WWthwdK2sXSCtuFJ0_YmevvDDpUaNLfDn2SF2PGgIgURac5MspmGFyJxccGOYOAVghb_gX-ogR7StG3y-9u-c=",   
  "EE_SENDER": "0xai_A6IrUO8pNoZrezX7UhYSjD7mAhpqt-p8wTVNHfuTzg-G",
  "EE_HASH": "38b557b6b673f75d7a809d637bf93420d477fdddc109a90b86c2fc7a3a57a7aa"
} 
 
"""
from naeural_core.business.base import BasePluginExecutor
import os


class UpdateCt:
  VERSION_RESTART = "VERSION UPDATE RESTART"
  FORCED_RESTART = "FORCED RESTART"

__VER__ = '0.1.0.0'

_CONFIG = {
  **BasePluginExecutor.CONFIG,

  "WORKING_HOURS"       : [["08:30", "09:30"]],    
  
  "FORCE_RESTART_AFTER" : 3600 * 24 * 1,  # days restart time
  "REBOOT_ON_RESTART"   : False,
  
  "BUILD_DELAY"         : 30 * 60,

  'ALLOW_EMPTY_INPUTS'  : True,
  
  'SERVER_URL'          : '',

  "PROCESS_DELAY"       : 100,

  "VERSION_TOKEN"       : None,
  "VERSION_URL"         : None,
  
  "LOG_INFO"            : False,

  "RESTART_ON_BEHIND"   : True,

  "USE_YAML"            : False,
  "RELEASE_TAG"         : "staging",

  # debug stuff
  # end debug stuff

  'VALIDATION_RULES': {
    **BasePluginExecutor.CONFIG['VALIDATION_RULES'],
  },
}

class UpdateMonitor01Plugin(BasePluginExecutor):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(UpdateMonitor01Plugin, self).__init__(**kwargs)
    return


  def on_init(self):
    self._update_monitor_count = 0
    self.__logo = False
    self.__failed_download = False
    self.__restart_inititated = False
    self.__restart_inititated_time = None
    DEFAULT_STATE = {
      "last_update" : None,
      "last_prev"   : None,
      "last_new"    : None,
    }
    self.__state = self.cacheapi_load_json(default=DEFAULT_STATE)
    return
  
    
  def __get_config_startup_as_base64(
    self, 
    compress=True, 
  ):
    """Loads the config_startup.json file and generates a base64 encoded string"""
    config_file = self.log.config_file
    if not os.path.isfile(config_file):
      raise ValueError("Config file '{}' not found!".format(config_file))
    self.P("Loading and sending config file {}".format(config_file))
    with open(config_file, 'r') as f:
      str_config = f.read()
    str_b64 = self.string_to_base64(str_config, compress=compress)
    return str_b64
  
  def _send_config_payload(self):
    # TODO: upgrade this method to send the config payload to the target_id with simmetric encrypton
    #       and decryption on the other side (the initiator or "target_id" side)
    str_config_startup_b64 = self.__get_config_startup_as_base64()
    result = self.add_payload_by_fields(
      config_startup=str_config_startup_b64,      
    )
    return
  
  
  def __validate_config(self, dct_config):
    """Validates the config_startup.json file"""
    is_ok = True
    mandatory_keys = self.ct.CONFIG_STARTUP_MANDATORY_KEYS
    for k in mandatory_keys:
      if (k not in dct_config) or (dct_config[k] is None):
        is_ok = False
      elif isinstance(dct_config[k], (list, str, dict)) and len(dct_config[k]) == 0:
        is_ok = False
    # endfor mandatory keys
    return is_ok
  
  
  def __save_config_startup(
    self, 
    str_input : str, 
    decompress=True,
  ):
    """Saves the config_startup.json file from a base64 encoded string or straight json"""
    config_file = self.log.config_file
    self.P("Saving received config in {}".format(config_file))

    is_ok = True
    fail_reason = None

    if str_input.startswith('{'):
      str_json = str_input
    else:
      str_json = self.base64_to_str(str_input, decompress=decompress)
    try:
      dct_config = self.json_loads(str_json)
      is_ok = True
    except Exception as e:
      is_ok = False
      fail_reason = str(e)

    # now validate that the received config contains the required fields
    if is_ok:
      is_ok = self.__validate_config(dct_config)
      if not is_ok:
        fail_reason = "Validation failed! Missing mandatory keys or wrong value types."
    # endif validation

    if is_ok:
      with open(config_file, 'w') as f:
        f.write(str_json)
      self.P("Node config saved OK!", boxed=True, box_char='*')
    else:
      self.P("Node config save failed: {}".format(fail_reason), color='r')
    return
  
  def _process_config_payload(self, data):
    # TODO: upgrade this method to decrypt the config payload received from the other side
    #       and update the local config with the received values
    if isinstance(data, dict):
      str_data = self.safe_json_dumps(data)
    elif isinstance(data, str):
      str_data = data
    else:
      raise ValueError("Invalid data type: {}".format(type(data)))
    self.__save_config_startup(str_input=str_data)
    return
  
  def _send_whitelist_payload(self):
    lst_allowed = self.bc.get_whitelist()
    result = self.add_payload_by_fields(
      ee_whitelist=lst_allowed,      
    )
    return
  
  
  ###

  def on_command(self, data, **kwargs):
    self.P("Update Monitor on {} received: {}".format(self.eeid, str(data)[:100]))
    command = 'UPDATE_CHECK'
    target_id = None
    if isinstance(data, dict):
      data = {k.upper():v for k,v in data.items()}
      # target_id = data.get('TARGET_ID') # Obsolete
      command = data.get('COMMAND', "UPDATE_CHECK").upper()

    if command == 'UPDATE_CHECK': 
      self.P("Running on-demand git ver check and validation {} ...".format(
        self._update_monitor_count)
      )
      self._process_update_check()
    #end update_check command
    elif command == 'GET_CONFIG':
      self.P("Running on-demand config send...")
      self._send_config_payload()
    #end get_config command
    elif command == 'SAVE_CONFIG':
      self.P("Running on-demand config saving...")
      data = data.get('DATA') 
      self._process_config_payload(data=data)
    #end save_config command      
    elif command == 'GET_WHITELIST':
      self.P("Running on-demand whitelist request ...")
      self._send_whitelist_payload()
    #endif commands
    return


  def needs_forced_restart(self):
    if self.cfg_force_restart_after is None:
      return False
    elif self.get_node_running_time() > self.cfg_force_restart_after:
      return True
    return False
  

  def get_int_ver(self, ver):
    try:
      digits = [int(x) for x in ver.split('.')]
      int_ver = 0
      for i, digit in enumerate(reversed(digits)):
        int_ver += (1000 ** i) * digit
    except:
      str_error = self.trace_info()
      msg = "Error decoding version string: {}\n{}".format(ver, str_error)
      self.P(msg, color='r')
      raise ValueError("`get_int_ver FAILED: " + msg)
    return int_ver

  def get_version_from_raw(self, resp):
    """
    Assume the response is a string with the version in the format:
    version="__VER__"

    Parameters
    ----------
    resp : str
        The response string

    Returns
    -------
    str
        The version string.
    """
    ver = resp.split('=')[1]
    ver = ver.rstrip().lstrip().replace('"','').replace("'", '')
    return ver

  def get_version_from_yaml(self, resp):
    """
    Parse the response as a yaml file and extract the version from the release tag.
    The response is expected to be in the format:
    application:
      envs:
        - name: staging
          version: "__VER__"
          info: "..."
        - name: production
          version: "__VER__"
          info: "..."
        ...

    Parameters
    ----------
    resp : str
        The response string

    Returns
    -------
    str
        The version string.
    """
    dct = self.yaml.safe_load(resp)
    lst_releases = dct["application"]["envs"]
    release = [x for x in lst_releases if x["name"] == self.cfg_release_tag][0]
    ver = release["version"]
    ver = ver.rstrip().lstrip().replace('"','').replace("'", '')

    return ver

  def get_git_server_version(self):
    ver = None
    branch = self.docker_branch if self.runs_in_docker else self.log.git_branch
    token = self.cfg_version_token
    url0 = self.cfg_version_url
    if not token is None and not url0 is None:
      if '{}' in url0:
        url0 = url0.format(branch)
      resp = None
      try:
        headers = {'Authorization': 'token ' + token}      
        resp0 = self.requests.get(url0, headers=headers)
        resp = resp0.content.decode()
        self.P("Retrieved for `{}:{}` data: {}".format(
          "docker" if self.runs_in_docker else "git-src", branch,
          resp[:100].replace('\n', ' '))
        )
        if self.cfg_use_yaml:
          ver = self.get_version_from_yaml(resp)
        else:
          ver = self.get_version_from_raw(resp)
      except Exception as exc:
        self.P("Exception while retrieving version with url:{} , headers:{}. Response was: {}, exception: {}".format(
          url0, headers, resp, exc), color='r')
    return ver  
  
  
  def _maybe_raise_failed_update_alert_and_save(self, prev_ver, new_ver):
    stored_prev_ver = self.__state.get('last_prev')
    stored_new_ver = self.__state.get('last_new')
    stored_update_time = self.__state.get('last_update')
    send_notif = False
    str_time = self.time_to_str()
    if prev_ver == stored_prev_ver and new_ver == stored_new_ver:
      msg = f"{self._signature} detected a previous FAILED update attempt on {stored_update_time}. "
      send_notif = True
    else:
      msg = f"Saving update initiation for prev:{prev_ver} new:{new_ver} at {str_time}"
    self.P(msg, color='r' if send_notif else None)
    self.__state['last_prev'] = prev_ver
    self.__state['last_new'] = new_ver
    self.__state['last_update'] = str_time
    self.cacheapi_save_json(self.__state)
    if send_notif:
      self._create_abnormal_notification(
        msg=msg,
      )
      self.add_payload_by_fields(
        status=msg,
        is_alert=True,
        is_new_raise=True,
      )
    return


  def _process_update_check(self):
    needs_restart = False
    self._update_monitor_count += 1
    
    self.P("Running git ver check and validation {} at {} running time. Forced restart required: {}. Parsing a yaml file: {}".format(
      self._update_monitor_count, self.get_node_running_time_str(), self.needs_forced_restart(), self.cfg_use_yaml
    ))
    server_ver = self.get_git_server_version()
    self.P("  Received: {}".format(server_ver))
    
    if not self.__logo:
      msg = "============================================================\n"
      msg+= "Running git ver check and validation {} received: {}\n".format(
        self._update_monitor_count, server_ver)
      msg+= "============================================================"   
      self.__logo = True
      self.P("Initial version check\n{}".format(msg))
    #endif log info 
    
    local_ver = self.ee_ver
    if server_ver is None:
      self.__failed_download = True
    else:
      self.__failed_download = False
      i_sver = self.get_int_ver(server_ver)
      i_lver = self.get_int_ver(local_ver)      
      if local_ver != server_ver:
        msg = "Current version {}({}) differs from server '{}' ver {}({}).".format(
          local_ver, i_lver,
          self.log.git_branch,
          server_ver, i_sver,
        )
              
        if self.__restart_inititated:
          self.P("WARNING: E2 restart already initiated at {}".format(self.__restart_inititated_time), color='r')
        elif i_sver > i_lver:
          full_msg = "Local version is below server version. E2 update is required! {}".format(msg)
          if self.cfg_restart_on_behind:
            full_msg = "Restarting E2 initiated for auto-update: " + full_msg
          self.P(full_msg, color='r')
          self.add_payload_by_fields(
            local_ver=local_ver,
            server_ver=server_ver,
            message=full_msg,
            status=msg + " INITIATED",
            is_alert=True,
            is_new_raise=True,
            restart_initiated=self.cfg_restart_on_behind,
            restart_now=False,
            forced_restart=False,
            forced_restart_running_time=self.get_node_running_time_str(),            
          )
          # now save the update initiation and check for potential previous failed update
          self._maybe_raise_failed_update_alert_and_save(prev_ver=local_ver, new_ver=server_ver)
          
          if self.cfg_restart_on_behind:
            needs_restart = True            
          #endif restart on behind            
        else:
          self.P("{} Local version is synced or better than server. All good.".format(msg))
        #endif server version is higher or lower
      #endif versions differs
    #endif server version is None

    
    if needs_restart or self.needs_forced_restart():
      msg = UpdateCt.VERSION_RESTART if needs_restart else UpdateCt.FORCED_RESTART
      self.P(f"INITIATING NODE REBOOT DUE TO <{msg}>...", color='r', boxed=True, box_char='*')
      skip = False
      
      if not self.needs_forced_restart() and (self.cfg_working_hours is None or len(self.cfg_working_hours) == 0):
        # now we need to wait as this instance is configured to restart at first sign!
        # we dont wait if the instance is configured to restart at certain uptime and is outside working hours
        t_start = self.time()
        sleep_time = self.cfg_build_delay // 30
        while (self.time() - t_start) <= self.cfg_build_delay:                
          shown = False
          if skip:
            break
          t_sleep = self.time()
          while (self.time() - t_sleep) <= sleep_time:
            if self.done_loop:
              self.P("EXTERNAL STOP RECEIVED. EXITING...", color='r', boxed=True, box_char='*')
              skip = True
              break
            elapsed = self.time() - t_start
            remaining = self.cfg_build_delay - elapsed                  
            self.sleep(0.1)
            if not shown:
              self.P("Delayed RESTART in {:.1f}s ...".format(remaining), color='r', boxed=True, box_char='*')                
              shown = True
            #display first iter
          #endwhile delay in delay :)
        #endwhile delay
      #endif working hours if around the clock
      
      if not skip or self.needs_forced_restart():
        use_reboot = self.cfg_reboot_on_restart
        if use_reboot:
          self.cmdapi_restart_current_box()
        else:
          self.cmdapi_stop_current_box()
        #endif use restart
        self.__restart_inititated = True
        self.__restart_inititated_time = self.time_to_str()
        status = "E2 Node reboot initiated at {} based on `{}`".format(
          self.__restart_inititated_time,
          msg,
        )
        self.add_payload_by_fields(
          local_ver=local_ver,
          server_ver=server_ver,
          message=status,
          status=msg,
          is_alert=True,
          is_new_raise=True,
          restart_initiated=self.cfg_restart_on_behind,
          restart_now=True,
          forced_restart=self.needs_forced_restart(),
          forced_restart_running_time=self.get_node_running_time_str(),                      
        )
        self.P(status, color='r', boxed=True, box_char='*')
      #endif skip restart due to external stop
    #endif needs restart

    if self.__failed_download:
      self.P("WARNING: version check failed!", color='r')    
    
    return


  def process(self):
    self._process_update_check()
    return

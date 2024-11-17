"""
  OBSERVATION:
    
    Within this file the terms "stream" and "pipeline" have the same meaning - that of a execution engine pipeline
    While the correct term is "pipeline" as "stream" naturally refers to the data being consumed this is still used
    for backward compatibility.
"""

import os
import shutil
import json

from naeural_core import constants as ct
from copy import deepcopy
from collections import deque

from naeural_core import Logger
from naeural_core.manager import Manager
from naeural_core.config.mixins import _ConfigManagerCheckMixin
from naeural_core.config.config_manager_commands import ConfigCommandHandlers

EXTENSION = '.json'

CONST_ADMIN_PIPELINE = 'admin_pipeline'

class ConfigManager(
  Manager, 
  _ConfigManagerCheckMixin,
  ConfigCommandHandlers,
  ):

  def __init__(self, log : Logger, shmem,
               owner,
               subfolder_path=None,
               fn_app_config=None,
               folder_streams_configs=None,
               **kwargs):
    self.shmem = shmem
    self.config_app = None
    self.owner = owner
    self.dct_config_streams = None
    self.admin_pipeline_name = CONST_ADMIN_PIPELINE

    self._dct_retrievers = None

    self._subfolder_path = subfolder_path
    self._fn_app_config = fn_app_config
    self._folder_streams_configs = folder_streams_configs
    
    self.__command_cache = {}

    self._path_streams = None
    super(ConfigManager, self).__init__(log=log, prefix_log='[CFGM]', **kwargs)
    return

  def startup(self):
    super().startup()
    self._dct_retrievers = self._dct_subalterns

    if self._subfolder_path is not None:
      save_path = os.path.join(self.log.get_data_folder(), self._subfolder_path)
      if not os.path.exists(save_path):
        os.makedirs(save_path)

    if self._fn_app_config is None:
      self._fn_app_config = 'config_app.txt'

    if self._folder_streams_configs is None:
      self._folder_streams_configs = 'streams'

    if self._subfolder_path is not None:
      self._fn_app_config = os.path.join(self._subfolder_path, self._fn_app_config)
      self._folder_streams_configs = os.path.join(self._subfolder_path, self._folder_streams_configs)

    self._path_streams = os.path.join(self.log.get_data_folder(), self._folder_streams_configs)
    if not os.path.exists(self._path_streams):
      os.makedirs(self._path_streams)
    return

  @property
  def cfg_app_communication(self):
    # self.config_app points to the `config_app.txt` json available in `_data/box...`
    return self.config_app.get(ct.CONFIG_APP_v2.K_COMMUNICATION, None)

  @property
  def cfg_app_file_upload(self):
    # self.config_app points to the `config_app.txt` json available in `_data/box...`
    return self.config_app.get('FILE_UPLOAD', None) ### TODO ct  
  
  
  @property
  def formatter(self):
    io_formatter_manager = self.shmem.get('io_formatter_manager', None)
    if io_formatter_manager is not None:
      formatter, formatter_name =  io_formatter_manager.get_formatter()
      return formatter
    return
  
  def _check_valid_payload_type(self, payload_type):
    assert payload_type in [
      ct.PAYLOAD_CT.COMMANDS.UPDATE_CONFIG,
      ct.PAYLOAD_CT.COMMANDS.UPDATE_PIPELINE_INSTANCE,
      ct.PAYLOAD_CT.COMMANDS.BATCH_UPDATE_PIPELINE_INSTANCE,
    ]
    if payload_type not in self.__command_cache:
      self.__command_cache[payload_type] = deque(maxlen=10)
    return
  
  
  def _add_command_to_cache(self, payload, payload_type):
    self._check_valid_payload_type(payload_type)
    self.__command_cache[payload_type].append(payload)
    return
  
  
  def _check_duplicate_last(self, payload, payload_type):
    self._check_valid_payload_type(payload_type)    
    if len(self.__command_cache[payload_type]) > 0:
      if self.__command_cache[payload_type] == payload:
        return True
    self._add_command_to_cache(payload, payload_type)
    return False
  
    

  def _get_config_plugin_instance(self, name, config):
    _module_name, _class_name, _class_def, _class_config = self._get_module_name_and_class(
      locations=ct.PLUGIN_SEARCH.LOC_CONFIG_RETRIEVE_PLUGINS,
      name=name,
      suffix=ct.PLUGIN_SEARCH.SUFFIX_CONFIG_RETRIEVE_PLUGINS,
      safe_locations=ct.PLUGIN_SEARCH.SAFE_LOC_CONFIG_PLUGINS,
    )

    if _class_def is None:
      msg = "Error loading config retrieve plugin '{}'".format(name)
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        info="No code/script defined for config retrieve plugin '{}' in {}".format(name, ct.PLUGIN_SEARCH.LOC_CONFIG_RETRIEVE_PLUGINS)
      )
    #endif

    try:
      if self.owner.runs_in_docker:
        self.P("Running in Docker - adding fallback config '{}'".format(ct.CONFIG_APP_DOCKER_FALLBACK))
        config[ct.CONFIG_APP_FALLBACK] = ct.CONFIG_APP_DOCKER_FALLBACK
      retriever = _class_def(
        log=self.log,
        config=config,
        subfolder_path=self._subfolder_path,
        fn_app_config=self._fn_app_config,
        folder_streams_configs=self._folder_streams_configs
      )
    except Exception as exc:
      msg = "Exception '{}' when initializing config retrieve plugin {}".format(exc, name)
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        autocomplete_info=True
      )
      raise exc
    #end try-except
    return retriever



  def _save_app_configuration(self, config_app):
    self.log.save_data_json(config_app, self._fn_app_config)
    return
  
  
  def _save_instance_modifications(
    self,
    pipeline_name,
    signature,
    instance_id, 
    config, 
    skip_update_time=False,
    verbose=False,
  ):
    """
    Will save the instance modifications in the local cache. 
    This is used when the instance is modified in-memory

    Parameters
    ----------
    pipeline_name : str
      The name of the pipeline
      
    signature : str
      The signature of the plugin
      
    instance_id : str
      The instance id of the plugin
      
    config : dict
      The configuration of the plugin instance    
    """
    result = False
    instance_config = self._get_plugin_instance(
      stream_name=pipeline_name,
      signature=signature,
      instance_id=instance_id,
    )
    if instance_config is not None:
      if verbose:
        self.log.P("Saving instance  <{}:{}:{}> config to local cache for following keys:\n{}".format(
          pipeline_name, signature, instance_id, json.dumps(config, indent=4)), color='b'
        )
      else:
        self.log.P("Saving instance  <{}:{}:{}> config to local cache".format(
          pipeline_name, signature, instance_id), color='b'
        )
      for k,v in config.items():
        instance_config[k] = v
      #endfor  
      self._save_stream_config(self.dct_config_streams[pipeline_name], skip_update_time=skip_update_time)
      result = True
    #endif saving config
    return result


  def _save_stream_config(self, config_stream, subfolder_path=None, prefix_fn=None, skip_update_time=False):
    if subfolder_path is None:
      subfolder_path = self._folder_streams_configs
      if not skip_update_time:
        config_stream[ct.CONFIG_STREAM.LAST_UPDATE_TIME] = self.log.now_str(nice_print=True)
    #endif
    name = config_stream['NAME']
    fname = name + EXTENSION
    if prefix_fn is not None:
      fname = prefix_fn.rstrip('_') + '_' + fname
    self.P("Saving pipeline {}:{} in {}".format(name, fname, subfolder_path))
    self.log.save_data_json(config_stream, fname, subfolder_path=subfolder_path)
    return
  


  def _delete_stream_config(self, stream_name):
    fname = stream_name + EXTENSION
    fn_full =  os.path.join(self.log.get_data_folder(), self._folder_streams_configs, fname)
    if os.path.isfile(fn_full):
      os.remove(fn_full)
    else:
      msg = "WARNING: Seems the pipeline was already deleted: {}".format(fn_full)
      self.P(msg, color='r')      
    return


  def _save_streams_configurations(self, dct_config_streams):
    for _,config_s in dct_config_streams.items():
      self._save_stream_config(config_s)
    return


  def _flush_existing_app_configuration(self):
    path_config_app = self.log.get_data_file(self._fn_app_config)
    if path_config_app is not None:
      os.remove(path_config_app)
    return


  def _flush_existing_streams_configurations(self):
    path_streams = self.log.get_data_subfolder(self._folder_streams_configs)
    if path_streams is not None:
      # shutil.rmtree(path_streams) # destroy everything - not advisable
      lst_files_txt = [x for x in os.listdir(path_streams) if EXTENSION in x]
      for fn in lst_files_txt:
        fn_full = os.path.join(path_streams, fn)
        self.P("Deleting pipeline file '{}'".format(fn_full))
        os.remove(fn_full)
    return
  
  def __apply_nested_key(self, config: dict, nested_key: str, value: any):
    # will apply the value to the nested key in the configuration
    keys = nested_key.split('.')
    for nested_key in keys[:-1]:
      if nested_key not in config:
        config[nested_key] = {}
      config = config[nested_key]
    #endfor
    config[keys[-1]] = value
    return

  def _apply_delta_to_config(self, original_config: dict, delta_config: dict, ignore_fields: list = None) -> dict:
    """
    Will apply the delta configuration to the original configuration inplace.
    The delta configuration is a dictionary with the keys that need to be updated.
    The keys can be nested, separated by '.'. In this case, the original configuration
    will be traversed in depth.
    
    Parameters
    ----------
    original_config : dict
        The original configuration
    delta_config : dict
        The proposed changes to the original configuration
    ignore_fields : list | None, optional
        List of fields to ignore when applying the delta configuration, by default None

    Returns
    -------
    dict
        The updated configuration
    """

    if ignore_fields is None:
      ignore_fields = []

    for k, v in delta_config.items():
      if k in ignore_fields:
        continue
      
      if '.' not in k:
        original_config[k] = v
      else:
        self.__apply_nested_key(original_config, k, v)
    # endfor

    return original_config
  
  def save_instance_modifications(self, pipeline_name, signature, instance_id, config):
    """
    Will save the instance modifications in the local cache. 
    This is used when the instance is modified in-memory

    Parameters
    ----------
    pipeline_name : str
      The name of the pipeline
      
    signature : str
      The signature of the plugin
      
    instance_id : str
      The instance id of the plugin
      
    config : dict
      The configuration of the plugin instance    
    """
    return self._save_instance_modifications(pipeline_name, signature, instance_id, config)  
  
  
  def save_pipeline_modifications(self, pipeline_name, pipeline_config, skip_update_time=False):
    """
    Will save the pipeline modifications in the local cache. 
    This is used when the pipeline is modified in-memory

    Parameters
    ----------
    pipeline_name : str
      The name of the pipeline
      
    pipeline_config : dict
      The configuration of the pipeline    
    """
    if pipeline_name not in self.dct_config_streams:
      self.P("Cannot save pipeline modifications for non-existing pipeline '{}'".format(pipeline_name), color='error')
      return None
    #endif
    
    for k,v in pipeline_config.items():
      self.dct_config_streams[pipeline_name][k] = v
      
    return self._save_stream_config(self.dct_config_streams[pipeline_name], skip_update_time=skip_update_time)


  def retrieve(self, lst_config_retrieve):
    self.P("Performing config retrieve...")
    for config in lst_config_retrieve:
      plugin_name = config[ct.CONFIG_RETRIEVE.K_TYPE]
      retriever = self._get_config_plugin_instance(plugin_name, config)
      endpoint = config.get(ct.CONFIG_RETRIEVE.K_APP_CONFIG_ENDPOINT, None)
      self.P("  Running retriever {}:{}".format(plugin_name, endpoint))
      if retriever:
        self._dct_retrievers[plugin_name] = retriever
        retriever.connect(**config.get(ct.CONFIG_RETRIEVE.K_CONNECT_PARAMS, {}))

        config_app, dct_config_streams = retriever.retrieve(
          app_config_endpoint=endpoint,
          streams_configs_endpoint=config.get(ct.CONFIG_RETRIEVE.K_STREAMS_CONFIGS_ENDPOINT, None)
        )

        if config_app is not None:
          self.P("  Connected and retrieved comms config of size {}...".format(len(config_app)))
          self.P("  Running config check...")
          config_app = self._check_config_app(config_app)
          self.P("  App config - Flushing existing config and saving the new one ...")
          self._flush_existing_app_configuration()
          self._save_app_configuration(config_app)
        else:
          self.P("  No comms config retrieved from given endpoint", color='r')          
        #endif

        if dct_config_streams is not None:
          self.P("  Connected and retrieved streams config...")
          formatter = self.formatter
          if formatter is not None:
            dct_config_streams = formatter.decode_streams(dct_config_streams)
          #endif

          dct_config_streams = self.keep_all_good_streams(dct_config_streams)
          if len(dct_config_streams) > 0:
            self.P("  Streams configs - Flush existing configs and saving the new ones ...")
            self._flush_existing_streams_configurations()
            self._save_streams_configurations(dct_config_streams)
          #endif
        else:
          self.P("  No streams configured from endpoint", color='r')          
        #endif if dct-config-streams
      #endif
    #endfor
    return
  
  
  def maybe_setup_admin_pipeline(self):
    ADMIN_PIPELINE_VER = "2.0.0"
    INSTANCE_STR = "{}_INST"
    pipeline_name = self.admin_pipeline_name
    self.P("Setting-up admin jobs pipeline...")  
    # we setup the default admin pipeline type and params  
    defualt_admin_pipeline_setup = {
      ct.CONFIG_STREAM.K_NAME     : self.admin_pipeline_name,
      ct.CONFIG_STREAM.K_TYPE     : ct.CONFIG_STREAM.DEFAULT_ADMIN_PIPELINE_TYPE,
      
      
      ct.CONFIG_STREAM.PIPELINE_OPTIONS.NETWORK_LISTENER_PATH_FILTER : [
        None, None, 
        ["UPDATE_MONITOR_01", "NET_MON_01"],
        None
      ],
      ct.CONFIG_STREAM.PIPELINE_OPTIONS.NETWORK_LISTENER_MESSAGE_FILTER : {},
      ct.CONFIG_STREAM.PIPELINE_OPTIONS.ADMIN_PIPELINE_VER : ADMIN_PIPELINE_VER,
    }
    # we setup the default admin pipeline
    default_admin_pipeline = {
      **defualt_admin_pipeline_setup,
      ct.CONFIG_STREAM.K_PLUGINS  : [
        # no plugins in the default admin pipeline - will be added later based on the template
        ],
    }
    # get the mandatory admin pipeline template 
    # this does NOT contain instances, only the plugin signatures 
    # and their default configuration
    admin_pipeline_template = self.owner.admin_pipeline_template
    if False:
      self.P("Using admin pipeline BASE TEMPLATE configuration:\n{}".format(json.dumps(admin_pipeline_template, indent=4)))
    # this is the reference pipeline
    dct_admin_pipeline = deepcopy(default_admin_pipeline)
    # get all the mandatory plugins
    lst_admin_signatures = list(admin_pipeline_template.keys())
    # for each plugin in the template, create a new instance and add it to the "reference" admin pipeline
    for i, plg in enumerate(lst_admin_signatures):
      plgcfg = deepcopy(admin_pipeline_template[plg])
      plgcfg[ct.BIZ_PLUGIN_DATA.INSTANCE_ID] = INSTANCE_STR.format(plg)
      dct_admin_pipeline[ct.CONFIG_STREAM.K_PLUGINS].append({
        ct.BIZ_PLUGIN_DATA.SIGNATURE  : plg,
        ct.BIZ_PLUGIN_DATA.INSTANCES : [plgcfg]
      })
    #endfor all mandatory plugins injected in the "reference" admin pipeline
    
    dct_current_admin = self.dct_config_streams.get(pipeline_name)
    needs_save = False
    if dct_current_admin is None:
      # no admin pipeline found - add the reference pipeline
      # maybe this is the first time the device is started
      self.dct_config_streams[pipeline_name] = dct_admin_pipeline
      dct_current_admin = dct_admin_pipeline
      needs_save = True # save the new admin pipeline
      self.P("  No admin pipleine found. Added administration pipeline")
    else:
      admin_pipeline_type = dct_current_admin.get(ct.CONFIG_STREAM.K_TYPE, None)
      # pipeline found - check if all mandatory plugins are present
      lst_current_signatures = [x[ct.PLUGIN_INFO.SIGNATURE] for x in dct_current_admin[ct.CONFIG_STREAM.K_PLUGINS]]
      # get all the instances of the current admin pipeline assuming single instances
      dct_curr_plugins_instances = {
        x[ct.PLUGIN_INFO.SIGNATURE] : x[ct.BIZ_PLUGIN_DATA.INSTANCES][0]  for x in dct_current_admin[ct.CONFIG_STREAM.K_PLUGINS] 
      }
      self.P("Found admin pipeline type `{}` with {}".format(admin_pipeline_type, lst_current_signatures))
      # get the "reference" admin pipeline with the default configuration in same signature: data format as the current admin pipeline
      if admin_pipeline_type != dct_admin_pipeline[ct.CONFIG_STREAM.K_TYPE]:
        self.P("Admin pipeline type is `{}` - should be `{}`".format(
          admin_pipeline_type, dct_admin_pipeline[ct.CONFIG_STREAM.K_TYPE]
        ))
        needs_save = True # update the admin pipeline type at save time
      #endif admin pipeline type is not correct
      dct_std = {x[ct.PLUGIN_INFO.SIGNATURE] : x for x in dct_admin_pipeline[ct.CONFIG_STREAM.K_PLUGINS]}
      self.P("  Required signatures from template: {}".format(lst_admin_signatures))
      for plg in lst_admin_signatures:
        # for each mandatory plugin in the template, check if it is present in the current admin pipeline
        # and if not, add it. Also check if the instance id is correct and if all the mandatory keys are present
        if plg not in lst_current_signatures:
          dct_current_admin[ct.CONFIG_STREAM.K_PLUGINS].append(dct_std[plg])
          self.P("    Missing admin plugin instance `{}` added".format(plg))
          needs_save = True  
        else:
          default_instance_config = dct_std[plg][ct.BIZ_PLUGIN_DATA.INSTANCES][0] # instance 0 is the default instance from the reference pipeline
          curr_instance_id = dct_curr_plugins_instances[plg][ct.BIZ_PLUGIN_DATA.INSTANCE_ID]
          correct_instance_id = INSTANCE_STR.format(plg)
          # check if the instance id is correct
          if curr_instance_id != correct_instance_id:
            dct_curr_plugins_instances[plg][ct.BIZ_PLUGIN_DATA.INSTANCE_ID] = correct_instance_id
            self.P("    Corrected instance id for {}: {} -> {}".format(plg, curr_instance_id, correct_instance_id))
            needs_save = True
          #endif instance id must respect the standard
          # now check if all the mandatory keys are present
          for k in default_instance_config:
            if k not in dct_curr_plugins_instances[plg]:
              dct_curr_plugins_instances[plg][k] = default_instance_config[k]
              self.P("    Missing {}:{} admin plugin instance key added added".format(plg, k))
              needs_save = True
            #endif missing key
          #endfor all keys              
        #endif not already admin plugin in config
      #endfor all must-in plugins
    #endif current is null or not      
    
    if needs_save:
      # we make sure we respect the default configuration
      dct_current_admin = {
        **dct_current_admin,
        **defualt_admin_pipeline_setup, # we add the default type, etc
      }
      self.dct_config_streams[pipeline_name] = dct_current_admin # update the admin pipeline in the memory cache      
      self.P("  Saving admin pipeline post modification:\n{}".format(json.dumps(dct_current_admin, indent=2)))
      self._save_stream_config(dct_current_admin)
    else:
      self.P("  Admin pipeline is already correctly configured - no need to save")
    names_available_pipelines = list(self.dct_config_streams.keys())
    return names_available_pipelines
    

  def __convert_txt_to_json(self, fn_txt):
    fn_json = fn_txt.replace('.txt', EXTENSION)
    if not os.path.isfile(fn_json):
      self.P("Converting '{}' to '{}'".format(fn_txt, fn_json))
      shutil.copy(fn_txt, fn_json)
    if os.path.isfile(fn_txt):
      self.P("Removing '{}'".format(fn_txt))
      os.remove(fn_txt)
    return fn_json

  def load_streams_configurations(self):
    self.P("Loading streams from local cache...")
    dct_config_streams = {}
    available_streams = os.listdir(self._path_streams)
    available_streams = [os.path.join(self._path_streams, x) for x in available_streams]
    available_streams = list(filter(lambda x: x.endswith(EXTENSION) or x.endswith('.txt'), available_streams))
    
    for i in range(len(available_streams)):
      fn = available_streams[i]
      if fn.endswith('.txt'):
        try:
          new_fn = self.__convert_txt_to_json(fn)
          available_streams[i] = new_fn
        except:
          self.P("Cannot convert '{}' to '{}'. Skipping.".format(fn, EXTENSION), color='r')
          continue
        #endtry
      #endif .txt file        
    #endfor
    
    names_available_streams = []

    for fn in available_streams:
      crt_config_stream = self.log.load_json(
        fname=fn, folder=None, numeric_keys=False,
        replace_environment_secrets='$EE_',
      )
      if crt_config_stream is not None:
        crt_config_stream = self.keep_good_stream(crt_config_stream)
      else:
        self.P("Stream {} could not be loaded. Skipping.".format(fn), color='r')
      #endif

      if crt_config_stream is not None:
        stream_name = crt_config_stream[ct.CONFIG_STREAM.K_NAME]
        names_available_streams.append(stream_name)
        dct_config_streams[stream_name] = crt_config_stream

        # sometimes the name of the config file may differ from the configured name. 
        # In this case we replace the file
        good_fn = os.path.join(self._path_streams, stream_name) + EXTENSION
        if fn != good_fn:
          os.remove(fn)
          self._save_stream_config(crt_config_stream)
        #endif
      else:
        self.P("Stream {} is not correct configured. Skipping.".format(fn), color='r')
      #endif
    #endfor
    
    self.dct_config_streams = dct_config_streams

    names_available_streams = self.maybe_setup_admin_pipeline()

    if len(names_available_streams) > 0:
      self.P("Available streams: {}".format("; ".join(names_available_streams)), color='b')
    else:
      self.P("No available pipelines. At least administration pipeline should be running!", color='error')

    return
  
  def load(self):
    # loads `config_app.txt` json available in `_data/box...`
    self.P("Loading '{}' from local cache...".format(self._fn_app_config), color='b')
    self.config_app = self.log.load_data_json(
      fname=self._fn_app_config, 
      numeric_keys=False,
      replace_environment_secrets='$EE_',
    )
    if self.config_app is None:
      msg = "CRITICAL ERROR! The device cannot start - there is no app configuration"
      raise Exception(msg)

    # set the communication configuration in shmem to be used in a facile way in map reduce streams
    self.shmem['config_communication'] = self.cfg_app_communication

    # now lets load the streams
    self.load_streams_configurations()
    self.P("OK! The device can start", color='g')
    return 
  

  
  





if __name__ == '__main__':

  log = Logger(lib_name='A', base_folder='.', app_folder='_local_cache', TF_KERAS=False)

  lst_config = [
    {
      'TYPE' : 'remote',
      'APP_CONFIG_ENDPOINT' : 'https://www.dropbox.com/s/yjhgzd6uwuo49po/config_app_new.txt?dl=1',
      'STREAMS_CONFIGS_ENDPOINT' : 'https://www.dropbox.com/sh/9fhxp5ob9cnf82r/AAAJn5loxbhRK03t-VmsQQXMa?dl=1',
    }
  ]
  
  m = ConfigManager(
    log=log,
    subfolder_path='__test_config3'
  )
  m.retrieve(lst_config)
  m.load()

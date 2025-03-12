import traceback
from copy import deepcopy
from datetime import datetime as dt
from time import sleep, time

from naeural_core import DecentrAIObject, Logger
from naeural_core import constants as ct
from naeural_core.data_structures import MetadataObject
from naeural_core.local_libraries import _ConfigHandlerMixin
from naeural_core.utils.plugins_base.bc_wrapper import BCWrapper
from naeural_core.utils.shm_manager import SharedMemoryManager

_CONFIG = {
  'IS_THREAD'               : False,
  'URL'                     : None,
  
  'MAX_IDLE_TIME'           : 10,
  
  'STREAM_CONFIG_METADATA'  : {},
  
  'PIPELINE_COMMAND'        : None,
  
  'SESSION_ID'              : None,
  'INITIATOR_ID'            : None,
  
  'USE_LOCAL_COMMS_ONLY'     : False,
  
  'VALIDATION_RULES' : {
    'IS_THREAD' : {'TYPE' : 'bool'},
  },
}

class BaseDataCapture(DecentrAIObject, _ConfigHandlerMixin):
  CONFIG = _CONFIG
  def __init__(
    self, 
    log, 
    default_config, 
    upstream_config, 
    environment_variables, 
    shmem, 
    signature, 
    fn_loop_stage_callback, 
    **kwargs
  ):
    self._default_config = default_config
    self._upstream_config = upstream_config
    self._environment_variables = environment_variables

    self.shmem = shmem
    self.__blockchain_manager = shmem[ct.BLOCKCHAIN_MANAGER]
    
    self.bc = BCWrapper(self.__blockchain_manager, owner=self) # blockchain wrapper (for encryption/decryption
    
    self.shm_manager = None
    self.nr_connection_issues = 0
    self._fn_loop_stage_callback = fn_loop_stage_callback
    self._initial_setup_done = False
    
    self.config = None
    self._signature = signature
    self._stream_metadata = MetadataObject() # this is a overall metadata object
    
    self.__last_data_timestamp = time()
    
    self.__require_on_config_trigger = False # on_config event trigger

    # control variables used mostly in DCT but declared here for functions defined in BaseDataCapture
    self._capture_loop_in_exec = False
    self._loop_paused = False
    self._capture_thread_waiting = False
    # end loop control variables
    
    super(BaseDataCapture, self).__init__(log=log, **kwargs)
    return
  
  def P(self, s, color=None, **kwargs):
    if color is None or (isinstance(color,str) and color[0] not in ['e', 'r']):
      color = ct.COLORS.DCT
    super().P(s, prefix=True, color=color, **kwargs)
    return
  
  
  @property 
  def is_idle_alert(self):
    if self.cfg_is_thread:
      # only works for DCTs
      margin = 1 / self.cfg_cap_resolution
    else:
      margin = 1

    is_idle = self.time_since_last_data > (margin + self.cfg_max_idle_time)
    return is_idle  
  
  
  @property
  def in_initial_setup(self):
    return not self._initial_setup_done
  
  @property
  def time_since_last_input(self):
    return -1 # defined only in DCT
  
  @property
  def time_since_last_data(self):
    elapsed = time() - self.__last_data_timestamp
    return elapsed
    

  def startup(self):
    super().startup()
    self._update_config()
    self.shm_manager = SharedMemoryManager(
      dct_shared=self.shmem,
      stream=self.cfg_name,
      plugin='DEFAULT',
      instance='DEFAULT',
      category=ct.SHMEM.DCT,
      linked_instances=None,
      log=self.log,
    )
    self._populate_stream_metadata_from_config()
    return
  
  def _populate_stream_metadata_from_config(self):
    """
    This protected method populates the `_stream_metadata` object with basic information.
    Should be populated in derived classes.

    Returns
    -------
    None.

    """
    return
  
  

  @property
  def cfg_alive_until(self):
    alive_until = self.cfg_stream_config_metadata.get('ALIVE_UNTIL', None)
    if alive_until is not None:
      alive_until = dt.strptime(alive_until, '%Y-%m-%d %H:%M')
    return alive_until


  @property
  def alive_until_passed(self):
    alive_until = self.cfg_alive_until
    if alive_until is not None:
      return (alive_until - dt.now()).total_seconds() < 0
    return False


  @property
  def has_data(self):
    return False


  @property
  def can_delete(self):
    return False


  @property
  def _device_id(self):
    eeid = self.log.config_data.get(ct.CONFIG_STARTUP_v2.K_EE_ID, '')[:ct.EE_ALIAS_MAX_SIZE]
    return eeid



  def _create_notification(self, notif, msg, notif_code=None, info=None, stream_name=None, error_code=None, **kwargs):
    return super()._create_notification(
      notif=notif, 
      notif_code=notif_code,
      msg=msg, 
      info=info,
      stream_name=self.cfg_name,
      session_id=self.cfg_session_id,
      initiator_id=self.cfg_initiator_id,
      error_code=error_code,
      ct=ct,
      use_local_comms_only=self.cfg_use_local_comms_only,
      **kwargs
    )


  def get_nr_parallel_captures(self):
    return self.shmem[ct.CAPTURE_MANAGER][ct.NR_CAPTURES]
  
  
  def maybe_trigger_on_config(self):
    if self.__require_on_config_trigger:
      self.__on_config_changed()
      self.__require_on_config_trigger = False
    return

  def get_plugin_specific_stats(self):
    """
    This method should be implemented in derived classes if they need to return user defined stats

    Returns
    -------
    status : dict
      The user defined status
    """
    result = {
    }
    return result

  def maybe_update_config(self, upstream_config):
    # if the following keys are modified we do not need to update the config
    EXCEPT_KEYS = [
      ct.CONFIG_STREAM.PLUGINS,
      ct.CONFIG_STREAM.LAST_UPDATE_TIME,
      ct.CONFIG_STREAM.K_MODIFIED_BY_ADDR,
      ct.CONFIG_STREAM.K_MODIFIED_BY_ID, 
    ]
    
    if upstream_config == self._upstream_config:
      return

    self.P("<MAIN THR> Changing config for {}".format(self), color='b')
    self._upstream_config = upstream_config
    
    need_update, lst_to_update = self.needs_update(self._upstream_config, except_keys=EXCEPT_KEYS)
    if not need_update:
      self.P("  <MAIN THR> No need to update config for {}. Modified {}, Excluded {}".format(
        self, lst_to_update, EXCEPT_KEYS), color='b'
      )
      self.config_data[ct.CONFIG_STREAM.LAST_UPDATE_TIME] = self._upstream_config.get(ct.CONFIG_STREAM.LAST_UPDATE_TIME, None)
      return
    
    # now while changing config we must stop loop exec
    self._fn_loop_stage_callback('4.collect.update_streams._check_captures.maybe_update_config.{}.wait'.format(self.cfg_name))
    # while self._capture_loop_in_exec:
    #   sleep(0.001)
    self._loop_paused = True
    start_waiting = time()
    while not self._capture_thread_waiting:
      sleep(0.001)
      if (time() - start_waiting) > 2: # 3s
        #TODO(AID): send notification with this behavior\
        self.P("<MAIN THR> WARNING! Updating through stream collecting!", color='r')
        break
    self._fn_loop_stage_callback('4.collect.update_streams._check_captures.maybe_update_config.{}.post-wait'.format(self.cfg_name))
    
    
    last_config = deepcopy(self.config_data)
    self._fn_loop_stage_callback('4.collect.update_streams._check_captures.maybe_update_config.{}.post-deepcopy'.format(self.cfg_name))
    try:
      self._update_config()
      self._fn_loop_stage_callback('4.collect.update_streams._check_captures.maybe_update_config.{}.post-update'.format(self.cfg_name))
      # resume loop
      self._loop_paused = False
      # TODO: add specific config modifications to below message
      msg = "Successfully updated data capture thread config for pipeline '{}' with new config".format(self.cfg_name)
      self.P("<MAIN THR> " + msg)
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_NORMAL, 
        notif_code=ct.NOTIFICATION_CODES.PIPELINE_DCT_CONFIG_OK,
        msg=msg,
        displayed=True,
      )
    except Exception as exc:
      # rollback
      self.config = last_config
      self.config_data = last_config
      msg = "Exception occured while updating the capture config: '{}' Rollback to the last good config.".format(exc)
      info = traceback.format_exc()
      self.P(msg, color='r')

      self.__save_config(keys=[k for k in upstream_config.keys() if k in self.config])

      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION, 
        notif_code=ct.NOTIFICATION_CODES.PIPELINE_DCT_CONFIG_FAILED,
        msg=msg,
        info=info,
        displayed=True,
      )

      # resume loop even on exception
      self._loop_paused = False

    self.__require_on_config_trigger = True # TODO: WARNING: simple plugins update trigger reconnects!!!
    self._fn_loop_stage_callback('4.collect.update_streams._check_captures.maybe_update_config.{}.post-callback'.format(self.cfg_name))
    return
  
  
  def _on_config_changed(self):
    """
    This should be defined in each individual DCT to act upon changes - i.e. such as the case when a
    video stream DCT receives a new URL and needs to reconnect
    """
    return
  
  def __on_config_changed(self):
    self.P("********************************************************")
    self.P("On config triggered. Current data connection issues: {}".format(
      self.nr_connection_issues))
    self.P("********************************************************")
    self._on_config_changed()
    return


  def _maybe_add_mandatory_plugin_to_pipeline(self, dct_config):
    """
    Add default mandatory plugins to pipeline, if the plugins do not already exist
    and if pipeline requires them.

    Parameters
    ----------
    dct_config : dict
      the pipeline config.

    Returns
    -------
    None.

    """
    dct_default_signature_instances = dct_config.get(ct.CONFIG_STREAM.DEFAULT_PLUGINS, {})
    dct_default_signature_instances = {
      signature: [
        {
          **instance,
          ct.INSTANCE_ID: instance.get(ct.INSTANCE_ID, self.log.get_short_name(signature) + '_default_' + str(i))
        }
        for i, instance in enumerate(instances)
      ]
      for signature, instances in dct_default_signature_instances.items()
    }

    plugins = dct_config.get(ct.CONFIG_STREAM.PLUGINS, [])
    dct_existing_signature_instances = {x[ct.SIGNATURE] : x[ct.INSTANCES] for x in plugins}
    lst_added_signature_instance_ids = []

    for default_plugin_signature, default_plugin_instances in dct_default_signature_instances.items():
      if default_plugin_signature not in dct_existing_signature_instances:
        dct_existing_signature_instances[default_plugin_signature] = default_plugin_instances
        plugins.append({ ct.SIGNATURE : default_plugin_signature, ct.INSTANCES : default_plugin_instances })
        lst_added_signature_instance_ids.extend([(default_plugin_signature, instance[ct.INSTANCE_ID]) for instance in default_plugin_instances])
      else:
        for default_instance_config in default_plugin_instances:
          if default_instance_config[ct.INSTANCE_ID] not in [x[ct.INSTANCE_ID] for x in dct_existing_signature_instances[default_plugin_signature]]:
            dct_existing_signature_instances[default_plugin_signature].append(default_instance_config)
            lst_added_signature_instance_ids.append((default_plugin_signature, default_instance_config[ct.INSTANCE_ID]))
          else:
            overwrite_config = dct_config.get(ct.CONFIG_STREAM.OVERWRITE_DEFAULT_PLUGIN_CONFIG, False)
            if overwrite_config:
              pos, instance_config = next((pos, x) for pos, x in enumerate(dct_existing_signature_instances[default_plugin_signature]) if x[ct.INSTANCE_ID] == default_instance_config[ct.INSTANCE_ID])
              if instance_config != {**instance_config, **default_instance_config}:
                dct_existing_signature_instances[default_plugin_signature][pos].update(default_instance_config)
                lst_added_signature_instance_ids.append((default_plugin_signature, default_instance_config[ct.INSTANCE_ID]))
              # endif default config different from existing config
            # endif overwrite config
          # endif instance not in default instances
        # end for default instances
      # endif signature not in plugins
    # end for default plugins

    if len(lst_added_signature_instance_ids) > 0:
      self.P("Adding mandatory plugins {} to pipeline '{}'".format(
        ", ".join("'{}:{}'".format(sig, inst) for sig, inst in lst_added_signature_instance_ids), 
        dct_config[ct.NAME]
      ))
      self.__save_config(keys=[ct.CONFIG_STREAM.PLUGINS])
    return

  def _update_config(self):
    # special section for PIPELINE_COMMAND
    is_pipeline_command = False 
    if self.config is not None:
      current_pipeline_command = self.config.get('PIPELINE_COMMAND', None)
      new_pipeline_command = self._upstream_config.get('PIPELINE_COMMAND', None)
      if new_pipeline_command is not None and current_pipeline_command != new_pipeline_command:
        is_pipeline_command = True
    # end special section
    
    self.__name__ = 'DCT:' + self.log.name_abbreviation(self._signature)
    if self._environment_variables is not None and len(self._environment_variables) > 0:
      self.P("<MAIN THR> Updating capture config with node environment config....", color='m')
      self.config = self._merge_prepare_config(
        default_config=self.config,
        delta_config=self._environment_variables,
      )
    #endif

    # next line will create a new config basically so we need then to validate and re-create the cfg_* handlers
    # otherwise they will point to the old data
    self.config = self._merge_prepare_config(default_config=self.config)    

    # now we set config_data, create handlers and validate
    self.setup_config_and_validate(dct_config=self.config)

    # now we have cfg_name and we can use it
    self.__name__ = 'DCT:' + self.log.name_abbreviation(self._signature) + '=' + self.cfg_name
    self._stream_metadata.update(**self.cfg_stream_config_metadata)

    # now we add mandatory plugins to the pipeline
    # these plugins are defined under "DEFAULT_PLUGINS"
    self._maybe_add_mandatory_plugin_to_pipeline(dct_config=self.config)

    self._initial_setup_done = True
    return

  def data_template(self, inputs=None, metadata=None):
    if inputs is None:
      inputs = []

    if metadata is None:
      metadata = self._stream_metadata.__dict__
    
    self.__last_data_timestamp = time()
    
    return {
      'STREAM_NAME' : self.cfg_name, ##TODO ct
      'STREAM_METADATA' : metadata.copy(), ## We copy it because we need to know the "snapshot" metadata for each append TODO ct
      'INPUTS' : inputs ##TODO ct
    }

  def stop(self, join_time=10):
    return

  def __save_config(self, keys):
    # writes the config for a set of particular keys on the local cache!
    save_config_fn = self.shmem[ct.CALLBACKS.PIPELINE_CONFIG_SAVER_CALLBACK]
    try:
      config = {k: self.config[k] for k in keys}
      save_config_fn(self.cfg_name, config_data=config)
      # the save will trigger a update config when the BusinessManager will get the new config from
      # the ConfigManager and will pass it as upstream_config to the plugin
      # so we already update the upstream_config to avoid this
      for k in keys:
        self._upstream_config[k] = self.config[k]
    except Exception as exc:
      self.P("Error '{}' while saving keys {}".format(exc, keys))
      raise exc
    return

  
  def __repr__(self):
    return '<{}:{}>'.format(self._signature, self.cfg_name)

from naeural_core.constants import COMMANDS, CONFIG_STREAM, PAYLOAD_DATA

class _CmdAPIMixin(object):

  def __init__(self):
    self._commands = []
    super(_CmdAPIMixin, self).__init__()
    return

  def _cmdapi_refresh(self):
    self._commands = []
    return

  # TODO: change all cmdapi to work with addresses, not ids
  def cmdapi_register_command(self, node_address, command_type, command_content):
    """
    Send a command to a particular Execution Engine

    Parameters
    ----------
    node_address : str
      target Execution Engine.
    command_type : st
      type of the command - can be one of 'RESTART','STATUS', 'STOP', 'UPDATE_CONFIG', 
      'DELETE_CONFIG', 'ARCHIVE_CONFIG', 'DELETE_CONFIG_ALL', 'ARCHIVE_CONFIG_ALL', 'ACTIVE_PLUGINS', 
      'RELOAD_CONFIG_FROM_DISK', 'FULL_HEARTBEAT', 'TIMERS_ONLY_HEARTBEAT', 'SIMPLE_HEARTBEAT',
      'UPDATE_PIPELINE_INSTANCE', etc.
    command_content : dict
      the actual content - can be None for some commands.

    Returns
    -------
    None.

    """
    if isinstance(command_content, dict) and PAYLOAD_DATA.TIME not in command_content:
      command_content[PAYLOAD_DATA.TIME] = self.log.now_str(nice_print=True)
    box_id = self.net_mon.network_node_eeid(node_address)
    self.P("CMDAPI: Sending command '{}' for node '{}' <{}>".format(command_type, box_id, node_address))
    self._commands.append((box_id, node_address, self.use_local_comms_only, command_type, command_content))
    return
  
  ###
  ### START Official API 
  ### 
  
  def cmdapi_start_pipeline(self, config, node_address=None):
    """
    Sends a start pipeline to a particular destination Execution Engine

    Parameters
    ----------
    node_address : str, optional
      destination Execution Engine, `None` will default to local Execution Engine. The default is None
      .
    config : dict
      the pipeline configuration. 

    Returns
    -------
    None.
    
    Example:
      
      ```
      config = {
        "NAME" : "test123",
        "TYPE" : "Void",
      }
      ee_id = None # using current processing node
      plugin.cmdapi_start_pipeline(config=config, node_address=ee_addr)
      ```

    """
    self._cmdapi_start_stream_by_config(config_stream=config, node_address=node_address)
    return
  
  
  def cmdapi_update_instance_config(self, pipeline, signature, instance_id, instance_config, node_address=None):
    """
    Sends update config for a particular plugin instance in a given box/pipeline
    

    Parameters
    ----------
      
    pipeline : str
      Name of the pipeline. 
      
    signature: str
      Name (signature) of the plugin 
      
    instance_id: str
      Name of the instance
      
    instance_config: dict
      The configuration for the given box/pipeline/plugin/instance

    node_address : str, optional
      destination Execution Engine, `None` will default to local Execution Engine. The default is None.

    Returns
    -------
    None.
    
    """
    self._cmdapi_update_pipeline_instance(
      pipeline=pipeline,
      signature=signature,
      instance_id=instance_id,
      instance_config=instance_config,
      node_address=node_address,
    )
    return
  
  
  def cmdapi_batch_update_instance_config(self, lst_updates, node_address=None):
    """Send a batch of updates for multiple plugin instances within their individual pipelines

    Parameters
    ----------
    lst_updates : list of dicts
        The list of updates for multiple plugin instances within their individual pipelines
        
    node_address : str, optional
        Destination node, by default None
        
    Returns
    -------
    None.
    
    Example
    -------
    
      ```python
      # in this example we are modifying the config for 2 instances of the same plugin `A_PLUGIN_01`
      # within the same pipeline `test123`
      lst_updates = [
        {
          "NAME" : "test123",
          "SIGNATURE" : "A_PLUGIN_01",
          "INSTANCE_ID" : "A_PLUGIN_01_INSTANCE_01",
          "INSTANCE_CONFIG" : {
            "PARAM1" : "value1",
            "PARAM2" : "value2",
          }
        },
        {
          "NAME" : "test123",
          "SIGNATURE" : "A_PLUGIN_01",
          "INSTANCE_ID" : "A_PLUGIN_01_INSTANCE_02",
          "INSTANCE_CONFIG" : {
            "PARAM1" : "value1",
            "PARAM2" : "value2",
          }
        },
      ] 
      plugin.cmdapi_batch_update_instance_config(lst_updates=lst_updates, node_address=None)
      ```
    
    """
    # first check if all updates are valid
    for update in lst_updates:
      assert isinstance(update, dict), "All updates must be dicts"
      assert PAYLOAD_DATA.NAME in update, "All updates must have a pipeline name"
      assert PAYLOAD_DATA.SIGNATURE in update, "All updates must have a plugin signature"
      assert PAYLOAD_DATA.INSTANCE_ID in update, "All updates must have a plugin instance id"
      assert PAYLOAD_DATA.INSTANCE_CONFIG in update, "All updates must have a plugin instance config"
      assert isinstance(update[PAYLOAD_DATA.INSTANCE_CONFIG], dict), "All updates must have a plugin instance config as dict"
    #endfor check all updates
    self.cmdapi_register_command(
      node_address=node_address,
      command_type=COMMANDS.BATCH_UPDATE_PIPELINE_INSTANCE,
      command_content=lst_updates
    )
    return
  
  
  def cmdapi_send_instance_command(self, pipeline, signature, instance_id, instance_command, node_address=None):
    """
    Sends a INSTANCE_COMMAND for a particular plugin instance in a given box/pipeline

    Parameters
    ----------
    pipeline : str
      Name of the pipeline. 
      
    instance_id: str
      Name of the instance
      
    signature: str
      Name (signature) of the plugin     
      
    instance_command: any
      The configuration for the given box/pipeline/plugin/instance. Can be a string, dict, etc

    node_address : str, optional
      destination Execution Engine, `None` will default to local Execution Engine. The default is None.
      
      
    Returns
    -------
    None.
    
    Example:
    --------
    
    
      ```
      pipeline = "test123"
      signature = "A_PLUGIN_01"
      instance_id = "A_PLUGIN_01_INSTANCE_01"
      instance_command = {
        "PARAM1" : "value1",
        "PARAM2" : "value2",
      }
      plugin.cmdapi_send_instance_command(
        pipeline=pipeline,
        signature=signature,
        instance_id=instance_id,
        instance_command=instance_command,
        node_address=None,
      )
      ```
        
    """
    instance_config = {
      'INSTANCE_COMMAND' : instance_command
    }
    self._cmdapi_update_pipeline_instance(
      pipeline=pipeline,
      signature=signature,
      instance_id=instance_id,
      instance_config=instance_config,
      node_address=node_address,
    )
    return
  
  
  def cmdapi_archive_pipeline(self, node_address=None, name=None):
    """
    Stop and archive a active pipeline on destination Execution Engine

    Parameters
    ----------
    node_address : str, optional
      destination Execution Engine, `None` will default to local Execution Engine. The default is None.
    name : str, optional
      Name of the pipeline. The default is `None` and will point to current pipeline where the plugin instance 
      is executed.

    Returns
    -------
    None.

    """
    self._cmdapi_archive_stream(node_address=node_address, stream_name=name)
    return


  def cmdapi_stop_pipeline(self, node_address=None, name=None):
    """
    Stop and delete a active pipeline on destination Execution Engine

    Parameters
    ----------
    node_address : str, optional
      destination Execution Engine, `None` will default to local Execution Engine. The default is None.
    name : str, optional
      Name of the pipeline. The default is `None` and will point to current pipeline where the plugin instance 
      is executed.

    Returns
    -------
    None.

    """
    self._cmdapi_stop_stream(node_address=node_address, stream_name=name)
    return


  def cmdapi_archive_all_pipelines(self, node_address=None):
    """
    Stop all active pipelines on destination Execution Engine

    Parameters
    ----------
    node_address : str, optional
      Address of the target E2 instance. The default is `None` and will run on local E2.

    Returns
    -------
    None.

    """
    node_address = node_address or self.node_addr
    self.cmdapi_register_command(node_address=node_address, command_type=COMMANDS.ARCHIVE_CONFIG_ALL, command_content=None)
    return
  
  
  def cmdapi_start_pipeline_by_params(self, name, pipeline_type, node_address=None, url=None,
                                      reconnectable=None, live_feed=False, plugins=None,
                                      stream_config_metadata=None, cap_resolution=None, 
                                      **kwargs):
    """
    Start a pipeline by defining specific pipeline params

    Parameters
    ----------
    name : str
      Name of the pipeline.
      
    pipeline_type : str
      type of the pipeline. Will point the E2 instance to a particular Data Capture Thread plugin
      
    node_address : str, optional
      Address of the target E2 instance. The default is `None` and will run on local E2.
      
    url : str, optional
      The optional URL that can be used by the DCT to acquire data. The default is None.
      
    reconnectable : str, optional
      Attempts to reconnect after data stops if 'YES'. 'KEEP_ALIVE' will not reconnect to
      data source but will leave the DCT in a "zombie" state waiting for external pipeline 
      close command.
      The default is 'YES'.
      
    live_feed : bool, optional
      Will always try to generate the real-time datapoint (no queued data). The default is False.
      
    plugins : list of dicts, optional
      Lists all the business plugins with their respective individual instances. The default is None.
      
    stream_config_metadata : dict, optional
      Options (custom) for current DCT. The default is None.
      
    cap_resolution : float, optional
      Desired frequency (in Hz) of the DCT data reading cycles. The default is None.
      

    Returns
    -------
    None (actually)
    
    Example
    -------
    
      ```
      name = "test123"
      pipeline_type = "Void"
      plugins = [
        {
          "SIGNATURE" : "A_PLUGIN_01",
          "INSTANCES" : [
            {
              "INSTANCE_ID" : "A_PLUGIN_01_INSTANCE_01",
              "PARAM1" : "value1",
              "PARAM2" : "value2",
            }
          ]
        }
      ]
      plugin.cmdapi_start_pipeline_by_params(
        name=name, 
        pipeline_type=pipeline_type, 
        plugins=plugins,
      )
      ```

    """
    return self._cmdapi_start_stream_by_params(
      name=name, stream_type=pipeline_type, node_address=node_address, url=url, reconnectable=reconnectable,
      live_feed=live_feed, plugins=plugins, stream_config_metadata=stream_config_metadata,
      cap_resolution=cap_resolution, **kwargs
    )
  
  
  def cmdapi_start_simple_custom_pipeline(self, *, base64code, node_address=None, name=None, instance_config={}, **kwargs):
    """
    Starts a CUSTOM_EXEC_01 plugin on a Void pipeline
    

    Parameters
    ----------
    base64code : str
      The base64 encoded string that will be used as custom exec plugin.
      
    node_address : str, optional
      The destination processing node. The default is None and will point to current node.
      
    name : str, optional
      Name of the pipeline. The default is None and will be uuid generated.
      
    instance_config / kwargs: dict
      Dict with params for the instance that can be given either as a dict or as kwargs


    Returns
    -------
    name : str
      returns the name of the pipeline.
      
    
    Example
    -------
    
      ```
      worker = plugin.cfg_destination                     # destination worker received in plugin json command
      worker_code = plugin.cfg_worker_code                # base64 code that will be executed 
      custom_code_param = plugin.cfg_custom_code_param    # a special param expected by the custom code
      pipeline_name = plugin.cmdapi_start_simple_custom_pipeline(
        base64code=worker_code, 
        node_address=worker,
        custom_code_param=pcustom_code_param,
      )
      ```
    """
    if name is None:
      name = self.uuid(8)
    plugins = [
      {
        self.const.BIZ_PLUGIN_DATA.INSTANCES: [
          {
            self.const.BIZ_PLUGIN_DATA.INSTANCE_ID: "SIMPLE_C_EXEC",
            "CODE": base64code,
            **instance_config,
            **kwargs,
          }
        ],
        self.const.BIZ_PLUGIN_DATA.SIGNATURE: "CUSTOM_EXEC_01",
      }
    ]
    self.cmdapi_start_pipeline_by_params(
        name=name, 
        pipeline_type="Void", 
        node_address=node_address, 
        plugins=plugins,
    )
    return name
  
  
  def cmdapi_send_pipeline_command(self, command, node_address=None, pipeline_name=None):
    """Sends a command to a particular pipeline on a particular destination E2 instance

    Parameters
    ----------
    command : any
        the command content

    node_address : str, optional
        name of the destination e2, by default None (self)

    pipeline_name : str, optional
        name of the pipeline, by default None (self)
        
    
    Returns
    -------
    None.
    
    Example
    -------
    
      ```
      # send a command directly to the current pipeline
      plugin.cmdapi_send_pipeline_command(
        command={"PARAM1" : "value1", "PARAM2" : "value2"},
        node_address=None,
        pipeline_name=None,
      )
      ```
    
    """
    node_address = node_address or self.node_addr
    pipeline_name = pipeline_name or self.get_stream_id()
    payload = {
      PAYLOAD_DATA.NAME : pipeline_name,
      COMMANDS.PIPELINE_COMMAND : command,
    }
    self.cmdapi_register_command(
      node_address=node_address,
      command_type=COMMANDS.PIPELINE_COMMAND,
      command_content=payload
    )    
    return
  ###
  ### END Official API 
  ### 




  # STOP BOX SECTION
  if True:
    def _cmdapi_stop_box(self, node_address=None):
      node_address = node_address or self.node_addr
      self.cmdapi_register_command(node_address=node_address, command_type=COMMANDS.STOP, command_content=None)
      return

    def cmdapi_stop_current_box(self):
      self._cmdapi_stop_box(node_address=None)
      return

    def cmdapi_stop_other_box(self, node_address):
      self._cmdapi_stop_box(node_address=node_address)
      return
  #endif

  # RESTART BOX SECTION
  if True:
    def _cmdapi_restart_box(self, node_address=None):
      node_address = node_address or self.node_addr
      self.cmdapi_register_command(node_address=node_address, command_type=COMMANDS.RESTART, command_content=None)
      return

    def cmdapi_restart_current_box(self):
      self._cmdapi_restart_box(node_address=None)
      return

    def cmdapi_restart_other_box(self, node_address):
      self._cmdapi_restart_box(node_address=node_address)
      return
  #endif

  # START STREAM SECTION
  if True:
    def _cmdapi_start_stream_by_config(self, config_stream, node_address=None):
      node_address = node_address or self.node_addr

      for param in CONFIG_STREAM.MANDATORY:
        if param not in config_stream:
          self.P("CMDAPI: Param '{}' not configured for commanded stream. Cannot register command".format(
            param), color='r'
          )
          return

      self.cmdapi_register_command(node_address=node_address, command_type=COMMANDS.UPDATE_CONFIG, command_content=config_stream)
      return

    def cmdapi_start_stream_by_config_on_current_box(self, config_stream):
      self._cmdapi_start_stream_by_config(config_stream=config_stream, node_address=None)
      return

    def cmdapi_start_stream_by_config_on_other_box(self, node_address, config_stream):
      self._cmdapi_start_stream_by_config(config_stream=config_stream, node_address=node_address)
      return

    # Metastreams
    def _cmdapi_start_metastream_by_config(self, config_metastream, node_address=None, collected_streams=None):
      config_metastream[CONFIG_STREAM.TYPE] = CONFIG_STREAM.METASTREAM
      if collected_streams is not None and isinstance(collected_streams, list) and len(collected_streams) > 0:
        config_metastream[CONFIG_STREAM.COLLECTED_STREAMS] = collected_streams
        CONFIG_STREAM.COLL
      self._cmdapi_start_stream_by_config(config_stream=config_metastream, node_address=node_address)
      return

    def cmdapi_start_metastream_by_config_on_current_box(self, config_metastream, collected_streams=None):
      self._cmdapi_start_metastream_by_config(config_metastream=config_metastream, node_address=None, collected_streams=collected_streams)
      return

    def cmdapi_start_metastream_by_config_on_other_box(self, node_address, config_metastream, collected_streams=None):
      self._cmdapi_start_metastream_by_config(config_metastream=config_metastream, node_address=node_address, collected_streams=collected_streams)
      return
    # end Metastreams


    def _cmdapi_start_stream_by_params(self, name, stream_type, url=None,
                                      reconnectable=None, live_feed=False, plugins=None,
                                      stream_config_metadata=None, cap_resolution=None, node_address=None, **kwargs):

      config_stream = {
        CONFIG_STREAM.K_NAME          : name,
        CONFIG_STREAM.K_TYPE          : stream_type,
      }

      if reconnectable is not None:
        config_stream[CONFIG_STREAM.K_RECONNECTABLE] = reconnectable

      if live_feed is not None:
        config_stream[CONFIG_STREAM.K_LIVE_FEED] = live_feed

      if url is not None:
        config_stream[CONFIG_STREAM.K_URL] = url

      if plugins is not None:
        config_stream[CONFIG_STREAM.K_PLUGINS] = plugins

      if stream_config_metadata is not None:
        config_stream[CONFIG_STREAM.STREAM_CONFIG_METADATA] = stream_config_metadata

      if cap_resolution is not None:
        config_stream[CONFIG_STREAM.CAP_RESOLUTION] = cap_resolution
      
      config_stream = {
        **config_stream, 
        **{k.upper():v for k,v in kwargs.items()},
      }
      self._cmdapi_start_stream_by_config(config_stream=config_stream, node_address=node_address)
      return


    def cmdapi_start_stream_by_params_on_current_box(self, name, stream_type, url=None,
                                                     reconnectable=None, live_feed=False, plugins=None,
                                                     stream_config_metadata=None, cap_resolution=None, **kwargs):
      self._cmdapi_start_stream_by_params(
        name=name, stream_type=stream_type, url=url,
        reconnectable=reconnectable, live_feed=live_feed, plugins=plugins,
        stream_config_metadata=stream_config_metadata, cap_resolution=cap_resolution,
        node_address=None, **kwargs
      )
      return

    def cmdapi_start_stream_by_params_on_other_box(self, node_address, name, stream_type, url=None,
                                                   reconnectable=None, live_feed=False, plugins=None,
                                                   stream_config_metadata=None, cap_resolution=None, **kwargs):
      self._cmdapi_start_stream_by_params(
        name=name, stream_type=stream_type, url=url,
        reconnectable=reconnectable, live_feed=live_feed, plugins=plugins,
        stream_config_metadata=stream_config_metadata, cap_resolution=cap_resolution,
        node_address=node_address, **kwargs
      )
      return
    
    def _cmdapi_update_pipeline_instance(self, pipeline, signature, instance_id, instance_config, node_address=None):
      node_address = node_address or self.node_addr
      payload = {
        PAYLOAD_DATA.NAME : pipeline,
        PAYLOAD_DATA.SIGNATURE : signature,
        PAYLOAD_DATA.INSTANCE_ID : instance_id,
        PAYLOAD_DATA.INSTANCE_CONFIG : instance_config,
      }
      self.cmdapi_register_command(
        node_address=node_address,
        command_type=COMMANDS.UPDATE_PIPELINE_INSTANCE,
        command_content=payload
      )
      return
  #endif

  # STOP STREAM SECTION
  if True:
    def _cmdapi_stop_stream(self, node_address=None, stream_name=None):
      node_address = node_address or self.node_addr
      stream_name = stream_name or self.get_stream_id()
      self.cmdapi_register_command(node_address=node_address, command_type=COMMANDS.DELETE_CONFIG, command_content=stream_name)
      return
    
    def cmdapi_stop_pipeline(self, node_address, name):
      self._cmdapi_stop_stream(node_address=node_address, stream_name=name)
      return
    
    def cmdapi_stop_current_pipeline(self):
      self._cmdapi_stop_stream(node_address=None, stream_name=None)
      return

    def cmdapi_stop_current_stream(self):
      self._cmdapi_stop_stream(node_address=None, stream_name=None)
      return

    def cmdapi_stop_other_stream_on_current_box(self, stream_name):
      self._cmdapi_stop_stream(node_address=None, stream_name=stream_name)
      return

    def cmdapi_stop_stream_on_other_box(self, node_address, stream_name):
      self._cmdapi_stop_stream(node_address=node_address, stream_name=stream_name)
      return
  #endif

  # ARCHIVE STREAM SECTION
  if True:
    def _cmdapi_archive_stream(self, node_address=None, stream_name=None):
      node_address = node_address or self.node_addr
      stream_name = stream_name or self.get_stream_id()
      self.cmdapi_register_command(node_address=node_address, command_type=COMMANDS.ARCHIVE_CONFIG, command_content=stream_name)
      return

    def cmdapi_archive_current_stream(self):
      self._cmdapi_archive_stream(node_address=None, stream_name=None)
      return

    def cmdapi_archive_other_stream_on_current_box(self, stream_name):
      self._cmdapi_archive_stream(node_address=None, stream_name=stream_name)
      return

    def cmdapi_archive_stream_on_other_box(self, node_address, stream_name):
      self._cmdapi_archive_stream(node_address=node_address, stream_name=stream_name)
      return
  #endif

  # FINISH STREAM ACQUISITION SECTION
  if True:
    def _cmdapi_finish_stream_acquisition(self, node_address=None, stream_name=None):
      node_address = node_address or self.node_addr
      stream_name = stream_name or self.get_stream_id()
      
      delta_config = {
        CONFIG_STREAM.NAME : stream_name,
        COMMANDS.COMMANDS : [{COMMANDS.FINISH_ACQUISITION : True}]
      }

      self.cmdapi_register_command(node_address=node_address, command_type=COMMANDS.UPDATE_CONFIG, command_content=delta_config)
      return

    def cmdapi_finish_current_stream_acquisition(self):
      self._cmdapi_finish_stream_acquisition(node_address=None, stream_name=None)
      return

    def cmdapi_finish_other_stream_acquisition_on_current_box(self, stream_name):
      self._cmdapi_finish_stream_acquisition(node_address=None, stream_name=stream_name)
      return

    def cmdapi_finish_stream_acquisition_on_other_box(self, node_address, stream_name):
      self._cmdapi_finish_stream_acquisition(node_address=node_address, stream_name=stream_name)
      return
  #endif


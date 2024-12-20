
class _BasePluginAPIMixin:
  def __init__(self) -> None:
    super(_BasePluginAPIMixin, self).__init__()
    
    self.__chain_state_initialized = False
    return
  
  # Obsolete
  def _pre_process(self):
    """
    Called before process. Currently (partially) obsolete

    Returns
    -------
    TBD.

    """
    return
  
  def _post_process(self):
    """
    Called after process. Currently (partially) obsolete

    Returns
    -------
    TBD.

    """
    return
  
  
  def step(self):
    """
    The main code of the plugin (loop iteration code). Called at each iteration of the plugin loop.

    Returns
    -------
    None.

    """
    return
  
  
  def process(self):
    """
    The main code of the plugin (loop iteration code). Called at each iteration of the plugin loop.

    Returns
    -------
    Payload.

    """
    return self.step()
  
  def _process(self):
    """
    The main code of the plugin (loop iteration code.

    Returns
    -------
    Payload.

    """
    return self.process()

  
  def on_init(self):
    """
    Called at init time in the plugin thread.

    Returns
    -------
    None.

    """      
    return
  
  def _on_init(self):
    """
    Called at init time in the plugin thread.

    Returns
    -------
    None.

    """
    self.P("Default plugin `_on_init` called for plugin initialization...")
    self.on_init()
    return


  def on_close(self):
    """
    Called at shutdown time in the plugin thread.

    Returns
    -------
    None.

    """      
    return


  def _on_close(self):
    """
    Called at shutdown time in the plugin thread.

    Returns
    -------
    None.

    """
    self.P("Default plugin `_on_close` called for plugin cleanup at shutdown...")
    self.maybe_archive_upload_last_files()
    self.on_close()
    return

  def on_command(self, data, **kwargs):
    """
    Called when the instance receives new INSTANCE_COMMAND

    Parameters
    ----------
    data : any
      object, string, etc.

    Returns
    -------
    None.

    """
    return

  def _on_command(self, data, default_configuration=None, current_configuration=None, **kwargs):
    """
    Called when the instance receives new INSTANCE_COMMAND

    Parameters
    ----------
    data : any
      object, string, etc.

    Returns
    -------
    None.

    """
    self.P("Default plugin `_on_command`...")

    if (isinstance(data, str) and data.upper() == 'DEFAULT_CONFIGURATION') or default_configuration:
      self.P("Received \"DEFAULT_CONFIGURATION\" command...")
      self.add_payload_by_fields(
        default_configuration=self._default_config,
        command_params=data,
      )
      return
    if (isinstance(data, str) and data.upper() == 'CURRENT_CONFIGURATION') or current_configuration:
      self.P("Received \"CURRENT_CONFIGURATION\" command...")
      self.add_payload_by_fields(
        current_configuration=self._upstream_config,
        command_params=data,
      )
      return

    self.on_command(data, **kwargs)
    return


  def _on_config(self):
    """
    Called when the instance has just been reconfigured

    Parameters
    ----------
    None

    Returns
    -------
    None.

    """
    self.P("Default plugin {} `_on_config` called...".format(self.__class__.__name__))
    if hasattr(self, 'on_config'):
      self.on_config()
    return


  ###
  ### Chain State
  ### 
  
  def __maybe_wait_for_chain_state_init(self):
    # TODO: raise exception if not found after a while

    while not self.plugins_shmem.get('__chain_storage_set'):
      self.sleep(0.1)
    
    if not self.__chain_state_initialized:
      self.P(" ==== Chain state initialized.")
    self.__chain_state_initialized = True
    return
  
  def chainstore_set(self, key, value, debug=False):
    result = False
    try:
      self.__maybe_wait_for_chain_state_init()
      func = self.plugins_shmem.get('__chain_storage_set')
      if func is not None:
        if debug:
          self.P("Setting data: {} -> {}".format(key, value), color="green")
        self.start_timer("chainstore_set")
        result = func(key, value, debug=debug)
        elapsed = self.end_timer("chainstore_set")        
        if debug:
          self.P(" ====> `chainstore_set` elapsed time: {:.6f}".format(elapsed), color="green")
      else:
        if debug:
          self.P("No chain storage set function found", color="red")
    except:
      pass
    return result
  
  
  def chainstore_get(self, key, debug=False):
    self.__maybe_wait_for_chain_state_init()
    value = None
    try:
      while self.plugins_shmem.get('__chain_storage_get') is None:
        self.sleep(0.1)
        # TODO: raise exception if not found after a while
      func = self.plugins_shmem.get('__chain_storage_get')
      if func is not None:
        value = func(key, debug=debug)
        if debug:
          self.P("Getting data: {} -> {}".format(key, value), color="green")
      else:
        if debug:
          self.P("No chain storage get function found", color="red")
    except:
      pass
    return value
  
  
  # # @property
  # # This CANNOT be a property, as it can be a blocking operation.
  # def _chainstorage(self): # TODO: hide/move/protect this
  #   self.__maybe_wait_for_chain_state_init()
  #   return self.plugins_shmem.get('__chain_storage')

  
  def get_instance_path(self):
    return [self.ee_id, self._stream_id, self._signature, self.cfg_instance_id]  
  
  ###
  ### END Chain State
  ###
  
    
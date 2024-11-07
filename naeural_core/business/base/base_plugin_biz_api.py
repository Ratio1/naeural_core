
class _BasePluginAPIMixin:
  def __init__(self) -> None:
    super(_BasePluginAPIMixin, self).__init__()
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
    return
  
"""
IMPORTANT:
  The command handlers are defined in this file. The actual commands are dinamically inferred from the available handlers.
"""

from naeural_core import constants as ct


class ExecutionEngineCommandHandlers:
    
  ###########################################
  #         command definition area         #
  ###########################################
  
  def get_commands_list(self):
    return list(self.get_cmd_handlers().keys())
  
  
  def cmd_handler_stop(self, initiator_id=None, session_id=None, **kwargs):    
    msg = "  Running 'STOP' command in main loop received from '{}'".format(initiator_id)
    self.P(msg, color='y')
    self._create_notification(
      notif=ct.NOTIFICATION_TYPE.STATUS_NORMAL,
      msg=msg,
      initiator_id=initiator_id,
      session_id=session_id,
    )    
    return ct.CODE_STOP, ct.DEVICE_STATUS_STOP
  
  def cmd_handler_restart(self, initiator_id=None, session_id=None, **kwargs):
    msg = "  Running 'RESTART' command in main loop received from '{}'".format(initiator_id)
    self.P(msg, color='y')
    self._create_notification(
      notif=ct.NOTIFICATION_TYPE.STATUS_NORMAL,
      msg=msg,
      initiator_id=initiator_id,
      session_id=session_id,
    )    
    return ct.CODE_RESTART, ct.DEVICE_STATUS_RESTART

  def cmd_handler_pipeline_command(self, command_content=None, initiator_id=None, session_id=None, **kwargs):
    self.P("  Running 'PIPELINE_COMMAND' command in main loop received from '{}'".format(initiator_id), color='y')
    self._config_manager.pipeline_command(
      dct_config_data=command_content, 
      initiator_id=initiator_id, session_id=session_id, 
      **kwargs
    )
    return

  def cmd_handler_update_pipeline_instance(self, command_content=None, initiator_id=None, session_id=None, **kwargs):
    self.P("  Running 'UPDATE_PIPELINE_INSTANCE' command in main loop received from '{}'".format(initiator_id), color='y')
    self._config_manager.update_pipeline_instance(
      dct_config_data=command_content,
      initiator_id=initiator_id, session_id=session_id
    )
    return
  
  def cmd_handler_batch_update_pipeline_instance(self, command_content=None, initiator_id=None, session_id=None, **kwargs):
    self.P("  Running 'BATCH_UPDATE_PIPELINE_INSTANCE' command in main loop received from '{}'".format(initiator_id), color='y')
    self._config_manager.batch_update_pipeline_instance(
      lst_configs=command_content,
      initiator_id=initiator_id, session_id=session_id,
    )
    return
  
  
  def cmd_handler_update_config(self, command_content=None, initiator_id=None, session_id=None, **kwargs):
    self.P("  Running 'UPDATE_CONFIG' command in main loop received from '{}'".format(initiator_id), color='y')
    self._config_manager.update_config_stream(
      delta_config_stream=command_content, 
      initiator_id=initiator_id, session_id=session_id
    )
    return

  def cmd_handler_delete_config(self, command_content=None, initiator_id=None, session_id=None, **kwargs):
    self.P("  Running 'DELETE_CONFIG' command in main loop received from '{}'".format(initiator_id), color='y')
    self._config_manager.delete_config_stream(
      stream_name=command_content, 
      initiator_id=initiator_id, session_id=session_id
    )
    return

  def cmd_handler_delete_config_all(self, command_content=None, initiator_id=None, session_id=None, **kwargs):
    msg = "  Running 'DELETE_CONFIG_ALL' command in main loop received from '{}'".format(initiator_id)
    self.P(msg, color='y')
    self._create_notification(
      notif=ct.NOTIFICATION_TYPE.STATUS_NORMAL,
      msg=msg,
      initiator_id=initiator_id,
    )    
    self._config_manager.delete_all_streams(initiator_id=initiator_id, session_id=session_id)
    return
      
  
  def cmd_handler_archive_config(self, command_content=None, initiator_id=None, session_id=None, **kwargs):
    self.P("  Running 'ARCHIVE_CONFIG' command in main loop received from '{}'".format(initiator_id), color='y')
    self._config_manager.archive_single_stream(stream_name=command_content, initiator_id=initiator_id, session_id=session_id)
    return


  def cmd_handler_archive_config_all(self, command_content=None, initiator_id=None, session_id=None, **kwargs):
    msg = "  Running 'ARCHIVE_CONFIG_ALL' command in main loop received from '{}'".format(initiator_id)
    self.P(msg, color='y')
    self._create_notification(
      notif=ct.NOTIFICATION_TYPE.STATUS_NORMAL,
      msg=msg,
      initiator_id=initiator_id,
    )    
    self._config_manager.archive_all_streams(initiator_id=initiator_id, session_id=session_id)
    return


  def cmd_handler_reload_config_from_disk(self, initiator_id=None, session_id=None, **kwargs):
    msg = "  Running 'RELOAD_CONFIG_FROM_DISK' command in main loop received from '{}'".format(initiator_id)
    self.P(msg, color='y')
    self._create_notification(
      notif=ct.NOTIFICATION_TYPE.STATUS_NORMAL,
      msg=msg,
      initiator_id=initiator_id,
    )    
    self._config_manager.load_streams_configurations()
    return


  def cmd_handler_simple_heartbeat(self, initiator_id=None, session_id=None, **kwargs):
    self.P("  Running 'SIMPLE_HEARTBEAT' command in main loop coming from '{}'".format(initiator_id), color='y')
    self._maybe_send_heartbeat(
      status=None,
      full_info=False,
      send_log=False,
      force=True,
      initiator_id=initiator_id,
      session_id=session_id,
    )
    return

  
  def cmd_handler_timers_only_heartbeat(self, initiator_id=None, session_id=None, **kwargs):
    self.P("  Running 'TIMERS_ONLY_HEARTBEAT' command in main loop coming from '{}'".format(initiator_id), color='y')
    self._maybe_send_heartbeat(
      status=None,
      full_info=True,
      send_log=False,
      force=True,
      initiator_id=initiator_id,
      session_id=session_id,
    )
    return

  def cmd_handler_full_heartbeat(self, initiator_id=None, session_id=None, **kwargs):
    self.P("  Running `FULL_HEARTBEAT` command in main loop coming from '{}'".format(initiator_id), color='y')
    self._maybe_send_heartbeat(
      status=None,
      full_info=True,
      send_log=True,
      force=True,
      initiator_id=initiator_id,
      session_id=session_id,
    )
    return

  def cmd_handler_reset_whitelist_commands_to_template(self, initiator_id=None, session_id=None, **kwargs):
    self.P("  Running 'RESET_WHITELIST_COMMANDS_TO_TEMPLATE' command in main loop received from '{}'".format(initiator_id), color='y')
    self.comm_manager._reset_whitelist_commands_to_template()
    return

  ##############################################
  #         end command definition area        #
  ##############################################

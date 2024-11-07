import os

from naeural_core import constants as ct

class ConfigCommandHandlers:
  
  def update_config_stream(self, delta_config_stream, initiator_id, session_id, verbose=1):
    stream_name = delta_config_stream[ct.CONFIG_STREAM.NAME]
    validated_command = delta_config_stream.get(ct.COMMS.COMM_RECV_MESSAGE.K_VALIDATED, False)
    # we pop the sender address and we will use it for initiator and/or modified-by
    sender_addr = delta_config_stream.pop(ct.COMMS.COMM_RECV_MESSAGE.K_SENDER_ADDR, None)
    # now we pop the initiator Id and decide if we should use it or use it as modified-by id
    sender_id = delta_config_stream.pop(ct.CONFIG_STREAM.INITIATOR_ID, None)
    # now just a sanity check
    if sender_id != initiator_id:
      msg = "ERROR: Received initiator inconsistent with the command for pipeline '{}'. Initiator: {} vs Sender: {}".format(
        stream_name, initiator_id, sender_id
      )
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING, 
        notif_code=ct.NOTIFICATION_CODES.PIPELINE_FAILED,
        initiator_id=initiator_id,
        session_id=session_id,
        stream_name=stream_name,
        msg=msg,
      )
      return
    
    if stream_name.lower() == self.admin_pipeline_name.lower():
      msg = "WARNING: Attempted detected to modify whole administration pipeline. You can only use update_pipeline_instance to modify individual plugins."
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING, 
        notif_code=ct.NOTIFICATION_CODES.PIPELINE_DCT_CONFIG_FAILED,
        initiator_id=initiator_id,
        session_id=session_id,
        stream_name=stream_name,
        msg=msg,
      )
      return     
    #endif admin pipeline modification is not allowed 
      

    is_duplicate = self._check_duplicate_last(
      payload=delta_config_stream,
      payload_type=ct.PAYLOAD_CT.COMMANDS.UPDATE_CONFIG,
      
    )
    if is_duplicate:
      msg = "Received duplicate {} for pipeline {} from {}. In future this will be skipped.".format(
        ct.PAYLOAD_CT.COMMANDS.UPDATE_CONFIG,
        stream_name, initiator_id,
      )
      self.P(msg, color='error')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING, 
        notif_code=ct.NOTIFICATION_CODES.PIPELINE_FAILED,
        msg=msg, 
        stream_name=stream_name,
        session_id=session_id,
        initiator_id=initiator_id,
        displayed=True,
      )
      # return
    #endif

    config_stream = self.dct_config_streams.get(stream_name, {})
    is_new = False
    if len(config_stream) > 0:
      msg = "Received {} command to update for existing pipeline '{}' ({}) from {}:{}".format(
        "VALIDATED" if validated_command else "NON-VALIDATED", 
        stream_name, config_stream.get(ct.CONFIG_STREAM.TYPE),
        initiator_id,
        session_id
      )
    else:
      msg = "Received {} command for new pipeline '{}' from {}:{}".format(
        "VALIDATED" if validated_command else "NON-VALIDATED", 
        stream_name,
        initiator_id,
        session_id
      )
      is_new = True
    #endif
    self.P(msg)
    self._create_notification(
      notif=ct.STATUS_TYPE.STATUS_NORMAL, 
      msg=msg, 
      initiator_id=initiator_id,
      session_id=session_id,
      stream_name=None,
      displayed=True,
    )
    
    if is_new:
      delta_config_stream[ct.CONFIG_STREAM.K_INITIATOR_ADDR] = sender_addr
      delta_config_stream[ct.CONFIG_STREAM.K_INITIATOR_ID] = initiator_id
    else:
      previous_initiator_id = config_stream.get(ct.CONFIG_STREAM.INITIATOR_ID)
      previous_initiator_addr = config_stream.get(ct.CONFIG_STREAM.K_INITIATOR_ADDR)
      if previous_initiator_id != initiator_id or previous_initiator_addr != sender_addr:
        msg = "Received update for '{}' from a different initiator <{}:{}> than original <{}:{}>".format(
          stream_name,           
          initiator_id, sender_addr,
          previous_initiator_id, previous_initiator_addr,          
        )
    #endif
    # now set the modified-by fields
    delta_config_stream[ct.CONFIG_STREAM.K_MODIFIED_BY_ADDR] = sender_addr
    delta_config_stream[ct.CONFIG_STREAM.K_MODIFIED_BY_ID] = initiator_id
    
    config_stream = self._apply_delta_to_config(config_stream, delta_config_stream)
    config_stream = self.keep_good_stream(config_stream)

    if config_stream is not None:
      stream_name = config_stream[ct.CONFIG_STREAM.NAME]
      self._save_stream_config(config_stream)
      self.dct_config_streams[stream_name] = config_stream
      
      str_new = "new" if is_new else "existing"
      msg = "Successfully updated {} configuration for pipeline '{}' initiated by '{}'".format(str_new, stream_name, initiator_id)
      if verbose >= 1:
        self.P(msg, color='g')
        
      if session_id is None:
        session_id = self.dct_config_streams[stream_name].get(ct.CONFIG_STREAM.SESSION_ID)
      if initiator_id is None:
        initiator_id = self.dct_config_streams[stream_name].get(ct.CONFIG_STREAM.INITIATOR_ID)
        
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_NORMAL, 
        notif_code=ct.NOTIFICATION_CODES.PIPELINE_OK, # if not is_new else None, # REMOVED: always ok to send notif on success
        msg=msg,
        stream_name=stream_name, # this only confirms the config NOT the actual running
        session_id=session_id,
        initiator_id=initiator_id,
        # displayed=verbose >= 1,
      )      
    else:
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING, 
        notif_code=ct.NOTIFICATION_CODES.PIPELINE_FAILED,
        msg=msg,
        stream_name=stream_name, # this only confirms the config NOT the actual running
        session_id=session_id,
        initiator_id=initiator_id,
        # displayed=verbose >= 1,
      )      
    #endif
    return    
  

  def update_pipeline_instance(self, dct_config_data, initiator_id, session_id):
    pipeline_name = dct_config_data.get(ct.PAYLOAD_DATA.NAME)
    signature = dct_config_data.get(ct.PLUGIN_INFO.SIGNATURE)
    instance_id = dct_config_data.get(ct.PLUGIN_INFO.INSTANCE_ID)
    instance_update = dct_config_data.get(ct.PAYLOAD_DATA.INSTANCE_CONFIG)
    validated_command = dct_config_data.get(ct.COMMS.COMM_RECV_MESSAGE.K_VALIDATED, False)
    sender_addr = dct_config_data.get(ct.COMMS.COMM_RECV_MESSAGE.K_SENDER_ADDR, None)
    if not (isinstance(instance_update, dict) and len(instance_update) > 0):
      instance_update = None
    incomplete_request = pipeline_name is None or signature is None or instance_id is None or instance_update is None
    non_existant_stream = pipeline_name is not None and pipeline_name not in self.dct_config_streams
    notif_code = None
    notif = None
    if incomplete_request or non_existant_stream:
      msg = "Received invalid '{}' command for {}:{}:{}. Incomplete request: {}, Non existant stream: {}".format(
        ct.PAYLOAD_CT.COMMANDS.UPDATE_PIPELINE_INSTANCE,
        pipeline_name, signature, instance_id,
        incomplete_request, non_existant_stream,
      )
      notif = ct.STATUS_TYPE.STATUS_EXCEPTION
      notif_code=ct.NOTIFICATION_CODES.PIPELINE_FAILED
      info = "Command should contain NAME, SIGNATURE, INSTANCE_ID as well as INSTANCE_CONFIG 'delta' object and MUST address a existing pipeline. Full received command: {}".format(dct_config_data)
    else:
      
      if session_id is None:
        session_id = self.dct_config_streams[pipeline_name].get(ct.PAYLOAD_DATA.SESSION_ID)
      else:
        self.dct_config_streams[pipeline_name][ct.PAYLOAD_DATA.SESSION_ID] = session_id

      if initiator_id is None:
        initiator_id = self.dct_config_streams[pipeline_name].get(ct.PAYLOAD_DATA.INITIATOR_ID)    
      else:
        # update modified-by fields
        self.dct_config_streams[pipeline_name][ct.CONFIG_STREAM.K_MODIFIED_BY_ID] = initiator_id
      #endif
      self.dct_config_streams[pipeline_name][ct.CONFIG_STREAM.K_MODIFIED_BY_ADDR] = sender_addr
        
        
      instance_config = self._get_plugin_instance(
        stream_name=pipeline_name, signature=signature, instance_id=instance_id
      )
      if instance_config is not None:
        # the actual update
        instance_config = self._apply_delta_to_config(instance_config, instance_update, ignore_fields=[ct.PLUGIN_INFO.INSTANCE_ID])
        # end update
        self._save_stream_config(self.dct_config_streams[pipeline_name])
        notif = ct.STATUS_TYPE.STATUS_NORMAL
        notif_code=ct.NOTIFICATION_CODES.PIPELINE_OK
        msg = "Successfully updated with {} command {}:{}:{}".format(
          "VALIDATED" if validated_command else "NON-VALIDATED (sign/hash issue)", instance_id, pipeline_name, signature
        )
        info = ""
      else:
        msg = "Cannot find instance '{}' in pipeline/plugin <{}:{}>".format(instance_id, pipeline_name, signature)
        info = "Full command: {}".format(dct_config_data)
        notif = ct.STATUS_TYPE.STATUS_EXCEPTION
        notif_code=ct.NOTIFICATION_CODES.PIPELINE_FAILED
      #endif success or not
    #endif good request or not
    
    self.P(msg + ". " + info, color='r' if notif == ct.STATUS_TYPE.STATUS_EXCEPTION else 'g')
    self._create_notification(
      notif=notif, 
      notif_code=notif_code,
      msg=msg,
      info=info,
      stream_name=pipeline_name,
      signature=signature,
      instance_id=instance_id,
      session_id=session_id,
      initiator_id=initiator_id,
      displayed=True,
    )
    if notif == ct.STATUS_TYPE.STATUS_EXCEPTION:
      return None
    else:
      return (pipeline_name, signature, instance_id)
    
    


  def batch_update_pipeline_instance(self, lst_configs, initiator_id, session_id):
    """
    Will update a list of pipelines. Each pipeline is updated with `update_pipeline_instance`
    

    Parameters
    ----------
    lst_configs : list
        the list of dicts
        
    Example
    -------
    
    ```json
    {
      "ACTION"  : "BATCH_UPDATE_PIPELINE_INSTANCE",
      "PAYLOAD" : [
        {
          "NAME"            : "test_for_batch_1",
          "SIGNATURE"       : "REST_CUSTOM_EXEC_01",
          "INSTANCE_ID"     : "RECE01_DFLT",
          "INSTANCE_CONFIG" : {
            "key11" : "value100",
            "key12" : "value200"
          }
        },
        {
          "NAME"            : "test_for_batch_2",
          "SIGNATURE"       : "REST_CUSTOM_EXEC_01",
          "INSTANCE_ID"     : "RECE01_DFLT",
          "INSTANCE_CONFIG" : {
            "key21" : "value1",
            "key22" : "value2"
          }
        }
      ]
    }
    ```
    
    TODO: must retrieve the address and pass it to each update_pipeline_instance call
    
    """
    results = []
    if not (isinstance(lst_configs, list) and len(lst_configs) >=1):
      msg = "Received invalid '{}' command. Command should contain a list of pipeline updates".format(
        ct.PAYLOAD_CT.COMMANDS.BATCH_UPDATE_PIPELINE_INSTANCE,
      )
      notif = ct.STATUS_TYPE.STATUS_EXCEPTION
      info = "Full received payload: {}".format(lst_configs)
    else:
      instances = [
        "  - {}:{}:{}".format(x.get(ct.PAYLOAD_DATA.NAME), x.get(ct.PLUGIN_INFO.SIGNATURE), x.get(ct.PLUGIN_INFO.INSTANCE_ID)) 
        for x in lst_configs
      ]
      self.P("Updating {} pipelines:\n{}".format(len(lst_configs), len(instances))) # TODO: delete 2nd len after testing
      for dct_config in lst_configs:
        res = self.update_pipeline_instance(
          dct_config, 
          session_id=session_id, initiator_id=initiator_id,
        )
        if res is not None:
          results.append(res)
      #endfor
      msg = "Successfully updated {} pipelines with `batch_update_pipeline_instance`".format(len(lst_configs))
      info = " Pipelines:\n  - " +"\n  - ".join([
        str(res)
        for res in results
      ])
      notif = ct.STATUS_TYPE.STATUS_NORMAL
    #endif valid command
    
    self.P(msg + ". " + info, color='r' if notif == ct.STATUS_TYPE.STATUS_EXCEPTION else 'g')
    self._create_notification(
      notif=notif, 
      msg=msg,
      info=info,
      displayed=True,
      BATCH_UPDATE_PIPELINE_INSTANCE=results,
      initiator_id=initiator_id,
      session_id=session_id,
    )
    return
  
  
  
  def pipeline_command(self, dct_config_data, initiator_id, session_id, **kwargs):
    pipeline_name = dct_config_data.get(ct.CONFIG_STREAM.NAME) 
    command = dct_config_data.get(ct.CONFIG_STREAM.PIPELINE_COMMAND)
    sender_addr = dct_config_data.get(ct.COMMS.COMM_RECV_MESSAGE.K_SENDER_ADDR, None)
    info = ""
    if pipeline_name not in self.dct_config_streams:
      msg = "Received invalid data '{}...' for commanding pipeline '{}'. Pipeline does not exist".format(
        str(dct_config_data)[:10], pipeline_name
      )
      notif = ct.STATUS_TYPE.STATUS_EXCEPTION
    else:      
      dct_pipeline = self.dct_config_streams[pipeline_name]
      
      if session_id is None: 
        session_id = dct_pipeline.get(ct.PAYLOAD_DATA.SESSION_ID)
        
      if initiator_id is None:
        initiator_id = dct_pipeline.get(ct.PAYLOAD_DATA.INITIATOR_ID)    
      else:
        # update modified-by fields
        dct_pipeline[ct.CONFIG_STREAM.K_MODIFIED_BY_ID] = initiator_id
        dct_pipeline[ct.CONFIG_STREAM.K_MODIFIED_BY_ADDR] = sender_addr
      #endif  
      
        
      dct_pipeline[ct.CONFIG_STREAM.PIPELINE_COMMAND] = command
      notif = ct.STATUS_TYPE.STATUS_NORMAL      
      self._save_stream_config(dct_pipeline) # save with null command
      msg = "Prepared pipeline command for pipeline '{}'".format(pipeline_name)
    #endif pipeline exists or not

    self.P(msg + ". " + info, color='r' if notif == ct.STATUS_TYPE.STATUS_EXCEPTION else 'g')
    self._create_notification(
      notif=notif, 
      msg=msg,
      info=info,
      stream_name=pipeline_name,
      session_id=session_id,
      initiator_id=initiator_id,
      displayed=True,
    )
    return     
  
  
  def delete_config_stream(self, stream_name, initiator_id, session_id, verbose=1):
    if stream_name not in self.dct_config_streams:
      msg = "Attempted to delete pipeline '{}' that does not exist. Current pipelines: {}".format(
        stream_name, list(self.dct_config_streams.keys())
      )
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING, 
        msg=msg,
        stream_name=stream_name,
        displayed=True,
      )
      return
    elif stream_name.lower() == self.admin_pipeline_name.lower():
      msg = "WARNING: Attempted detected to delete administration pipeline"
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_NORMAL, 
        notif_code=ct.NOTIFICATION_CODES.PIPELINE_ARCHIVE_FAILED,
        msg=msg,
      )
      return      
    #endif
    if session_id is None:
      session_id = self.dct_config_streams[stream_name].get(ct.PAYLOAD_DATA.SESSION_ID)
    if initiator_id is None:
      initiator_id = self.dct_config_streams[stream_name].get(ct.PAYLOAD_DATA.INITIATOR_ID)

    self._delete_stream_config(stream_name)
    self.dct_config_streams.pop(stream_name)
    
    msg = "Successfully deleted pipeline {}".format(stream_name)    
    if verbose >= 1:
      self.P(msg, color='g')
    
    self._create_notification(
      notif=ct.STATUS_TYPE.STATUS_NORMAL, 
      msg=msg,
      stream_name=stream_name,
      session_id=session_id,
      initiator_id=initiator_id,
      displayed=verbose >= 1,
    )
    #endif
    return
  

  def archive_single_stream(self, stream_name, initiator_id, session_id, verbose=1):    
    if not isinstance(stream_name, str):
      msg = "Attempt to archive a pipeline with no/wrong pipeline name/information: {}.".format(stream_name)
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING, 
        notif_code=ct.NOTIFICATION_CODES.PIPELINE_ARCHIVE_FAILED,
        session_id=session_id,
        initiator_id=initiator_id,
        msg=msg, 
        stream_name=stream_name,
        ct=ct,
        displayed=True
      )      
      return
    elif stream_name not in self.dct_config_streams:
      msg = "Attempted to archive pipeline '{}' that does not exist. Current pipelines: {}".format(
        stream_name, list(self.dct_config_streams.keys())
      )
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING, 
        notif_code=ct.NOTIFICATION_CODES.PIPELINE_ARCHIVE_FAILED,
        session_id=session_id,
        initiator_id=initiator_id,
        msg=msg,
        stream_name=stream_name,
        ct=ct,
        displayed=True,        
      )
      return
    elif stream_name.lower() == self.admin_pipeline_name.lower():
      msg = "WARNING: Attempted detected to archive administration pipeline"
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_NORMAL, 
        notif_code=ct.NOTIFICATION_CODES.PIPELINE_ARCHIVE_FAILED,
        session_id=session_id,
        initiator_id=initiator_id,
        msg=msg,
      )
      return      

    #endif bad stuff
    if session_id is None:
      session_id = self.dct_config_streams[stream_name].get(ct.PAYLOAD_DATA.SESSION_ID)
    if initiator_id is None:
      initiator_id = self.dct_config_streams[stream_name].get(ct.PAYLOAD_DATA.INITIATOR_ID)
    
    self.P("Archiving pipeline '{}' from '{}', sessid: {}...".format(stream_name, initiator_id, session_id))

    self._save_stream_config(
      self.dct_config_streams[stream_name],
      subfolder_path=os.path.join(self._folder_streams_configs, 'archived_streams'),
      prefix_fn=self.log.now_str(nice_print=False, short=True)
    )

    self.delete_config_stream(
      stream_name=stream_name, 
      session_id=session_id, initiator_id=initiator_id,
      verbose=0,
    )

    msg = "Successfully archived (and deleted) pipeline '{}' (currently {} active pipelines)".format(stream_name, len(self.dct_config_streams))
    self.P(msg)
      
    self._create_notification(
      notif=ct.STATUS_TYPE.STATUS_NORMAL, 
      notif_code=ct.NOTIFICATION_CODES.PIPELINE_ARCHIVE_OK,
      msg=msg,
      stream_name=stream_name,
      session_id=session_id,
      initiator_id=initiator_id,
      ct=ct,
      displayed=True,
    )
    return

  

  
  def archive_streams(self, stream_names, initiator_id, session_id):
    for stream_name in stream_names:
      self.archive_single_stream(stream_name, initiator_id=initiator_id, session_id=session_id)
    return

  
  def archive_all_streams(self, initiator_id, session_id):
    lst_streams = list(self.dct_config_streams.keys())
    self.P("Proceeding to ARCHIVING of {}".format(lst_streams))
    self.archive_streams(lst_streams, initiator_id=initiator_id, session_id=session_id)
    return


  def delete_streams(self, stream_names, initiator_id, session_id):
    for stream_name in stream_names:
      self.delete_config_stream(stream_name, initiator_id=initiator_id, session_id=session_id)
    return

  
  def delete_all_streams(self, initiator_id, session_id):
    lst_streams = list(self.dct_config_streams.keys())
    self.P("Proceeding to DELETION of {}".format(lst_streams))
    self.delete_streams(lst_streams, initiator_id=initiator_id, session_id=session_id)
    return
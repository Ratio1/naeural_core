"""
IMPORTANT:
  The commands that the `CommunicationManager` responds to are defined directly in the Orchestrator `cmd_handlers_...` methods defined in `core/main/command_handlers.py`
"""

import json
import os
from time import time

import numpy as np

from naeural_core import Logger
from naeural_core import constants as ct
from naeural_core.bc import DefaultBlockEngine, VerifyMessage
from naeural_core.local_libraries import _ConfigHandlerMixin
from naeural_core.manager import Manager


class CommunicationManager(Manager, _ConfigHandlerMixin):
  def __init__(self,
               log: Logger,
               config,
               shmem,
               device_version,
               environment_variables=None,
               avail_commands=[],
               **kwargs):
    self.shmem = shmem
    # TODO: add Shared Memory Manager instance to each plugin under ct.SHMEM.COMM
    self.config = config
    self._device_version = device_version

    self._environment_variables = environment_variables
    self._dct_comm_plugins = None
    self._last_print_info = time()
    self._command_queues = None
    self.__lst_commands_from_self = []
    self._predefined_commands = avail_commands

    self.avg_comm_loop_timings = 0

    self.__local_communication_enabled = False # controls if we should start the local communication
    self.__local_communication_active = False # flag that tells us if the local communication has been started

    super(CommunicationManager, self).__init__(log=log, prefix_log='[COMM]', **kwargs)
    return

  def startup(self):
    super().startup()
    self.config_data = self.config
    self._dct_comm_plugins = self._dct_subalterns
    self._command_queues = self._empty_commands()
    return
  
  @property
  def should_bypass_commands_to_self(self):
    return self._environment_variables.get("LOCAL_COMMAND_BYPASS", True)
  
  @property
  def save_received_commands(self):
    return self._environment_variables.get("SAVE_RECEIVED_COMMANDS", False)
  
  @property
  def blockchain_manager(self) -> DefaultBlockEngine:
    return self.shmem[ct.BLOCKCHAIN_MANAGER]
  
  @property
  def is_secured(self):
    return self.log.config_data.get(ct.CONFIG_STARTUP_v2.K_SECURED, False)

  @property
  def _device_id(self):
    id = self.log.config_data.get(ct.CONFIG_STARTUP_v2.K_EE_ID, '')
    return id  


  @property
  def has_failed_comms(self):
    for comm in self._dct_comm_plugins.values():
      if comm.comm_failed_after_retries:
        self.P("Detected total communication failure on comm {}. This may generate shutdown/restart.".format(comm.__class__.__name__), color='error')
        return True
    return False  


  def _verify_command_signature(self, cmd, verify_allowed=False) -> VerifyMessage:
    result = None
    if self.blockchain_manager is not None:
      result = self.blockchain_manager.verify(cmd, return_full_info=True, verify_allowed=verify_allowed)
      if not result.valid:
        self.P("Command received from sender (verify allowed: {}) addr <{}> signature verification failed: {}".format(
          verify_allowed, result.sender, result.message
          ),color='r'
        )
      else:
        self.P("Command received from sender (verify allowed: {}) addr <{}> signature verification OK.".format(
          verify_allowed, result.sender
        ))
    else:
      raise ValueError("Blockchain Manager is unavailable for verifying the incoming command data")
    return result

  def _load_whitelist_commands(self):
    whitelist_commands_path = os.path.join(self.log.base_folder, ct.WHITELIST_COMMANDS_FILE)
    whitelist_commands = self.log.load_json(whitelist_commands_path)

    if whitelist_commands is None:
      self.P("Creating default whitelist commands file.", verbosity=1)
      self.log.save_json(ct.TEMPLATE_WHITELIST_COMMANDS, whitelist_commands_path)
      whitelist_commands = ct.TEMPLATE_WHITELIST_COMMANDS
    # endif whitelist_commands is None

    return whitelist_commands

  def _reset_whitelist_commands_to_template(self):
    whitelist_commands_path = os.path.join(self.log.base_folder, ct.WHITELIST_COMMANDS_FILE)
    self.log.save_json(ct.TEMPLATE_WHITELIST_COMMANDS, whitelist_commands_path)
    return

  def _verify_whitelist_command(self, cmd):
    template_whitelist_command: list[dict] = self._load_whitelist_commands()
    return any([self.log.match_template(cmd, template) for template in template_whitelist_command])

  def _verify_command_allowed(self, cmd):
    is_authorized = self.blockchain_manager.is_allowed(cmd.get(ct.COMMS.COMM_RECV_MESSAGE.K_SENDER_ADDR, None))
    is_whitelisted = self._verify_whitelist_command(cmd)

    result = is_authorized or is_whitelisted
    result_message = ""

    if is_authorized:
      result_message = "Sender is authorized."
    elif is_whitelisted:
      result_message = "Sender is not authorized but command is allowed due to WHITELIST."
    else:
      result_message = "Sender is not authorized."

    return result, result_message

  def _empty_commands(self):
    return {
      x: [] for x in self._predefined_commands
    }

  def get_received_commands(self):
    # When someone from outside wants to get the received commands, they are automatically refreshed
    # in order to 'make space' for new commands

    # TODO: refactor with deque - this implementation is bad
    ret_received_commands = []
    for k, v in self._command_queues.items(): # for each command type
      for c in v: # for each command of that type
        # each command should be a tuple `payload, sender, session`
        ret_received_commands.append((k, c))
      # endfor each command of that type
    # endfor each command type
    self._command_queues = self._empty_commands()
    return ret_received_commands

  def _get_plugin_class(self, name):
    _module_name, _class_name, _class_def, _class_config = self._get_module_name_and_class(
      locations=ct.PLUGIN_SEARCH.LOC_COMM_PLUGINS,
      name=name,
      suffix=ct.PLUGIN_SEARCH.SUFFIX_COMM_PLUGINS,
      safety_check=True,  # perform safety check
      safe_locations=ct.PLUGIN_SEARCH.SAFE_LOC_COMM_PLUGINS,
    )

    if _class_def is None:
      msg = "Error loading communication plugin '{}'".format(name)
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        info="No code/script defined for communication plugin '{}' in {}".format(
          name, ct.PLUGIN_SEARCH.LOC_COMM_PLUGINS)
      )
      raise ValueError(msg)
    # endif

    return _class_def, _class_config

  def __start_communication(self, config_instance, is_local=False):
    plugin_name = self.config["TYPE"]
    config_instance[ct.EE_ID] = self._device_id
    config_instance[ct.EE_ADDR] = self.blockchain_manager.address

    _class_def, _default_config = self._get_plugin_class(plugin_name)
    id_comm = 0
    for comm_type, paths in self.config["INSTANCES"].items():
      id_comm += 1

      if not is_local:
        # the default connection, used to communicate with the network
        comm_type = comm_type.upper()
      else:
        # the local connection, used for local communication
        comm_type = "L_" + comm_type.upper()
      send_channel_name = paths.get("SEND_TO", None)
      recv_channel_name = paths.get("RECV_FROM", None)
      if send_channel_name is not None:
        send_channel_name = send_channel_name.upper()
      if recv_channel_name is not None:
        recv_channel_name = recv_channel_name.upper()

      try:
        comm_plugin = _class_def(
          log=self.log,
          shmem=self.shmem,
          signature=plugin_name,
          comm_type=comm_type,
          default_config=_default_config,
          upstream_config=config_instance,
          environment_variables=self._environment_variables,
          send_channel_name=send_channel_name,
          recv_channel_name=recv_channel_name,
          timers_section=str(id_comm) + '_' + comm_type,
        )
        comm_plugin.validate(raise_if_error=True)
        comm_plugin.start()
        self.add_subaltern(comm_type, comm_plugin)
      except Exception as exc:
        msg = "Exception '{}' when initializing communication plugin {}".format(exc, plugin_name)
        self.P(msg, color='r')
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
          msg=msg,
          autocomplete_info=True
        )
        raise exc
      # end try-except
    # endfor
    return

  def __maybe_start_local_communication(self):
    if self.__local_communication_active:
      self.P("Local communication has already been started", color='y')
      return

    if not self.__local_communication_enabled:
      self.P("Local communication is disabled", color='y')
      return

    # TODO: maybe change LOCAL_PARAMS to something else
    if "LOCAL_PARAMS" not in self.config:
      self.config["LOCAL_PARAMS"] = {
        "HOST": "localhost",
        "PASS": "",
        "PORT": 1883,
        "USER": "",
        "QOS": 0,
        "SECURED": 0,
      }
      self.P("Local communication is not configured! Using default values: localhost:1883 no user and no password")

    local_config_instance = {**self.config["PARAMS"], **self.config["LOCAL_PARAMS"]} 
    self.__start_communication(local_config_instance, is_local=True)
    self.__local_communication_active = True
    return

  def start_communication(self):
    self.__start_communication(self.config["PARAMS"])

    # we use the local communication server for development and on-edge deployments
    self.__maybe_start_local_communication()
    return

  def __select_communicators_and_send(self, data, communicator_name, local_only=False):
    if isinstance(data, dict):
      local_only = local_only or data.get('USE_LOCAL_COMMS_ONLY', False)

    central_communicator = self._dct_comm_plugins[communicator_name]
    local_communicator = self._dct_comm_plugins.get("L_" + communicator_name)

    # maybe the local communicator is disabled
    # TODO: maybe remove redundant check  (local_communicator is not None)
    if self.__local_communication_enabled and local_communicator is not None and self.__local_communication_active:
          local_communicator.send(data)

    if not local_only:
      central_communicator.send(data)

    return central_communicator

  def send(self, data, event_type='PAYLOAD'):
    if event_type == 'COMMAND':
      receiver_id, receiver_addr, local_only, command_type, command_content = data

      is_command_to_self = receiver_addr == self.blockchain_manager.address

      if self.should_bypass_commands_to_self and is_command_to_self:
        # if we are the receiver, we bypass sending and receiving the command to the network
        self.P("Bypassing network communication for {} local command to self".format(command_type), color='y')
        self.__lst_commands_from_self.append((command_type, command_content))
      else:
        # we send the command to the network
        command = {
          ct.EE_ID: receiver_id,
          ct.COMMS.COMM_SEND_MESSAGE.K_ACTION: command_type,
          ct.COMMS.COMM_SEND_MESSAGE.K_PAYLOAD: command_content,
          ct.COMMS.COMM_SEND_MESSAGE.K_INITIATOR_ID: self._device_id,
          ct.COMMS.COMM_SEND_MESSAGE.K_SESSION_ID: self.log.session_id,
          ct.COMMS.COMM_SEND_MESSAGE.K_TIME: self.log.now_str()
        }
        # commands generated by plugins that are in local mode will be sent only locally
        # TODO: check if this is the correct behavior 
        self.__select_communicators_and_send((receiver_id, receiver_addr, command), ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL, local_only=local_only)
    else:
      message = {
        'EE_ID': self._device_id,
        'EE_EVENT_TYPE': event_type,
        'EE_VERSION': self._device_version,
        **data
      }

      if event_type == 'PAYLOAD':
        # payloads should be sent to both local and central communicators
        # payloads need to be sent to local communicators only if a flag is present (flag checked in __select_communicators_and_send)
        communicator = self.__select_communicators_and_send(message, ct.COMMS.COMMUNICATION_DEFAULT, local_only=False)
        if len(communicator.loop_timings) > 0:
          self.avg_comm_loop_timings = np.mean(communicator.loop_timings)
      elif event_type == 'NOTIFICATION':
        self.__select_communicators_and_send(message, ct.COMMS.COMMUNICATION_NOTIFICATIONS, local_only=False)
      elif event_type == 'HEARTBEAT':
        self.__select_communicators_and_send(message, ct.COMMS.COMMUNICATION_HEARTBEATS, local_only=False)
      # endif
    # endif
    return

  def close(self):
    self.P("Clossing all comm plugins", color='y')
    for comm in self._dct_comm_plugins.values():
      comm.stop()
    self.__local_communication_active = False
    self.P("Done closing all comm plugins.", color='y')
    return
  
  def __maybe_close_local_communication(self):
    self.P("Clossing all local comm plugins", color='y')
    if not self.__local_communication_active:
      self.P("Local communication is already closed", color='y')
      return

    comms_to_close = [comm for comm in self._dct_comm_plugins.keys() if comm.startswith("L_")]
    for comm_name in comms_to_close:
      comm = self._dct_comm_plugins.pop(comm_name)
      comm.stop()
      del comm
    # endfor
    self.__local_communication_active = False
    self.P("Done closing all local comm plugins.", color='y')
    return

  def enable_local_communication(self):
    """
    Enable local communication. This method will start the local communication if it is not already started.
    """
    self.__local_communication_enabled = True
    self.__maybe_start_local_communication()
    return

  def disable_local_communication(self):
    """
    Disable local communication. This method will stop the local communication if it is started.
    """
    self.__local_communication_enabled = False
    self.__maybe_close_local_communication()
    return

  def maybe_process_incoming(self):
    communicator_for_config = self._dct_comm_plugins[ct.COMMS.COMMUNICATION_HEARTBEATS]
    local_communicator_for_config = self._dct_comm_plugins.get("L_" + ct.COMMS.COMMUNICATION_HEARTBEATS)
    incoming_commands = communicator_for_config.get_messages()

    if local_communicator_for_config is not None:
      # process local commands
      incoming_commands_local = local_communicator_for_config.get_messages()
      incoming_commands.extend(incoming_commands_local)

    for json_msg in incoming_commands:
      self.process_command_message(json_msg)
    self.process_commands_from_self()
    return self.get_received_commands()

  def get_total_bandwidth(self):
    inkB, outkB = 0, 0
    keys = list(self._dct_comm_plugins.keys())
    for name in keys:
      comm = self._dct_comm_plugins.get(name)
      if comm is not None:
        inkB += comm.get_incoming_bandwidth()
        outkB += comm.get_outgoing_bandwidth()
    return inkB, outkB

  def get_comms_status(self):
    dct_stats = {}
    keys = list(self._dct_comm_plugins.keys())
    for name in keys:
      comm = self._dct_comm_plugins.get(name)
      if comm is not None:
        errors, times = comm.get_error_report()
        dct_stats[name] = {
          'SVR': comm.has_server_conn,
          'RCV': comm.has_recv_conn,
          'SND': comm.has_send_conn,
          'ACT': comm.last_activity_time,
          'ADDR': comm.server_address,
          'FAILS': len(errors),
          'ERROR': errors[-1] if len(errors) > 0 else None,
          'ERRTM': times[-1] if len(times) > 0 else None,
          ct.HB.COMM_INFO.IN_KB: comm.get_incoming_bandwidth(),
          ct.HB.COMM_INFO.OUT_KB: comm.get_outgoing_bandwidth(),
        }
    return dct_stats

  def maybe_show_info(self):
    now = time()
    if (now - self._last_print_info) >= ct.COMMS.COMM_SECS_SHOW_INFO:
      communicator = self._dct_comm_plugins[ct.COMMS.COMMUNICATION_DEFAULT]
      dct_stats = self.get_comms_status()
      keys = list(dct_stats.keys())
      ml = max([len(k) for k in keys])
      lines = []
      inkB, outkB = 0, 0
      for n in dct_stats:
        inkB += dct_stats[n][ct.HB.COMM_INFO.IN_KB]
        outkB += dct_stats[n][ct.HB.COMM_INFO.OUT_KB]
        active = dct_stats[n]['ACT']
        line = '    {}: live {}, conn:{}, rcv:{}, snd:{}, fails:{}, err: {}, {}'.format(
          n + ' ' * (ml - len(n)),
          self.log.time_to_str(active),
          int(dct_stats[n]['SVR']),
          int(dct_stats[n]['RCV']),
          int(dct_stats[n]['SND']),
          dct_stats[n]['FAILS'],
          dct_stats[n]['ERRTM'],
          dct_stats[n]['ADDR'],
        )
        lines.append(line)

      self.P("Showing comms statistics (In/Out/Total {:.2f} kB / {:.2f} kB / {:.2f} kB):\n{}".format(
        inkB, outkB, inkB + outkB,
        "\n".join(lines)),
        color=ct.COLORS.COMM
      )
      self._last_print_info = now
      statistics_payloads_trip = communicator.statistics_payloads_trip
      if len(statistics_payloads_trip):
        self.P(statistics_payloads_trip)
    # endif
    return

  def _save_input_command(self, payload):
    fn = '{}.json'.format(self.log.now_str())
    self.log.save_output_json(
      data_json=payload,
      fname=fn,
      subfolder_path='received_commands',
      verbose=False
    )
    return

  def process_commands_from_self(self):
    for action, payload in self.__lst_commands_from_self:
      # we populate the fields with our data because the message is supposed to be from us
      self.process_decrypted_command(
        action=action,
        payload=payload,
        sender_addr=self.blockchain_manager.address,
        initiator_id=self._device_id,
        session_id=self.log.session_id,
        validated_command=True,
      )
    self.__lst_commands_from_self = []
    return

  def process_decrypted_command(self, action, payload, sender_addr=None, initiator_id=None, session_id=None, validated_command=False, json_msg=None):
    """
    This method is called to process a command that has been decrypted.
    We assume the command has been validated at this point, so we can proceed with processing it.
    We can discard a command if action is None or if the action is not recognized.

    action: str
      The action to be performed

    payload: dict
      The payload of the command

    sender_addr: str
      The address of the sender

    initiator_id: str
      The ID of the initiator

    session_id: str
      The ID of the session

    validated_command: bool
      Whether the command has been validated or not

    json_msg: dict
      The original JSON message
    """
    if action is not None:
      action = action.upper()
      if payload is None:
        self.P("  Message with action '{}' does not contain payload".format(
          action), color='y'
        )
        payload = {}  # initialize payload
      # endif no payload

      if isinstance(payload, dict):
        # we add the sender address to the payload
        payload[ct.COMMS.COMM_RECV_MESSAGE.K_SENDER_ADDR] = sender_addr
        # we add or modify payload session & initiator for downstream tasks
        if payload.get(ct.COMMS.COMM_RECV_MESSAGE.K_INITIATOR_ID) is None or initiator_id is not None:
          payload[ct.COMMS.COMM_RECV_MESSAGE.K_INITIATOR_ID] = initiator_id
        if payload.get(ct.COMMS.COMM_RECV_MESSAGE.K_SESSION_ID) is None or session_id is not None:
          payload[ct.COMMS.COMM_RECV_MESSAGE.K_SESSION_ID] = session_id
        # we send the message that this command was validated or not
        payload[ct.COMMS.COMM_RECV_MESSAGE.K_VALIDATED] = validated_command
      # endif

      if action not in self._command_queues.keys():
        self.P("  '{}' - command unknown".format(action), color='y')
      else:
        # each command is a tuple as below
        self._command_queues[action].append((payload, sender_addr, initiator_id, session_id))
      # endif
    else:
      self.P('  Message does not contain action. Nothing to process...', color='y')
      self.P('  Message received: \n{}'.format(json_msg), color='y')
    return

  def process_command_message(self, json_msg):
    device_id = json_msg.get(ct.EE_ID, None)
    action = json_msg.get(ct.COMMS.COMM_RECV_MESSAGE.K_ACTION, None)
    payload = json_msg.get(ct.COMMS.COMM_RECV_MESSAGE.K_PAYLOAD, None)
    initiator_id = json_msg.get(ct.COMMS.COMM_RECV_MESSAGE.K_INITIATOR_ID, None)
    session_id = json_msg.get(ct.COMMS.COMM_RECV_MESSAGE.K_SESSION_ID, None)
    sender_addr = json_msg.get(ct.COMMS.COMM_RECV_MESSAGE.K_SENDER_ADDR, None)
    
    if self.save_received_commands:
      self.P("Saving received command ...")
      self._save_input_command(json_msg)
    #endif save_received_commands

    self.P("Received message with action '{}' from <{}:{}>".format(
      action, initiator_id, session_id),
      color='y'
    )
    failed = False
    
    if device_id != self._device_id and device_id != self.blockchain_manager.address:
      self.P('  Message is not for the current device {} != {} ({})'.format(
        device_id, self._device_id, self.blockchain_manager.address), color='y')
      failed = True
      
    ### signature verification    
    # allowed list will be checked later due to the fact that we will do one shot allowed check and 
    # command-whitelist check conditioned by the decryption of the command itsels (to check for potential whitelist)
    _verify_allowed_at_signature = False
    verify_msg = self._verify_command_signature(json_msg, verify_allowed=_verify_allowed_at_signature) 
    if not verify_msg.valid:
      if self.is_secured:
        msg = "Received invalid command from {}({}):{} due to '{}'. Command will be DROPPED.".format(
          initiator_id, verify_msg.sender, json_msg, verify_msg.message
        )
        failed = True
      else:
        msg = "Received invalid command from {}({}):{} due to '{}'. Command is accepted due to UNSECURED node.".format(
          initiator_id, verify_msg.sender, json_msg, verify_msg.message
        )
        failed = False
      #endif failed or not
      notif_code=ct.NOTIFICATION_CODES.COMM_RECEIVED_BAD_COMMAND if failed else None
      # TODO: in future maybe we should NOT send notifications for invalid received commands
      #       just ignore them and ze zee zeet
      self.P(msg, color='error')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING,
        msg=msg,
        session_id=session_id,
        notif_code=notif_code,
        displayed=False,
      )
    else:
      if _verify_allowed_at_signature:
        msg = "Command from {}({}) signature VALIDATED (verify allowed: {}).".format(
          initiator_id, verify_msg.sender, _verify_allowed_at_signature
        )
        self.P(msg)
    ### end signature verification
    validated_command = verify_msg.valid 


    if failed:
      self.P("  Message dropped.", color='r')      
    else:
      # Not failed, lets process
      is_encrypted = json_msg.get(ct.COMMS.COMM_RECV_MESSAGE.K_EE_IS_ENCRYPTED, False)
      if is_encrypted:
        encrypted_data = json_msg.pop(ct.COMMS.COMM_RECV_MESSAGE.K_EE_ENCRYPTED_DATA, None)
        str_data = self.blockchain_manager.decrypt(
          encrypted_data_b64=encrypted_data, sender_address=sender_addr,
          # decompress=False, # we expect the data to be compressed and the compression flag is embedded in the data
          # embed_compressed=True, # we expect the data to be compressed
        )
        if str_data is None:
          self.P("  Decryption failed. Message dropped.", color='r')
        else:
          try:
            dict_data = json.loads(str_data)
            action = dict_data.get(ct.COMMS.COMM_RECV_MESSAGE.K_ACTION, None)
            payload = dict_data.get(ct.COMMS.COMM_RECV_MESSAGE.K_PAYLOAD, None)
            json_msg.update(dict_data)
          except Exception as e:
            self.P("Error while decrypting message: {}\n{}".format(str_data, e), color='r')
      # endif is_encrypted

      # TODO: change this value to False when all participants send encrypted messages
      if not is_encrypted and not self._environment_variables.get("ACCEPT_UNENCRYPTED_COMMANDS", True):
        self.P("  Message is not encrypted. Message dropped because `ACCEPT_UNENCRYPTED_COMMANDS=False`.", color='r')
      else:
        # now that the message is decrypted, we can check if it is allowed or not as well
        # as if it is whitelisted or not
        self.P("Message pre-processed (is_encrypted: {}), verifying allow list...".format(
          is_encrypted
        ))
        allowed, allowed_msg = self._verify_command_allowed(json_msg)
        self.P("  Command allowed: {}. Reason: {}".format(allowed, allowed_msg), color=None if allowed else 'r')
        if allowed:
          self.process_decrypted_command(
            action=action,
            payload=payload,
            sender_addr=sender_addr,
            initiator_id=initiator_id,
            session_id=session_id,
            validated_command=validated_command,
            json_msg=json_msg
          )
    return

  def validate_macro(self):
    communication = self.config_data
    if communication is None:
      msg = "'COMMUNICATION' is not configured in `config_app.txt`"
      self.add_error(msg)
      return

    plugin_name = communication.get("TYPE", None)
    params = communication.get("PARAMS", None)
    dct_instances = communication.get("INSTANCES", None)

    if plugin_name is None:
      msg = "Parameter 'TYPE' is not configured for 'COMMUNICATION' in `config_app.txt`"
      self.add_error(msg)

    if params is None:
      msg = "Parameter 'PARAMS' is not configured for 'COMMUNICATION' in `config_app.txt`"
      self.add_error(msg)
    else:
      found_channels = []
      for k in params:
        if k in ct.COMMS.COMMUNICATION_VALID_CHANNELS:
          found_channels.append(k)

      if len(set(ct.COMMS.COMMUNICATION_VALID_CHANNELS) - set(found_channels)) != 0:
        self.add_error("Make sure that all communication channels {} are configured as 'PARAMS' for 'COMMUNICATION' in `config_app.txt`".format(
          ct.COMMS.COMMUNICATION_VALID_CHANNELS))
      port = params.get("PORT", None)
      if port is not None:
        if not isinstance(port, int):
          self.add_warning("Parameter 'PORT' is not an integer for 'COMMUNICATION' in `config_app.txt` - casting to int")
          params["PORT"] = int(port)
        # endif not int
      # endif port
          
    #endif params

    if dct_instances is None:
      msg = "Parameter 'INSTANCES' is not configured for 'COMMUNICATION' in `config_app.txt`"
      self.add_error(msg)
    else:
      found_instances = []
      for comm_type, paths in dct_instances.items():
        comm_type = comm_type.upper()
        send_channel_name = paths.get("SEND_TO", None)
        recv_channel_name = paths.get("RECV_FROM", None)
        if send_channel_name is not None:
          send_channel_name = send_channel_name.upper()
        if recv_channel_name is not None:
          recv_channel_name = recv_channel_name.upper()
        if comm_type not in ct.COMMS.COMMUNICATION_VALID_TYPES:
          msg = "Parameter 'INSTANCE' is misconfigured for 'COMMUNICATION' in `config_app.txt` - unknown instance {}; please try one of these: {}".format(
            comm_type, ct.COMMS.COMMUNICATION_VALID_TYPES)
          self.add_error(msg)

        if send_channel_name is not None and send_channel_name not in ct.COMMS.COMMUNICATION_VALID_CHANNELS:
          msg = "Parameter 'INSTANCE' is misconfigured for 'COMMUNICATION' in `config_app.txt` - for instance {} 'SEND_TO' is not valid; please try one of these: {}".format(
            comm_type, ct.COMMS.COMMUNICATION_VALID_CHANNELS)
          self.add_error(msg)

        if recv_channel_name is not None and recv_channel_name not in ct.COMMS.COMMUNICATION_VALID_CHANNELS:
          msg = "Parameter 'INSTANCE' is misconfigured for 'COMMUNICATION' in `config_app.txt` - for instance {} 'RECV_FROM' is not valid; please try one of these: {}".format(
            comm_type, ct.COMMS.COMMUNICATION_VALID_CHANNELS)
          self.add_error(msg)

        found_instances.append(comm_type)
      # endfor
      if len(set(ct.COMMS.COMMUNICATION_VALID_TYPES) - set(found_instances)) != 0:
        self.add_error("Make sure that all communication instances {} are configured as 'INSTANCES' for 'COMMUNICATION' in `config_app.txt`".format(
          ct.COMMS.COMMUNICATION_VALID_TYPES))

    # endif

    return

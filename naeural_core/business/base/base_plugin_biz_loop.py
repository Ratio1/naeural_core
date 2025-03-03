import traceback
import numpy as np

from time import time, sleep
from collections import deque

from naeural_core import constants as ct

class _BasePluginLoopMixin(object):
  def __init__(self):
    super(_BasePluginLoopMixin, self).__init__()

    self.has_exec_error = False
    self.has_loop_error = False
    self.first_error_time = None
    self.first_error_info = None
    self.last_error_time = None
    self.last_error_info = None


    self.__loop_paused = False
    self.__plugin_loop_errors = 0
    self.__plugin_exec_errors = 0
    self.__post_error_ok_count = 0    
    self.__crash_simulated = False
    self.__last_error_inputs = None

    self.__lap_times = deque(maxlen=100) # process to process time - short queue to see dynamic in time
    self.__plugin_resolution = None
    

    self.__exec_times = deque(maxlen=100) # exec time in plugin_loop - short queue to see dynamic in time
    self.__exec_loop_check_time = time() # reset exec loop check time
    self.__last_execution_chain_ts = None
        
    self.__exec_counter = 0
    self.__current_iteration = 0
    self.__exec_counter_after_config = 0
    self.is_outside_working_hours = True
    
    return


  
  ############################################################
  # Public properties & methods
  ############################################################
  @property
  def actual_plugin_resolution(self):
    return self.__plugin_resolution
  
  
  @property
  def current_process_iteration(self):
    """
    Returns the current process iteration
    """
    return self.__current_iteration

  @property
  def current_exec_iteration(self):
    """
    Returns the current loop exec iteration
    """
    return self.__exec_counter

  @property
  def loop_timings(self):
    return [round(x, 2) for x in list(self.__lap_times)]

  @property
  def exec_timestamp(self):
    return self.__last_execution_chain_ts
  
  @property
  def n_plugin_exceptions(self):
    return self.__plugin_loop_errors

  @property
  def loop_paused(self):
    return self.__loop_paused
  
  @loop_paused.setter
  def loop_paused(self, val: bool):
    self.__loop_paused = val  
    return
        
  def pause_loop(self):
    self.loop_paused = True
    return
  
  def resume_loop(self):  
    self.loop_paused = False
    return
  
  def reset_exec_counter_after_config(self):
    self.__exec_counter_after_config = 0
    return

  ############################################################
  # End public properties & methods
  ###########################################################    
  
  
  def _recalc_plugin_resolution(self):
    if self.last_process_time is not None:
      this_lap = self.time_from_last_process
      self.__lap_times.append(this_lap)
      self.__plugin_resolution = round(1 / np.mean(self.__lap_times), 1)
    return  
  

  def _maybe_simulate_exec_crash(self):
    if isinstance(self.cfg_simulate_crash_exec_iter, int) and self.cfg_simulate_crash_exec_iter > 0:
      if self.__exec_counter == 1:
        self.P("Crash simulation active at iteration {}".format(
          self.cfg_simulate_crash_exec_iter),
          color='r'
        )
      if self.__exec_counter > self.cfg_simulate_crash_exec_iter and not (self.cfg_simulate_crash_exec_single and self.__crash_simulated):
        self.__crash_simulated = True
        self.P("Simulating crash in code execution...")
        raise ValueError("Test Exception")
    return
  
  
  def _maybe_simulate_loop_crash(self):
    if self.cfg_simulate_crash_loop:
      self.P("Simulating crash in plugin loop...")
      raise ValueError("Test Loop Exception")
    return
    

  def check_loop_exec_time(self):
    # check each 30s and if enough data is gathered
    if (time() - self.__exec_loop_check_time) > 30 and len(self.__exec_times) > 5:
      self.__exec_loop_check_time = time()
      # TODO: finish checking loop times!
      times = self.__exec_times
    return


  def __maybe_handle_exec_errors(self, sleep_time):
    # `has_exec_error` is triggered on exec exception
    # `has_loop_error` is triggered on loop exception (maybe a exception within on_command)
    if self.has_exec_error or self.has_loop_error:    
      nr_errors = self.__plugin_exec_errors if self.has_exec_error else self.__plugin_loop_errors
      sleep_time = ct.PLUGIN_EXCEPTION_DELAY * nr_errors
      # now we decrease each time we hit this point and no exec error occured in loop (except is below this point)
      msg = "Forcing plugin loop delay to {}s due to {} exception nr. {}. Lowering in {} good iteration(s).".format(
        sleep_time, 
        'EXEC' if self.has_exec_error else 'LOOP',
        self.__plugin_exec_errors if self.has_exec_error else self.__plugin_loop_errors, 
        self.__post_error_ok_count,
      )
      info = None
      self.__post_error_ok_count -= 1 

      if self.__post_error_ok_count <= 0:
        self.has_exec_error, self.has_loop_error = False, False
        msg_back = " Further delay cleared due to normal behaviour. Resuming normal plugin functioning."
        self.__post_error_ok_count = 0
        msg = msg + msg_back
      elif self.last_error_time is not None and self.last_error_info is not None:
        info = "Last exec error: {}:{}".format(
          self.last_error_time, "\n".join(self.last_error_info.split('\n')[-4:]),
        ) 
      #endif stop error

      self.P(msg, color='r')
      
      self._create_error_notification(
        msg=msg, 
        notif_code=ct.NOTIFICATION_CODES.PLUGIN_DELAYED,
        displayed=True,               
        info=info,
      )        
    #endif either exec or loop error   
    return sleep_time   

  def __mark_exec_error_time_and_info(self):
    self.__plugin_exec_errors += 1
    str_time = self.log.now_str(nice_print=True)
    info = traceback.format_exc()
    if self.first_error_time is None:
      self.first_error_time = str_time
      self.first_error_info = info
    self.last_error_time = str_time
    self.last_error_info = info
    return


  def _on_data(self):
    # this can be overwritten in child classes but will not benefit from various
    # mechanisms available in the pre-defined process
    self.high_level_execution_chain()
    return

  def _on_idle(self):
    # this can be overwritten in child classes but will not benefit from various
    # mechanisms available in the pre-defined process
    bool_received_input = self.dataapi_received_input()
    bool_run = bool_received_input or (not bool_received_input and self.cfg_allow_empty_inputs)
    if not bool_run:
      return
    self.high_level_execution_chain()
    return  
  

  def execute(self):
    """
    The main execution of a plugin instance.
    This public method should NOT be overwritten under any circumstance

    Returns
    -------
    None.

    """
    try:
      # testing
      self._maybe_simulate_exec_crash()
      # end testing

      # extra & "AUTO_..." processing
      self._maybe_exec_auto_processes()
      # end extra and "AUTO_..." processing

      if self.input_queue_size > 0:
        self.inputs = self.get_next_avail_input() # setting _inputs
        self._on_data()
        self._last_data_input = time()
      else:
        self._maybe_alert_on_no_data()
        self._on_idle()
      #endif data or idle
      self.config_changed_since_last_process = False

      # `__last_execution_chain_ts` moved here from `high_level_execution_chain`
      # as `high_level_execution_chain` may be missing/overwritten in some
      # inherited implementations
      self.__last_execution_chain_ts = self.log.now_str(nice_print=True, short=False)

      # payload queue addition - this is valid only for plugins that do not directly
      # use `add_payload` - do NOT use both `return payload` as well as `add_payload`
      # as below code will duplicate the payload (or use if so designed)
      payload = self.get_payload_after_exec()
      self.add_payload(payload)
      # end payload addition

      # if above code results no payload then maybe resend status
      self._maybe_resend_last_payload()
      # end resend status

      commands = self.get_commands_after_exec()
      if len(commands) > 0:
        self.commands_deque.append(commands)

      # we also log this exec time
      self.last_exec_time = time()

    except:
      msg = "CRITICAL ERROR in business plugin EXECUTE: {}, Queue: {}, {}".format(
        self.log.get_error_info(), self.input_queue_size,
        "Inputs not None" if self.inputs is not None else "No inputs"
      )
      # now we record the exception inputs
      self.__last_error_inputs = self.inputs
      info = traceback.format_exc()
      self.P(msg + '\n' + info, color='r')
      self._create_error_notification(
        msg=msg,
        info=info,
        displayed=True,
      )
      self.has_exec_error = True
      self.__post_error_ok_count = 5
      self.__mark_exec_error_time_and_info()

      if not self._threaded_execution_chain:
        delay = ct.PLUGIN_EXCEPTION_DELAY
        msg = "Forcing NON-threaded business plugin process delay extension to {}s due to exception".format(delay)
        self.P(msg, color='r')
        self._instance_config[ct.PROCESS_DELAY] = delay
        self._create_error_notification(
          msg=msg,
          displayed=True,
        )
    # finally we "consume" the inputs
    self.inputs = None
    return

  def high_level_execution_chain(self):
    """
    Standard processing cycle
    """
    self._cmdapi_refresh()
    self._payload = None

    self.__current_iteration += 1

    # now preproc, proc and postproc iteration
    self.pre_process_wrapper()

    # Initializing the params for DatasetBuilderMixin
    self._dataset_builder_info = None

    self._init_dataset_builder_params()
    # Here we gather data for DatasetBuilderMixin from the current iteration's inferences
    # This is done here instead of on_data because the inferences are retrieved in
    # the pre_process_wrapper method
    self._maybe_ds_builder_gather_before_process()
    self._maybe_ds_builder_save()

    self.process_wrapper()
    self.post_process_wrapper()
    # end actual processing

    # now we can check if we need send the payload
    payload = self.get_payload_after_exec()
    if payload is not None:
      dct_payload = self.add_payload(payload)
      # testing and benchmarking
      if self._testing_manager is not None:
        # TODO: AID & Bleo -- code review end-to-end
        self._maybe_register_payload_for_tester(payload=dct_payload)
        dct_payload = self._maybe_generate_testing_results_payload(payload=dct_payload)
      # end testing and benchmarking
    #endif

    ### next lines are backwards compat with SaveImageHeavyOps
    if self.cfg_debug_save:
      self._prepare_debug_save_payload()

    return  
  

  def __on_command(self, data, **kwargs):
    if self.cfg_instance_command_log:
      self.P("* * * * * Received INSTANCE_COMMAND: <{} ...> with kwargs {}".format(str(data)[:30], kwargs))
    self._maybe_simulate_loop_crash()
    use_local_comms_only=kwargs.get('use_local_comms_only', False)
    try:
      self._on_command(data, **kwargs)
      notif = ct.STATUS_TYPE.STATUS_NORMAL
      notif_code = ct.NOTIFICATION_CODES.PLUGIN_INSTANCE_COMMAND_OK
      msg = "Command {} executed.".format(data)
      info = ""
    except:
      notif = ct.STATUS_TYPE.STATUS_EXCEPTION
      notif_code = ct.NOTIFICATION_CODES.PLUGIN_INSTANCE_COMMAND_FAILED
      info = traceback.format_exc()
      msg = "Command {} failed.".format(data)

    self._create_notification(
      notif=notif,
      notif_code=notif_code,
      msg=msg,
      info=info,
      displayed=True,
      use_local_comms_only=use_local_comms_only,
    )

    return

  def __maybe_trigger_instance_command(self):
    triggered = False
    if self.cfg_instance_command is not None and len(self.cfg_instance_command)>0:
      # new command!
      command = self.cfg_instance_command
      # make sure we save config before code
      self.archive_config_keys(keys=['INSTANCE_COMMAND'], defaults=[{}])

      # get from instance command the standard COMMAND_PARAMS object
      command_kwargs = {}
      if isinstance(command, dict):
        command_kwargs = command.pop('COMMAND_PARAMS', {})
      # make the kwargs case insensitive
      processed_kwargs = {k.lower():v for k,v in command_kwargs.items()}
      # end get command params
      
      self.__on_command(command, **processed_kwargs)
      # copy, erase and save
      triggered = True
    #end has instance command
    return triggered  
  
  def __on_init(self):
    self.P("Running build-in & custom on_init events...")
    if self.cfg_disabled:
      self.P(f"WARNING: This plugin instance of `{self.__class__.__name__}` is DISABLED", boxed=True, color='r')
      return
    if self.cfg_dataset_builder:
      self.P(f"WARNING: Dataset builder active for `{self.__class__.__name__}`", boxed=True, color='r')      
    try:
      self._on_init()
    except Exception as e:
      self.P(f"START FAILURE: {e}", boxed=True, color='r')
      self._create_error_notification(
        msg=f"Custom on_init event failed: {e}",
        displayed=True,
      )
      raise e
    self._init_process_finalized = True
    return
  

  def plugin_loop(self):
    """
    This is BusinessPlugin main execution loop (plugin loop)

      - plugin.outside_working_hours and plugin.is_process_postponed need to be handled also
      - main thread execution actually is wrapped in the "execute"
      
      stop precedence:
      PROCESS_DELAY > FORCE_PAUSE > WORKING_HOURS
      
    """
    self.__on_init()
    self.P("Thread initialized.", color='g')

    while not self.done_loop:
      try:
        # START postpone area
        if self.__loop_paused: # triggered when updating config
          sleep(0.001)          
          continue
        # INSTANCE_COMMAND: if no imposed parallel updating
        # then we can check for instance commands and trigger callback
        self.__maybe_trigger_instance_command()
        # END INSTANCE_COMMAND
        
        # PROCESS_DELAY mechanism
        if self.is_process_postponed:  
          # skip if postpone is required
          sleep(0.001)
          continue
        # END PROCESS_DELAY mechanism
        
        # FORCE_PAUSE mechanism
        if self.is_plugin_temporary_stopped: 
          sleep(0.001)
          continue
        # END FORCE_PAUSE mechanism

        self.is_outside_working_hours = self.outside_working_hours
        if self.is_outside_working_hours: # WORKING_HOURS mechanism
          # always skip if outside hours
          sleep(0.001)
          continue
        # END postpone area
        # no postponing, now outside of scheduling and no pausing so we proceed
        # to execution and save last processing time (written in process wrapper)
        _last_run = self.last_process_time # cycle timing
        start_it = time()
        self._plugin_loop_in_exec = True
        self.__exec_counter += 1
        self.execute() # further inside process_wrapper  __last_process_time is resetted
        self.__exec_counter_after_config += 1
        # send "all-good with instance" after 1st post-config iter
        if self.__exec_counter_after_config == 1:
          msg = " >>>>>>> Instance {} exec ok post config (itr {}). <<<<<<<".format(
            self, self.__exec_counter
          )
          self.P(msg)
          self._create_notification(
            notif_code=ct.NOTIFICATION_CODES.PLUGIN_CONFIG_OK,
            msg=msg,              
            plugin_running=True,
            displayed=False
          )
        #endif send "all-good with instance" after 1st post-config iter 
        self._plugin_loop_in_exec = False
        if self.last_process_time == _last_run:
          # something was wrong in execution and no one has resetted __last_process_time
          # so we can postpone/delay if needed (otherwise process delay will not work)
          self._reset_last_process_time()
        #endif
        it_time = time() - start_it
        # add this exec iteration time to the queue
        self.__exec_times.append(it_time)
        # now we can check if something strange is happening in the loop such as a
        # abnormal duration of the execute
        self.check_loop_exec_time()
        if self.cfg_forced_loop_sleep is not None:
          sleep_time = self.cfg_forced_loop_sleep
        else:
          sleep_time = max(1 / self.get_plugin_loop_resolution() - it_time, 0.00001)
        # now check for previous errors
        sleep_time = self.__maybe_handle_exec_errors(sleep_time=sleep_time)
        # end error checking
        self.start_timer('loop_sleep')
        if sleep_time > 0:
          sleep(sleep_time)
        self.end_timer('loop_sleep')
      except:
        self._plugin_loop_in_exec = False
        SLEEP_ON_LOOP_ERROR = 10
        msg = "CRITICAL ERROR in business plugin loop (sleep {}) on: {}".format(
          SLEEP_ON_LOOP_ERROR, self.log.get_error_info()
        )
        info = traceback.format_exc()
        self.P(msg + '\n' + info, color='r')
        self._create_error_notification(msg=msg, displayed=True)
        sleep(SLEEP_ON_LOOP_ERROR)
        self.has_loop_error = True
        self.__plugin_loop_errors += 1 # increase nr of errors
        self.__post_error_ok_count += 3 # increase loop error level
    #endwhile

    self.__cleanup()

    # handle commands emitted on close 
    commands = self.get_commands_after_exec()
    if len(commands) > 0:
      self.commands_deque.append(commands)

    self.P(ct.PLUGIN_END_PREFIX + "* * * * Business Plugin thread {} execution ended * * * *".format(self.instance_hash), color='y')
    return


  
  def __cleanup(self):
    #### del self.__upstream_inputs_deque ### this should NOT be called here - please do not ADD/DECOMMENT
    self._on_close()
    return


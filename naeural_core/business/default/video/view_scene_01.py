#global dependencies


#local dependencies

from naeural_core.business.base import CVPluginExecutor as BaseClass

__VER__ = '2.0.1'

_CONFIG = {
  **BaseClass.CONFIG,

  # if this is set to true the on-idle will be triggered continously the process
  # thus at a one-time request it might not get a real image
  'RUN_WITHOUT_IMAGE'           : False, # DO NOT CHANGE THIS! This is a special plugin that needs to run with image
  'ALLOW_EMPTY_INPUTS'          : False, # DO NOT CHANGE THIS! This is a special plugin that needs to run with image
  'MAX_INPUTS_QUEUE_SIZE'       : 1,


  "NR_WITNESSES"                : 1200,  
  "PROCESS_DELAY"               : 1,

   
  "SIMPLE_WITNESS"              : False, # must not send simple due to witness marking needed
  "DEBUG_ON_WITNESS"            : False, # no bars!
  
  "INCREASED_PROCESS_DELAY"      : 3,
  
  "VIEW_SCENE_DEBUG_LOG"        : False,
  
  
  "MAX_PROCESS_DELAY"           : 10,
  
  
  'VALIDATION_RULES': {
    **BaseClass.CONFIG['VALIDATION_RULES'],
    
    "NR_WITNESSES" : {
      "TYPE"          : "int",
      "DESCRIPTION"   : "How many video stream witnesses should be sent before stopping. Set this to a low number for receivinf just the initial witnesses",
      "MIN_VAL"       :  3,
      "MAX_VAL"       :  1e10,
    },    
    
  },

}

class ViewScene01Plugin(BaseClass):
  CONFIG = _CONFIG
  def __init__(self, **kwargs):
    self.__force_send_image = False
    self.__force_send_image_prev_delay = None
    self.__view_scene_command_data = None
    super(ViewScene01Plugin, self).__init__(**kwargs)
    return

  
  def startup(self):
    super().startup()
    self.__witness_count = 0
    self.__last_valid_image = None
    self.__last_valid_time = None
    self.__last_valid_timestamp = None
    self.__sending_previous_image = False
    return


  def _draw_witness_image(self, img, **kwargs):
    if self.__sending_previous_image:
      self.P("Preparing previous image")
      img = self._painter.alpha_text_rectangle(
        image=img, 
        top=100,
        left=1, 
        text="Archive image from {} ({:.1f}s ago)".format(self.__last_valid_time, self.time() - self.__last_valid_timestamp),  
        color=self.ct.DEEP_RED,
        size=1.5
      )
    return img      
  
  def __force_run(self):
    self.__force_send_image = True
    self.__force_send_image_prev_delay = self.cfg_process_delay
    self.config_data['PROCESS_DELAY'] = 0.5
    self.config_data['RUN_WITHOUT_IMAGE'] = True
    self.config_data['ALLOW_EMPTY_INPUTS'] = True
    return
  
  def __reset_force_run(self):
    self.__force_send_image = False
    self.config_data['PROCESS_DELAY'] = self.__force_send_image_prev_delay
    self.config_data['RUN_WITHOUT_IMAGE'] = False
    self.config_data['ALLOW_EMPTY_INPUTS'] = False
    return
  
  def on_command(self, data, **kwargs):
    """
    Although we could send a witness image on command using self.add_payload() 
    we prefer to postpone the normal process and send the image on the next process hit. 
    """

    self.__view_scene_command_data = data
    if self.__last_valid_timestamp is None:
      last_msg = "No previous image - command will probably fail!"
      color = 'r'
    else:
      last_msg = "Last img {:.1f}s ago".format(self.time() - self.__last_valid_timestamp)
      color = 'b'

    # set to run no matter what
    self.__force_run()
    # end set to run no matter what

    msg = "View scene on-command. Forcing delay {:.1f}. Current: {:.1f}. IsPostponed: {}. {}".format(
      self.cfg_process_delay,
      self.__force_send_image_prev_delay,
      self.is_process_postponed,
      last_msg,
    )
    self.P(msg, color=color )
    return


  def _process(self):
    # triggered only if there is a image!
    
    payload = None
    curr_delay = None
    new_delay = None
    
    img = self.dataapi_image()
    self.__sending_previous_image = False

    force_send_needed = self.__force_send_image
    can_send_images = self.__witness_count < self.cfg_nr_witnesses
    send_image_now = force_send_needed or can_send_images
    
    # Stage 1: if need be we get an old image
    if force_send_needed:
      curr_delay = self.cfg_process_delay          
      new_delay = self.__force_send_image_prev_delay
      
      # reset to default behaviour
      self.__reset_force_run()
      # end reset to default behaviour
      
      self.P("Forcing image send due to on-command {}.".format(
        "using CURRENT image" if img is not None else "using PREVIOUS last valid image"
      ))
      if img is None:
        self.P("  No image to send - trying to use last valid image")
        if self.__last_valid_image is not None:
          img = self.__last_valid_image
          self.P("  Using last valid image from: {} ({:.1f}s old)".format(
            self.__last_valid_time, self.time() - self.__last_valid_timestamp)
          )
          self.__sending_previous_image = True
        else:
          msg = "ViewScene request failed: No last valid image to send"
          self.P(msg, color='r')
          self._create_abnormal_notification(
            msg=msg,
          )
        #endif we have a last valid image or not
      #endif no image but we have a command to send an image
    #endif we have a force send command
    
    
    # Stage 2: check if we have a image to send
    if img is not None:
      if not self.__sending_previous_image:
        self.__last_valid_image = img        
        self.__last_valid_time = self.time_to_str()
        self.__last_valid_timestamp = self.time()      
      #endif we have a new image

      self.int_cache['runs'] += 1
      if self.int_cache['runs'] == 1:
        self.P('First image at process: {} and exec: {}'.format(
          self.current_process_iteration, 
          self.current_exec_iteration)
        )
      elif self.cfg_view_scene_debug_log:
        self.P("Witness {} at proc: {} exec: {}, out.wrk.shft: {}, laps: {}".format(
          self.int_cache['runs'],
          self.current_process_iteration, 
          self.current_exec_iteration,
          self.outside_working_hours,
          self.loop_timings[-5:]
          )
        )
      #endif debug run

      if send_image_now:
        additional_data = None
        if force_send_needed:
          self.P("Forced image send. Delay {:.1f} -> {:.1f}".format(curr_delay, new_delay))
          additional_data = self.__view_scene_command_data
          self.__view_scene_command_data = None
        #endif we need to restore proc delay due to recent incoming command
        
        self.__witness_count += 1
        if self.cfg_increased_process_delay > self.cfg_process_delay:
          self.config_data['PROCESS_DELAY'] = self.cfg_increased_process_delay
        #endif maybe we need to increase the process delay
                  
        np_img = self.get_witness_image(img=img)    
        dct_payload = dict(
          status="Base video camera witness",
          img=np_img,
          img_witness_id=self.__witness_count,
          img_remaining_witnesses=self.cfg_nr_witnesses - self.__witness_count,          
        )
        if additional_data is not None:
          dct_payload['COMMAND_PARAMS'] = additional_data
        #endif we have additional data to send
          
        payload = self._create_payload(
          **dct_payload
        )        
      elif self.cfg_close_pipeline_when_done:
        self.cmdapi_archive_pipeline()     
      #endif normal send & forced send
    #endif we have an image to send
    return payload

# TODO Bleo: WIP
from naeural_core.business.base import CVPluginExecutor as BasePlugin
from naeural_core.business.mixins_libs.ai4e_mixin import _Ai4eMixin, AI4E_MIXIN_CONFIG

_CONFIG = {
  **BasePlugin.CONFIG,
  **AI4E_MIXIN_CONFIG,
  'AI_ENGINE': ['lowres_general_detector'],
  'OBJECT_TYPE': ['person'],

  'ALLOW_EMPTY_INPUTS': True,
  'RUN_WITHOUT_IMAGE': True,
  "CLASSES": None,
  'TRAIN_SIZE': 0.8,

  "FULL_DEBUG_LOG": False,
  "RAW_BACKUP_PERIOD": 30,
  'LOG_FAILED_SAVES': False,
  'FORCE_TERMINATE_COLLECT': False,
  'FORCE_OBJECT_TYPE_BALANCED': False,
  'CLOUD_PATH': 'DATASETS/',
  'COLLECT_UNTIL': None,
  'POSTPONE_THRESHOLD': 5,

  'SAVE_PERIOD': 1,
  'MAX_INPUTS_QUEUE_SIZE': 32,

  'VALIDATION_RULES': {
    **BasePlugin.CONFIG['VALIDATION_RULES'],
    **AI4E_MIXIN_CONFIG['VALIDATION_RULES'],
  },
}

__VER__ = '0.1.0.0'


class Ai4eCropDataPlugin(BasePlugin, _Ai4eMixin):

  def debug_log(self, msg, **kwargs):
    if self.cfg_full_debug_log:
      self.P(msg, **kwargs)
    return

  def on_init(self):
    super(Ai4eCropDataPlugin, self).on_init()
    self.label_updates_count = 0
    self.raw_dataset_updates_count = 0
    self.raw_dataset_rel_path = self.os_path.join('raw_datasets', self.cfg_objective_name)
    self.dataset_stats_increment = 0
    self.last_increment_time = self.time()
    self.last_save_time = None

    self.gathering_finished = False
    self.source_names = set()

    self.dataset_abs_path = self.os_path.join(self.get_output_folder(), self.raw_dataset_rel_path)
    self.total_image_count = 0
    self.raw_dataset_set = set()
    self.count_saved_by_object_type = self.defaultdict(lambda: 0)
    self.dataset_stats = self.defaultdict(lambda: 0)

    self.voting_status = 0
    self.postpone_counter = 0
    self.finished = False
    self.maybe_load_previous_state()
    return

  def maybe_load_previous_state(self):
    """
    Method for loading the previous state of the plugin.
    Returns
    """
    self.debug_log('Loading previous state')
    obj = self.persistence_serialization_load()
    if obj is None:
      return
    self.debug_log(f'Loaded previous state: {obj}')

    self.gathering_finished = obj.get('gathering_finished', self.gathering_finished)
    self.source_names = set(obj.get('source_names', self.source_names))

    self.total_image_count = obj.get('total_image_count', self.total_image_count)
    self.raw_dataset_set = set(obj.get('raw_dataset_set', self.raw_dataset_set))
    self.count_saved_by_object_type = obj.get('count_saved_by_object_type', self.count_saved_by_object_type)
    self.count_saved_by_object_type = self.defaultdict(lambda: 0, self.count_saved_by_object_type)
    self.dataset_stats = obj.get('dataset_stats', self.dataset_stats)
    self.dataset_stats = self.defaultdict(lambda: 0, self.dataset_stats)

    return

  def maybe_persistence_save(self, force=False):
    """
    Method for saving the plugin state to the disk for persistence.
    """
    to_save = force
    if self.raw_dataset_updates_count != 0 and self.raw_dataset_updates_count % self.cfg_raw_backup_period == 0:
      to_save = True
    if not to_save:
      return
    self.debug_log('Saving plugin state')
    self.persistence_serialization_save(
      obj={
        'gathering_finished': self.gathering_finished,
        'source_names': list(self.source_names),

        'total_image_count': self.total_image_count,
        'raw_dataset_set': list(self.raw_dataset_set),
        'count_saved_by_object_type': dict(self.count_saved_by_object_type),
        'dataset_stats': dict(self.dataset_stats),
      }
    )
    return

  """UTILS SECTION"""
  if True:
    @property
    def dataset_object_name_raw(self):
      return self.os_path.join(self.cfg_cloud_path, self.cfg_objective_name + '_RAW.zip')

    def get_collect_until(self):
      """
      Provides possibility to stop collecting data at a certain datetime
      """
      collect_until = self.cfg_collect_until
      if collect_until is not None:
        collect_until = self.datetime.strptime(collect_until, '%Y-%m-%d %H:%M')
      return collect_until

    @property
    def collect_until_passed(self):
      collect_until = self.get_collect_until()
      if collect_until is not None:
        return (collect_until - self.datetime.now()).total_seconds() < 0
      return False

    def get_job_status(self):
      """
      Method for getting the job status.
      """
      status = "Gathering"
      if not self.need_data():
        status = "Done Gathering"
      if self.voting_status == 2:
        status = "Publishing"
      return status

    def get_dataset_details(self):
      return {
        **self.cfg_dataset,
        'actualSize': self.total_image_count,
      }

    def get_max_size(self):
      dataset_details = self.cfg_dataset
      return dataset_details.get('desiredSize') if dataset_details is not None else None

    def enough_data(self):
      max_size = self.get_max_size()
      if max_size is None:
        return False
      return self.total_image_count >= max_size

    def need_data(self):
      return not self.enough_data() and not self.collect_until_passed and not self.cfg_force_terminate_collect
  """END UTILS SECTION"""

  """PAYLOAD SECTION"""
  if True:
    def get_status_payload_additional_kwargs(self, **kwargs):
      payload_kwargs = {
        'counts': self.dataset_stats,
        'counts_per_class': self.count_saved_by_object_type,
        'crop_increment': self.dataset_stats_increment
      }
      duration = self.time() - self.last_increment_time
      payload_kwargs['duration'] = duration
      payload_kwargs['crop_speed'] = self.dataset_stats_increment / duration if duration > 0 else 0
      self.last_increment_time = self.time()
      self.dataset_stats_increment = 0
      return payload_kwargs
  """END PAYLOAD SECTION"""

  """COMMANDS SECTION"""
  if True:
    def start_upload_plugin(self):
      self.P(f'Uploading the raw dataset to {self.dataset_object_name_raw}')
      config = {
        'TYPE': 'VOID',
        'NAME': f'UPLOAD_{self.get_instance_id()}',
        'PLUGINS': [{
          'SIGNATURE': 'minio_upload_dataset',
          'INSTANCES': [{
            'INSTANCE_ID': self.get_instance_id(),
            'DATASET_OBJECT_NAME': self.dataset_object_name_raw,
            'DATASET_LOCAL_PATH': self.dataset_abs_path,
            'IS_RAW': True
          }]
        }]
      }
      self.cmdapi_start_pipeline(config=config)
      return

    def get_label_until(self):
      rewards = self.cfg_rewards
      if not isinstance(rewards, dict):
        return None
      return rewards.get('expiration', None)

    def maybe_start_voting(self):
      if self.voting_status == 1:
        self.maybe_persistence_save(force=True)
        self.start_upload_plugin()
        # start upload plugin for the dataset
        # make the voting pipeline of type minio dataset
        config = {
          'TYPE': 'minio_dataset',
          'STREAM_CONFIG_METADATA': {
            'DATASET_OBJECT_NAME': self.dataset_object_name_raw,
          },
          'NAME': f'label_{self.get_instance_id()}',
          'PLUGINS': [
            {
              'SIGNATURE': 'ai4e_label_data',
              'INSTANCES': [
                {
                  # TODO: complete this
                  'INSTANCE_ID': self.get_instance_id(),

                  "OBJECT_TYPE": self.cfg_object_type,
                  'CLASSES': self.get_ds_classes(),
                  'TRAIN_SIZE': self.cfg_train_size,

                  'OBJECTIVE_NAME': self.cfg_objective_name,
                  'REWARDS': self.cfg_rewards,
                  'LABEL_UNTIL': self.get_label_until(),
                  'DATASET': self.get_dataset_details(),
                  'CREATION_DATE': self.cfg_creation_date,
                  'DESCRIPTION': self.cfg_description,
                  'REPORT_PERIOD': self.cfg_report_period
                }
              ]
            }
          ]
        }
        self.P('Starting the voting process')
        self.cmdapi_start_pipeline(
          config=config
        )
        self.voting_status = 2
      # endif voting needs to start
      return

    def stop_gather(self):
      self.P(f'Stopping all the other data sources: {self.source_names}')
      self.cmdapi_stop_current_stream()
      for s in self.source_names:
        self.cmdapi_stop_other_stream_on_current_box(s)
      self.finished = True
      return

    def sample_filename(self):
      return self.np.random.choice(list(self.raw_dataset_set)) if len(self.raw_dataset_set) > 0 else None

    def filename_to_path(self, filename):
      return self.os_path.join(
        self.get_output_folder(), self.raw_dataset_rel_path, f'{filename}.jpg'
      )

    def maybe_process_sample_requests(self, data, **kwargs):
      """
      Method for processing the sample requests received as commands.
      Parameters
      ----------
      data : dict, the data to handle
      kwargs : dict, additional keyword arguments

      Returns
      -------
      bool, whether the command is a request or not
      """
      is_request = False
      request_id = data.get('REQUEST_ID')
      if request_id is None:
        return is_request
      response_kwargs = {
        'request_id': request_id
      }
      sample_filename = data.get('FILENAME')
      chosen_filename = self.sample_filename() if sample_filename is None else sample_filename
      # if filename not provided, a random one will be selected
      sample_request = data.get('SAMPLE', False)
      img = self.diskapi_load_image(
        folder='output',
        filename=f'{chosen_filename}.jpg',
        subdir=self.raw_dataset_rel_path
      )
      if sample_request:
        response_kwargs['sample_filename'] = chosen_filename
        response_kwargs['IMG'] = img
        is_request = True
      elif sample_filename is not None:
        response_kwargs['sample_path'] = self.filename_to_path(chosen_filename)
        response_kwargs['IMG'] = img
        is_request = True
      if is_request:
        self.add_payload(
          self._create_payload(
            is_status=False,
            **response_kwargs
          )
        )
      return is_request

    def on_command(self, data, **kwargs):
      """
      Method for handling the command data.
      Parameters
      ----------
      data : dict, the data to handle
      kwargs : dict, additional keyword arguments

      Returns
      -------

      """
      self.P(f'Got command {data}')
      # In case the command is a sample request, register it and return
      if self.maybe_process_sample_requests(data, **kwargs):
        return
      start_voting = data.get('START_VOTING', False)
      if start_voting and self.voting_status == 0:
        self.P(f'Requesting the start of the voting process')
        self.voting_status = 1
      return

  """END COMMANDS SECTION"""

  """SAVE SECTION"""
  if True:
    def check_if_can_save_object_type(self, object_type):
      """
      Check if the object type can be saved based on the configuration.
      This is used to enforce a balanced dataset if desired.
      Parameters
      ----------
      object_type : str, the object type to check

      Returns
      -------
      bool, whether the object type can be saved
      """
      if not self.cfg_force_object_type_balanced:
        return True

      crt_object_type_count = self.count_saved_by_object_type[object_type]
      can_save = True
      for k in self.cfg_object_type:
        if k == object_type:
          continue

        if crt_object_type_count > self.count_saved_by_object_type[k]:
          can_save = False

      return can_save

    def crop_and_save_one_img(self, np_img, inference, source_name, current_interval):
      """
      Crop and save one image based on the inference data.
      Parameters
      ----------
      np_img : np.ndarray of shape (H, W, C), the image to crop
      inference : dict, the inference data
      source_name : str, the name of the source
      current_interval : str, the current interval

      Returns
      -------
      str, the subdir where the image was saved or None if the save failed
      """
      try:
        self.start_timer('crop_and_save_one_img')
        # Get the top, left, bottom, right positions
        top, left, bottom, right = list(map(lambda x: int(x), inference['TLBR_POS']))
        # Get the object type
        object_type = inference['TYPE']
        # Crop the image
        np_cropped_img = np_img[top:bottom + 1, left:right + 1, :]
        # Get the subdirectory where the image will be saved. This will also be used
        # for the gathering statistics and when generating the final dataset.
        rel_subdir = self.os_path.join(
          str(object_type),
          source_name,
        )
        if current_interval is not None:
          rel_subdir = self.os_path.join(rel_subdir, current_interval)
        # endif interval given
        fname = f'{object_type}_{self.count_saved_by_object_type[object_type]:06d}_{self.now_str(short=True)}'
        fname = self.os_path.join(rel_subdir, fname)
        # Save the image
        success = self.diskapi_save_image_output(
          image=np_cropped_img,
          filename=f'{fname}.jpg',
          subdir=self.raw_dataset_rel_path
        )
        if success:
          self.raw_dataset_set.add(fname)
        else:
          rel_subdir = None
        # endif successful save
        self.stop_timer('crop_and_save_one_img')
      except Exception as e:
        self.stop_timer('crop_and_save_one_img')
        if self.cfg_log_failed_saves:
          self.P(f'Failed save from {source_name} in {current_interval} with exception {e}', color='r')
        return None
      return rel_subdir

    def crop_and_save_all_images(self):
      """
      Crop and save all images from the dataapi.
      Retrieves the images and inferences from the dataapi and saves the images based on the inferences.
      This can also enforce a balanced dataset if configured accordingly.
      Returns
      -------

      """
      dct_imgs = self.dataapi_images()

      for i, np_img in dct_imgs.items():
        # Get the inferences and input metadata
        lst_inferences = self.dataapi_specific_image_instance_inferences(idx=i)
        inp_metadata = self.dataapi_specific_input_metadata(idx=i)
        # Get the source name and current interval
        source_name = inp_metadata.get('SOURCE_STREAM_NAME', self.dataapi_stream_name())
        current_interval = inp_metadata.get('current_interval', 'undefined')
        # Save the source name. Done in order to stop the source when the data gathering is finished.
        # For the moment this is done when the labelling is done. In the future it will be done when the
        # data gathering is finished.
        self.source_names.add(source_name)
        for infer in lst_inferences:
          # Get the object type
          object_type = infer.get('TYPE', None)
          if object_type is None:
            self.P("Inference did not return 'TYPE', cannot save the crop", color='r')
            continue
          # endif no object type
          # Check if the object type can be saved
          if not self.enough_data() and self.check_if_can_save_object_type(object_type):
            # Save the image
            subdir = self.crop_and_save_one_img(
              np_img=np_img, inference=infer, source_name=source_name, current_interval=current_interval
            )
            # In case the image was saved, update the statistics
            if subdir is not None:
              self.count_saved_by_object_type[object_type] += 1
              self.dataset_stats[subdir] += 1
              self.total_image_count += 1
              # Increment the raw dataset counter and maybe backup the state
              self.raw_dataset_updates_count += 1
              self.dataset_stats_increment += 1
              self.maybe_persistence_save()
            # endif successfully saved
          # endif allowed to save
        # endfor inferences
      # endfor images
      return
  """END SAVE SECTION"""

  def _process(self):
    # Step 1: Start the voting process if not already started in case
    # it was requested by a command.
    self.maybe_start_voting()
    # TODO: test data gathering when other pipelines are running
    # We should only gather from the specified streams
    # Step 2: If gathering not finished and input available, crop and save all images.
    if self.need_data() and self.dataapi_received_input():
      # This delay is done in order to avoid saving images too often
      # While allowing the plugin to solve received requests.
      if self.last_save_time is None or self.time() - self.last_save_time >= self.cfg_save_period:
        self.crop_and_save_all_images()
      # endif save period passed
    # endif need data and input available

    payload = None
    # Step 3: If report period passed, generate progress payload
    self.maybe_report_status(add_crop_speed=True)

    # Step 4: If the voting started, there is no need
    # for this plugin or the data sources to continue.
    if self.voting_status == 2:
      if self.postpone_counter < self.cfg_postpone_threshold:
        self.P(f'Postponing the labelling finish for the upload command to be received'
               f'({self.postpone_counter + 1}/{self.cfg_postpone_threshold})')
        self.postpone_counter += 1
      elif not self.finished:
        self.stop_gather()
      # endif postpone counter not reached
    # endif voting started

    return payload



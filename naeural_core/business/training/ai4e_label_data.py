from naeural_core.business.base import BasePluginExecutor as BaseClass
from naeural_core.business.mixins_libs.ai4e_mixin import _Ai4eMixin, AI4E_MIXIN_CONFIG
from os import walk


_CONFIG = {
  **BaseClass.CONFIG,
  **AI4E_MIXIN_CONFIG,
  # TODO: add job initiator here and in crop_data
  'ALLOW_EMPTY_INPUTS': True,
  'RUN_WITHOUT_IMAGE': True,
  "FULL_DEBUG_LOG": True,
  "CLASSES": None,
  'TRAIN_SIZE': 0.8,
  'MIN_DECISION_PRC': 0.5,

  "LABEL_BACKUP_PERIOD": 10,
  'CLOUD_PATH': 'DATASETS/',
  'LABEL_UNTIL': None,

  'VALIDATION_RULES': {
    **BaseClass.CONFIG['VALIDATION_RULES'],
    **AI4E_MIXIN_CONFIG['VALIDATION_RULES'],
  },
}


class Ai4eLabelDataPlugin(BaseClass, _Ai4eMixin):
  _CONFIG = _CONFIG

  def debug_log(self, msg, **kwargs):
    if self.cfg_full_debug_log:
      self.P(msg, **kwargs)
    return

  def on_init(self):
    super(Ai4eLabelDataPlugin, self).on_init()
    self.accepting_votes = False
    self.started_label_finish = False
    self.label_updates_count = 0
    self.raw_dataset_path = None
    self.raw_dataset_rel_path = None
    self.final_dataset_rel_path = self.os_path.join('final_datasets', self.cfg_objective_name)
    self.final_dataset_full_path = self.os_path.join(self.get_output_folder(), self.final_dataset_rel_path)

    self.filenames = set()
    # All the votes in format
    # {'filename1': {'class1': nr_votes1}}
    self.filename_votes = {}
    # All the votes in format
    # {'worker_id': {'filename1': {'class1': nr_votes1}}}
    self.worker_votes = {}
    # All the decisions in format
    # {'filename1': 'class1'}
    # The above means that for filename1 the class1 was decided by the majority of votes
    self.filename_decisions = {}
    # All the stats for the decisions
    # {'class1': count1}
    # The above means that class1 was decided for count1 files
    self.decision_stats = self.default_classes_stats()
    self.done_labeling = False
    self.publish_status = 0
    self.train_subdirs = {}
    self.train_subdir_stats = {
      'train': 0,
      'dev': 0,
    }
    self.maybe_load_previous_state()
    return

  def maybe_load_previous_state(self):
    self.debug_log('Loading previous state')
    obj = self.persistence_serialization_load()
    if obj is None:
      return
    self.debug_log(f'Loaded previous state: {obj}')
    self.accepting_votes = obj.get('accepting_votes', self.accepting_votes)
    self.started_label_finish = obj.get('started_label_finish', self.started_label_finish)
    self.label_updates_count = obj.get('label_updates_count', self.label_updates_count)
    self.raw_dataset_path = obj.get('raw_dataset_path', self.raw_dataset_path)
    self.raw_dataset_rel_path = obj.get('raw_dataset_rel_path', self.raw_dataset_rel_path)

    self.filenames = obj.get('filenames', self.filenames)
    self.filename_votes = obj.get('filename_votes', self.filename_votes)
    self.worker_votes = obj.get('worker_votes', self.worker_votes)
    self.filename_decisions = obj.get('filename_decisions', self.filename_decisions)
    self.decision_stats = obj.get('decision_stats', self.decision_stats)
    self.done_labeling = obj.get('done_labeling', self.done_labeling)
    self.publish_status = obj.get('publish_status', self.publish_status)
    self.train_subdirs = obj.get('train_subdirs', self.train_subdirs)
    self.train_subdir_stats = obj.get('train_subdir_stats', self.train_subdir_stats)
    return

  def maybe_save_current_state(self, force=False):
    to_save = force
    if self.label_updates_count != 0 and self.label_updates_count % self.cfg_label_backup_period == 0:
      to_save = True
    if not to_save:
      return
    self.debug_log('Saving current state')
    obj = {
      'accepting_votes': self.accepting_votes,
      'started_label_finish': self.started_label_finish,
      'label_updates_count': self.label_updates_count,
      'raw_dataset_path': self.raw_dataset_path,
      'raw_dataset_rel_path': self.raw_dataset_rel_path,

      'filenames': self.filenames,
      'filename_votes': self.filename_votes,
      'worker_votes': self.worker_votes,
      'filename_decisions': self.filename_decisions,
      'decision_stats': self.decision_stats,
      'done_labeling': self.done_labeling,
      'publish_status': self.publish_status,
      'train_subdirs': self.train_subdirs,
      'train_subdir_stats': self.train_subdir_stats,
    }
    self.persistence_serialization_save(obj)
    return

  def delay_process(self):
    # Raw dataset initialized and still accepting labels
    if not self.started_label_finish:
      return False
    # Raw dataset initialized, no longer accepting labels
    # due to finalizing the label files
    if not self.done_labeling:
      return True
    # Final dataset ready to be published
    return False

  def get_plugin_loop_resolution(self):
    return self.cfg_plugin_loop_resolution if not self.delay_process() else 1 / 30


  def maybe_read_filenames(self, root_path=None):
    if len(self.filenames) == 0 and root_path is not None:
      start_idx = len(root_path) + len(self.os_path.sep)
      # TODO: maybe make diskapi_walk for safety instead of plain walk
      for (dirpath, dirnames, filenames) in walk(root_path):
        rel_path = dirpath[start_idx:]
        for filename in filenames:
          self.filenames.add(self.os_path.join(rel_path, self.os_path.splitext(filename)[0]))
        # endfor filenames
      # endfor walk
      self.raw_dataset_path = root_path
      output_absolute_path = self.os_path.abspath(self.log.get_data_folder())
      root_path_absolute = self.os_path.abspath(root_path)
      self.raw_dataset_rel_path = self.os_path.relpath(root_path_absolute, output_absolute_path)
      self.accepting_votes = True
      self.maybe_save_current_state(force=True)
    # endif no filenames
    return

  def get_present_workers(self):
    # TODO: implement
    return 0

  @property
  def dataset_object_name_final(self):
    return self.os_path.join(self.cfg_cloud_path, self.cfg_objective_name + '.zip')

  def get_job_status(self):
    status = "Publishing"
    if len(self.filenames) > 0:
      status = "Labeling"
    if self.done_labeling:
      status = "Done Labeling"
    if self.publish_status != 0:
      status = "Publishing labels"
    return status

  def get_training_subdir(self):
    """
    Get the training subdirectory based on the configured TRAIN_SIZE.
    Returns
    -------
    str, the training subdirectory
    """
    if (self.train_subdir_stats['train'] * self.train_subdir_stats['dev']) == 0:
      if self.train_subdir_stats['train'] == 0:
        return 'train'
      return 'dev'
    # endif no files in train or dev
    train_size = self.cfg_train_size
    train_size = max(0.0, min(1.0, train_size))
    choice = self.np.random.choice([0, 1], p=[1 - train_size, train_size])
    train_subdir = 'dev' if choice == 0 else 'train'
    return train_subdir

  def default_classes_stats(self):
    return {
      k: 0 for k in self.get_ds_classes().keys()
    }

  def maybe_create_additional_file(self):
    dataset_info_path = self.os_path.join(self.final_dataset_rel_path, 'ADDITIONAL.json')
    if self.os_path.exists(dataset_info_path):
      return
    classes_data = {
      'classes': self.get_ds_classes(),
      'name': self.cfg_objective_name,
    }
    classes_path = self.diskapi_save_json_to_output(
      dct=classes_data,
      filename=dataset_info_path,
    )
    return

  """VOTE SECTION"""
  if True:
    def maybe_register_filename(self, filename: str, votes_dict: dict=None):
      if votes_dict is None:
        votes_dict = self.filename_votes
      if filename not in votes_dict.keys():
        votes_dict[filename] = self.default_classes_stats()
      return

    def maybe_register_worker(self, worker_id):
      if worker_id not in self.worker_votes.keys():
        self.worker_votes[worker_id] = {}
      return self.worker_votes[worker_id]

    def copy_datapoint_to_final_dataset(self, filename):
      raw_path = self.os_path.join(self.raw_dataset_path, f'{filename}.jpg')
      train_subdir = self.get_training_subdir()
      final_path = self.os_path.join(
        self.final_dataset_full_path, train_subdir, f'{filename}.jpg'
      )
      self.debug_log(f'Copying {raw_path} to {final_path}')
      self.train_subdirs[filename] = train_subdir
      self.train_subdir_stats[train_subdir] += 1
      self.diskapi_copy_file(
        src_path=raw_path,
        dst_path=final_path
      )
      return

    def remove_datapoint_from_final_dataset(self, filename):
      train_subdir = self.train_subdirs[filename]
      final_path = self.os_path.join(
        self.final_dataset_full_path, train_subdir, f'{filename}.jpg'
      )
      del self.train_subdirs[filename]
      self.debug_log(f'Removing {final_path}')
      self.train_subdir_stats[train_subdir] -= 1
      self.diskapi_delete_file(final_path)
      return

    def maybe_add_decision(self, filename, decision):
      if filename not in self.filename_decisions.keys():
        # The filename did not have a decided label before
        # Copy it to the final dataset
        self.copy_datapoint_to_final_dataset(filename)
      else:
        # The filename had a decided label before.
        # Remove it from the statistics in case the decision changes.
        self.decision_stats[self.filename_decisions[filename]] -= 1
      # endif filename had a decided label before
      # Add the new decision to the statistics
      self.filename_decisions[filename] = decision
      self.decision_stats[decision] += 1
      return

    def maybe_remove_decision(self, filename):
      if filename not in self.filename_decisions.keys():
        # The filename does not have a decided label so nothing to do.
        return
      # endif filename had a decided label
      # The filename had a decided label, so it will be removed from the final dataset.
      self.remove_datapoint_from_final_dataset(filename)
      decision = self.filename_decisions[filename]
      self.decision_stats[decision] -= 1
      del self.filename_decisions[filename]
      return

    def maybe_update_decision(self, filename):
      if filename not in self.filename_votes.keys():
        return
      self.debug_log(f'Updating decision for {filename}')
      votes = self.filename_votes[filename]
      max_votes = max(votes.values())
      total_votes = sum(votes.values())
      # If the minimum percentage is 0.5 the decision is made if the max votes are at least 50% + 1 of the total votes
      is_decided = max_votes >= int(total_votes * self.cfg_min_decision_prc + 1)
      decision = [k for k, v in votes.items() if v == max_votes][0]
      # If the label of the filename is decided
      self.debug_log(f'[{is_decided=}]Decision for {filename} is {decision}')
      if is_decided:
        self.maybe_add_decision(filename, decision)
      else:
        self.maybe_remove_decision(filename)
      # endif is decided
      max_votes_classes = [k for k, v in votes.items() if v == max_votes]
      if len(max_votes_classes) == 1:
        self.filename_decisions[filename] = max_votes_classes[0]
        self.decision_stats[max_votes_classes[0]] += 1
      return

    def count_vote(self, filename, label, worker_id):
      """
      Method for counting a vote for a filename and label and updating the stats.
      Parameters
      ----------
      filename : str, the filename
      label : str, the label
      worker_id : str, the worker id

      Returns
      -------
      bool : True if the vote was counted, False otherwise
      """
      # Filename does not exist in the raw dataset, do nothing
      if filename not in self.filenames:
        return False
      # Label does not exist in the dataset classes, do nothing
      if label not in self.get_ds_classes().keys():
        return False
      # If the worker is seen for the first time, its statistics are initiated
      worker_votes = self.maybe_register_worker(worker_id)
      # If the filename is seen for the first time, its statistics are initiated
      self.maybe_register_filename(filename)
      # If the filename is seen for the first time by this worker, its statistics are initiated
      self.maybe_register_filename(filename, worker_votes)
      self.filename_votes[filename][label] += 1
      worker_votes[filename][label] += 1
      self.maybe_update_decision(filename)
      return True

    def finalize_labels(self):
      if self.done_labeling:
        return
      self.P("Finalizing labels", color='g')
      self.accepting_votes = False
      self.started_label_finish = True
      self.maybe_save_current_state(force=True)
      self.P('Saving the labels according to the majority votes', color='g')
      for filename, decision in self.filename_decisions.items():
        train_subdir = self.train_subdirs.get(filename)
        if train_subdir is None:
          continue
        label_subdir = self.os_path.join(self.final_dataset_rel_path, train_subdir)
        label_path = self.os_path.join(label_subdir, f'{filename}.txt')
        if self.os_path.exists(label_path):
          continue
        self.diskapi_save_file_output(
          data=decision,
          filename=f'{filename}.txt',
          subdir=label_subdir
        )
      # endfor filename, decision
      self.done_labeling = True
      self.maybe_save_current_state(force=True)
      return
  """END VOTE SECTION"""

  """COMMAND SECTION"""
  if True:
    def label_until_passed(self):
      label_until = self.cfg_label_until
      if label_until is None:
        return False
      return self.datetime.fromtimestamp(label_until) < self.datetime.now()

    def maybe_handle_datapoint_label(self, datapoint: dict, worker_id: str):
      """
      Method for handling the vote from a worker_id about a datapoint.
      The datapoint has to contain both the filename and the label.
      Parameters
      ----------
      datapoint : dict, the datapoint to label
      worker_id : str, the worker id

      Returns
      -------

      """
      # If the voting is done, no more votes are accepted.
      if not self.accepting_votes or self.label_until_passed():
        return
      # No datapoint is present, nothing is done.
      if datapoint is None:
        return
      datapoint = {
        k.upper(): v for k, v in datapoint.items()
      }
      filename = datapoint.get('FILENAME', None)
      label = datapoint.get('LABEL', None)
      # No filename or label is present, nothing is done.
      if label is None or filename is None:
        return
      # Increment the label updates count
      self.label_updates_count += 1
      self.count_vote(filename, label, worker_id)
      return

    def filename_to_path(self, filename):
      return self.os_path.join(self.raw_dataset_path, filename)

    def process_request(self, data, raw=True, filename=None):
      request_id = data.get('REQUEST_ID')
      if filename is None:
        filenames = list(self.filenames) if raw else list(self.filename_votes.keys())
        filename = self.np.random.choice(filenames) if len(filenames) > 0 else None
      # endif filename not provided
      if filename is None:
        response_kwargs = {
          'request_id': request_id,
          'is_status': False,
          'status': 'No data available',
        }
        self.add_payload_by_fields(**response_kwargs)
        return
      img = self.diskapi_load_image(
        folder='data',
        filename=f'{filename}.jpg',
        subdir=self.raw_dataset_rel_path,
      )
      response_kwargs = {
        'request_id': request_id,
        'is_status': False,
        'img': img,
        'sample_filename': filename,
        'file_path': self.filename_to_path(filename)
      }
      if not raw:
        response_kwargs['votes'] = self.filename_votes.get(filename, {})
      self.add_payload_by_fields(**response_kwargs)
      return

    def publish_dataset(self):
      self.P(f'Received request for publishing dataset.')
      self.publish_status = 1
      self.maybe_save_current_state(force=True)
      return

    def on_command(self, data: dict, **kwargs):
      is_sample_datapoint_request = data.get('SAMPLE_DATAPOINT', False)
      is_sample_request = data.get('SAMPLE', False)
      is_filename_request = data.get('FILENAME_REQUEST', False)
      # In case of data request, payload with random filename is sent.
      if is_sample_request:
        self.process_request(data)
        return
      # endif data request
      # In case of sample request, payload with random filename, data and votes are sent
      if is_sample_datapoint_request:
        self.process_request(data, raw=False)
        return
      # endif sample request
      # In case of filename request, payload with data and votes
      if is_filename_request:
        self.process_request(data, raw=False, filename=data.get('FILENAME'))
      # endif filename request
      self.maybe_create_additional_file()
      datapoint = data.get('DATAPOINT', None)
      worker_id = data.get('WORKER_ID', None)
      self.maybe_handle_datapoint_label(datapoint, worker_id)

      finish_labeling = data.get('FINISH_LABELING', False)
      if finish_labeling:
        self.finalize_labels()
      # endif finish_labeling
      publish_dataset = data.get('PUBLISH', False)
      if publish_dataset:
        self.publish_dataset()
      return
  """END COMMAND SECTION"""

  def get_status_payload_additional_kwargs(self, **kwargs):
    return {
      'worker_count': self.get_present_workers(),
      'total_files_voted': len(self.filename_votes.keys()),
      'total_files_decided': len(self.filename_decisions.keys()),
      'decided_stats': self.decision_stats,
    }

  def maybe_publish_final(self):
    if self.publish_status == 1:
      if self.done_labeling:
        config = {
          'NAME': f'upload_final_{self.get_instance_id()}',
          'TYPE': 'VOID',
          'PLUGINS': [{
            'SIGNATURE': 'minio_upload_dataset',
            'INSTANCES': [{
              'INSTANCE_ID': self.get_instance_id(),
              'DATASET_OBJECT_NAME': self.dataset_object_name_final,
              'DATASET_LOCAL_PATH': self.final_dataset_full_path,
            }]
          }]
        }
        self.cmdapi_start_pipeline(config=config)
        self.publish_status = 2
      else:
        self.P('Labeling not finished yet, cannot publish the dataset.')
    return

  def maybe_finalize_labels(self):
    if self.done_labeling:
      return
    if self.label_until_passed():
      self.finalize_labels()
    return

  def _process(self):
    self.maybe_report_status()
    ds_status = self.dataapi_struct_data()
    if ds_status is None:
      return
    is_dataset_ready = ds_status.get('dataset_ready', False)
    if not is_dataset_ready:
      return

    self.maybe_finalize_labels()
    self.maybe_publish_final()
    self.maybe_read_filenames(root_path=ds_status.get('dataset_path'))
    return


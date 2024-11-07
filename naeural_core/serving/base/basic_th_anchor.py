"""
This module contains the BasicThAnchor class, which is a base class for comparing images to anchors.
The class is designed to hide as much as possible the lifecycle of the anchors in the serving process.
This class assumes that models have a 2 stage inference process (create embeddings and compare embeddings)
and that the models have the following methods exposed:
- get_embedding(th_image) -> th_embedding
- forward(th_embedding, th_image) -> th_prediction
"""

from naeural_core.serving.base import UnifiedFirstStage as BaseServingProcess
from naeural_core.local_libraries.nn.th.utils import th_resize_with_pad

_CONFIG = {
  **BaseServingProcess.CONFIG,

  'IMAGE_HW': (480, 850),  # values should be taken from the training grid search

  "MODEL_WEIGHTS_FILENAME": None,
  "URL": None,
  'MAX_BATCH_FIRST_STAGE': 10,


  'ANCHOR_RELOAD_PERIOD': 24 * 60 * 60,  # seconds
  'ANCHOR_CHECK_NEWER_PERIOD': 1 * 60,  # seconds
  'ANCHOR_MAX_OLD_TIME': 3 * 24 * 60 * 60,  # seconds
  'DATE_FORMAT': '%Y/%m/%d_%H:%M:%S',
  'DEFAULT_DATE': '1000/10/10_00:00',

  "LOAD_PREVIOUS_SERIALIZATION": True,

  'VALIDATION_RULES': {
    **BaseServingProcess.CONFIG['VALIDATION_RULES'],
  },
}

__VER__ = '0.3.0.0'


class BasicThAnchor(BaseServingProcess):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    self._anchor_images = {}

    self._empty_anchor_embedding = None
    self._last_verbse_load_time = 0
    self._last_check_newer_anchor_time = 0
    self._verbose_load = True
    super(BasicThAnchor, self).__init__(**kwargs)
    return

  def _startup(self):
    msg = super()._startup()
    self._maybe_load_anchors_startup()

    self._empty_image = self.th.zeros(1, 3, *self.cfg_image_hw, dtype=self.th_dtype, device=self.dev)
    self._empty_anchor_embedding = self._get_anchor_embedding(self._empty_image)

    return msg

  def _get_model(self, config):
    """
    This method assumes the usage of torchscript models
    """
    model = self._prepare_ts_model(fn_model=config, post_process_classes=False)
    return model

  def _get_anchor_embedding(self, th_anchor_image):
    """
    Wrapper for the model's get_embedding method.
    Overwrite this method if you want to change the way the embedding is computed.
    """
    return self.th_model.get_embedding(th_anchor_image)

  def _save_anchor(self, unique_instance_identifier, anchor, update_time, metadata):
    """
    Save the anchor for the given instance.

    Parameters
    ----------
    unique_instance_identifier : str
        The unique instance identifier for the anchor.
    anchor : th.Tensor
        The anchor image.
    update_time : str
        The save time of the anchor.
    metadata : dict
        The metadata of the anchor.
    """
    th_transformed_image = self._transform_image(anchor, metadata)
    self._anchor_images[unique_instance_identifier] = {
      'image': th_transformed_image,
      'encoding': self._get_anchor_embedding(th_transformed_image),
      'update_time': update_time,
      'metadata': metadata
    }
    return

  def _save_known_anchor(self, known_anchor_images, unique_instance_identifier):
    """
    Save the known anchor for the given instance.
    Extracts the anchor from the known_anchor_images and saves it to the current anchor_images.

    Parameters
    ----------
    known_anchor_images : dict
        The dictionary containing the known anchor images from previous iteration.
    unique_instance_identifier : str
        The unique instance identifier for the anchor.
    """
    cached_entry = known_anchor_images[unique_instance_identifier]

    self._anchor_images[unique_instance_identifier] = {
      'image': cached_entry['image'],
      'encoding': cached_entry['encoding'],
      'update_time': cached_entry['update_time'],
      'metadata': cached_entry['metadata']
    }
    return

  def _maybe_load_anchors_startup(self):
    """
    This method is called in startup and loads the anchors from the previous serialization.
    """
    if self.cfg_load_previous_serialization:
      saved_data = self.persistence_serialization_load(default={})
      self._maybe_reset_anchors(saved_data)
    # endif
    return

  def _anchor_expired(self, unique_instance_identifier):
    """
    This method checks if the anchor is old enough to be changed.

    Parameters
    ----------
    unique_instance_identifier : str
        The unique instance identifier for which we check the anchor.

    Returns
    -------
    bool
        True if the anchor is old enough to be changed, False otherwise.
    """
    if unique_instance_identifier not in self._anchor_images:
      return True

    # if the reload period is None, we never reload the anchor
    if self.cfg_anchor_reload_period is None:
      return False

    last_recorded_update_time = self.datetime.strptime(
      self._anchor_images[unique_instance_identifier]['update_time'], self.cfg_date_format)
    update_time = self.datetime.now()
    seconds_since_last_update = (update_time - last_recorded_update_time).seconds
    return seconds_since_last_update >= self.cfg_anchor_reload_period

  def _new_anchor_available(self, known_anchor_images, unique_instance_identifier, update_time):
    """
    This method checks if a new anchor is available for an instance.

    Parameters
    ----------
    known_anchor_images: dict
        The dictionary containing the known anchor images from previous iteration.
    unique_instance_identifier : str
        The unique instance identifier for which we check the anchor.
    update_time : datetime
        The time of the new anchor.

    Returns
    -------
    bool
        True if a new anchor is available, False otherwise.
    """
    # if the anchor is not in known, we load it
    if unique_instance_identifier not in known_anchor_images:
      return True

    last_recorded_update_time = self.datetime.strptime(
      known_anchor_images[unique_instance_identifier]['update_time'], self.cfg_date_format)

    # if the anchor is newer than the last recorded update time, we load it
    if (update_time - last_recorded_update_time).seconds > 0:
      return True

    return False


  def _ready_to_check_newer_anchor(self):
    """
    This method checks if it is time to check for newer anchors.

    Returns
    -------
    bool
        True if it is time to check for newer anchors, False otherwise.
    """
    current_time = self.time()
    seconds_since_last_check = (current_time - self._last_check_newer_anchor_time)
    return seconds_since_last_check > self.cfg_anchor_check_newer_period

  def _maybe_reset_anchors(self, saved_data):
    """
    This method checks if there are newer anchors available and resets the anchors if needed.

    Parameters
    ----------
    saved_data : dict
        The saved data from the previous serialization. (The data loaded from disk)
    """

    known_anchor_images = self._anchor_images
    self._anchor_images = {}

    # this method now resets only changes, not all images
    dct_pipeline_anchors_images = saved_data.get(self.ct.ThAnchor.ANCHOR_IMAGES, {})
    dct_pipeline_anchor_metadata = saved_data.get(self.ct.ThAnchor.ANCHOR_METADATA, {})

    for unique_instance_identifier, pipeline_metadata in dct_pipeline_anchor_metadata.items():
      # anchor image does not exist, discard entry
      if unique_instance_identifier not in dct_pipeline_anchors_images:
        continue

      # anchor is too old, discard entry
      str_pipeline_last_update_time = pipeline_metadata.get(self.ct.ThAnchor.ANCHOR_UPDATED_AT, self.cfg_default_date)
      pipeline_last_update_time = self.datetime.strptime(str_pipeline_last_update_time, self.cfg_date_format)
      if self.cfg_anchor_max_old_time is not None and (self.datetime.now() - pipeline_last_update_time).seconds > self.cfg_anchor_max_old_time:
        continue

      # anchor is new, compute embedding and save
      if self._new_anchor_available(known_anchor_images, unique_instance_identifier, pipeline_last_update_time):
        self._save_anchor(
          unique_instance_identifier=unique_instance_identifier,
          anchor=dct_pipeline_anchors_images[unique_instance_identifier],
          update_time=str_pipeline_last_update_time,
          metadata=pipeline_metadata
        )
      # anchor is not new but is known, load from cache
      else:
        self._save_known_anchor(known_anchor_images, unique_instance_identifier)
        
    return

  def _comment_reason_for_spam(self, verbose):
    """
    This method prints a warning if there are no anchors available for any pipeline.

    Parameters
    ----------
    verbose : bool
        True if the warning should be printed, False otherwise.
    """
    bad_pipelines = [
        x for x in self._anchor_images.keys()
        if self._anchor_expired(x)
      ]

    if verbose:
      if len(self._anchor_images) == 0:
        self.P("Warning! No anchors found for any pipeline! This is an expected behavior if the pipeline has just been started...", color='r')

      if len(bad_pipelines) > 0:
        self.P("Warning! Unable to retrieve newer anchors for pipelines {}. Please see plugin warnings for detailed information.".format(
          str(bad_pipelines)), color='r')

    return

  def get_pipeline_name(self, uiid):
    """
    This method extracts the pipeline name from the unique instance identifier.

    Parameters
    ----------
    uiid : str
        The unique instance identifier.

    Returns
    -------
    str
        The pipeline name.
    """
    # unique_instance_identifier = '(pipeline_name, plugin_signature, instance_id)'
    return uiid.split('\'')[1]

  def _check_anchor_update(self, pipeline_names):
    """
    This method checks if the anchors should be updated and updates them if needed.
    """
    can_reset_anchors = (
      len(self._anchor_images) == 0
      or any([self._anchor_expired(x) for x in self._anchor_images.keys()])
      or self._ready_to_check_newer_anchor()
      or any(len([uiid for uiid in self._anchor_images if self.get_pipeline_name(uiid) == pipeline_name]) == 0 for pipeline_name in pipeline_names)
    )
    if can_reset_anchors:
      # possible fail here, should turn off verbosity
      if self.time() - self._last_verbse_load_time > 60:  # seconds
        self._verbose_load = True
        self._last_verbse_load_time = self.time()
      else:
        self._verbose_load = False

      self._comment_reason_for_spam(verbose=self._verbose_load)
      saved_data = self.persistence_serialization_load(default={}, verbose=self._verbose_load)
      self._maybe_reset_anchors(saved_data)
      self._last_check_newer_anchor_time = self.time()
    return

  def _transform_image(self, image, metadata):
    """
    This method transforms the image to the desired format.
    This method outputs an image in RGB format, normalized to [-1, 1].

    Parameters
    ----------
    image : th.Tensor
        The image to be transformed.

    Returns
    -------
    th.Tensor
        The transformed image.
    """
    image, _ = th_resize_with_pad(
      img=image,
      h=self.cfg_image_hw[0],
      w=self.cfg_image_hw[1],
      normalize=True,
      sub_val=127.5,
      div_val=127.5,
      device=self.dev,
      half=self.cfg_fp16
    )

    image = image.unsqueeze(0)
    return image

  def _pre_process(self, inputs):
    """
    Preprocess the inputs and create the inputs for the model.

    Parameters
    ----------
    inputs : dict
        The inputs to be preprocessed.

    Returns
    -------
    dict
        The preprocessed inputs.
    """
    input_images = inputs[self.ct.DATA]
    # TODO: maybe change this key from STREAM_NAME to PIPELINE_NAME
    pipeline_names = inputs[self.ct.PAYLOAD_DATA.STREAM_NAME]

    # check if we need to update the anchors
    self._check_anchor_update(pipeline_names)

    # save a mapping from pipeline name to image
    dct_pipeline_name_image = {pipeline_name: input_image for pipeline_name, input_image in zip(pipeline_names, input_images)}

    input_images = []
    anchors = []
    anchor_load_times = []

    for unique_instance_identifier in self._anchor_images:
      pipeline_name = self.get_pipeline_name(unique_instance_identifier)

      if pipeline_name not in dct_pipeline_name_image:
        # did not receive an input image from this pipeline
        input_images.append(self._empty_image)
        anchors.append(self._empty_anchor_embedding)
        anchor_load_times.append(None)
      else:
        # we have an input image from this pipeline
        metadata = self._anchor_images[unique_instance_identifier]['metadata']
        pipeline_image = dct_pipeline_name_image[pipeline_name]

        input_images.append(self._transform_image(pipeline_image, metadata))
        anchors.append(self._anchor_images[unique_instance_identifier]['encoding'])
        anchor_load_times.append(self._anchor_images[unique_instance_identifier]['update_time'])
      # endif pipeline_name in dct_pipeline_name_image
    # endfor prepare test_images

    if len(input_images) == 0:
      # if we did not receive input from any pipeline, we will process an empty image
      input_images.append(self._empty_image)
      anchors.append(self._empty_anchor_embedding)
    # endif

    th_images = self.th.cat(input_images, dim=0)
    th_anchor_encodings = self.th.cat(anchors, dim=0)

    self._anchor_load_times = anchor_load_times
    self._pipeline_names = pipeline_names

    prep_inputs = {
      'inputs': {
        'th_image': th_images,
        'th_anchor_embedding': th_anchor_encodings
      }

    }

    return prep_inputs

  def _get_inputs_batch_size(self, inputs):
    """
    We overwrite this method since we have two inputs, not one
    """
    return len(inputs['th_image'])

  def _post_process(self, preds):
    """
    Postprocess the predictions and create the outputs for the model.
    This method can be overwritten to change the way the outputs are created.

    Parameters
    ----------
    preds : th.Tensor
        The predictions from the model.

    Returns
    -------
    list
        The postprocessed outputs.
    """
    preds = preds.detach().cpu().numpy()

    # uiid = unique_instance_identifier
    dct_uiid_pred_index = {uiid: j for j, uiid in enumerate(self._anchor_images.keys())}

    lst = []
    for pipeline_name in self._pipeline_names:
      dct_uiid_preds = {
        self.ct.ThAnchor.ANCHOR_RELOAD_TIME: self.cfg_anchor_reload_period,
      }

      for uiid in self._anchor_images:
        if self.get_pipeline_name(uiid) == pipeline_name:
          dct_uiid_preds[uiid] = {
            self.ct.ThAnchor.PREDICTION: preds[dct_uiid_pred_index[uiid]],
            self.ct.ThAnchor.ANCHOR_UPDATED_AT: self._anchor_load_times[dct_uiid_pred_index[uiid]],
          }
        # endif
      # end for uiid
      lst.append([dct_uiid_preds])
    # end for pipeline_name

    return lst

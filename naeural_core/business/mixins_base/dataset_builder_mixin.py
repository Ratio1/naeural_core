"""
  This mixin can be used in order to automatically create custom datasets
  based on one or more business plugins.
  The plugins function by having inferences as an input and payload or payloads as output.
  To make the dataset this mixin will retrieve data both from the inferences and the payload.

  For reference, we will take the PerimeterIntrusion01 plugin. It is designed to analyze a
  CCTV video stream of a location and report any human that passes through the monitored area
  using a general detector.
  As the input the plugin will have the inferences packed in the following format:
  [
    {
      'PROB_PRC': 0.7, # the confidence of the current detection
      'TLBR_POS': [100, 230, 432, 316], # the localization of the current detection
      'TYPE': 'person', # class name of the current detection
      ...
    },
    ...
  ]
  The plugin will also produce a payload in the following format:
  {
    'IMG': numpy image, # witness image produced by the plugin
    'IMG_ORIG': numpy image, # original image on which the inference was computed
    ...
  }

  In the following we will analyze 3 levels of use for this Mixin.
  1) Beginner:
  For example, let's say we have a plugin, WeaponAssailant02, deployed on a CCTV camera that
  uses firstly a general detector in order to detect all the persons in
  the current frame and then a second model that tells us whether a person has a
  weapon in possession or not.
  This mixin can be used to crop the persons from all the images and save the crops
  along with each cropped image's prediction by the current working model.

  {
    ...
    "PLUGINS": [
        ...
        {
            "INSTANCES": [
                {
                    "INSTANCE_ID": "example dataset",
                    "DATASET_BUILDER": {
                      "DATASET_NAME": "weapon_1",
                      "LABEL_FILE_TEMPLATE": "naeural_core/xperimental/dataset_builder/weapon_label_template.txt"
                      "INFERENCE_MAPPING": {
                        "IS_ARMED": "TYPE"
                      }
                    }
                }
            ],
            "SIGNATURE": "WEAPON_ASSAILANT_02"
        }
        ...
    ],
    ...
  }

  In the above provided template for the label files we have:

  <annotation>
  <size>
    <width>##IMG_WIDTH##</width>
    <height>##IMG_HEIGHT##</height>
  </size>
  <object>
    <label>##IS_ARMED##</label>
  </object>
  </annotation>

"""

# TODO: in future versions in case we are referring to a list in a tag name we should
#  be able to specify the i-th element of that list (example: ##TLBR_POS:0## to specify the top coordinate)
# TODO: maybe not run ds builder at every main loop and add RESOLUTION parameter
# TODO: use witness_period
DEFAULT_DATASET_BUILDER_PARAMS = {
  "LABEL_FILE_TEMPLATE": None,
  "LABEL_TEMPLATE_TYPE": "FILE",  # TODO url
  "LABEL_EXTENSION": 'csv',
  "X_EXTENSION": "jpg",
  # TODO: maybe make x accept a template too (for non-images)
  "IMAGE_CROP": ["TLBR_POS"],  # parameters to use for cropping
  "IMAGE_SAVE": ["IMG_ORIG"],  # images from where to crop? | maybe dictionary in order to
  # specify different cropping coordinates for multiple images

  # in this we will specify additional keys that should be stored
  # from either the inferences or the payload
  "ADDITIONAL_KEYS": [],

  # in this we will specify keys that if missing the datapoint won't be saved
  "MANDATORY_KEYS": [],

  "INFERENCE_MAPPING": {},  # dictionary used if we want to use aliases for certain values from
  # either the model serving or from the payload (maybe we want to reduce the size of the template file)

  "DATASET_NAME": None,  # name of dataset
  "GENERIC_PATH": '',
  "EXPAND_VALUE": 0,  # in case of cropping from any images we can use this parameter to enlarge the crop
  # for example we may want to crop all the persons in a frame, but we want to save more of the scene than just
  # the person's detection box
  # If this parameter is > 1 it will be considered as the absolute value, in pixels, of the expansion
  # If this is <=1 it will be considered as a relative value and we will expand the cropped image by
  # ceil(expand_value * w) on the sides and ceil(expand_value * h) on the top and bottom, where
  # h, w are the height and width of the detection
  "OBJECT_MAX_SAVES": 0,  # this will determine how many appearances of a certain object will be saved
  # if this is 0 all the appearances will be saved
  # if the ai_engine used for the dataset creation does not use the tracker this will not be effective
  "TOTAL_MAX_SAVES": 0,
  "WITNESS_PERIOD": -1,  # if this x >= 0 this mixin will produce at most a witness every x seconds
  "ZIP_PERIOD": 0,  # after how many saved datapoints to archive the saved files in a zip,
  "STATS_UPDATE_PERIOD": 20,  # after how many saved datapoints to update the cached stats of the data saved so far
  # this will be used in order to continue the dataset creation in case of restarts without ignoring the already
  # saved data
  "SAVE_CAP_RESOLUTION": 0,  # from maximum how many frames per second to save datapoints
  "BACKGROUND_PERIOD_SAVE": 0,  # if this is > 0 we will save a background image every x processed frames
}


class _DatasetBuilderMixin(object):
  def __init__(self):
    self.__first_timestamp = None
    self.__dir_cnt = 0
    self.__saved_data_counter = 0
    self.__object_saves = {}
    self._ds_params_inferences = []
    self._ds_params_frame = {}
    self._template_fragments = None
    self._path_elements = None
    self._label_keys = set()
    self._mandatory_keys = set()
    self._last_save_ts = None
    self._is_multilabel = False
    self._background_saves = 0
    super(_DatasetBuilderMixin, self).__init__()
    return

  """VALID SECTION"""
  if True:
    def _valid_label_file_type(self, file_type):
      valid_types = self.ds_consts.VALID_FILE_TYPES
      if file_type not in valid_types:
        self.add_error(
          f"\"LABEL_TEMPLATE_TYPE\" parameter from "
          f"\"DATASET_BUILDER\" should be one of the "
          f"following: {valid_types}, but instead is {file_type}"
        )
      # TODO: validate correct format of the template file
      return

    def _valid_ds_name(self, ds_name):
      if not isinstance(ds_name, str):
        self.add_error(
          f"\"DATASET_NAME\" parameter from \"DATASET_BUILDER\" should be a string, but instead is {type(ds_name)}"
        )
      return

    def _valid_period(self, period_key, period_value, strict=False):
      if not isinstance(period_value, (int, float)):
        self.add_error(
          f"\"{period_key}\" parameter from \"DATASET_BUILDER\" should be an integer or a float,"
          f" but instead is {type(period_value)}"
        )
      else:
        value_check = period_value <= 0 if strict else period_value < 0
        requirement = 'strictly positive' if strict else 'positive'
        if value_check:
          self.add_error(
            f"\"{period_key}\" parameter from \"DATASET_BUILDER\" should be a {requirement} number,"
            f" but instead is {period_value}"
          )
        # endif value_check
      return

    def get_dataset_name(self):
      return self.get_dataset_builder_params.get(self.ds_consts.DATASET_NAME)

    def validate_dataset_builder(self):
      self.P('Validating DATASET_BUILDER')
      # this is reset in order to reparse it in case the template file was changed
      self._template_fragments = None
      self._path_elements = None
      ds_builder = self.cfg_dataset_builder
      if ds_builder is None:
        return

      current_params = self.get_dataset_builder_params
      self._valid_label_file_type(current_params.get(self.ds_consts.LABEL_TEMPLATE_TYPE, self.ds_consts.FILE))
      self._valid_ds_name(self.get_dataset_name())
      self._valid_period(period_key='ZIP_PERIOD', period_value=self.get_zip_period)
      self._valid_period(period_key='STATS_UPDATE_PERIOD', period_value=self.get_stats_update_period, strict=True)
      if self.__first_timestamp is None:
        self.__first_timestamp = self.log.now_str(nice_print=False, short=True)
      return
  """END VALID SECTION"""

  """GETTER SECTION"""
  if True:
    @property
    def get_plugin_default_dataset_builder_params(self):
      """
      Method that will be used for the plugins where the dataset builder mixin will be enabled by default
      in order to facilitate the configuration of this mixin without ignoring the default ds builder params
      provided in the plugin default config.
      Returns
      -------

      """
      return {}

    @property
    def get_dataset_builder_params(self):
      ds_builder_params = self.cfg_dataset_builder
      if ds_builder_params is None:
        ds_builder_params = {}
      return {
        **DEFAULT_DATASET_BUILDER_PARAMS,
        **self.get_plugin_default_dataset_builder_params,
        **ds_builder_params
      }

    @property
    def get_label_template_type(self):
      return self.get_dataset_builder_params.get(self.ds_consts.LABEL_TEMPLATE_TYPE)

    @property
    def get_label_file_template(self):
      return self.get_dataset_builder_params.get(self.ds_consts.LABEL_FILE_TEMPLATE)

    @property
    def get_inference_mapping(self):
      return self.get_dataset_builder_params.get(self.ds_consts.INFERENCE_MAPPING, {})

    @property
    def get_object_max_saves(self):
      # string instead of constant in the event we change its name
      return self.get_dataset_builder_params.get("OBJECT_MAX_SAVES", 0)

    @property
    def get_image_save(self):
      # string instead of constant in the event we change its name
      return self.get_dataset_builder_params.get("IMAGE_SAVE", [])

    @property
    def get_image_crop(self):
      # string instead of constant in the event we change its name
      return self.get_dataset_builder_params.get("IMAGE_CROP", [])

    @property
    def get_additional_keys(self):
      # string instead of constant in the event we change its name
      return self.get_dataset_builder_params.get("ADDITIONAL_KEYS", [])

    @property
    def get_mandatory_keys(self):
      return self.get_dataset_builder_params.get("MANDATORY_KEYS", [])

    @property
    def get_generic_path(self):
      return self.get_dataset_builder_params.get("GENERIC_PATH", '')

    @property
    def get_expand_value(self):
      return self.get_dataset_builder_params.get("EXPAND_VALUE", 0)

    @property
    def get_total_max_saves(self):
      # string instead of constant in the event we change its name
      return self.get_dataset_builder_params.get("TOTAL_MAX_SAVES", 0)

    @property
    def get_zip_period(self):
      return self.get_dataset_builder_params.get("ZIP_PERIOD", 0)

    @property
    def get_stats_update_period(self):
      return self.get_dataset_builder_params.get("STATS_UPDATE_PERIOD", 20)

    @property
    def get_background_period_save(self):
      return self.get_dataset_builder_params.get("BACKGROUND_PERIOD_SAVE", 0)

    def get_label_template_lines(self):
      if self.get_label_template_type == self.ds_consts.FILE:
        if self.get_label_file_template is None:
          return []
        with open(self.get_label_file_template, 'r') as temp_in:
          return temp_in.readlines()
      # endif self.cfg_label_template_type == "FILE"

      return []

    def get_x_file_params(self):
      """
      This method will compute a list of tuples of 2 elements that describe what
      keys to use in order to compute and save data for the X files in our dataset.
      The above-mentioned keys will be found in the data provided either by
      the plugin or the model_serving(we will refer to that as dict).
      Can also be further extended by the plugin developer in order to customise
      the extracted params.
      Returns
      -------
        res - list of format
        [
          (SOURCE_KEY1, PROP_KEY1),
          ..
        ]
        where dict[PROP_KEYi] will be used to extract data from dict[SOURCE_KEYi]
      """
      res = []
      sources = self.get_image_save
      props = self.get_image_crop
      for source in sources:
        for prop in props:
          res.append((source, prop))
        # endfor props
      # endfor sources
      return res

    def get_save_cap_resolution(self):
      return self.get_dataset_builder_params.get('SAVE_CAP_RESOLUTION', 0)
  """END GETTER SECTION"""

  def maybe_init_ds_builder_saved_stats(self):
    if self.cfg_dataset_builder is None:
      return
    saved_stats = self.get_saved_stats()
    if saved_stats is not None:
      self.P('Save stats backup found. Loading saved stats for dataset builder mixin')
      self.__dir_cnt = saved_stats.get('dir_cnt', 0)
      self.__saved_data_counter = saved_stats.get('saved_data_counter', 0)
      self.__object_saves = saved_stats.get('object_saves', {})
      self.P(f'Saved stats loaded! Currently on chunk {self.__dir_cnt} after {self.__saved_data_counter} total saves.')
    # endif saved stats found
    return

  def __enough_saves(self):
    return self.get_total_max_saves > 0 and self.__saved_data_counter >= self.get_total_max_saves

  def _maybe_init_template_fragments(self):
    """
    Here we will parse the text from the template label file.
    The text in our template can either be normal text or the
    name of a tag(which will be enclosed in ##).
    Because of this if we will split one fragment of the above-mentioned file
    by '##' we will obtain an array of words indexed from 0 where on
    the even positions we will have normal text and on the odd positions
    we will have the name of a tag.
    In the event that we want to put multiple inference objects in our label
    (for example we want to save the bounding boxes of multiple persons in the same image)
    we will use the separator $$ in order to separate the different fragments of label
    so that we know if a certain sequence of the label will repeat in case of multiple objects.
    Returns
    -------

    """
    # TODO: maybe replace here the aliases from the template file in order to optimize
    if self._template_fragments is None:
      self.P("Init template fragments for dataset builder mixin")
      self._is_multilabel = False
      self._label_keys = set()
      self._mandatory_keys = set(self.get_mandatory_keys)
      template_lines = self.get_label_template_lines()
      lines_str = ''.join(template_lines)
      label_fragments = lines_str.split('$$')
      if len(label_fragments) > 1:
        self._is_multilabel = True
      # endif multiple labels

      self._template_fragments = [
        fragment.split('##')
        for fragment in label_fragments
      ]
      for fragment in self._template_fragments:
        for i, word in enumerate(fragment):
          if i % 2 == 1:
            # odd position in the split line, so we have the name of a tag
            if word.startswith('!'):
              # if the tag starts with '!' it will be considered mandatory (won't be saved if we do not have this value)
              word = word[1:]
              self._mandatory_keys.add(word)
            # endif mandatory key
            self._label_keys.add(word)
      # endfor line in self.template_lines
      for keys_list in [self.get_image_save, self.get_image_crop, self.get_additional_keys]:
        for key in keys_list:
          self._label_keys.add(key)
      # endfor keys_lists
    # endif self._template_fragments is None
    return

  def _maybe_init_path_elements(self):
    """
    Here we will parse the generic path with the purpose of identifying the name tags
    in order to search them both in the inference dictionaries and the payload dictionaries.
    Returns
    -------

    """
    if self._path_elements is None:
      self.P("Init path elements for dataset builder mixin")

      self._path_elements = self.get_generic_path.split('##')
      for i, word in enumerate(self._path_elements):
        if i % 2 == 1:
          # odd position in the split line, so we have the name of a tag
          if word.startswith('!'):
            # if the tag starts with '!' it will be considered mandatory (won't be saved if we do not have this value)
            word = word[1:]
            self._mandatory_keys.add(word)
          # endif mandatory key
          self._label_keys.add(word)
      # endfor i, word in enumerate(self._path_elements)
    # endif self._path_elements is None
    return

  def _init_dataset_builder_params(self):
    # Parameters reset for consistency
    self._ds_params_inferences = []
    self._ds_params_frame = {}
    if self.cfg_dataset_builder is not None:
      self._maybe_init_template_fragments()
      self._maybe_init_path_elements()

    return

  def _maybe_add_original_image(self, dct, img_orig=None):
    """
    Method used to add 'IMG_ORIG' value to the retrieved data dictionary
    in case it is needed and is not already in dct
    Parameters
    ----------
    dct - dictionary of retrieved data
    img_orig - numpy image, image that will be added to the resulted dict if check_img_orig == True
    -if this is not provided the added image will come from self.dataapi_image()

    Returns
    -------

    """
    if dct.get('IMG_ORIG') is None:
      inference_mapping = self.get_inference_mapping
      if 'IMG_ORIG' in self._label_keys or 'IMG_ORIG' in inference_mapping.values():
        dct['IMG_ORIG'] = self.dataapi_image() if img_orig is None else img_orig
      # endif 'IMG_ORIG' necessary
    # endif 'IMG_ORIG' not already in dct
    return dct

  def _extract_data(self, dct, check_img_orig=False, img_orig=None):
    """
    Method for extracting data from either an inference dictionary or a payload dictionary
    with the purpose of further using it for the built dataset.
    Parameters
    ----------
    dct - dictionary of raw information provided either by the inference or by the payload
    check_img_orig - bool, flag that dictates if 'IMG_ORIG' should be in the resulted dictionary
    img_orig - numpy image, image that will be added to the resulted dict if check_img_orig == True
    -if this is not provided the added image will come from self.dataapi_image()

    Returns
    -------
    res - dictionary of the retrieved data
    """
    if dct is None:
      return {}
    res = {
      key: dct.get(
        # in case the current key in self._label_keys is an alias
        # we will try to retrieve the real key from dct
        self.get_inference_mapping.get(key, key)
      )
      for key in self._label_keys
      if self.get_inference_mapping.get(key, key) in dct.keys()
    }
    if 'FULL' in self._label_keys:
      res['FULL'] = 'FULL'
    # endif full image necessary
    if check_img_orig:
      res = self._maybe_add_original_image(res, img_orig=img_orig)
    res[self.consts.TYPE] = dct.get(self.consts.TYPE)
    res[self.consts.TRACK_ID] = dct.get(self.consts.TRACK_ID)
    res[self.consts.APPEARANCES] = dct.get(self.consts.APPEARANCES, 0)

    return res

  def __eligible_object(self, inference):
    """
    Method for determining if the current inference will be saved or not based on the number of appearances.
    Parameters
    ----------
    inference - dictionary describing the current object inference

    Returns
    -------
      True if the inference will be saved
      False otherwise
    """
    track_id = inference.get(self.consts.TRACK_ID)
    if track_id is None or self.get_object_max_saves < 1:
      # the current serving process does not use any tracker, therefore we do not know
      # the current object's identity
      return True
    obj_type = inference.get(self.consts.TYPE)
    if obj_type not in self.__object_saves.keys():
      # first time seeing the current type | also applies if the type will not be provided and obj_type == None
      self.__object_saves[obj_type] = {}
    current_stats = self.__object_saves[obj_type]
    if track_id not in current_stats.keys():
      current_stats[track_id] = 0

    # checking to see if we have a limit for how many times to save
    # an object and if the current object reached that limit
    return current_stats[track_id] < self.get_object_max_saves

  def _maybe_ds_builder_gather_before_process(self, inferences=None):
    """
    Method to process the data from the inferences provided by the serving plugin.

    Parameters
    ----------
    inferences: None or list of inferences
    If None this will take the list of inferences from self.dataapi_image_instance_inferences().
    In case the plugin developer needs to process here a different list of inferences it can
    be done in the process method of the plugin by calling this method and passing the desired
    list of inferences.

    Returns
    -------

    """
    if self.cfg_dataset_builder is None or self.__enough_saves():
      return
    if inferences is None:
      inferences = self.dataapi_image_plugin_inferences()

      if inferences is None:
        return
      # endif secondary check
    # endif inferences provided

    # Reset this in case it is being called for the second time in the plugin
    self._ds_params_inferences = []
    for inf in inferences:
      if self.__eligible_object(inf):
        curr_dict = self._extract_data(inf)
        self._ds_params_inferences.append(curr_dict)
    # endfor inf in inferences
    return

  def __maybe_gather_from_payload(self, payload):
    """
    Method for gathering data from a certain payload if it was not used before
    Parameters
    ----------
    payload

    Returns
    -------

    """
    if not vars(payload).get('_P_DATASET_BUILDER_USED', False) and not vars(payload).get('IS_NO_DATA_ALERT', False):
      self._ds_params_frame = self._extract_data(vars(payload), check_img_orig=True)
      vars(payload)['_P_DATASET_BUILDER_USED'] = True
    # endif unused payload
    return

  def _maybe_ds_builder_gather_after_process(self, payload=None):
    """
    # TODO: discuss strategy for multiple payloads
    Method to process the data from the provided payload.
    If not payload is provided the last payload provided by the process method will be used.

    Parameters
    ----------
    payload - GeneralPayload, result of an iteration of a certain plugin

    Returns
    -------

    """
    if self.cfg_dataset_builder is None or self.__enough_saves():
      return

    if payload is None:
      # This is done in order to keep the values of the last payload
      # but in case the last payload does not have a specific field
      # we also search it in the previous available payloads
      for payload in self.payloads_deque:
        self.__maybe_gather_from_payload(payload=payload)
      # endfor payloads

      if self._payload is not None:
        self.__maybe_gather_from_payload(payload=self._payload)
    else:
      self.__maybe_gather_from_payload(payload=payload)
    # endif payload provided
    return

  def _process_saved_data(self, data):
    """
    Method present in order to allow the user to perform custom actions on the data
    that will be saved for our current dataset.
    Parameters
    ----------
    data - dictionary of data from both model serving and payload

    Returns
    -------
    res - dictionary with the processed data
    """
    return data

  def check_mandatory_keys(self, data, mandatory_keys=None):
    """
    Method used for checking if a certain dictionary has all the mandatory keys or not
    Parameters
    ----------
    data - dictionary
    mandatory_keys - list of mandatory keys

    Returns
    -------

    """
    if mandatory_keys is None:
      mandatory_keys = self._mandatory_keys
    # endif provided mandatory_keys
    for key in mandatory_keys:
      if data.get(key) is None:
        return False
      # endif mandatory key present in data dictionary
    # endfor mandatory keys
    return True

  def _process_saved_data_multilabel(self, data, inferences):
    """
    Method present in order to allow the user to perform custom actions on the data
    that will be saved for our current dataset in case of multiple inferences in the same label.
    By default, this method will apply _process_saved_data on each inference and then merge the results.
    Parameters
    ----------
    data - dictionary of data from payload
    inferences - list of inferences from which we will extract the data for the current fragment

    Returns
    -------
    res - (new_data, new_inferences), where:
    new_data - dictionary with the processed data from the payload
    new_inferences - list of dictionaries with the processed data from the inferences
    """
    processed_data_list = [self._process_saved_data({**data, **inf}) for inf in inferences]
    new_inferences = []
    new_data = data
    for i, processed_data in enumerate(processed_data_list):
      new_inference = {
        **processed_data,
        **inferences[i]
      }
      new_data = {
        key: processed_data.get(key, val)
        for key, val in data.items()
      }
      new_inferences.append(new_inference)
    # endfor processed_data_list
    return new_data, new_inferences

  def __process_template_fragment(self, fragment, data):
    """
    Method for converting fragment of template file in a fragment for specific data point.
    Parameters
    ----------
    fragment - list of strings that define a fragment from the template file
    The strings from fragment will be as follows:
    fragment[2 * k] - normal text from the template file
    fragment[2 * k + 1] - tag describing text from the template file
    data - dictionary of data from both model serving and payload

    Returns
    -------
      res - concatenation of the converted strings contained in line
    """
    return ''.join([
      str(
        data.get(
          word if not word.startswith('!') else word[1:],  # in case our current word is an alias we will decode it
          # using the mapping provided in the "INFERENCE_MAPPING" parameter of the dataset builder
          '_'  # in case we do not have the desired data in our dictionary we will print _ instead
        )
      ) if i % 2 == 1 else word
      for i, word in enumerate(fragment)
    ])

  def __process_repeatable_template_fragment(self, fragment, data, inferences):
    """
    Method for converting fragment of template file in a fragment for specific data point
    in case we will have to repeat the fragment for multiple inferences.
    Parameters
    ----------
    fragment - list of strings that define a fragment from the template file
    The strings from fragment will be as follows:
    fragment[2 * k] - normal text from the template file
    fragment[2 * k + 1] - tag describing text from the template file
    data - dictionary of data from both model serving and payload
    inferences - list of inferences from which we will extract the data for the current fragment

    Returns
    -------
      res - concatenation of the converted strings contained in line
    """
    return ''.join([
      self.__process_template_fragment(
        fragment=fragment,
        data={
          **data,
          **inf
        }
      )
      for inf in inferences
    ])

  def _generate_label_file_text(self, data, inferences=None):
    """
    Method for generating the content in the label file based on the text lines
    in the template file and the specific data for the current datapoint.
    Parameters
    ----------
    data - dictionary of data from both model serving and payload
    inferences - list of inferences from which we will extract the data
    for the current fragment in case of multiple inferences in the same label

    Returns
    -------
      res - concatenation of the processed template lines using specific values
    """
    res = ''.join([
      self.__process_template_fragment(fragment, data)
      if i % 2 == 0 else
      self.__process_repeatable_template_fragment(fragment, data, inferences)
      for i, fragment in enumerate(self._template_fragments)
    ])
    return res

  def _compute_saved_x(self, source, used_prop):
    """
    Method used for computing the saved data used for the X files.
    By default, source will have to be an image and used_prop either
    the string 'FULL' or an array of 4 values that will be considered
    the top, left, bottom, right coordinates of the desired crop from the source.
    The plugin user can customize this however he needs based on its plugin.
    Parameters
    ----------
    source - source of data
    used_prop - property used to extract data from the source

    Returns
    -------
    saved_data - data that will be saved
    """
    if used_prop == 'FULL':
      return source
    t, l, b, r = used_prop
    expand = self.get_expand_value
    if expand > 0:
      if expand > 1:
        expand = int(expand)
        t, l, b, r = max(t - expand, 0), max(l - expand, 0), b + expand, r + expand
      else:
        h = b - t
        w = r - l
        exp_h = int(self.np.ceil(h * expand))
        exp_w = int(self.np.ceil(w * expand))
        t, l, b, r = max(t - exp_h, 0), max(l - exp_w, 0), b + exp_h, r + exp_w
    return source[t: b, l: r]

  def _save_x_method(self, data_dict, subdir, fn, source_key, prop_key):
    """
    Method used for saving data for X file based on its source and the used property.
    Can be customised by the plugin developer.
    Parameters
    ----------
    data_dict - dictionary of data from both model serving and payload
    fn - name of the saved file
    source_key - key from data_dict indicating value used as data source
    prop_key - key from data_dict indicating value used for extracting data from the source
    Returns
    -------
    True if saved, False otherwise
    """
    source = data_dict.get(source_key)
    used_prop = data_dict.get(prop_key)
    if source is not None and used_prop is not None:
      try:
        saved_data = self._compute_saved_x(source=source, used_prop=used_prop)
        self.diskapi_save_image_output(
          image=saved_data,
          filename=fn,
          subdir=subdir,
          extension=self.get_dataset_builder_params.get(self.ds_consts.X_EXTENSION, '')
        )
      except Exception as e:
        return False
    # endif found source and used_prop
    return True

  """PATH HELPERS SECTION"""
  if True:
    def _generate_file_name(self, data):
      """
      Method for generating the file name of the current datapoint.
      Parameters
      ----------
      data - dictionary of data from both model serving and payload

      Returns
      -------
      fn - filename used for the label and input files
      """
      track_id = data.get(self.consts.TRACK_ID)
      obj_type = data.get(self.consts.TYPE)
      if track_id is None:
        current_fn = f'{"__".join(self.unique_identification)}_{self.__saved_data_counter}'
      else:
        # the following checks should not be necessary but are here for safety reasons
        if obj_type not in self.__object_saves.keys():
          self.__object_saves[obj_type] = {}
        curr_saves = self.__object_saves[obj_type]
        if track_id not in curr_saves.keys():
          curr_saves[track_id] = 0

        n_saves = curr_saves[track_id]
        type_prefix = '' if obj_type is None else f'_{obj_type}'
        current_fn = f'{"__".join(self.unique_identification)}{type_prefix}_{track_id}_{n_saves}_{self.__saved_data_counter}'
        curr_saves[track_id] += 1
      # endif track_id is None

      return current_fn

    def parse_generic_path(self, data):
      """
      Method used to generate the path where the current datapoint will be saved at.
      Parameters
      ----------
      data - dictionary of data from both model serving and payload

      Returns
      -------

      """
      return ''.join([
        word if i % 2 == 0 else str(data.get(word, 'unknown'))
        for i, word in enumerate(self._path_elements)
      ])

    def _generate_subdir(self, data):
      """
      Method for generating datapoint subdirectory in case needed
      Parameters
      ----------
      data - dictionary of data from both model serving and payload

      Returns
      -------
      subdir - subdirectory in the dataset
      """
      return self.parse_generic_path(data=data)

    def __get_ds_dir_path(self):
      """
      Private method for accessing the name of the saving directory.
      Returns
      -------
      dir - name of the directory where all the data will be found.
      """
      return self.os_path.join('dataset', self.get_dataset_name())

    def __get_current_chunk_path(self):
      """
      Private method for accessing the name of the current saving directory(this is used for zipping
      files without worrying about the loss of files saved while the archived files are deleted).
      Returns
      -------
      dir - name of the directory where the current chunk of data will be found.
      """
      return f'{self.get_dataset_name()}_{self.__dir_cnt}'

    def __get_subdir_path(self, data):
      """
      Private method for generating the full path of the current datapoint's saved files
      Parameters
      ----------
      data - dictionary of data from both model serving and payload

      Returns
      -------

      """
      custom_subdir = str(self._generate_subdir(data=data))
      subdir = self.os_path.join(self.__get_ds_dir_path(), self.__get_current_chunk_path())
      if len(custom_subdir) > 0:
        subdir = self.os_path.join(subdir, custom_subdir)

      return subdir

    def get_saved_stats(self):
      """
      Method used for retrieving the saved stats of the dataset builder.
      Returns
      -------
      stats_dict - dictionary containing the saved stats
      """
      stats_dict = self.diskapi_load_pickle_from_output(
        filename='saved_stats.pkl',
        subfolder=self.__get_ds_dir_path(),
      )
      return stats_dict

    def update_saved_stats(self):
      """
      Method used for updating the saved stats of the dataset builder.
      Returns
      -------

      """
      self.P(
        f'Updating saved stats. Current saved datapoints: {self.__saved_data_counter} and current chunk: {self.__dir_cnt}'
      )
      stats_dict = {
        'saved_data_counter': self.__saved_data_counter,
        'dir_cnt': self.__dir_cnt,
        'object_saves': self.__object_saves
      }
      self.diskapi_save_pickle_to_output(
        obj=stats_dict,
        filename='saved_stats.pkl',
        subfolder=self.__get_ds_dir_path(),
      )
      return

    def _archive_and_upload_current_chunk(self):
      """
      Method for archiving and uploading the current chunk of data.
      Returns
      -------

      """
      curr_dir_name = self.__get_current_chunk_path()
      self.P(
        f'Archiving and uploading current data chunk ({curr_dir_name})[containing {(self.__saved_data_counter - 1) % self.get_zip_period + 1} datapoints]')
      curr_zip_name = f'{self.__first_timestamp}_{self.__get_current_chunk_path()}.zip'
      save_path = self.os_path.join(self.log.get_target_folder(target='output'), self.__get_ds_dir_path())
      dir_path = self.os_path.join(save_path, curr_dir_name)
      zip_path = self.os_path.join(save_path, curr_zip_name)

      self.diskapi_zip_dir(dir_path=dir_path, zip_path=zip_path)
      self.diskapi_delete_directory(dir_path=dir_path)
      self.upload_file(
        file_path=zip_path,
        target_path=self.os_path.join(
          self.get_dataset_name(),
          curr_zip_name
        ),
        bucket_name='ds-builder-datasets',
        verbose=1
      )
      return
  # endif
  """END PATH HELPERS SECTION"""

  def _save_datapoint(self, data, inferences=None):
    """
    Method for saving a single datapoint from the gathered data.
    Parameters
    ----------
    data - dictionary of data from both model serving and payload

    Returns
    -------
    True if saved, False otherwise
    """
    success = False
    label_success = False
    current_fn = self._generate_file_name(data=data)
    current_subdir = self.__get_subdir_path(data=data)
    if self.get_label_template_type is not None:
      label_file_text = self._generate_label_file_text(data, inferences=inferences)
      label_success = self.diskapi_save_file_to_output(
        data=label_file_text,
        filename=current_fn,
        subdir=current_subdir,
        extension=self.get_dataset_builder_params.get(self.ds_consts.LABEL_EXTENSION, '')
      )
    # endif save label_file
    x_file_params = self.get_x_file_params()
    for x_file_param in x_file_params:
      source_key, prop_key = x_file_param
      fn_suffix = '' if len(x_file_params) < 2 else f'_{source_key}_{prop_key}'
      save_result = self._save_x_method(
        data_dict=data,
        subdir=current_subdir,
        fn=current_fn + fn_suffix,
        source_key=source_key,
        prop_key=prop_key
      )
      if save_result:
        success = True
      # endif save successful
    # endfor x_file_params
    success = success or label_success
    if success:
      self.__saved_data_counter += 1
      if self.get_zip_period > 0 and self.__saved_data_counter % self.get_zip_period == 0:
        self._archive_and_upload_current_chunk()
        self.P(f'Incrementing current chunk from {self.__dir_cnt} to {self.__dir_cnt + 1}')
        self.__dir_cnt += 1
      # endif archive current chunk
      if self.__saved_data_counter % self.get_stats_update_period == 0:
        self.update_saved_stats()
        self.P(f'Updated saved stats. Current saved datapoints: {self.__saved_data_counter}')
    # endif success
    return success

  def maybe_archive_upload_last_files(self):
    """
    Method used for archiving and uploading the remaining datapoints (if it's the case) when the plugin instance closes.
    Returns
    -------

    """
    if self.cfg_dataset_builder is None:
      return
    # endif dataset builder configured
    if self.get_zip_period > 0 and self.__saved_data_counter % self.get_zip_period > 0:
      self._archive_and_upload_current_chunk()
      self.__dir_cnt += 1
      self.update_saved_stats()
    # endif unarchived datapoints
    return

  def is_valid_datapoint(self, data):
    """
    Method used for checking if the provided data is valid for saving or not.
    This is implemented in order for the plugin developer to be able to custom filter
    the data that will be saved.
    Parameters
    ----------
    data

    Returns
    -------
    True if valid, False otherwise
    """
    return True

  def __is_valid_datapoint(self, data):
    """
    Wrapper method used for checking if the provided data is valid for saving or not.
    Parameters
    ----------
    data - dictionary of data from both model serving and payload

    Returns
    -------
    True if valid, False otherwise
    """
    return self.check_mandatory_keys(data=data) and self.is_valid_datapoint(data)

  def _maybe_save_datapoint(self, data):
    """
    Wrapper method of _save_datapoint that also checks for the
    data dictionary to have all the mandatory keys
    Parameters
    ----------
    data - dictionary of data from both model serving and payload

    Returns
    -------
    True if saved, False otherwise
    """
    try:
      processed_data = self._process_saved_data(self.deepcopy(data))
      return self.__is_valid_datapoint(data=processed_data) and self._save_datapoint(data=processed_data)
    except:
      return False

  def filter_inferences_idx(self, data, inferences):
    """
    Method used for filtering the inferences that will be saved.
    This is implemented in order for the plugin developer to be able to custom filter
    the data that will be saved.
    Parameters
    ----------
    inferences - list of inferences

    Returns
    -------
    res - list of filtered inferences indexes
    """
    return [
      i for i, inf in enumerate(inferences)
      if self.__is_valid_datapoint(data={**data, **inf})
    ]

  def filter_inferences(self, data, inferences):
    """
    Method used for filtering the inferences that will be saved.
    This is implemented in order for the plugin developer to be able to custom filter
    the data that will be saved.
    Parameters
    ----------
    inferences - list of inferences

    Returns
    -------
    res - list of filtered inferences
    """
    return [inferences[i] for i in self.filter_inferences_idx(data=data, inferences=inferences)]

  def _maybe_save_multilabel_datapoint(self, data, inferences):
    """
    Wrapper method of _save_datapoint that is used in case of multiple inferences in the same label.
    Parameters
    ----------
    data - dictionary of data from both model serving and payload
    inferences - list of inferences from which we will extract the data for the current fragment

    Returns
    -------
    res = (success, filtered)
    success - True if saved something, False otherwise
    filtered - list of inferences that were saved
    """
    try:
      # TODO: maybe refactor this so that the user will receive the filtered inferences in the process method
      # maybe have 2 methods of processing the data, one before the validation and one after
      proc_data, proc_inferences = self._process_saved_data_multilabel(data, inferences)
      filtered_idxs = self.filter_inferences_idx(data=proc_data, inferences=proc_inferences)
      filtered_proc_inferences = [proc_inferences[i] for i in filtered_idxs]
      filtered_inferences = [inferences[i] for i in filtered_idxs]
      if len(filtered_idxs) == 0:
        # if no inferences were kept after filtering it means we have a background image
        if self.get_background_period_save == 0 or self._background_saves % self.get_background_period_save > 0:
          return False, filtered_inferences
        # endif background period
      # endif no inferences
      success = self._save_datapoint(data=self.deepcopy(proc_data), inferences=filtered_proc_inferences)
      if len(filtered_idxs) == 0:
        self._background_saves += success
      # endif background saved
      return success, filtered_inferences
    except:
      return False, []

  def _maybe_ds_builder_save(self):
    """
    # TODO: add save every x seconds
    # TODO: add maybe_upload at the end of this method
    Main method of the mixin.
    Here we save the gathered data from both the model serving and the payload generated by the plugin.
    Returns
    -------

    """
    if self.cfg_dataset_builder is None or self.__enough_saves():
      return

    # check to see if enough time has passed from the last save
    if self.get_save_cap_resolution() > 0 and self._last_save_ts is not None \
            and self.time() - self._last_save_ts < 1 / self.get_save_cap_resolution():
      return
    # endif
    success = False
    if len(self._ds_params_frame.keys()) < 1:
      self._ds_params_frame = self._extract_data(self._ds_params_frame, check_img_orig=True)
    # endif no frame data
    if self._is_multilabel:
      inferences = [inf for inf in self._ds_params_inferences if not inf.get('SAVED', False)]
      current_params = self._ds_params_frame
      success, filtered_inferences = self._maybe_save_multilabel_datapoint(data=current_params, inferences=inferences)
      for inf in filtered_inferences:
        inf['SAVED'] = success
      # endfor filtered_inferences
    else:
      for inference_params in self._ds_params_inferences:
        if inference_params.get('SAVED', False):
          continue
        # endif datapoint already saved
        current_params = {
          **self._ds_params_frame,
          **inference_params
        }
        # if self.consts.TRACK_ID in inference_params and self.consts.TRACK_ID not in current_params.keys():
        #   current_params[self.consts.TRACK_ID] = inference_params[self.consts.TRACK_ID]
        #   current_params[self.consts.APPEARANCES] = inference_params[self.consts.APPEARANCES]

        inference_params['SAVED'] = self._maybe_save_datapoint(current_params)
        if inference_params['SAVED']:
          success = True
        # endif success
      # endfor inferences data
    # endif multilabel
    if success:
      self._last_save_ts = self.time()
    # endif success
    return

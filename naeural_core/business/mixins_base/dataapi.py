from copy import deepcopy

class _DataAPIMixin(object):
  """
  Mixin for `BasePluginExecutor` that transparently handles the work with a plugin instance input.

  Most common methods
  -------------------
    * dataapi_image
    * dataapi_image_instance_inferences
    * dataapi_image_plugin_inferences

  Plugin instance input examples
  ------------------------------
  1.
  {
    'STREAM_NAME' : 'simple_image_stream',
    'STREAM_METADATA' : {Dictionary with general stream metadata},
    'INPUTS' : [
      {
        'IMG' : np.ndarray,
        'STRUCT_DATA' : None,
        'INIT_DATA' : None,
        'TYPE' : 'IMG',
        'METADATA' : {Dictionary with current input metadata}
      }
    ],
    'INFERENCES' : {
      'object_detection_model' : [
        [{'TLBR_POS' : ...}, {'TLBR_POS' : ...}] # there are 2 detections on the first (single) image
      ]
    },

    'INFERENCES_META' : {
      'object_detection_model' : {'SYSTEM_TYME' : ..., 'VER' : ..., 'PICKED_INPUT' : 'IMG'},
    }
  }

  2.
  {
    'STREAM_NAME' : 'multi_modal_2_images_one_sensor_stream',
    'STREAM_METADATA' : {Dictionary with general stream metadata},
    'INPUTS' : [
      {
        'IMG' : np.ndarray(1),
        'STRUCT_DATA' : None,
        'INIT_DATA' : None,
        'TYPE' : 'IMG',
        'METADATA' : {Dictionary with current input metadata}
      },

      {
        'IMG' : None,
        'STRUCT_DATA' : an_object,
        'INIT_DATA' : None,
        'TYPE' : 'STRUCT_DATA',
        'METADATA' : {Dictionary with current input metadata}
      },

      {
        'IMG' : np.ndarray(2),
        'STRUCT_DATA' : None,
        'INIT_DATA' : None,
        'TYPE' : 'IMG',
        'METADATA' : {Dictionary with current input metadata}
      },
    ],
    'INFERENCES' : {
      'object_detection_model' : [ # `object_detection_model` returns a list with 2 elements (because there are 2 images in 'INPUTS')
        [{'TLBR_POS' : ...}, {'TLBR_POS' : ...}, {'TLBR_POS' : ...}], # there are 3 detections on the first image
        [{'TLBR_POS' : ...}] # there is 1 detection on the first image
      ],

      'anomaly_detection_model' : [ # `anomaly_detection_model` returns a list with 1 elemnt (because there is 1 structured data in 'INPUTS')
        'True/False'
      ]
    },
    'INFERENCES_META' : {
      'object_detection_model' : {'SYSTEM_TYME' : ..., 'VER' : ..., 'PICKED_INPUT' : 'IMG'},
      'anomaly_detection_model' : {'SYSTEM_TYME' : ..., 'VER' : ..., 'PICKED_INPUT' : 'STRUCT_DATA'}
    }
  }

  Understanding "INPUTS" in-depth
  -------------------------------
  The value for "INPUTS" key in the plugin instance input is a list of dictionaries (depending on how many inputs/substreams a stream collects -
  it may collect one frame from a video feed, or it may collect one frame and one chunk of structured data, without any
  restriction on the number of the inputs).
  A dictionary in the "INPUTS" list - i.e. a substream - has a clear structure:
    {
      "IMG" : None or np.ndarray (None if the input is a structured data, np.ndarray otherwise - mutual exclusive with "STRUCT_DATA"),
      "STRUCT_DATA" : None or object (None if the input is an image, object otherwise - mutual exlusive with "IMG",
      "INIT_DATA" : None or object (some initial data that is provided by the substream - None if the substream does not provide initial data),
      "TYPE" : "IMG" or "STRUCT_DATA" (specifies which field is completed),
      "METADATA" : dict (dictionary with current input metadata)
    }
  """

  def __init__(self):
    super(_DataAPIMixin, self).__init__()
    return

  def dataapi_full_input(self):
    """
    Returns
    -------
    dict
      full input, as it comes from upstream (empty dictionary if there is not data from upstream):
      {
        'STREAM_NAME' : 'multi_modal_2_images_one_sensor_stream',
        'STREAM_METADATA' : ...,
        'INPUTS' : [{...}, {...}, {...}],
        'INFERENCES : {
          'object_detection_model' : [
            [{'TLBR_POS' : ...}, {'TLBR_POS' : ...}, {'TLBR_POS' : ...}],
            [{'TLBR_POS' : ...}]
          ],

          'anomaly_detection_model' : [
            'True/False'
          ]
        },
        'INFERENCES_META' : {
          'object_detection_model' : {'SYSTEM_TYME' : ..., 'VER' : ..., 'PICKED_INPUT' : 'IMG'},
          'anomaly_detection_model' : {'SYSTEM_TYME' : ..., 'VER' : ..., 'PICKED_INPUT' : 'STRUCT_DATA'}
        }
      }
    """
    if self.inputs is not None:
      res = self.inputs
    else:
      res = {}
    return res
  
  def dataapi_stream_info(self):
    dct_inp = self.dataapi_full_input()
    return {k:v for k,v in dct_inp.items() if k not in ['INPUTS', 'INFERENCES', 'INFERENCES_META']}

  def dataapi_plugin_input(self):
    """
    Alias for `self.dataapi_full_input`

    Returns
    -------
    dict
      full input, as it comes from upstream (empty dictionary if there is not data from upstream)
    """
    return self.dataapi_full_input()

  def dataapi_received_input(self):
    """
    Returns
    -------
    bool
      whether input received from upstream or not (a plugin can run also without input)
    """
    return bool(self.dataapi_full_input())

  """
  Section for methods that return the first level information of the received input.
  """
  if True:
    def dataapi_stream_name(self):
      """
      Returns
      -------
      str
        the name of the stream that sends data to the current plugin instance
      """
      return self.dataapi_full_input().get('STREAM_NAME', None)    
    
    def dataapi_stream_metadata(self):
      """
      This function serves returns all the params that configured the current execution
      pipeline where the plugin instance is executed.
      
      Returns
      -------
      dict
        the metadata of the stream that sends data to the current plugin instance
      """
      return self.dataapi_full_input().get('STREAM_METADATA', {})

    def dataapi_inputs(self):
      """
      Returns
      -------
      list[dict]
        the inputs of the stream that sends data to the current plugin instance
      """
      return self.dataapi_full_input().get('INPUTS', [])

    def dataapi_inferences(self, squeeze=False):
      """
      Returns
      -------
      dict{str:list}
        the inferences that come from the serving plugins configured for the current plugin instance. 
        Each key is the name of the serving plugin (AI engine). 
        Each value is a list where each item in the list is an inference.

        Example:
          {
            'object_detection_model' : [
              [{'TLBR_POS' : ...}, {'TLBR_POS' : ...}, {'TLBR_POS' : ...}],
              [{'TLBR_POS' : ...}]
            ],

            'anomaly_detection_model' : [
              'True/False'
            ]
          }
      """
      dct_inferences = self.dataapi_full_input().get('INFERENCES', {})
      if squeeze and len(dct_inferences) == 1:
        model_name = list(dct_inferences.keys())[0]
        result = dct_inferences[model_name]
      else:
        result = dct_inferences
      return result
    
        
    def dataapi_inferences_by_model(self, model_name : str) -> list:
      """
      Returns the inference results for a specific model.

      Parameters
      ----------
      model_name : str
        The name of the model for which the inference results are requested.

      Returns
      -------
      list
        The inference results.
      """
      dct_results = self.dataapi_inferences(squeeze=False)
      result = dct_results.get(model_name, [])
      return result
    
    
    def dataapi_inference_results(self, model_name : str = None, idx : int = 0) -> list:
      """
      Returns the inference results for a specific model and a specific input index.
      
      Parameters
      ----------
      model_name : str
        The name of the model for which the inference results are requested.
      
      idx : int, optional
        The index of the input for which the inference results are requested.
        The default value is 0.
      
      Returns
      -------
      list
        The inference results.
      """
      results = []
      raw_results = self.dataapi_inferences(squeeze=True)
      if model_name is not None and isinstance(raw_results, dict):
        results = raw_results.get(model_name, [])
      else:
        results = raw_results
        
      if len(results) > idx and isinstance(results, list):
        result = results[idx]
      else:
        result = results
      return result
    
    
    def dataapi_inference_result(self):
      return self.dataapi_inferences(squeeze=True)
    

    def dataapi_inferences_meta(self):
      """
      Returns
      -------
      dict{str:dict}
        the inference metadata that comes from the serving plugins configured for the current plugin instance

        Example:
          {
            'object_detection_model' : {'SYSTEM_TYME' : ..., 'VER' : ..., 'PICKED_INPUT' : 'IMG'},
            'anomaly_detection_model' : {'SYSTEM_TYME' : ..., 'VER' : ..., 'PICKED_INPUT' : 'STRUCT_DATA'}
          }
      """
      return self.dataapi_full_input().get('INFERENCES_META', {})

  """
  Section for methods that handle inputs / substreams.
  """
  if True:
    def dataapi_specific_input(self, idx=0, raise_if_error=False):
      """
      API for accessing a specific index (by its index in the 'INPUTS' list).

      Parameters
      ----------
      idx : int, optional
        The index of the input in the 'INPUTS' list
        The default value is 0.

      raise_if_error : bool, optional
        Whether to raise IndexError or not when the requested index is out of range.
        The default value is False

      Returns
      -------
      dict
        The requested input / substream.
        For the second example in the class docstring ('multi_modal_2_images_one_sensor_stream'), if `idx==0`, the API will return
          {
            'IMG' : np.ndarray(1),
            'STRUCT_DATA' : None,
            'INIT_DATA' : None,
            'TYPE' : 'IMG',
            'METADATA' : {Dictionary with current input metadata}
          }

      Raises
      ------
      IndexError
        when the requested index is out of range
      """
      lst_inputs = self.dataapi_inputs()
      if idx >= len(lst_inputs):
        if raise_if_error:
          raise IndexError("_DataAPIMixin - requested index {} in `dataapi_specific_input` is wrong".format(idx))
        else:
          return

      return lst_inputs[idx]

    def dataapi_images(self, full=False):
      """
      API for accessing all the images in the 'INPUTS' list
      Parameters
      ----------
      full : bool, optional
        Specifies whether the images are returned full (the whole input dictionary) or not (just the value of 'IMG' in the input dictionary)
        The default value is False

      Returns
      -------
      dict{int : dict} (if full==True) / dict{int : np.ndarray} (if full=False)
        The substreams that have images.
        For the second example in the class docstring ('multi_modal_2_images_one_sensor_stream'), the API will return
          {
            0 : {
              'IMG' : np.ndarray(1),
              'STRUCT_DATA' : None,
              'INIT_DATA' : None,
              'TYPE' : 'IMG',
              'METADATA' : {Dictionary with current input metadata}
            },

            1 : {
              'IMG' : np.ndarray(2),
              'STRUCT_DATA' : None,
              'INIT_DATA' : None,
              'TYPE' : 'IMG',
              'METADATA' : {Dictionary with current input metadata}
            }
          } if full==True

          or

          {
            0 : np.ndarray(1),
            1 : np.ndarray(2)
          } if full==False
      """
      lst_inp_img = list(filter(lambda x: x['TYPE'] == 'IMG', self.dataapi_inputs()))
      dct_idx_to_img = {i : x for i,x in enumerate(lst_inp_img)}

      if not full:
        dct_idx_to_img = {i : x['IMG'] for i,x in dct_idx_to_img.items()}

      return dct_idx_to_img
    
    def dataapi_images_as_list(self):
      lst_inp_imgs = [x['IMG'] for x in self.dataapi_inputs() if x['TYPE'] == 'IMG']
      return lst_inp_imgs

    def dataapi_specific_image(self, idx=0, full=False, raise_if_error=False):
      """
      API for accessing a specific image in the 'INPUTS' list

      Parameters
      ----------
      idx : int, optional
        The index of the image in the images list
        Attention! If there is a metastream that collects 3 inputs - ['IMG', 'STRUCT_DATA', 'IMG'], for accessing the last
        image, `idx` should be 1!
        The default value is 0

      full : bool, optional
        Passed to `dataapi_images`
        The default value is False

      raise_if_error : bool, optional
        Whether to raise IndexError or not when the requested index is out of range.
        The default value is False

      Returns
      -------
      dict (if full==True) / np.ndarray (if full==False)
        dict -> the whole input dictionary
        np.ndarray -> the value of 'IMG' in the input dictionary

      Raises
      ------
      IndexError
        when the requested index is out of range
      """
      dct_images = self.dataapi_images(full=full)
      if idx not in dct_images:
        if raise_if_error:
          raise IndexError("_DataAPIMixin - requested index {} in `dataapi_specific_image` is wrong".format(idx))
        else:
          return

      res = dct_images[idx]
      return res

    def dataapi_image(self, full=False, raise_if_error=False):
      """
      API for accessing the first image in the 'INPUTS' list
      (shortcut for `dataapi_specific_image`, most of the cases will have a single image on a stream)

      Parameters
      ----------
      full : bool, optional
        Passed to `dataapi_specific_image`
        The default value is False

      raise_if_error : bool, optional
        Passed to `dataapi_specific_image`
        The default value is False

      Returns
      -------
      dict (if full==True) / np.ndarray (if full==False)
        Returned by `dataapi_specific_image`

      Raises
      ------
      IndexError
        when there are no images on the stream if `raise_if_error == True`
      """
      return self.dataapi_specific_image(idx=0, full=full, raise_if_error=raise_if_error)

    def dataapi_struct_datas(self, full=False):
      """
      API for accessing all the structured datas in the 'INPUTS' list

      Parameters
      ----------
      full : bool, optional
        Specifies whether the structured datas are returned full (the whole input dictionary) or not (just the value of 'STRUCT_DATA' in the input dictionary)
        The default value is False

      Returns
      -------
      dict{int : dict} (if full==True) / dict{int : object} (if full==False)
        The substreams that have structured data.
        For the second example in the class docstring ('multi_modal_2_images_one_sensor_stream'), the API will return
          {
            0: {
              'IMG' : None,
              'STRUCT_DATA' : an_object,
              'INIT_DATA' : None,
              'TYPE' : 'STRUCT_DATA',
              'METADATA' : {Dictionary with current input metadata}
            }
          } if full==True

          or

          {
            0 : an_object
          } if full==False
      """
      lst_inp_struct_data = list(filter(lambda x: x['TYPE'] == 'STRUCT_DATA', self.dataapi_inputs()))
      dct_idx_to_struct_data = {i : x for i,x in enumerate(lst_inp_struct_data)}

      if not full:
        dct_idx_to_struct_data = {i : x['STRUCT_DATA'] for i,x in dct_idx_to_struct_data.items()}

      keys = list(dct_idx_to_struct_data.keys())
      for k in keys:
        v = dct_idx_to_struct_data[k]
        if (hasattr(v, '__len__') and len(v) == 0) or (v is None):
          dct_idx_to_struct_data.pop(k)
      #endfor

      return dct_idx_to_struct_data

    def dataapi_specific_struct_data(self, idx=0, full=False, raise_if_error=False):
      """
      API for accessing a specific structured data in the 'INPUTS' list

      Parameters
      ----------
      idx : int, optional
        The index of the structured data in the structured datas list
        Attention! If there is a metastream that collects 3 inputs - ['IMG', 'STRUCT_DATA', 'IMG'], for accessing the structured data
        `idx` should be 0!
        The default value is 0

      full : bool, optional
        Passed to `dataapi_struct_datas`
        The default value is False

      raise_if_error : bool, optional
        Whether to raise IndexError or not when the requested index is out of range.
        The default value is True

      Returns
      -------
      dict (if full==True) / object (if full==False)
        dict -> the whole input dictionary
        object -> the value of 'STRUCT_DATA' in the input dictionary

      Raises
      ------
      IndexError
        when the requested index is out of range
      """
      dct_struct_datas = self.dataapi_struct_datas(full=full)
      if idx not in dct_struct_datas:
        if raise_if_error:
          raise IndexError("_DataAPIMixin - requested index {} in `dataapi_specific_struct_data` is wrong".format(idx))
        else:
          return

      res = dct_struct_datas[idx]
      return res

    def dataapi_struct_data(self, full=False, raise_if_error=False):
      """
      API for accessing the first structured data in the 'INPUTS' list
      (shortcut for `dataapi_specific_struct_data`, most of the cases will have a single structured data on a stream)

      Parameters
      ----------
      full : bool, optional
        Passed to `dataapi_specific_struct_data`
        The default value is False

      raise_if_error : bool, optional
        Passed to `dataapi_specific_struct_data`
        The default value is True

      Returns
      -------
      dict (if full==True) / object (if full==False)
        Returned by `dataapi_specific_struct_data`

      Raises
      ------
      IndexError
        when there are no struct_data on the stream
      """
      return self.dataapi_specific_struct_data(idx=0, full=full, raise_if_error=raise_if_error)

  """
  Section for methods that handle metadata
  """
  if True:
    def dataapi_inputs_metadata(self, as_list=False):
      """
      API for accessing the concatenated metadata from all inputs (images and structured datas together)
      This is not the same as the stream metadata that points to the overall params of the execution
      pipeline.

      Returns
      -------
      dict
        the concatenated metadata from all inputs
      """
      if as_list:
        metadata = []
        for inp in self.dataapi_inputs():
          metadata.append(inp['METADATA'])
      else:
        metadata = {}
        for inp in self.dataapi_inputs():
          metadata = {**metadata, **inp['METADATA']}
      return metadata

    def dataapi_specific_input_metadata(self, idx=0, raise_if_error=False):
      """
      API for accessing the metadata of a specific input

      Parameters
      ----------
      idx : int, optional
        Passed to `dataapi_specific_input`
        The default value is 0

      raise_if_error : bool, optional
        Passed to `dataapi_specific_input`
        The default value is False

      Returns
      -------
      dict
        the value of "METADATA" key in the requested input

      Raises
      ------
      IndexError
        when the requested index is out of range
      """
      inp = self.dataapi_specific_input(idx=idx, raise_if_error=raise_if_error)
      if inp is None:
        return
      return inp['METADATA']

    def dataapi_input_metadata(self, raise_if_error=False):
      """
      API for accessing the metadata of the first input
      (shortcut for `dataapi_specific_input_metadata`, most of the cases will have a single input on a stream)

      Parameters
      ----------
      raise_if_error : bool, optional
        Passed to `dataapi_specific_input_metadata`
        The default value is False

      Returns
      -------
      dict
        Returned by `dataapi_specific_input_metadata`

      Raises
      ------
      IndexError
        raised by `dataapi_specific_input_metadata`
      """
      return self.dataapi_specific_input_metadata(idx=0, raise_if_error=raise_if_error)

    def dataapi_all_metadata(self):
      """
      API for accessing the concatenated stream metadata and metadata from all inputs

      Returns
      -------
      dict
        the concatenated stream metadata and metadata from all inputs
      """
      metadata = {
        **self.dataapi_stream_metadata(),
        **self.dataapi_inputs_metadata()
      }
      return metadata

  """
  Section for methods that handle initial data
  """
  if True:
    def dataapi_specific_input_init_data(self, idx=0, raise_if_error=False):
      """
      API for accessing the initial data of a specific input

      Parameters
      ----------
      idx : int, optional
        Passed to `dataapi_specific_input`
        The default value is 0

      raise_if_error : bool, optional
        Passed to `dataapi_specific_input`
        The default value is False

      Returns
      -------
      dict
        the value of "INIT_DATA" key in the requested input

      Raises
      ------
      IndexError
        when the requested index is out of range
      """
      inp = self.dataapi_specific_input(idx=idx, raise_if_error=raise_if_error)
      if inp is None:
        return
      return inp['INIT_DATA']

  """
  Section for methods that handle images inferences
  """
  if True:
    def dataapi_images_inferences(self):
      """
      API for accessing just the images inferences.
      Filters the output of `dataapi_inferences`, keeping only the AI engines that run on images

      Returns
      -------
      dict{str:list}
        the inferences that comes from the images serving plugins configured for the current plugin instance.
      """
      dct_inferences = self.dataapi_inferences()
      dct_inferences_meta = self.dataapi_inferences_meta()

      filtered_dct_inferences = {}
      for k, v in dct_inferences.items():
        if dct_inferences_meta[k]['PICKED_INPUT'] == 'IMG':
          filtered_dct_inferences[k] = v

      return filtered_dct_inferences

    def dataapi_images_global_inferences(self):
      """
      Alias for `dataapi_images_inferences`
      """
      return self.dataapi_images_inferences()

    def dataapi_images_plugin_inferences(self):
      """
      API for accessing the images inferences, filtered by confidence threshold and object types.
      More specifically, all the plugin inferences are the global inferences that surpass a configured confidence
      threshold and have a specific type. For example, an object detector basically infers for all the objects in
      COCO dataset. But, a certain plugin may need only persons and dogs.

      Returns
      -------
      dict{str:list}
        filtered images inferences by confidence threshold and object types
      """
      return self._pre_process_outputs['DCT_PLUGIN_INFERENCES']

    def dataapi_images_instance_inferences(self):
      """
      API for accessing the images inferences, filtered by confidence threshold, object types and target zone.
      More specifically, all the instance inferences are the plugin inferences that intersects (based on PRC_INTERSECT)
      with the configured target zone.

      Returns
      -------
      dict{str:list}
        filtered images inferences by confidence threshold, object types and target zone
      """
      return self._pre_process_outputs['DCT_INSTANCE_INFERENCES']

    def dataapi_images_plugin_positional_inferences(self):
      """
      API for accessing the images inferences that have positions (TLBR_POS).
      Returns
      -------
      dict{str:list}
        filtered images inferences by having positions (TLBR_POS)
      """
      return self._pre_process_outputs['DCT_PLUGIN_POSITIONAL_INFERENCES']

    def dataapi_specific_image_inferences(self, idx=0, how=None, mode=None, raise_if_error=False):
      """
      API for accesing inferences for a specific image (global, plugin or instance inferences)
      See `dataapi_images_global_inferences`, `dataapi_images_plugin_inferences`, `dataapi_images_instance_inferences`

      Parameters
      ----------
      idx : int, optional
        The index of the image in the images list
        Attention! If there is a metastream that collects 3 inputs - ['IMG', 'STRUCT_DATA', 'IMG'], for accessing the last
        image, `idx` should be 1!
        The default value is 0

      how : str, optional
        Could be: 'list' or 'dict'
        Specifies how the inferences are returned. If 'list', then the AI engine information will be lost and all the
        inferences from all the employed AI engines will be concatenated in a list; If 'dict', then the AI engine information
        will be preserved.
        The default value is None ('list')

      mode : str, optional
        Could be: 'global', 'plugin' or 'instance'
        Specifies which inferences are requested.
        The default value is None ('instance')

      raise_if_error : bool, optional
        Whether to raise IndexError or not when the requested index is out of range.
        The default value is False

      Returns
      -------
      dict (if how == 'dict') or list (if how == 'list')
        the requested image inferences (global, plugin or instance) in the requested format (dict or list)

      Raises
      ------
      IndexError
        when the requested index is out of range
      """
      if how is None:
        how = 'list'

      if mode is None:
        mode = 'instance'

      assert how in ['dict', 'list']
      assert mode in ['global', 'plugin', 'instance', 'plugin_positional']
      img = self.dataapi_specific_image(idx=idx, raise_if_error=raise_if_error)
      if img is None:
        return

      dct_inferences = None
      if mode == 'global':
        dct_inferences = self.dataapi_images_global_inferences()
      elif mode == 'plugin':
        dct_inferences = self.dataapi_images_plugin_inferences()
      elif mode == 'instance':
        dct_inferences = self.dataapi_images_instance_inferences()
      elif mode == 'plugin_positional':
        dct_inferences = self.dataapi_images_plugin_positional_inferences()
      #endif

      dct_inferences_idx = {
        model : lst_2d[idx] for model, lst_2d in dct_inferences.items()
      }

      if how == 'dict':
        return dct_inferences_idx
      elif how == 'list':
        return self.log.flatten_2d_list(list(dct_inferences_idx.values()))
      #endif

    def dataapi_image_inferences(self, how=None, mode=None, raise_if_error=False):
      """
      API for accessing the first image inferences
      (shortcut for `dataapi_specific_image_inferences`, most of the cases will have a single image on a stream)

      Parameters
      ----------
      how : str, optional
        Passed to `dataapi_specific_image_inferences`
        The default value is None

      mode : str, optional
        Passed to `dataapi_specific_image_inferences`
        The default value is None

      raise_if_error : bool, optional
        Passed to `dataapi_specific_image_inferences`
        The default value is False

      Returns
      -------
      dict (if how == 'dict') or list (if how == 'list')
        returned by `dataapi_specific_image_inferences`

      Raises
      ------
      IndexError
        raised by `dataapi_specific_image_inferences`
      """
      return self.dataapi_specific_image_inferences(idx=0, how=how, mode=mode, raise_if_error=raise_if_error)

    def dataapi_specific_image_global_inferences(self, idx=0, how=None, raise_if_error=False):
      """
      API for accessing a specific image global inferences
      (shortcut for `dataapi_specific_image_inferences`)

      Parameters
      ----------
      idx : int, optional
        Passed to `dataapi_specific_image_inferences`
        The default value is None

      how : str, optional
        Passed to `dataapi_specific_image_inferences`
        The default value is None

      raise_if_error : bool, optional
        Passed to `dataapi_specific_image_inferences`
        The default value is False

      Returns
      -------
      dict (if how == 'dict') or list (if how == 'list')
        returned by `dataapi_specific_image_inferences`

      Raises
      ------
      IndexError
        raised by `dataapi_specific_image_inferences`
      """
      return self.dataapi_specific_image_inferences(idx=idx, how=how, mode='global', raise_if_error=raise_if_error)

    def dataapi_specific_image_plugin_inferences(self, idx=0, how=None, raise_if_error=False):
      """
      API for accessing a specific image plugin inferences
      (shortcut for `dataapi_specific_image_inferences`)

      Parameters
      ----------
      idx : int, optional
        Passed to `dataapi_specific_image_inferences`
        The default value is None

      how : str, optional
        Passed to `dataapi_specific_image_inferences`
        The default value is None

      raise_if_error : bool, optional
        Passed to `dataapi_specific_image_inferences`
        The default value is False

      Returns
      -------
      dict (if how == 'dict') or list (if how == 'list')
        returned by `dataapi_specific_image_inferences`

      Raises
      ------
      IndexError
        raised by `dataapi_specific_image_inferences`
      """
      return self.dataapi_specific_image_inferences(idx=idx, how=how, mode='plugin', raise_if_error=raise_if_error)

    def dataapi_specific_image_instance_inferences(self, idx=0, how=None, raise_if_error=False):
      """
      API for accessing a specific image inferences for the current plugin instance
      (shortcut for `dataapi_specific_image_inferences`)

      Parameters
      ----------
      idx : int, optional
        Passed to `dataapi_specific_image_inferences`
        The default value is None

      how : str, optional
        Passed to `dataapi_specific_image_inferences`
        The default value is None ('list')

      raise_if_error : bool, optional
        Passed to `dataapi_specific_image_inferences`
        The default value is False

      Returns
      -------
      dict (if how == 'dict') or list (if how == 'list')
        returned by `dataapi_specific_image_inferences`

      Raises
      ------
      IndexError
        raised by `dataapi_specific_image_inferences`
      """
      return self.dataapi_specific_image_inferences(idx=idx, how=how, mode='instance', raise_if_error=raise_if_error)

    def dataapi_specific_image_plugin_positional_inferences(self, idx=0, how=None, raise_if_error=False):
      """
      API for accessing a specific image plugin positional inferences
      (shortcut for `dataapi_specific_image_inferences`)

      Parameters
      ----------
      idx : int, optional
        Passed to `dataapi_specific_image_inferences`
        The default value is None

      how : str, optional
        Passed to `dataapi_specific_image_inferences`
        The default value is None

      raise_if_error : bool, optional
        Passed to `dataapi_specific_image_inferences`
        The default value is False

      Returns
      -------
      dict (if how == 'dict') or list (if how == 'list')
        returned by `dataapi_specific_image_inferences`

      Raises
      ------
      IndexError
        raised by `dataapi_specific_image_inferences`
      """
      return self.dataapi_specific_image_inferences(idx=idx, how=how, mode='plugin_positional', raise_if_error=raise_if_error)

    def dataapi_image_global_inferences(self, how=None, raise_if_error=False):
      """
      API for accessing the first image global inferences
      (shortcut for `dataapi_specific_image_global_inferences`, most of the cases will have a single image on a stream)

      Parameters
      ----------
      how : str, optional
        Passed to `dataapi_specific_image_global_inferences`
        The default value is None

      raise_if_error : bool, optional
        Passed to `dataapi_specific_image_global_inferences`
        The default value is False

      Returns
      -------
      dict (if how == 'dict') or list (if how == 'list')
        returned by `dataapi_specific_image_global_inferences`

      Raises
      ------
      IndexError
        raised by `dataapi_specific_image_global_inferences`
      """
      return self.dataapi_specific_image_global_inferences(idx=0, how=how, raise_if_error=raise_if_error)

    def dataapi_image_plugin_inferences(self, how=None, raise_if_error=False):
      """
      API for accessing the first image plugin inferences
      (shortcut for `dataapi_specific_image_plugin_inferences`, most of the cases will have a single image on a stream)

      Parameters
      ----------
      how : str, optional
        Passed to `dataapi_specific_image_plugin_inferences`
        The default value is None

      raise_if_error : bool, optional
        Passed to `dataapi_specific_image_plugin_inferences`
        The default value is False

      Returns
      -------
      dict (if how == 'dict') or list (if how == 'list')
        returned by `dataapi_specific_image_plugin_inferences`

      Raises
      ------
      IndexError
        raised by `dataapi_specific_image_plugin_inferences`
      """
      return self.dataapi_specific_image_plugin_inferences(idx=0, how=how, raise_if_error=raise_if_error)

    def dataapi_image_instance_inferences(self, how=None, raise_if_error=False):
      """
      API for accessing the first image instance inferences
      (shortcut for `dataapi_specific_image_instance_inferences`, most of the cases will have a single image on a stream)

      Parameters
      ----------
      how : str, optional
        Passed to `dataapi_specific_image_instance_inferences`
        The default value is None ('list')

      raise_if_error : bool, optional
        Passed to `dataapi_specific_image_instance_inferences`
        The default value is False

      Returns
      -------
      dict (if how == 'dict') or list (if how == 'list')
        returned by `dataapi_specific_image_instance_inferences`

      Raises
      ------
      IndexError
        raised by `dataapi_specific_image_instance_inferences`
      """
      return self.dataapi_specific_image_instance_inferences(idx=0, how=how, raise_if_error=raise_if_error)

    def dataapi_image_plugin_positional_inferences(self, how=None, raise_if_error=False):
      """
      API for accessing the first image plugin positional inferences
      (shortcut for `dataapi_specific_image_plugin_positional_inferences`, most of the cases will have a single image on a stream)

      Parameters
      ----------
      how : str, optional
        Passed to `dataapi_specific_image_plugin_positional_inferences`
        The default value is None

      raise_if_error : bool, optional
        Passed to `dataapi_specific_image_plugin_positional_inferences`
        The default value is False

      Returns
      -------
      dict (if how == 'dict') or list (if how == 'list')
        returned by `dataapi_specific_image_plugin_positional_inferences`

      Raises
      ------
      IndexError
        raised by `dataapi_specific_image_plugin_positional_inferences`
      """
      return self.dataapi_specific_image_plugin_positional_inferences(idx=0, how=how, raise_if_error=raise_if_error)

  """
  Section for methods that handle struct datas inferences
  """
  if True:
    def dataapi_struct_datas_inferences(self):
      """
      API for accessing just the structured datas inferences.
      Filters the output of `dataapi_inferences`, keeping only the AI engines that run on structured datas

      Returns
      -------
      dict{str:list}
        the inferences that comes from the structured datas serving plugins configured for the current plugin instance.
      """
      dct_inferences = self.dataapi_inferences()
      dct_inferences_meta = self.dataapi_inferences_meta()

      filtered_dct_inferences = {}
      for k, v in dct_inferences.items():
        if dct_inferences_meta[k]['PICKED_INPUT'] == 'STRUCT_DATA':
          filtered_dct_inferences[k] = v

      return filtered_dct_inferences

    def dataapi_specific_struct_data_inferences(self, idx=0, how=None, raise_if_error=False):
      """
      API for accesing a specific structured data inferences

      Parameters
      ----------
      idx : int, optional
        The index of the structured data in its list
        Attention! If there is a metastream that collects 3 inputs - ['IMG', 'STRUCT_DATA', 'IMG'], for accessing the structured data,
        `idx` should be 0!
        The default value is 0

      how : str, optional
        Could be: 'list' or 'dict'
        Specifies how the inferences are returned. If 'list', then the AI engine information will be lost and all the
        inferences from all the employed AI engines will be concatenated in a list; If 'dict', then the AI engine information
        will be preserved.
        The default value is None ('list')

      raise_if_error : bool, optional
        Whether to raise IndexError or not when the requested index is out of range.
        The default value is False

      Returns
      -------
      dict (if how == 'dict') or list (if how == 'list')
        the requested structured data inferences in the requested format (dict or list)

      Raises
      ------
      IndexError
        when the requested index is out of range
      """
      if how is None:
        how = 'list'

      assert how in ['dict', 'list']

      sd = self.dataapi_specific_struct_data(idx=idx, raise_if_error=raise_if_error)
      if sd is None:
        return

      dct_inferences = self.dataapi_struct_datas_inferences()
      dct_inferences_idx = {
        model : lst[idx] for model, lst in dct_inferences.items()
      }

      if how == 'dict':
        return dct_inferences_idx
      elif how == 'list':
        return list(dct_inferences_idx.values())
      #endif

    def dataapi_struct_data_inferences(self, how=None, raise_if_error=False):
      """
      API for accesing a the first structured data inferences
      (shortcut for `dataapi_specific_struct_data_inferences`, most of the cases will have a single struct data on a stream)

      Parameters
      ----------
      how : str, optional
        Passed to `dataapi_specific_struct_data_inferences`
        The default value is None

      raise_if_error : bool, optional
        Passed to `dataapi_specific_struct_data_inferences`
        The default value is False

      Returns
      -------
      dict (if how == 'dict') or list (if how == 'list')
        returned by `dataapi_specific_struct_data_inferences`

      Raises
      ------
      IndexError
        raised by `dataapi_specific_struct_data_inferences`
      """
      return self.dataapi_specific_struct_data_inferences(idx=0, how=how, raise_if_error=raise_if_error)
# global dependencies
import os
import abc
import cv2
import numpy as np

from collections import defaultdict

# local dependencies
from naeural_core import constants as ct
from naeural_core.business.mixins_base.debug_info_mixin import _DebugInfoMixin

from .base_plugin_biz import BasePluginExecutor
from naeural_core.serving.ai_engines import AI_ENGINES
from naeural_core.business import utils
from naeural_core.utils import nms
from decentra_vision.draw_utils import DrawUtils

from naeural_core.business.mixins_base import _LimitedDataMixin

# TODO: this will be moved or replaced
try:
  from extensions.business.mixins_base.multi_class_tracker_mixin import _MultiClassTrackerMixin
except:
  from naeural_core.business.mixins_base.multi_class_tracker_mixin import _MultiClassTrackerMixin

from naeural_core.business.mixins_libs import _AlertWitnessMixin

_CONFIG = {
  **BasePluginExecutor.CONFIG,

  'DEBUG_ON_WITNESS': True,  # if true, the debug info will not be drawn on the image

  # Tracker
  'TRACKING_ENABLED': True,
  'TRACKING_MODE': 0,  # 0 - linear, 1-sort,
  'LINEAR_RESET_MINUTES': 60,
  'LINEAR_MAX_DISTANCE_RATIO': 4,
  'LINEAR_MAX_DIST_SCALE': 1.4,
  'LINEAR_MAX_RELATIVE_DIST': 1.2,
  'LINEAR_CENTER_DIST_WEIGHT': 1,
  'LINEAR_HW_DIST_WEIGHT': 0.8,
  'LINEAR_MAX_AGE': 4,
  'SORT_MIN_IOU': 0,
  'SORT_MAX_AGE': 3,
  'SORT_MIN_HITS': 1,


  'PRC_INTERSECT': 0.5,
  'TRUSTED_PRC': 0.65,
  'DEBUG_OBJECTS': False,
  'DEBUG_OBJECTS_PATHS': False,
  'META_TYPE_MAPPING': {},
  'META_NMS_IOU_THR': 0.6,


  'RUN_WITHOUT_IMAGE': False,
  'ALLOW_EMPTY_INPUTS': False,  # if this is set to true the on-idle will be triggered continously the process

  'DEBUG_INFO_ACTIVE_ALERTER_TIME': 30,  # seconds, used to reduce the data dumped by alerters
  'DEBUG_SHOW_DEFAULT_ALERTER': True,

  'ALPHA_OUTER_ZONE': True,

  'ALPHA_OUTER_ZONE_INTENSITY': 170,

  'SAVE_TARGET_ZONE': True,

  'THREAD_SAFE_DRAWING': True,

  'POINTS': [],
  'OBJECT_TYPE': [],

  'ADD_ORIGINAL_IMAGE': False,

  'DEMO_MODE_TIME_LIMIT': 3600,

  'VALIDATION_RULES': {
    **BasePluginExecutor.CONFIG['VALIDATION_RULES'],

    'ADD_ORIGINAL_IMAGE': {
      'TYPE': 'bool',
      'DESCRIPTION': "Add original image to payloads as 'IMG_ORIG' beside the usual witness 'IMG' key",
    },

    'META_TYPE_MAPPING': {
      'TYPE': 'dict',
      'DESCRIPTION': 'dict(key: list())  Mapping dictionary that defines each meta type as a list of types. '
                     'If an meta type is set for a given type, the object tracker will use the said type for aggregation'
    },

    'POINTS': {
      'TYPE': 'list',
      # No need for `'MIN_LEN' : 4,` as there is validate_coords
    },

    'RUN_WITHOUT_IMAGE': {
      'DESCRIPTION': "The cv plugin instance will run its inner loop even if there is no image from upstream",
      'TYPE': 'bool'
    },

    'DEBUG_OBJECTS_PATHS': {
      'TYPE': 'bool',
      'DESCRIPTION': 'Draw the paths history of all objects. Works only with debug objects true'
    },

    ### Tracking ###
    'TRACKING_ENABLED': {
      'TYPE': 'bool',
      'DESCRIPTION': 'Flag for enabling tracking. Default `True`'
    },

    'TRACKING_MODE': {
      'TYPE': 'int',
      'DESCRIPTION': 'What tracking algorithm to be used. 1 - linear, 2 - sort. Default 1'
    },

    'LINEAR_RESET_MINUTES': {
      'TYPE': 'int',
      'DESCRIPTION': 'The maximum life of an tracked object. After the number of minutes passes the object is given a '
                     'new id. Only for linear tracking mode'
    },

    'LINEAR_MAX_DISTANCE_RATIO': {
      'TYPE': 'float',
      'DESCRIPTION': 'Parameter used to heuristically compute the maximum allowed distance between objects to have the'
                     'same id. Formula is: max_distance = sqrt(image_h * image_w) / LINEAR_MAX_DISTANCE_RATIO. '
                     'Only for linear tracking mode'
    },

    'LINEAR_MAX_RELATIVE_DIST': {
      'TYPE': 'float',
      'DESCRIPTION': 'Parameter used to heuristically compute the maximum allowed distance between 2 consecutive '
                     'appearances of the same object. The formula is: '
                     'max_relative_distance = dist / max(h1, w1, h2, w2), where dist is the distance between the '
                     'centroid of a detection in the current frame and the centroid of the last appearance of the '
                     'checked track_id and (h1, w1), (h2, w2) are the heights and widths of the current detection '
                     'and the candidate\'s last appearance.'
    },

    'LINEAR_MAX_DIST_SCALE': {
      'TYPE': 'float',
      'DESCRIPTION': 'Parameter used to compute maximum distance per distance type.  Formula is :'
                     'distance_type_max = max_distance * dist_weight / (hw_dist_weight + center_dist_weight) * max_dist_scale.'
                     'Should have values between 1 and 2. Only for linear tracking mode'
    },

    'LINEAR_CENTER_DIST_WEIGHT': {
      'TYPE': 'float',
      'DESCRIPTION': 'The weight of the centroid distance in computing the total distance. Only for linear tracking mode'
    },

    'LINEAR_HW_DIST_WEIGHT': {
      'TYPE': 'float',
      'DESCRIPTION': 'The weight of the sizes distance in computing the total distance. Only for linear tracking mode'
    },
    'LINEAR_MAX_AGE': {
      'TYPE': 'int',
      'DESCRIPTION': 'Number of untracked frames until the object id is no loger tracked. Only for linear tracking mode'
    },

    'SORT_MIN_IOU': {
      'TYPE': 'float',
      'DESCRIPTION': 'The minimum IOU between objects from different frames to be considered tracking candidates'
    },
    'SORT_MAX_AGE': {
      'TYPE': 'int',
      'DESCRIPTION': 'Number of untracked frames until the object id is no loger tracked. Only for sort tracking mode'
    },
    'SORT_MIN_HITS': {
      'TYPE': 'int',
      'DESCRIPTION': 'Number of identifications to be assigned an id.'
    }
  },
}


class CVPluginExecutor(BasePluginExecutor,
                       _LimitedDataMixin,
                       _MultiClassTrackerMixin,
                       _DebugInfoMixin,
                       _AlertWitnessMixin
                       ):
  __metaclass__ = abc.ABCMeta

  CONFIG = _CONFIG

  def __init__(self, **kwargs):

    self._irregular_target_area = None
    self._coords_type = None
    self._top, self._left, self._bottom, self._right = None, None, None, None
    self.saved_zone = False
    self.demo_mode_time = None

    self.__last_witness_images = None
    self.__last_witness_count = 0

    super(CVPluginExecutor, self).__init__(**kwargs)
    return

  def set_output_image(self, img):
    self.default_image = img
    return

  def set_image(self, img):
    return self.set_output_image(img)

  def startup(self):
    super().startup()
    self._painter = DrawUtils(log=self.log, timers_section=self._timers_section)
    return

  def _get_detector_ai_engine(self):
    configured_ai_engines = self.cfg_ai_engine
    if type(configured_ai_engines) != list:
      configured_ai_engines = [configured_ai_engines]

    # TODO: FIND A BETTER METHOD
    detectors = [x for x in AI_ENGINES.keys() if 'detector' in x]

    for ai_engine in configured_ai_engines:
      if ai_engine in detectors:
        return ai_engine

    return None

  def _get_detector_ai_engines(self):
    """
    Get a list of all detector engines that are configured for this plugin
    """
    configured_ai_engines = self.cfg_ai_engine
    if type(configured_ai_engines) != list:
      configured_ai_engines = [configured_ai_engines]

    # TODO: FIND A BETTER METHOD
    configured_detectors = [x for x in AI_ENGINES.keys() if 'detector' in x and x in configured_ai_engines]

    return configured_detectors

  @property
  def _type_to_meta_type_map(self):
    return {type: meta_type for meta_type, types in self.cfg_meta_type_mapping.items() for type in types}

  def _get_object_areas(self, filter_object_type):
    # Returns object areas / total image are
    detector_ai_engine = self._get_detector_ai_engine()
    if detector_ai_engine is None:
      return []

    inferences = self.dataapi_inferences()[detector_ai_engine][0]

    if filter_object_type:
      inferences = [inference for inference in inferences if inference['TYPE'] == filter_object_type]

    img_area = self.dataapi_image().shape[0] * self.dataapi_image().shape[1]

    proportions = [
      (inference['TLBR_POS'][3] - inference['TLBR_POS'][1]) *
      (inference['TLBR_POS'][2] - inference['TLBR_POS'][0]) /
      img_area
      for inference in inferences
    ]
    return proportions

  def _get_tlbr(self):
    points = self.cfg_points
    if len(points) == 0:
      self._coords_type = ct.COORDS_NONE
    elif isinstance(points[0], int):
      self._coords_type = ct.COORDS_TLBR
    elif isinstance(points[0], list):
      self._coords_type = ct.COORDS_POINTS
    else:
      self._coords_type = ct.COORDS_NONE
    # endif

    self._irregular_target_area = (self._coords_type == ct.COORDS_POINTS)

    if self._coords_type == ct.COORDS_POINTS:
      self._top, self._left, self._bottom, self._right = utils.convert_points_to_tlbr(
        points=self.cfg_points
      )
    elif self._coords_type == ct.COORDS_TLBR:
      self._top, self._left, self._bottom, self._right = self.cfg_points

    return

  def _keep_only_intersected_objects(self, dct_positional_plugin_inference):
    prc_intersect = self.cfg_prc_intersect

    dct_intersected = {}
    if self._coords_type == ct.COORDS_POINTS:
      func = utils.keep_boxes_inside_irregular_target_area
      target_area = self.cfg_points
    else:
      func = utils.keep_boxes_inside_regular_target_area
      target_area = [self._top, self._left, self._bottom, self._right]

    for model, lst_2d in dct_positional_plugin_inference.items():
      new_lst_2d = []
      for lst in lst_2d:
        new_lst, intersect = [], []
        if len(lst) > 0:
          new_lst, intersect = func(
            lst_box=lst,
            target_area=target_area,
            prc_intersect=prc_intersect
          )
        # endif

        for idx, x in enumerate(new_lst):
          x[ct.PRC_INTERSECT] = intersect[idx]

        new_lst_2d.append(new_lst)
      # endfor
      dct_intersected[model] = new_lst_2d
    # endfor

    return dct_intersected

  def _filter_confidence_threshold(self, dct_inference):
    dct = {}
    conf_thr = self._get_confidence_threshold()
    if conf_thr is None or conf_thr == 0:
      dct = dct_inference
    else:
      for model, lst_2d in dct_inference.items():
        new_lst_2d = []
        for lst in lst_2d:
          new_lst = []
          for elem in lst:
            if isinstance(elem, dict):
              prob_prc = elem.get(ct.PROB_PRC, None)
              if isinstance(prob_prc, (tuple, list, type(None))):
                new_lst.append(elem)
              elif prob_prc >= conf_thr:
                elem['IS_TRUSTED'] = int(prob_prc > self.cfg_trusted_prc)
                new_lst.append(elem)
              # endif
            else:
              new_lst.append(elem)
            # endif
          # endfor - elem
          new_lst_2d.append(new_lst)
        # endfor - lst
        dct[model] = new_lst_2d
      # endfor dct_inference

    return dct

  def _filter_object_types(self, dct_inference):
    dct = {}
    for model, lst_2d in dct_inference.items():
      new_lst_2d = self._keep_only_plugin_object_types(lst_2d)
      dct[model] = new_lst_2d
    return dct

  def _keep_only_plugin_object_types(self, lst_2d):
    if len(self.cfg_object_type) > 0:
      l_filtered = []
      for lst in lst_2d:
        l_filtered.append([x for x in lst if x[ct.TYPE] in self.cfg_object_type])
    else:
      l_filtered = lst_2d
    return l_filtered

  def _get_generic_positional_objects(self, dct_inference):
    """
    Returns both spatial (with TLBR) objects as well as non-spatial ones

    Parameters
    ----------
    dct_inference : dict
      Upstream inference dict.

    Returns
    -------
    dct_generic : dict
      Non-TLBR objects.
    dct_positional : dict
      TLBR objects.

    """
    dct_generic, dct_positional = {}, {}
    for model, lst_2d in dct_inference.items():
      if len(lst_2d) > 0 and len(lst_2d[0]) > 0:
        if ct.TLBR_POS in lst_2d[0][0]:
          dct_positional[model] = lst_2d
        else:
          dct_generic[model] = lst_2d
      else:
        dct_generic[model] = lst_2d
      # endif
    # endfor

    return dct_generic, dct_positional

  def get_debug_objects_summary(self, debug_objects):
    objs = debug_objects
    if isinstance(objs, list) and len(objs) > 0:
      dct_res = defaultdict(lambda: 0)
      for obj in objs:
        dct_res[obj[ct.TYPE]] += 1
      return dict(dct_res)
    return "Nothing"

  def _draw_and_label_target_area(self, img_witness, irregular_target_area, cfg_instance_id, points, tlbr):
    if irregular_target_area:
      img_witness = self._draw_target_area(
        image=img_witness,
        name=cfg_instance_id,
        irregular_target_area=irregular_target_area,
        points=points,
        tlbr=tlbr
      )

      if self.cfg_alpha_outer_zone:
        img_witness = self._painter.alpha_outer_poly_area(
          image=img_witness,
          points=points,
          intensity=self.cfg_alpha_outer_zone_intensity,
        )
    else:
      MARGIN = 10
      top, left, bottom, right = tlbr
      _h, _w = img_witness.shape[:2]
      if left > MARGIN or top > MARGIN or bottom < (_h - MARGIN) or right < (_w - MARGIN):
        img_witness = self._draw_target_area(
          image=img_witness,
          name=cfg_instance_id,
          irregular_target_area=irregular_target_area,
          points=points,
          tlbr=tlbr
        )

        if self.cfg_alpha_outer_zone:
          img_witness = self._painter.alpha_outer_area(
            image=img_witness,
            top=top,
            left=left,
            bottom=bottom,
            right=right,
            intensity=self.cfg_alpha_outer_zone_intensity,
          )
      # endif
    # endif
    return img_witness

  def _draw_target_area(self, image, name, irregular_target_area, points, tlbr, blur=False, use_rectangle_target=True):
    _top, _left, _bottom, _right = tlbr
    top, left = None, None
    if irregular_target_area:
      # draw polygon
      img = self._painter.polygon(
        image=image,
        pts=points,
        color=ct.GREEN,
        thickness=3
      )

      # extract positions for name
      arr = np.array(points)
      top = arr[:, 1].min()
      argmin = arr[:, 1].argmin()
      left = points[argmin][0]
      top = top if top > 10 else 10
      left = left if left > 10 else 10

      if blur:
        img = self._painter.alpha_inner_poly_area(
          image=image,
          points=points
        )
    else:
      # draw rectangle
      if use_rectangle_target:
        fn = self._painter.rectangle_target
      else:
        fn = self._painter.rectangle

      img = fn(
        image=image,
        pt1=(_left, _top),
        pt2=(_right, _bottom),
        color=ct.GREEN,
        thickness=3
      )

      # extract positions for name
      top = _top if _top > 0 else 10
      left = _left if _left > 0 else 10

      if blur:
        img = self._painter.alpha_inner_area(
          image=image,
          top=_top,
          left=_left,
          bottom=_bottom,
          right=_right
        )
      # endif
    # endif

    if name:
      img = self._painter.alpha_text_rectangle(
        image=img,
        top=top,
        left=left,
        text=name,
        color=ct.DARK_GREEN
      )
    # endif
    return img

  def on_command(self, data, get_last_witness=None, **kwargs):
    """
    Called when the instance receives new INSTANCE_COMMAND

    Parameters
    ----------
    data : any
      object, string, etc.

    Returns
    -------
    None.

    Observation
    -----------

      For "get-last-witness" command we have the following command structure:
      ```
        {
          'NAME': '<name of pipeline>', 
          'INSTANCE_ID': '<name of instance>', 
          'SIGNATURE': '<name of signature>', 
          'INSTANCE_CONFIG': 
          {
            "REQUESTED": "example-key-from-a-backend-appd", 
            "REQUEST_ID": "e6b3abfd-8001-45fd-b0d7-2a6fea6b8949",
            "COMMAND_PARAMS": 
            {
              "GET_LAST_WITNESS": true
            }
          }
        }
      ```
      all the `COMMAND_PARAMS` are optional, but if present, they must be a dict and will be transformed
      into kwargs for the `_on_command` method.

    """
    if (isinstance(data, str) and data.upper == 'GET_LAST_WITNESS') or get_last_witness:
      self.P("Received `GET_LAST_WITNESS` request command. Running default callback.)")
      images = self.__last_witness_images
      self.add_payload_by_fields(
        img=images,
        last_witness_count=self.__last_witness_count,
        command_params=data,
      )
    return

  # Section A - methods overwritten from parenn
  # When reimplementing, pay double attention if you want to overwrite the entire functionality;
  # otherwise, first line should be parent_result = super().method_name(...)
  if True:
    def high_level_execution_chain(self):
      if (not self.cfg_run_without_image and len(self.dataapi_images()) == 0) and not self.cfg_allow_empty_inputs:
        # cancel execution if and only if we must have image and no image is avail from upstream
        # and allow_empty inputs is false
        # due to `allow_empty_inputs` image check is half-redundant
        return
      # endif

      if len(self.dataapi_images()) > 0:
        self.__last_witness_images = self.dataapi_images_as_list()
        self.__last_witness_count += 1
      # endif
      super().high_level_execution_chain()
      return

    def _update_instance_config(self):
      super()._update_instance_config()
      self._get_tlbr()
      return

    def _prepare_payload_for_testing_registration(self, payload):
      super()._prepare_payload_for_testing_registration(payload)
      payload['_T_FRAME_CURRENT'] = self.limited_data_frame_current
      payload['_T_FPS'] = self.limited_data_fps
      payload['_T_SECONDS_ELAPSED'] = self.limited_data_seconds_elapsed
      payload['_T_CRT_TIME'] = self.limited_data_crt_time
      return payload

    def _prepare_payload_for_generating_testing_results(self, payload):
      super()._prepare_payload_for_generating_testing_results(payload)
      if self._limited_data_finished:
        _payload = {
          'Y_HAT': self._testing_manager.tester.y_hat,
          'EXCEPTIONS': self._testing_manager.tester.exceptions,
          'PROCESSING_SECONDS': self._last_process_time - self._first_process_time,
          'MOVIE_SECONDS': self.limited_data_duration
        }
      return _payload

    def _print_warnings(self):
      super()._print_warnings()
      if self.cfg_debug_objects:
        self.P("Plugin instance draws ALL objects for DEBUG purposes.", color='y')

      if not self.cfg_thread_safe_drawing:
        self.P("Plugin instance NOT use thread-safe for witness drawing.", color='y')

      if self.cfg_cancel_witness:
        self.P("Plugin instance does NOT send witness image in payloads.", color='y')
      return

    def _prepare_debug_save_payload(self):
      # this method is "connected" with "debug_save" heavy ops plugin

      if self._payload is None:
        return

      self._payload['_H_ORIGINAL_IMAGE'] = self.dataapi_image()
      self._payload['_H_RELATIVE_PATH'] = self.instance_relative_path
      # TODO: maybe remove? we have cfg_debug_save_payload now
      self._payload['_H_ARCHIVE_M'] = self.cfg_debug_save_archive_m
      self._payload['_H_UPLOAD'] = self.cfg_debug_save_upload
      ####
      return

    # TODO: maybe remove
    def _add_plugin_identifiers_to_payload(self, payload):
      super()._add_plugin_identifiers_to_payload(payload)
      vars(payload)['_P_' + ct.CONFIDENCE_THRESHOLD] = self._get_confidence_threshold()

      if len(self.cfg_points) > 0:
        vars(payload)['_P_' + ct.LOCATION] = self.cfg_points
      if len(self.cfg_object_type) > 0:
        vars(payload)['_P_' + ct.OBJECT_TYPE] = self.cfg_object_type
      return

    def _draw_object_centroid_history(self, img, inference, color=None, nr_steps=None, draw_orientation=False):
      centroid_history = self._get_object_history(
        object_id=inference[self.consts.TRACK_ID],
        object_type=inference[self.consts.TYPE]
      )
      if nr_steps is not None and nr_steps > 0:
        centroid_history = centroid_history[-nr_steps:]
      # endif

      if color is None:
        color_list = self.sns.color_palette("Paired")
        color_list = [[int(channel * 255) for channel in color] for color in color_list]
        color = color_list[inference[self.consts.TRACK_ID] % len(color_list)]
      # endif color not provided

      centroid_history = [self.np.flip(x) for x in centroid_history]
      img = self._painter.crosses(img, centroid_history, color=color, thickness=2, size_factor=2e-3)
      lines = [(centroid_history[i], centroid_history[i + 1]) for i in range(len(centroid_history) - 1)]
      for line in lines:
        img = self._painter.line(img, line[0], line[1], color=color, thickness=2)
      # endfor lines

      if draw_orientation:
        orientation = self._get_current_orientation(
          object_id=inference['TRACK_ID'],
          object_type=inference['TYPE']
        )
        img = self._painter.arrow(
          img,
          centroid_history[-1],
          (centroid_history[-1][0] + orientation[1], centroid_history[-1][1] + orientation[0]),
          # In tracker axises are reversed
          color=color, thickness=2
        )
      # endif draw_orientation

      return img

    def _draw_object_centroid_histories(self, img, inferences):
      color_list = self.sns.color_palette("Paired")
      color_list = [[int(channel * 255) for channel in color] for color in color_list]
      for inference in inferences:
        color = color_list[inference[self.consts.TRACK_ID] % len(color_list)]
        img = self._draw_object_centroid_history(img=img, inference=inference, color=color)
      # endfor inference
      return img

    def _witness_pre_process(self, img_witness, **kwargs):
      img_witness = super()._witness_pre_process(img_witness, **kwargs)
      lst_plugin_inferences = kwargs.get('lst_plugin_inferences', [])

      # focus main target area
      if self.cfg_simple_witness:
        return img_witness

      draw_all_boxes = self.cfg_debug_objects

      if draw_all_boxes:
        img_witness = self._painter.draw_inference_boxes(
          image=img_witness,
          lst_inf=lst_plugin_inferences
        )

        if self.cfg_debug_objects_paths and self.cfg_tracking_enabled:
          img_witness = self._draw_object_centroid_histories(img_witness, lst_plugin_inferences)
      # endif

      img_witness = self._draw_and_label_target_area(
        img_witness=img_witness,
        irregular_target_area=self._irregular_target_area,
        cfg_instance_id=self.cfg_instance_id,
        points=self.cfg_points,
        tlbr=(self._top, self._left, self._bottom, self._right)
      )
      return img_witness

    def __get_instance_debug_info(self, img_witness):
      dct_input_metadata = self.dataapi_input_metadata() or {}
      dct_stream_metadata = self.dataapi_stream_metadata() or {}
      cap_time = dct_stream_metadata.get('cap_time', "????-??-?? ??:??:??.??????")
      # nod - pipeline - plug - inst, ver, fps:{}, cap:{}, proc:{}
      instance_info = "{}:{}:{}:{}, {}, fps:{} S:{}x{}, W:{}x{}, cap:{}, proc:{}, tz:{}".format(
        self._device_id,
        self._stream_id,
        self._signature,
        self.cfg_instance_id,
        self.system_version,
        dct_input_metadata.get('fps'),
        dct_input_metadata.get('frame_w'),
        dct_input_metadata.get('frame_h'),
        img_witness.shape[1],
        img_witness.shape[0],
        cap_time.split('.')[0].split(' ')[1],
        self.time_to_str().split(' ')[1],
        self.log.timezone
      )

      return instance_info

    def _witness_post_process(self, img_witness, **kwargs):
      img_witness = super()._witness_post_process(img_witness, **kwargs)
      if self.cfg_simple_witness:  # draw image only if the application runs for `demo purposes`
        return img_witness

      if self.cfg_debug_on_witness:
        img_witness = self._painter.draw_text_outer_image(
          image=img_witness,
          text=[self.__get_instance_debug_info(img_witness=img_witness), str(self.get_serving_processes())],
          font=cv2.FONT_HERSHEY_SIMPLEX,
          font_size=0.4 if img_witness.shape[1] > 720 else 0.32,
          thickness=1,
          location="bottom",
        )

        img_witness = self._draw_debug_info_on_witness(img_witness)
      return img_witness

    def get_witness_image(self, img=None,
                          prepare_witness_kwargs=None,
                          pre_process_witness_kwargs=None,
                          draw_witness_image_kwargs=None,
                          post_process_witness_kwargs=None,
                          zone_only=False):
      if img is None and len(self.dataapi_images()) >= 1:
        # if no image provided just take default image received from upstream
        img = self.dataapi_image()

      if zone_only:
        return self.get_witness_image_zone_only(img=img)

      if pre_process_witness_kwargs is None:
        lst_plugin_positional_inferences = self.dataapi_image_plugin_positional_inferences(raise_if_error=False)
        if lst_plugin_positional_inferences is not None:
          pre_process_witness_kwargs = dict(lst_plugin_inferences=lst_plugin_positional_inferences)

      return super().get_witness_image(
        img=img,
        prepare_witness_kwargs=prepare_witness_kwargs,
        pre_process_witness_kwargs=pre_process_witness_kwargs,
        draw_witness_image_kwargs=draw_witness_image_kwargs,
        post_process_witness_kwargs=post_process_witness_kwargs
      )

    def get_witness_image_zone_only(self, img):
      self.start_timer('get_witness_image_zone_only')
      if img is not None:
        if not isinstance(img, np.ndarray):
          self.P(
            "`get_witness_image` called having a non numpy img {}. Returning no witness".format(repr(self), type(img)),
            color='error')
          return

      uses_BGR = False

      img_witness = self._witness_prepare(img)
      if img_witness is not None:
        img_witness = self._witness_pre_process(img_witness)
        uses_BGR = True

      if img_witness is not None:
        img_witness = self._witness_post_process(img_witness)
        # maybe we need to resize the whole thing so that is respects the model input size
        if len(self.cfg_resize_witness) == 2:
          h, w = self.cfg_resize_witness
          img_witness = self.log.center_image2(
            np_src=img_witness,
            target_h=h,
            target_w=w
          )

        # because we flipped channels in `_witness_prepare` to BGR in order to draw using cv2; now we come back to RGB..
        if not uses_BGR:
          img_witness = img_witness[:, :, ::-1]
      # endif

      self.stop_timer('get_witness_image_zone_only')
      return img_witness

    def process_wrapper(self):
      if not self.saved_zone and self.cfg_save_target_zone:
        # this section will force saving the witness in the local cache if the
        # witness was not previously saved.
        if self.dataapi_received_input():
          self.start_timer('save_zone')
          try:
            dir_path = os.path.join(self.log.get_target_folder('data'), 'zone_witnesses')
            save_path = os.path.join(dir_path, self._signature, self.get_stream_id())

            os.makedirs(save_path, exist_ok=True)
            img_witness = self.get_witness_image(zone_only=True)
            if img_witness is not None:
              fn = os.path.join(save_path, f'{self.cfg_instance_id}.jpg')
              cv2.imwrite(
                filename=fn,
                img=img_witness
              )
          except:
            pass
          self.saved_zone = True
          self.stop_timer('save_zone')
        # end if we have input
      # end if we have not previously saved the target zone
      return super(CVPluginExecutor, self).process_wrapper()

    @staticmethod
    def _maybe_process_meta_types(dct_plugin_inference, meta_type_mapping={}, ai_engine='', nms_iou_thr=0.6):
      detector_inferences = dct_plugin_inference.get(ai_engine, [])
      meta_types = list(meta_type_mapping.keys())
      # if we have no meta_types there is no reason to add them
      if len(meta_types) > 0:
        type_to_meta_type_map = {type: meta_type for meta_type, types in meta_type_mapping.items() for type in types}
        types_idx = {meta_type: it for it, meta_type in enumerate(meta_types)}
        other_cnt = len(meta_types) + 100
        for i, lst_2d in enumerate(detector_inferences):
          # top, left, bottom, right, confidence, class
          tlbrcc = []
          for inf in lst_2d:
            # if the current object's type belongs to a meta type we will add the meta_type
            if inf[ct.TYPE] in type_to_meta_type_map:
              current_meta_type = type_to_meta_type_map[inf[ct.TYPE]]
              inf[ct.META_TYPE] = current_meta_type
              type_id = types_idx[current_meta_type]
            else:
              type_id = other_cnt
              other_cnt += 1
            # endif inf[self.consts.TYPE] in self._type_to_meta_type_map
            # we will append the TLBR, confidence and type_id for nms
            tlbrcc.append(
              [
                *inf[ct.TLBR_POS],
                inf[ct.PROB_PRC],
                type_id
              ]
            )
          # endfor all inferences in image
          if len(tlbrcc) > 0:
            # if we have at least one detection we will filter out the overlapping objects with the same meta_type
            kept_objects_indexes = nms.class_non_max_suppression(
              predictions=np.array(tlbrcc),
              iou_threshold=nms_iou_thr
            )
            detector_inferences[i] = [inf for (j, inf) in enumerate(lst_2d) if kept_objects_indexes[j]]
        # endfor all images
      # endif len(meta_types) > 0
      return dct_plugin_inference

    def _pre_process(self):
      """
      Pre-process upstream data:
        1. Create list with ai-engine/model for each inference
        2. Filter within location with threshold
      """

      if self._coords_type == ct.COORDS_NONE and len(self.dataapi_images()) >= 1:
        img = self.dataapi_image()
        self._top, self._left, self._bottom, self._right = 0, 0, img.shape[-3], img.shape[-2]

      dct_global_inferences = self.dataapi_images_inferences()

      dct_plugin_inference = self._filter_confidence_threshold(dct_global_inferences)
      dct_plugin_inference = self._filter_object_types(dct_plugin_inference)
      dct_plugin_inference = self._maybe_process_meta_types(
        dct_plugin_inference,
        meta_type_mapping=self.cfg_meta_type_mapping,
        ai_engine=self._get_detector_ai_engine(),
        nms_iou_thr=self.cfg_meta_nms_iou_thr
      )

      # call tracker
      dct_plugin_inference = self._track_objects(dct_plugin_inference)

      dct_generic_plugin_inference, \
          dct_positional_plugin_inference = self._get_generic_positional_objects(dct_plugin_inference)

      dct_positional_inst_inference = self._keep_only_intersected_objects(dct_positional_plugin_inference)

      # now we can update/initiate tracking objects time within target zone
      dct_positional_inst_inference = self._track_in_zone_objects(dct_inference=dct_positional_inst_inference)
      # end track objects time in target zone

      dct_instance_inference = {**dct_generic_plugin_inference, **dct_positional_inst_inference}

      pre_process_outputs = {
        'DCT_PLUGIN_INFERENCES': dct_plugin_inference,
        'DCT_INSTANCE_INFERENCES': dct_instance_inference,

        'DCT_PLUGIN_POSITIONAL_INFERENCES': dct_positional_plugin_inference,
      }

      return pre_process_outputs

    def _post_process(self):
      if len(self.dataapi_images()) >= 1:
        lst_global_inferences = self.dataapi_image_global_inferences()
        lst_instance_inferences = self.dataapi_image_instance_inferences()
      else:
        lst_global_inferences, lst_instance_inferences = [], []

      if self._payload is not None:
        if self.alerter_is_new_alert() and ct.ALERT_OBJECTS not in vars(self._payload):
          vars(self._payload)[ct.ALERT_OBJECTS] = lst_instance_inferences
      else:
        # this is a legacy section
        if self.cfg_debug_payloads and self.need_refresh():  # if in "demo mode"
          self._payload = self._create_payload(
            img=self.get_witness_image()
          )
          self._add_alert_info_to_payload(self._payload)
          debug_objects = lst_global_inferences if self.cfg_debug_objects else "Not enabled"
          vars(self._payload)[ct.DEBUG_OBJECTS] = debug_objects
        # endif send "just image" payloads
      # endif

      if self.is_last_data:
        self._limited_data_finished = True

      self._maybe_disable_demo_mode()

      # reset debug info for next iteration
      self.reset_debug_info()

      return
  # endif

  def _maybe_disable_demo_mode(self):
    if self.demo_mode_time is None and self.cfg_demo_mode:
      self.demo_mode_time = self.time_alive
    elif not self.cfg_demo_mode:
      self.demo_mode_time = None

    if self.cfg_demo_mode and self.time_alive - self.demo_mode_time > self.cfg_demo_mode_time_limit:
      self.update_config_data({'DEMO_MODE': False, 'DEBUG_MODE': False})

  def validate_prc_intersect(self):

    prc_intersect = self.config_data.get(ct.PRC_INTERSECT, None)

    if prc_intersect == 0:
      msg = "Please be careful that 'PRC_INTERSECT' is configured to 0 in {}.".format(self.unique_identification)
      self.add_warning(msg)

    return

  def validate_points(self):
    points = self.config_data.get(ct.POINTS, [])

    if len(points) == 0:
      coords = ct.COORDS_NONE
    elif isinstance(points[0], int):
      coords = ct.COORDS_TLBR
    elif isinstance(points[0], list):
      coords = ct.COORDS_POINTS
    else:
      msg = "Cannot deduce coords type given the points {} in instance {}".format(points, self.unique_identification)
      self.add_error(msg)
      return
    # endif

    if coords == ct.COORDS_TLBR:
      if len(points) != 4:
        msg = "'POINTS' bad configured for COORDS=TLBR in instance {}. It should be a list that contains 4 integers".format(
          self.unique_identification)
        self.add_error(msg)
      elif None in points:
        msg = "'POINTS' bad configured for COORDS=TLBR (it contains None) in instance {}".format(
          self.unique_identification)
        self.add_error(msg)
      # endif
    # endif

    if coords == ct.COORDS_POINTS:
      if not all([len(x) == 2 for x in points]):
        msg = "'POINTS' bad configured for COORDS=POINTS in instance {}.".format(self.unique_identification)
        self.add_error(msg)
      elif None in self.log.flatten_2d_list(points):
        msg = "'POINTS' bad configured for COORDS=POINTS (it contains None) in instance {}".format(
          self.unique_identification)
        self.add_error(msg)
      # endif
    # endif

    return

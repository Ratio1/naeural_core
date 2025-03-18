from naeural_core.serving.base.basic_th import UnifiedFirstStage as ParentServingProcess
from naeural_core.local_libraries.nn.th.utils import th_resize_with_pad

_CONFIG = {
  **ParentServingProcess.CONFIG,

  "MODEL_CLASSES_FILENAME": "coco.txt",
  "URL_CLASS_NAMES": None,  # "minio:Y5/coco.txt",

  "MODEL_WEIGHTS_FILENAME": "20230723_y8s_nms.ths",
  "MODEL_WEIGHTS_FILENAME_DEBUG": "20230723_y8s_nms_top6.ths",

  "URL": "minio:Y8/20230723_y8s_nms.ths",
  "URL_DEBUG": "minio:Y8/20230723_y8s_nms_top6.ths",

  'IMAGE_HW': (448, 640),
  "DEBUG_SERVING": False,

  "PICKED_INPUT": "IMG",
  
  "COLOR_TAGGING": False,

  'MAX_BATCH_FIRST_STAGE': 8,

  'VALIDATION_RULES': {
    **ParentServingProcess.CONFIG['VALIDATION_RULES'],
  },
}

DEBUG_COVERED_SERVERS = False

__VER__ = '0.2.0.0'


class YfBase(ParentServingProcess):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    ver = kwargs.pop('version', None)
    if ver is None:
      ver = __VER__

    # this must be correctly configured by each individual serving process
    self._has_second_stage_classifier = False
    self.resized_input_images = None
    self.original_input_images = None
    super(YfBase, self).__init__( **kwargs)
    return

  @property
  def get_model_weights_filename(self):
    if self.cfg_debug_serving:
      return self.cfg_model_weights_filename_debug
    return self.cfg_model_weights_filename

  @property
  def get_url(self):
    if self.cfg_debug_serving:
      return self.cfg_url_debug
    return self.cfg_url

  @property
  def has_second_stage_classifier(self):
    return self._has_second_stage_classifier and self.server_name == self.predict_server_name
  
  

  def _get_model(self, config):
    if not isinstance(config, dict):
      config = {
        'ths' : (config, None)
      }
    #endif check for torchscript file config
    model, model_loaded_config, fn = self.prepare_model(
      backend_model_map=config,
      forced_backend=self.cfg_backend,
      post_process_classes=True,
      return_config=True,
      batch_size = self.cfg_max_batch_first_stage
    )
    # FIXME: is the model file path appropriate here to use as the config key?
    self.graph_config[fn] = model_loaded_config
    return model

  def _pre_process_images(self, images, return_original=False, normalize_original=False, half_original=False, **kwargs):
    lst_original_shapes = []
    # run GPU based pre-processing
    self._start_timer('resize_in_gpu')
    results = th_resize_with_pad(
      img=images,
      h=self.cfg_input_size[0],
      w=self.cfg_input_size[1],
      device=self.dev,
      normalize=True,  # Why below ?
      return_original=return_original,
      normalize_original=normalize_original,
      half_original=half_original,
      half=self.cfg_fp16
    )
    if len(results) < 3:
      prep_inputs, lst_original_shapes = results
    else:
      prep_inputs, lst_original_shapes, lst_original_images = results
      self.original_input_images = lst_original_images
    # endif check for results length
    
    self.resized_input_images = prep_inputs
    self._stop_timer('resize_in_gpu')

    self._lst_original_shapes = lst_original_shapes
    return prep_inputs

  def _make_crop(self, frame, ltrb, original_shape, return_offsets=False):
    l, t, r, b = self.scale_coords(
      img1_shape=self.cfg_input_size,
      coords=self.deepcopy(ltrb),
      img0_shape=original_shape,
    ).int().flatten().clamp(min=0)

    cropped_image = self.tv.transforms.functional.crop(
      img=frame,
      left=l.clamp(min=0),
      top=t.clamp(min=0),
      width=(r - l).clamp(min=4) + 1,
      height=(b - t).clamp(min=4) + 1,
    )

    return cropped_image if not return_offsets else (cropped_image, (l, t))

  def _second_stage_process(self, th_preds, th_inputs=None, **kwargs):
    th_pred_nms, th_n_dets = th_preds
    self.log.start_timer('slice_det')
    pred_nms = [x[:th_n_dets[i]] for i, x in enumerate(th_pred_nms)]
    self.log.stop_timer('slice_det')

    pred_second = None
    if self.has_second_stage_classifier:
      self.log.start_timer('2nd_stage_clf')
      pred_nms_second_stage = [x[:, :6] for x in pred_nms]
      pred_second = self._second_stage_classifier(pred_nms_second_stage, th_inputs)
      self.log.stop_timer('2nd_stage_clf')
    # endif

    self.log.start_timer('yolo_to_cpu')
    pred_nms_cpu = [x.cpu().numpy() for x in pred_nms]
    self.log.stop_timer('yolo_to_cpu')

    return pred_nms_cpu, pred_second

  def _aggregate_batch_predict(self, lst_results):
    th_preds_batch = self.th.cat([x[0] for x in lst_results])
    th_preds_n_det = self.th.cat([x[1] for x in lst_results])

    return th_preds_batch, th_preds_n_det

  def _post_process(self, preds):
    pred_nms_cpu, _ = preds
    nr_images = len(self._lst_original_shapes)
    lst_results = []
    for i in range(nr_images):
      # now we have each individual image and we generate all objects
      # what we need to do is to match `second_preds` to image id & then
      # match second clf with each box
      np_pred_nms_cpu = pred_nms_cpu[i]
      original_shape = self._lst_original_shapes[i]
      np_pred_nms_cpu[:, :4] = self.scale_coords(
        img1_shape=self.cfg_input_size,
        coords=np_pred_nms_cpu[:, :4],
        img0_shape=original_shape,
      ).round()
      lst_inf = []
      for det in np_pred_nms_cpu:
        det = [float(x) for x in det]
        # order is [left, top, right, bottom, proba, class] => [L, T, R, B, P, C, RP1, RC1, RP2, RC2, RP3, RC3]
        L, T, R, B, P, C = det[:6]
        # now check if color needs to be computed
        # the color tagging is done either in the serving or in the business logic
        # depending on the configuration and the target compute overhead
        # for heavy inference it is better to do it in the business logic
        color_tag = None
        if self.cfg_color_tagging:
          pass
        # endif compute color
        dct_obj = {
          self.consts.TLBR_POS: [int(T), int(L), int(B), int(R)],
          self.consts.PROB_PRC: round(float(P), 2),
          self.consts.TYPE: self.class_names[int(C)] if self.class_names is not None else C,          
          # additional properties such as color, etc.
          self.consts.COLOR_TAG: color_tag,
        }
        if self.cfg_debug_serving:
          dct_obj['CANDIDATES'] = [
            [x, self.class_names[int(y)] if self.class_names is not None else y]
            for (x, y) in list(zip(det[6:][::2], det[6:][1::2]))
          ]
        lst_inf.append(dct_obj)
      lst_results.append(lst_inf)
    return lst_results

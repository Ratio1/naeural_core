"""
  TODO:
    Add GPU SYNC !
"""
from naeural_core import constants as ct
from naeural_core.serving.base import UnifiedFirstStage as ParentServingProcess
from naeural_core.local_libraries.nn.th.utils import th_resize_with_pad

_CONFIG = {
  **ParentServingProcess.CONFIG,

  'IMAGE_HW': (840, 840),  # The model is size-invariant, but is trained on this size ( 1080, 1920),#
  'MAX_BATCH_FIRST_STAGE': 5,
  "MODEL_WEIGHTS_FILENAME": "20230723_RET_FACE_RES_bs1_nms.ths",
  "URL": "minio:retina_face/20230723_RET_FACE_RES_bs1_nms.ths",


  'VALIDATION_RULES': {
    **ParentServingProcess.CONFIG['VALIDATION_RULES'],
  },
}

__VER__ = '0.1.0.0'


class ThRface(ParentServingProcess):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    ver = kwargs.pop('version', None)
    if ver is None:
      ver = __VER__

    # this must be correctly configured by each individual serving process
    self._has_second_stage_classifier = False
    self.resized_input_images = None
    super(ThRface, self).__init__(version=ver, **kwargs)
    return

  def _startup(self):
    super(ThRface, self)._startup()
    # self.norm_sub_tensor = th.Tensor([123, 117, 104]).reshape((3,1,1)).to(self.dev)
    self.norm_sub_tensor = self.th.Tensor([104, 117, 123]).reshape((3, 1, 1)).to(self.dev)

  @property
  def has_second_stage_classifier(self):
    return self._has_second_stage_classifier and self.server_name == self.predict_server_name

  def _get_model(self, config):
    model = self._prepare_ts_model(fn_model=config, post_process_classes=False)
    return model

  def _pre_process_images(self, images):
    lst_original_shapes = []

    # Images must be BGR
    images = [self.cv2.cvtColor(x, self.cv2.COLOR_RGB2BGR) for x in images]

    # run GPU based pre-processing
    self._start_timer('resize')
    prep_inputs, lst_original_shapes = th_resize_with_pad(
      img=images,
      h=self.cfg_input_size[0],
      w=self.cfg_input_size[1],
      device=self.dev,
      normalize=True,
      sub_val=self.norm_sub_tensor,
      div_val=1,
      half=self.cfg_fp16
    )
    self._stop_timer('resize')

    self._lst_original_shapes = lst_original_shapes
    return prep_inputs

  def _aggregate_batch_predict(self, lst_results):
    th_preds_batch = self.th.cat([x[0] for x in lst_results])
    th_preds_n_det = self.th.cat([x[1] for x in lst_results])

    return th_preds_batch, th_preds_n_det

  def _second_stage_process(self, th_preds, th_inputs=None, **kwargs):
    th_pred_nms, th_n_dets = th_preds
    self.log.start_timer('slice_det')
    pred_nms = [x[:th_n_dets[i]] for i, x in enumerate(th_pred_nms)]
    self.log.stop_timer('slice_det')

    pred_second = None
    if self.has_second_stage_classifier:
      self.log.start_timer('2nd_stage_clf')
      pred_second = self._second_stage_classifier(pred_nms, th_inputs)
      self.log.stop_timer('2nd_stage_clf')
    # endif

    self.log.start_timer('yolo_to_cpu')
    pred_nms_cpu = [x.cpu().numpy() for x in pred_nms]
    self.log.stop_timer('yolo_to_cpu')

    return pred_nms_cpu, pred_second

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

      if np_pred_nms_cpu.shape[0] != 0:
        denorm_array = self.np.array(
          [self.cfg_input_size[1], self.cfg_input_size[0]] * 2 +
          [1] +
          [self.cfg_input_size[1], self.cfg_input_size[0]] * 5
        )
        np_pred_nms_cpu_denormed = np_pred_nms_cpu * denorm_array
        np_pred_nms_cpu[:, :4] = self.scale_coords(
          img1_shape=self.cfg_input_size,
          coords=np_pred_nms_cpu_denormed[:, :4],
          img0_shape=original_shape,
        ).round()

        np_pred_nms_cpu[:, 5:] = self.scale_coords(
          img1_shape=self.cfg_input_size,
          coords=np_pred_nms_cpu_denormed[:, 5:],
          img0_shape=original_shape,
        ).round()

      ####

      lst_inf = []
      for det in np_pred_nms_cpu:
        det = [float(x) for x in det]
        # order is [left, top, right, bottom, proba] =>
        # [L, T, R, B, P, P1x, P1y, P2x, P2y, P3x, P3y, P4x, P4y, P5x, P5y]
        # where Pix and Piy are coordinates for Pi and
        # Pi is from [right ear, left eat, nose, right mouth corner, left mouth corner]
        L, T, R, B, P = det[:5]  # order is [left, top, right, bottom, proba]
        kpts = det[5:]
        dct_obj = {
          ct.TLBR_POS: [int(T), int(L), int(B), int(R)],
          ct.PROB_PRC: round(float(P), 2),
          ct.TYPE: "FACE",
          "KEYPOINTS": [(int(kpts[i]), int(kpts[i + 1])) for i in range(0, len(kpts), 2)]
        }
        lst_inf.append(dct_obj)
      lst_results.append(lst_inf)
    return lst_results

"""
  TODO:
    Add GPU SYNC !
"""
from naeural_core.serving.base import UnifiedFirstStage as BaseServingProcess

from plugins.serving.architectures.th_effdet.backbone import EfficientDetBackbone
from plugins.serving.architectures.th_effdet.utils import BBoxTransform, ClipBoxes

_CONFIG = {
  **BaseServingProcess.CONFIG,
  'VALIDATION_RULES': {
    **BaseServingProcess.CONFIG['VALIDATION_RULES'],
  },

  "PICKED_INPUT"    : "IMG",
  "URL": "https://www.dropbox.com/s/pxl7ugf13e3ed0g/efficientdet-d7.pth?dl=1",
  "URL_CLASS_NAMES": "https://www.dropbox.com/s/zpiugi1pvje1bbc/class_names.txt?dl=1",
  "MODEL_NAME"      : "effdetd7",
  "DEFAULT_DEVICE"  : "cuda:0",

  'NMS_CONF_THR'    : 0.20,
  'NMS_IOU_THR'     : 0.20,
  # 'NMS_MAX_DET'     : 300,
  'COMPOUND_COEF'   : 7,
  'ANCHOR_RATIOS'    : [(1.0, 1.0), (1.4, 0.7), (0.7, 1.4)],
  'ANCHOR_SCALES'   : [2 ** 0, 2 ** (1.0 / 3.0), 2 ** (2.0 / 3.0)],
  'INPUT_SIZES'     : [512, 640, 768, 896, 1024, 1280, 1280, 1536, 1536],

  # 'IMAGE_HW'        : (),#(896, 1280),
  'STRIDE'          : 64,

  'GPU_PREPROCESS'  : True,
  'MAX_BATCH_FIRST_STAGE' : 5
}

__VER__ = '0.1.0.0'


class ThEffDet7(BaseServingProcess):
  CONFIG = _CONFIG
  def __init__(self, **kwargs):
    ver = kwargs.pop('version', None)
    if ver is None:
      ver = __VER__

    self.regress_boxes = BBoxTransform()
    self.clip_boxes = ClipBoxes()

    self._has_second_stage_classifier = False
    super(ThEffDet7, self).__init__( **kwargs)
    return

  @property
  def cfg_gpu_preprocess(self):
    return self.config_model.get('GPU_PREPROCESS')

  @property
  def cfg_compound_coef(self):
    return self.config_model.get('COMPOUND_COEF')

  @property
  def cfg_anchor_ratios(self):
    return self.config_model.get('ANCHOR_RATIOS')

  @property
  def cfg_anchor_scales(self):
    return self.config_model.get('ANCHOR_SCALES')

  @property
  def cfg_conf_thr(self):
    return self.config_model[self.ct.NMS_CONF_THR]

  @property
  def cfg_iou_thr(self):
    return self.config_model[self.ct.NMS_IOU_THR]

  @property
  def cfg_input_sizes(self):
    return self.config_model.get('INPUT_SIZES')

  @property
  def cfg_input_size(self):
    return (self.cfg_input_sizes[self.cfg_compound_coef], self.cfg_input_sizes[self.cfg_compound_coef])

  @property
  def has_second_stage_classifier(self):
    return self._has_second_stage_classifier and self.server_name == self.predict_server_name

  def _get_model(self, fn):
    model = self._prepare_effdet_model(fn=fn)
    return model

  def _prepare_effdet_model(self, fn):
    model_name = self.config_model[self.ct.MODEL_NAME]
    if model_name not in self.cfg_url:
      self.P("WARNING: Make sure the weights url is valid - Model:{}, URL:{}".format(
        model_name, self.cfg_url), color='red')

    model = EfficientDetBackbone(
      compound_coef=self.cfg_compound_coef,
      num_classes=len(self.class_names), #obj_list
      ratios=self.cfg_anchor_ratios,
      scales=self.cfg_anchor_scales
    )
    if len(self.os_path.split(fn)[0]) < 1:
      # this means the folder was not specified
      fn = self.log.get_models_file(fn)
    # endif

    self.P("  Loading weights from {}...".format(fn))
    model.load_state_dict(self.th.load(fn, map_location='cpu'))
    model.eval()
    self.P("  Done creating model.")
    self.P("  Model is accepting lists or variable size images.")
    self.P("  Tensor input size is {}".format(self.cfg_input_size))
    return model

  def _pre_process_images(self, images):
    lst_original_shapes = []
    # run GPU based pre-processing
    # TODO: maybe implement cpu-based as well
    self._start_timer('resize_in_gpu')
    prep_inputs, lst_original_shapes = self.th_resize_with_pad(
      img=images,
      h=self.cfg_input_size[0],
      w=self.cfg_input_size[1],
      device=self.dev,
      normalize=False
    )
    self._stop_timer('resize_in_gpu')

    if self.cfg_fp16:
      prep_inputs = prep_inputs.half()
    self._lst_original_shapes = lst_original_shapes
    return prep_inputs

  def _aggregate_batch_predict(self, lst_results):
    regression, classification = [self.th.cat([y[k] for y in lst_results]) for k in range(1, len(lst_results[0]) - 1)]
    anchors = lst_results[0][-1]
    return None, regression, classification, anchors

  def _post_process_effdet(self, x, anchors, regression, classification):
    self.log.start_timer("post_process_effdet")
    transformed_anchors = self.regress_boxes(anchors, regression)
    transformed_anchors = self.clip_boxes(transformed_anchors, x)
    scores = self.th.max(classification, dim=2, keepdim=True)[0]
    scores_over_thresh = (scores > self.cfg_conf_thr)[:, :, 0]
    out = []
    for i in range(x.shape[0]):
      if scores_over_thresh[i].sum() == 0:
        out.append({
          'rois': self.np.array(()),
          'class_ids': self.np.array(()),
          'scores': self.np.array(()),
        })
        continue

      classification_per = classification[i, scores_over_thresh[i, :], ...].permute(1, 0)
      transformed_anchors_per = transformed_anchors[i, scores_over_thresh[i, :], ...]
      scores_per = scores[i, scores_over_thresh[i, :], ...]
      scores_, classes_ = classification_per.max(dim=0)
      anchors_nms_idx = self.tv.ops.boxes.batched_nms(transformed_anchors_per, scores_per[:, 0], classes_, iou_threshold=self.cfg_iou_thr)

      if anchors_nms_idx.shape[0] != 0:
        classes_ = classes_[anchors_nms_idx]
        scores_ = scores_[anchors_nms_idx]
        boxes_ = transformed_anchors_per[anchors_nms_idx, :]

        out.append({
          'rois': boxes_.cpu().numpy(),
          'class_ids': classes_.cpu().numpy(),
          'scores': scores_.cpu().numpy(),
        })
      else:
        out.append({
          'rois': self.np.array(()),
          'class_ids': self.np.array(()),
          'scores': self.np.array(()),
        })
    self.log.end_timer("post_process_effdet")

    return out

  def _second_stage_process(self, th_preds, th_inputs=None, **kwargs):
    _, regression, classification, anchors = th_preds

    eff_det_preds = self._post_process_effdet(th_inputs, anchors, regression, classification)

    # now we can call a classified on each individual box

    pred_second = None
    if self.has_second_stage_classifier:
      self.log.start_timer('2nd_stage_clf')
      pred_second = self._second_stage_classifier(
        first_stage_out=eff_det_preds, th_inputs=th_inputs
      )
      self.log.stop_timer('2nd_stage_clf')
    #endif

    return eff_det_preds, pred_second

  def scale_coords_effdet(self, img1_shape, coords, img0_shape):
    # Rescale coords (xyxy) from img1_shape to img0_shape
    gain = min(img1_shape[0] / img0_shape[0], img1_shape[1] / img0_shape[1])  # gain  = old / new
    pad = (img1_shape[1] - img0_shape[1] * gain) / 2, (img1_shape[0] - img0_shape[0] * gain) / 2  # wh padding

    coords[[0, 2]] -= pad[0]  # x padding
    coords[[1, 3]] -= pad[1]  # y padding
    coords[:4] /= gain
    if isinstance(coords, self.th.Tensor):  # faster individually; #Todo: i think this doesn't work anymore
      coords[:, 0].clamp_(0, img0_shape[1])  # x1
      coords[:, 1].clamp_(0, img0_shape[0])  # y1
      coords[:, 2].clamp_(0, img0_shape[1])  # x2
      coords[:, 3].clamp_(0, img0_shape[0])  # y2
    else:  # np.array (faster grouped)
      coords[[0, 2]] = coords[[0, 2]].clip(0, img0_shape[1])  # x1, x2
      coords[[1, 3]] = coords[[1, 3]].clip(0, img0_shape[0])  # y1, y2

    coords[[0,1]] = coords[[1,0]]
    coords[[2,3]] = coords[[3,2]]
    return coords

  def _post_process(self, preds):
    eff_det_preds, _ = preds

    lst_results = []
    for i, img_pred in enumerate(eff_det_preds):
      lst_inf = []
      no_preds = img_pred['rois'].shape[0]
      original_shape = self._lst_original_shapes[i]
      for j in range(no_preds):
        dct_obj = {
          self.ct.TLBR_POS: self.scale_coords_effdet(
            img0_shape=original_shape,
            img1_shape=self.cfg_input_size,
            coords=img_pred['rois'][j]
          ).astype("uint32").tolist(),
          self.ct.PROB_PRC: img_pred['scores'][j].round(2),
          self.ct.TYPE: self.class_names[int(img_pred['class_ids'][j])]
        }
        lst_inf.append(dct_obj)
      #endfor
      lst_results.append(lst_inf)
    #endfor

    return lst_results


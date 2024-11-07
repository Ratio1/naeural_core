import torch as th

from naeural_core.serving.default_inference.th_yf8l import ThYf8l as BaseServingProcess

_CONFIG = {
  **BaseServingProcess.CONFIG,

  "DEBUG_TIMERS": False,
  'MAX_BATCH_SECOND_STAGE': 20,

  'COVERED_SERVERS': ['th_yf8l'],

  #####################
  "MSPN_URL": "minio:MSPN/ThYoloMSPN_bs1.ths",
  "MSPN_FILENAME": "ThYoloMSPN_bs1.ths",

  "MSPN_ONNX_URL" : "minio:MSPN/20240430_ThYoloMSPN_bs1.onnx",
  "MSPN_ONNX_FILENAME" : "20240430_ThYoloMSPN_bs1.onnx",
  "MSPN_BACKEND" : None,

  "MSPN_IMG_SHAPE": (256, 192),
  "NORM_MEANS": [0.406, 0.456, 0.485],
  "NORM_STDS": [0.225, 0.224, 0.229],
  "CROP_ORIGINAL": True,

  'VALIDATION_RULES': {
    **BaseServingProcess.CONFIG['VALIDATION_RULES'],
  },
}

DEBUG_COVERED_SERVERS = False


class ThYfMspn(BaseServingProcess):
  CONFIG = _CONFIG

  def get_mspn_model_config(self):
    """
    The filename of the selected mspn model.
    """
    # First try to get the TensorRT/ONXN config
    if self.cfg_mspn_onnx_filename is not None:
      onnx_config = self.graph_config.get(self.cfg_mspn_onnx_filename)
      if onnx_config is not None:
        return onnx_config
    #endif onnx config
    # No TensorRT/ONNX model was selected, we must have used ths.
    return self.graph_config[self.cfg_mspn_filename]

  def __init__(self, **kwargs):
    super(ThYfMspn, self).__init__(**kwargs)
    self._has_second_stage_classifier = True
    return

  def model_call_mspn(self, th_inputs, model):
    movenet_preds = model(th_inputs)
    return self._post_process_mspn(movenet_preds, warmup=True)

  def _mspn_warmup(self):
    self.model_warmup_helper(
      model=self.mspn_model,
      model_call_method=self.model_call_mspn,
      input_shape=(3, *self.cfg_mspn_img_shape),
      max_batch_size=self.cfg_max_batch_second_stage,
      model_name='mspn'
    )
    return

  def _load_mspn_model(self):
    # Note that we use the same onnx file for trt/onnx/openvino.
    config = {
      'ths' : (self.cfg_mspn_filename, self.cfg_mspn_url),
      'trt' : (self.cfg_mspn_onnx_filename, self.cfg_mspn_onnx_url),
      'onnx' : (self.cfg_mspn_onnx_filename, self.cfg_mspn_onnx_url),
      'openvino' : (self.cfg_mspn_onnx_filename, self.cfg_mspn_onnx_url),
    }
    self.mspn_model, model_loaded_config, fn = self.prepare_model(
      backend_model_map=config,
      forced_backend=self.cfg_mspn_backend,
      return_config=True,
      batch_size=self.cfg_max_batch_second_stage
    )
    self.graph_config[fn] = model_loaded_config

    self.transforms = self.tv.transforms.Compose([
      self.tv.transforms.Resize(self.cfg_mspn_img_shape),
      self.tv.transforms.Normalize(
        mean=self.cfg_norm_means,  # cfg.INPUT.MEANS,
        std=self.cfg_norm_stds  # cfg.INPUT.STDS
      )
    ])

    self._mspn_warmup()
    return

  def _second_stage_model_load(self):
    self._load_mspn_model()
    return

  def _pre_process_images(self, images, **kwargs):
    return super(ThYfMspn, self)._pre_process_images(
      images=images,
      return_original=True,
      normalize_original=True,
      **kwargs
    )

  def _post_process(self, preds):
    yolo_preds, second_preds = preds
    lst_yolo_results = super(ThYfMspn, self)._post_process(preds)
    if second_preds is not None:
      for i, yolo_result in enumerate(lst_yolo_results):
        for j, crop_results in enumerate(yolo_result):
          if crop_results['TYPE'] == 'person':
            crop_results["JOINTS"] = second_preds[i][j]
          # endif
        # endfor
      # endfor
    # endif
    return lst_yolo_results

  @staticmethod
  def _aggregate_second_stage_batch_predict(lst_results):
    return th.cat(lst_results)

  @staticmethod
  def unravel_index(
      indices: th.LongTensor,
      shape,
  ) -> th.LongTensor:
    r"""Converts flat indices into unraveled coordinates in a target shape.

    This is a `torch` implementation of `numpy.unravel_index`.

    Args:
        indices: A tensor of indices, (*, N).
        shape: The targeted shape, (D,).

    Returns:
        unravel coordinates, (*, N, D).
    """

    shape = th.tensor(shape)
    indices = indices % shape.prod()  # prevent out-of-bounds indices

    coord = th.zeros(indices.size() + shape.size(), dtype=int)

    for i, dim in enumerate(reversed(shape)):
      coord[..., i] = indices % dim
      indices = th.div(indices, dim, rounding_mode='floor')

    return coord.flip(-1)

  def _post_process_mspn(self, outputs, kernel=5, shifts=[0.25], warmup=False):
    if not warmup:
      self._start_timer("pproc_img")

    nr_img = outputs.shape[0]
    score_maps = self.th.clone(outputs)
    score_maps = score_maps / 255 + 0.5

    kps = self.th.zeros((nr_img, self.get_mspn_model_config()['DATASET_KEYPOINT_NUM'], 2), device=self.dev)
    dr = self.th.clone(outputs)

    # We need to apply to blur operation on each "channel" (keypoint probability distribution) individually.
    #   In order to achieve this we reshape from (no_imgs, no_kpts, out_h, out_w) to (no_imgs * no_kpts, 1, out_h, out_w)
    #   then apply the blur then reshape again to (no_imgs, no_kpts, out_h, out_w)
    old_shape = dr.shape
    new_shape = (dr.shape[0] * dr.shape[1], 1, dr.shape[2], dr.shape[3])
    dr = dr.reshape(*new_shape)

    # Apply blur
    sigma = 0.3 * ((kernel - 1) * 0.5 - 1) + 0.8
    dr = self.tv.transforms.GaussianBlur(kernel, sigma)(dr)
    dr = dr.reshape(*old_shape)

    # Next we need to find the argmax for each keypoint "channel". In order to do this we need to reshape from
    #   (no_imgs, no_kpts, out_h, out_w) to (no_imgs * no_kpts, out_h * out_w) apply argmax and then unravel the index
    #   resulting in an index tensor with the shape (no_imgs, no_kpts, 2) that contains the x and y coord for each keypoint
    argmax_shape = (dr.shape[0] * dr.shape[1], dr.shape[2] * dr.shape[3])
    lbs = dr.reshape(*argmax_shape).argmax(dim=1)
    indexes = self.unravel_index(lbs, dr.shape[-2:]).reshape(*old_shape[:2], 2)

    # Mspn postprocess requires for some reason the make the maximums 0 and redo the argmax operation and find
    #   a new set of indexes `pindexes`
    lst_index_tensors = [self.th.Tensor([i for i in range(argmax_shape[0])]).to(self.dev).long(), lbs]

    zeros = self.th.zeros([1], device=self.dev)
    if self.cfg_fp16:
      zeros = zeros.half()
    dr = dr.reshape(*argmax_shape).index_put(
      lst_index_tensors,
      zeros
    ).reshape(*old_shape)

    lbs = dr.reshape(*argmax_shape).argmax(dim=1)
    pindexes = self.unravel_index(lbs, dr.shape[-2:]).reshape(*old_shape[:2], 2)

    pindexes -= indexes

    # Now we need to apply a shift to the indexes that have the second norm of their respective p indexes > 1e-2
    lns = ((pindexes[:, :, 0] ** 2 + pindexes[:, :, 1] ** 2) ** 0.5).unsqueeze(-1)
    indexes = indexes.float()
    indexes += (lns >= 1e-3) * (shifts[0] * pindexes / lns)

    # Next we rescale the value of the indexes to match the image shape
    indexes[:, :, 0] = self.th.clamp(indexes[:, :, 0], min=0, max=self.cfg_mspn_img_shape[0] - 1)
    indexes[:, :, 1] = self.th.clamp(indexes[:, :, 1], min=0, max=self.cfg_mspn_img_shape[1] - 1)
    indexes_scaled = indexes * 4 + 2

    kps[:, :, 0] = indexes_scaled[:, :, 1]
    kps[:, :, 1] = indexes_scaled[:, :, 0]

    # And least, we need to select the confidence score for each kpt by getting the value from the core maps
    #   First we cast the index to long
    int_indexes = (indexes.round() + 1e-9).long()

    #   Next we reshape the score map from (no_imgs, no_kpts, out_h, out_w) to (no_imgs * no_kpts, out_h * out_w) in
    #      order to use advanced indexing - there may be some solution without reshape, but I did not manage to find it
    scr_mps_rs = score_maps.reshape(
      (score_maps.shape[0] * score_maps.shape[1], score_maps.shape[2] * score_maps.shape[3])
    )
    #   Next we also need to reshape the indexes from (no_imgs, no_kpts, 2) to (no_imgs * no_kpts, 1)
    raveled_indexes = (int_indexes[:, :, 0] * score_maps.shape[3] + int_indexes[:, :, 1]).reshape(
      int_indexes.shape[0] * int_indexes.shape[1], 1
    )
    #   Finally, select the values and reshape the scores tensor to (no_imgs, no_kpts, 1)
    scores = scr_mps_rs[
      [i for i in range(raveled_indexes.shape[0])],
      raveled_indexes.squeeze(-1)
    ].reshape(score_maps.shape[0], score_maps.shape[1], 1)

    # Concat the keypoints and scores and normalize the positions to (0, 1)
    preds = self.th.cat([kps, scores], dim=2) / self.th.tensor([self.cfg_mspn_img_shape[1], self.cfg_mspn_img_shape[0], 1],
                                                               device=self.dev)

    if not warmup:
      self._stop_timer("pproc_img")

    return preds

  def mspn_stage(self, pred_nms, th_inputs):
    """
    input:
      Im1 (1 pers 1 dog 1 cat)
      Im2 (2 pers)
      Im3 (3 pers)
    output:
      [
       ["ON", None, None],
       ["OFF", "WRONG"]
       ["OFF", "ON", "ON"]
      ]


    :param pred_nms:
    :param th_inputs:
    :return:
      list(list<pred_nms>)

      list(
        im1: list(pred1, pred2 ...)
        im2: list(pred3, pred4, ...)
        im2: list(pred5, pred6, ...)
      )

      Iterate over pred_nms
      Create 2 lists :
        1. Each individual crop:
          Ex:
          [
            pers_img, dog_img, cat_img, pers_img, pers_img, pers_img, pers_img, pers_img,
          ]
        2. Crop image identity
          Ex:
          [
            0 0 0 1 1 2 2 2
          ]
      =====
      for each image crop tlbr from tensor; add to input list

      batch = th_resize_with_crop(input_list)
    """

    crop_imgs = []
    masks = []
    self._start_timer("crop")
    for i, pred in enumerate(pred_nms):
      pred_mask = (pred[:, 5] == self.class_names.index('person'))  # TODO: get `person` id
      masks.append(pred_mask.tolist())
      for i_crop in range(pred.shape[0]):
        if pred_mask[i_crop]:
          # in the future this check will disappear
          if not self.cfg_crop_original:
            crop_imgs.append(
              self.tv.transforms.functional.crop(
                th_inputs[i, :],
                left=max(pred[i_crop, 0].int(), 0),
                top=max(pred[i_crop, 1].int(), 0),
                width=max(pred[i_crop, 2].int(), 0) - max(pred[i_crop, 0].int(), 0) + 1,
                height=max(pred[i_crop, 3].int(), 0) - max(pred[i_crop, 1].int(), 0) + 1,
              )
            )
          else:
            crop_imgs.append(
              self._make_crop(
                frame=self.original_input_images[i],
                ltrb=pred[i_crop, :4].unsqueeze(0),
                original_shape=self._lst_original_shapes[i],
                return_offsets=False
              )
            )
        # endif
      # endfor
    # endfor
    self._stop_timer("crop")

    if len(crop_imgs) == 0:
      return [], []

    self._start_timer("resize")
    batch = self.th.cat([
      self.transforms(x.unsqueeze(0))
      for x in crop_imgs
    ])
    self._stop_timer("resize")

    self._start_timer("mspn_p_b{}_{}".format(len(batch), self.cfg_max_batch_second_stage))
    mspn_preds = self._batch_predict(
      prep_inputs=batch,
      model=self.mspn_model,
      batch_size=self.cfg_max_batch_second_stage,
      aggregate_batch_predict_callback=self._aggregate_second_stage_batch_predict
    )
    self._stop_timer("mspn_p_b{}_{}".format(len(batch), self.cfg_max_batch_second_stage))

    return mspn_preds, masks

  def compute_pred_indexes(self, masks):
    l_idxs = []
    k = 0
    cumsums = [self.np.cumsum(mask) for mask in masks]
    for i in range(len(masks)):
      l_idxs.append([
        cumsums[i][j] - 1 + k if masks[i][j] else None
        for j in range(len(masks[i]))
      ])
      k += cumsums[i][-1] if len(cumsums[i]) > 0 else 0
    # endfor
    return l_idxs

  def _second_stage_classifier(self, pred_nms, th_inputs):
    mspn_preds, masks = self.mspn_stage(pred_nms=pred_nms, th_inputs=th_inputs)
    if len(mspn_preds) == 0:
      return None
    results = []

    self._start_timer("pproc")
    move_preds = self._post_process_mspn(mspn_preds)
    self._stop_timer("pproc")

    self._start_timer("agg_res")
    move_preds_c = move_preds.cpu().numpy()
    l_idxs = self.compute_pred_indexes(masks)
    for idxs in l_idxs:
      results.append([
        self.np.round(move_preds_c[idx], 3) if idx is not None else None
        for idx in idxs
      ])
    self._stop_timer("agg_res")
    return results

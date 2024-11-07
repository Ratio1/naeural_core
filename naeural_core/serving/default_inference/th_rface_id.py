"""
  TODO:
    Add GPU SYNC !
"""


import torchvision.transforms as T

from naeural_core import constants as ct
from naeural_core.serving.default_inference.th_rface import ThRface as ParentServingProcess

_CONFIG = {
  **ParentServingProcess.CONFIG,
  # PARAMETERS USED FOR THE `FACE_ID` MODEL
  'FACEID_MODEL_FILENAME': 'MobileFaceNetV3_large_ArcFace_glint360k_cosface_r100_fp16_0.1_cosine_student.ths',
  'FACEID_MODEL_URL': 'minio:FaceIdentification/MobileFaceNetV3_large_ArcFace_glint360k_cosface_r100_fp16_0.1_cosine_student.ths',
  'FACEID_INPUT_SIZE': (3, 112, 112),
  "MAX_BATCH_SECOND_STAGE": 5,

  "FACEID_IMAGE_HW": (112, 112),
  # USE_AMP"                     : False,

  'VALIDATION_RULES': {
    **ParentServingProcess.CONFIG['VALIDATION_RULES'],
  },
}

__VER__ = '0.1.0.0'


class ThRfaceId(ParentServingProcess):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    ver = kwargs.pop('version', None)
    if ver is None:
      ver = __VER__

    super(ThRfaceId, self).__init__(version=ver, **kwargs)
    self._has_second_stage_classifier = True
    # TODO(Adi): choose between resize and resize with pad
    return

  def _startup(self):
    super()._startup()
    self._face_id_transforms = T.Compose([
       T.Resize(size=(self.cfg_faceid_image_hw[0], self.cfg_faceid_image_hw[1])),
        #  PreprocessResizeWithPad(h=self.cfg_faceid_image_hw[0], w=self.cfg_faceid_image_hw[1], normalize=False),
    ])

  def _seconde_stage_aggregate_batch_predict_callback(self, lst_th_x):
    return self.th.cat(lst_th_x) if len(lst_th_x) > 0 else self.th.tensor([])

  def _second_stage_classifier(self, lst_th_faces_cropped):
    """ This will be the stage where we embed faces and return embeddings """

    # get batch size for each image
    result_mapping = [len(x) for x in lst_th_faces_cropped]

    # concat all images to batch predict all images at once
    th_faces_batch = self.th.cat(lst_th_faces_cropped)

    # run a batch predict with all crops
    th_results = self._batch_predict(
      prep_inputs=th_faces_batch,
      model=self.faceid,
      batch_size=self.cfg_max_batch_second_stage,
      aggregate_batch_predict_callback=self._seconde_stage_aggregate_batch_predict_callback
    )

    # extract batch of embeddings for each image
    face_features = []
    for b_size in result_mapping:
      face_features.append(th_results[:b_size])
      th_results = th_results[b_size:]
    return face_features, lst_th_faces_cropped

  def _scale_coords(self, lst_np_pred_nms_cpu):
    lst_np_pred_nms_cpu_denorm = []
    for i in range(len(lst_np_pred_nms_cpu)):
      np_pred_nms_cpu = lst_np_pred_nms_cpu[i].cpu().numpy()
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

        lst_np_pred_nms_cpu_denorm.append(np_pred_nms_cpu)
      else:
        lst_np_pred_nms_cpu_denorm.append([])
    return lst_np_pred_nms_cpu_denorm

  def _crop_faces(self, lst_th_pred_nms, lst_th_image):
    lst_lst_th_face_cropped = []

    # denorm input to be 0-255 uint8
    lst_th_image += self.norm_sub_tensor
    lst_th_image = lst_th_image.to(dtype=self.th.uint8)

    for th_preds, th_image in zip(lst_th_pred_nms, lst_th_image):
      lst_image_crops = []
      for th_pred in th_preds:

        L, T, R, B, P = th_pred[:5].cpu()
        L = int(L * self.cfg_input_size[1])
        R = int(R * self.cfg_input_size[1])
        T = int(T * self.cfg_input_size[0])
        B = int(B * self.cfg_input_size[0])
        H = th_image.shape[1]
        W = th_image.shape[2]

        if P < 0.3:
          # maybe ignore this image
          pass
        # Here we create a new image that is supposed to have the original dimensions of the detected box
        # and fill it with the corresponding data from the original image. If the detection is outside the image,
        # we fill the extra space with black
        th_crop = self.th.zeros((3, B - T, R - L), dtype=th_image.dtype, device=th_image.device)
        th_crop[:, max(0, -T):min(B - T, H - T), max(0, -L): min(R - L, W - L)] = \
            th_image[:, max(0, T): min(B, H), max(0, L): min(R, W)]
        # th_crop = th_image[:, T:B, L:R]
        lst_image_crops.append(th_crop)
      lst_lst_th_face_cropped.append(lst_image_crops)
    return lst_lst_th_face_cropped

  def _second_stage_pre_process(self, pred_nms, th_inputs):
    preds_nms_denormed = self._scale_coords(pred_nms)

    # cut the images according to `preds_nms_denormed`
    lst_lst_th_face_cropped = self._crop_faces(pred_nms, th_inputs)

    """    
    image = cap.read()
    
    image = cv2.resize(image, (96, 96)) 
    img = image[...,::-1]
    img = np.around(np.transpose(img, (2,0,1))/255.0, decimals=12)
    x_train = np.array([img])
    embedding = model.predict(x_train)
    """

    lst_th_faces_cropped = [self.th.stack([self._face_id_transforms(th_face) for th_face in lst_th_face])
                            if len(lst_th_face) > 0 else self.th.tensor([], dtype=self.th.uint8, device=self.dev)
                            for lst_th_face in lst_lst_th_face_cropped
                            ]

    return preds_nms_denormed, lst_th_faces_cropped

  def _retina_post_process(self, th_preds):
    th_pred_nms, th_n_dets = th_preds
    self.log.start_timer('slice_det')
    pred_nms = [x[:th_n_dets[i]] for i, x in enumerate(th_pred_nms)]
    self.log.stop_timer('slice_det')

    return pred_nms

  def _second_stage_process(self, th_preds, th_inputs=None, **kwargs):
    self.log.start_timer('retina_nms')

    pred_nms = self._retina_post_process(th_preds)
    self.log.stop_timer('retina_nms')

    # second stage preprocess
    preds_nms_denormed, lst_th_faces_cropped = self._second_stage_pre_process(pred_nms, th_inputs)

    pred_second = None
    if self.has_second_stage_classifier:

      self.log.start_timer('2nd_stage_clf')
      pred_second = self._second_stage_classifier(lst_th_faces_cropped)
      self.log.stop_timer('2nd_stage_clf')
    # endif

    return preds_nms_denormed, pred_second

  def _post_process(self, preds):
    pred_nms_cpu, (lst_th_embeds, lst_th_crop_faces) = preds
    nr_images = len(self._lst_original_shapes)
    lst_results = []
    for i in range(nr_images):
      # now we have each individual image and we generate all objects
      # what we need to do is to match `second_preds` to image id & then
      # match second clf with each box
      np_pred_nms_cpu = pred_nms_cpu[i]
      np_embed_cpu = lst_th_embeds[i].cpu().numpy()
      np_crop_faces_cpu = lst_th_crop_faces[i].cpu().numpy()

      lst_inf = []
      for det, embed, crop_face in zip(np_pred_nms_cpu, np_embed_cpu, np_crop_faces_cpu):
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
          "KEYPOINTS": [(int(kpts[i]), int(kpts[i + 1])) for i in range(0, len(kpts), 2)],
          "EMBEDDING": embed,
          "CROP_FACE": crop_face,
        }
        lst_inf.append(dct_obj)
      lst_results.append(lst_inf)
    return lst_results

  def _second_stage_model_load(self):

    self.download(
      url=self.cfg_faceid_model_url,
      fn=self.cfg_faceid_model_filename
    )

    self.faceid = self.th.jit.load(self.log.get_models_file(self.cfg_faceid_model_filename))

    model_dev = next(self.faceid.parameters()).device
    if (  # need complex check as dev1 != dev2 will be true (if one index is None and other is 0 on same CUDA)
        (model_dev.type != self.dev.type) or  # cpu vs cuda
        ((model_dev.type == self.dev.type) and (model_dev.index != self.dev.index))  # cuda but on different onex
    ):
      self.P("Model '{}' loaded & placed on '{}' -  moving model to device:'{}'".format(
        self.__class__.__name__, model_dev, self.dev), color='y')
      self.faceid.to(self.dev)

    self.faceid.eval()
    return

  def _second_stage_model_warmup(self):
    if self.faceid is not None:
      self.model_warmup_helper(
        model=self.faceid,
        input_shape=(3, *self.cfg_faceid_image_hw),
        max_batch_size=self.cfg_max_batch_second_stage,
        model_name='face_id'
      )
    return super()._second_stage_model_warmup()

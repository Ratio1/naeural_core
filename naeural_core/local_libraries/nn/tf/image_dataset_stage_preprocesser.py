import os
import numpy as np

import tensorflow as tf
import tensorflow_addons as tfa

from functools import partial
from naeural_client import BaseDecentrAIObject

__VER__ = '0.2.0.0'

class TFImageDatasetsStagePreprocesserWrapper(BaseDecentrAIObject):

  def __init__(self, log, batch_size,
               image_height, image_width,
               train_dataset_directory,
               dev_dataset_directory=None,
               test_dataset_directory=None,
               files_extension=None,
               apply_preprocess=True,
               change_stage_points=None,
               resize_with_pad=False,
               **kwargs):

    """
    Parameters:
    -----------
    batch_size : int, mandatory
      Batch size

    image_height : int, mandatory
      Input image height (the images may have any height as the preprocesser will resize them to this value)

    image_width : int, mandatory
      Input image width (the images may have any width as the preprocesser will resize them to this value)

    train_dataset_directory : str, mandatory
      Directory where the training images are stored per each class
      Example: (`train_dataset_directory` = '.../.../.../TRAIN')
        .../.../.../TRAIN
          class1
            img11.png
            img12.png
            ...
          class2
            img21.png
            img22.png

    dev_dataset_directory : str, optional
      Directory where the dev images are stored per each class (same structure as the training images)
      The default is None - no dev dataset

    test_dataset_directory : str, optional
      Directory where the test images are stored per each class (same structure as the training images)
      The default is None - no test dataset

    files_extension : str, optional
      The extension of image files (.png, .jpg, .jpeg)
      The default is None - .png

    apply_preprocess : bool, optional
      Whether to activate the staging preprocessing
      The default is True

    change_stage_points : dict, optional
      Dictionary that specifies the pairing between the epoch and the used preprocessing stage
      Example: (`change_stage_points` = {3:1, 10:2, 20: 1, 30: 2}
        The preprocesser will not apply preprocessing functions until epoch 3; then between
        epochs 3 and 9 will apply stage 1 preprocessing functions; then between epochs 10 and 10
        will apply stage 2 preprocessing functions and so on.
      The default is None - {3:1, 10:2}

    resize_with_pad : bool, optional
      Whether the resize to (`image_height`, `image_width`) keeps the aspect ratio or not.
      The default is False

    Public methods:
    ---------------
    - get_train_tf_dataset()
    - get_dev_tf_dataset()
    - get_test_tf_dataset()
    - get_dct_classes()
    - train_epoch_changed(epoch=epoch)
    - get_original_dev_dataset()
    - get_original_test_dataset()
    - get_stage1_dev_dataset(times=x)
    - get_stage2_dev_dataset(times=x)
    """

    if files_extension is None:
      files_extension = '.png'

    if change_stage_points is None:
      change_stage_points = {3:1, 10:2}

    self.version = __VER__
    super().__init__(log=log, prefix_log='[IDPW]', **kwargs)

    self._train_preproc = TFImageDatasetStagePreprocesser(
      log=log, batch_size=batch_size,
      image_height=image_height, image_width=image_width,
      dataset_directory=train_dataset_directory,
      files_extension=files_extension,
      apply_preprocess=apply_preprocess,
      change_stage_points=change_stage_points,
      resize_with_pad=resize_with_pad,
      **kwargs
    )

    self._dev_preproc = None
    if dev_dataset_directory is not None:
      self._dev_preproc = TFImageDatasetStagePreprocesser(
        log=log, batch_size=batch_size,
        image_height=image_height, image_width=image_width,
        dataset_directory=dev_dataset_directory,
        files_extension=files_extension,
        apply_preprocess=apply_preprocess,
        change_stage_points={1:1, 2:2},
        resize_with_pad=resize_with_pad,
        **kwargs
      )
    #endif

    self._test_preproc = None
    if test_dataset_directory is not None:
      self._test_preproc = TFImageDatasetStagePreprocesser(
        log=log, batch_size=batch_size,
        image_height=image_height, image_width=image_width,
        dataset_directory=test_dataset_directory,
        files_extension=files_extension,
        apply_preprocess=False,
        change_stage_points={},
        resize_with_pad=resize_with_pad,
        **kwargs
      )
    #endif

    return

  def get_train_tf_dataset(self):
    return self._train_preproc()

  def get_dev_tf_dataset(self):
    if self._dev_preproc:
      return self._dev_preproc()

  def get_test_tf_dataset(self):
    if self._test_preproc:
      return self._test_preproc()

  def get_dct_classes(self):
    return self._train_preproc.dct_classes

  def get_train_preprocesser(self):
    return self._train_preproc

  def get_dev_preprocesser(self):
    return self._dev_preproc

  def get_test_preprocesser(self):
    return self._test_preproc

  def train_epoch_changed(self, epoch):
    self._train_preproc.epoch_changed(epoch)

  def _append_to_results(self, results, batch):
    if results is None:
      results = batch
    else:
      results = [tf.concat([results[i], batch[i]], 0) for i in range(len(results))]
    return results

  def _full_dataset(self, ds='dev'):
    assert ds.lower() in ['dev', 'test']
    if ds == 'dev':
      tf_ds = self.get_dev_tf_dataset()
    else:
      tf_ds = self.get_test_tf_dataset()

    results = None
    if tf_ds is not None:
      for batch in tf_ds:
        results = self._append_to_results(results, batch)

    return results

  def _stage_dataset(self, stage, ds='dev', times=1):
    results = None
    if self._dev_preproc:
      self._dev_preproc.epoch_changed(stage)
      for _ in range(times):
        crt_results = self._full_dataset(ds)
        results = self._append_to_results(results, crt_results)

    return results

  def get_original_dev_dataset(self):
    return self._full_dataset('dev')

  def get_stage1_dev_dataset(self, times=1):
    """
    Augments the dev dataset using stage 1 preprocessing functions x times
    """
    return self._stage_dataset(stage=1, ds='dev', times=times)

  def get_stage2_dev_dataset(self, times=1):
    """
    Augments the dev dataset using stage 2 preprocessing functions x times
    """
    return self._stage_dataset(stage=2, ds='dev', times=times)

  def get_original_test_dataset(self):
    return self._full_dataset('test')


class TFImageDatasetStagePreprocesser(BaseDecentrAIObject):

  def __init__(self, log, batch_size,
               image_height, image_width,
               dataset_directory,
               files_extension=None,
               apply_preprocess=True,
               change_stage_points={3:1, 10:2},
               resize_with_pad=False,
               **kwargs):
    if files_extension is None:
      files_extension = '.png'
    assert files_extension in ['.png', '.jpg', '.jpeg']
    self.version = __VER__
    super().__init__(log=log, prefix_log='[IDP]', **kwargs)
    self.batch_size = batch_size
    self.image_height = image_height
    self.image_width = image_width
    self.change_stage_points = change_stage_points
    self.dataset_directory = dataset_directory
    self.apply_preprocess = apply_preprocess
    self.resize_with_pad = resize_with_pad
    self.files_extension = files_extension

    self.current_stage = 0
    self.tf_current_stage = tf.Variable(initial_value=0)
    self.epoch = None
    self.epoch_changed(0)
    self.dct_classes = None

    ########### TODO does not work yet with `get_stage_image`
    ### each stage is a list of preprocessors
    # self.stages = {s : [] for s in range(self.nr_stages)}
    #
    # self.stages[1] = [
    #   self.preprocess_flip_left_right,
    #   partial(self.preprocess_brightness, max_delta=0.5),
    # ]
    #
    # self.stages[2] = self.stages[1] + [
    #   partial(self.preprocess_crop_v2, min_crop_prc=0.05, max_crop_prc=0.3),
    #   partial(self.preprocess_rotation, max_degrees=20)
    # ]
    ############

    self.func1_1 = self.preprocess_flip_left_right
    self.func1_2 = partial(self.preprocess_brightness, max_delta=0.5)
    self.func2_1 = partial(self.preprocess_crop_v2, min_crop_prc=0.05, max_crop_prc=0.25)
    self.func2_2 = partial(self.preprocess_rotation, max_degrees=15)


    self.P("Preprocessing images from '{}' directory ...".format(self.dataset_directory))

    for _, l, _ in os.walk(self.dataset_directory):
      self.labels = l
      break

    self.P("  Found {} classes: {}".format(len(self.labels), self.labels))
    self.dct_classes = {i : self.labels[i] for i in range(len(self.labels))}

    return

  # def get_stage_image(self, image):
  #   lst_methods = self.stages[self.current_stage]
  #   loop_vars = (tf.constant(0), image)
  #   cond = lambda i, tf_img: i < len(lst_methods)
  #   body = lambda i, tf_img: (i+1, tf.cond(tf.random.uniform([], 0, 100) >= 50, lambda: tf_img, lambda: (tf.gather(lst_methods, i))(tf_img)))
  #   final = tf.while_loop(cond, body, loop_vars)
  #   image = final[1]
  #   return image

  def reset_stage(self):
    self.tf_current_stage.assign(0)
    self.current_stage = 0
    return

  def set_stage(self, stage):
    self.tf_current_stage.assign(stage)
    self.current_stage = stage
    return

  def set_max_stage(self):
    max_stage = max(self.change_stage_points.values())
    self.set_stage(max_stage)
    return

  def _get_stage_one_image(self, image):
    # self.log.start_timer('stage_one_image')
    image = tf.cond(tf.random.uniform([], 0, 100) >= 50, lambda: image, lambda: self.func1_1(image))
    image = tf.cond(tf.random.uniform([], 0, 100) >= 50, lambda: image, lambda: self.func1_2(image))
    # self.log.end_timer('stage_one_image')
    return image

  def _get_stage_two_image(self, image):
    # self.log.start_timer('stage_two_image')
    image = self._get_stage_one_image(image)
    image = tf.cond(tf.random.uniform([], 0, 100) >= 50, lambda: image, lambda: self.func2_1(image))
    image = tf.cond(tf.random.uniform([], 0, 100) >= 50, lambda: image, lambda: self.func2_2(image))
    # self.log.end_timer('stage_two_image')
    return image

  @tf.function
  def _staged_preprocess_image(self, image, apply_preprocess):
    # tf.print("Current stage", self.tf_current_stage)
    def true_fn(tf_img):
      tf_img = tf.cond(tf.math.equal(self.tf_current_stage, 1), lambda: self._get_stage_one_image(tf_img), lambda: tf_img)
      tf_img = tf.cond(tf.math.equal(self.tf_current_stage, 2), lambda: self._get_stage_two_image(tf_img), lambda: tf_img)
      return tf_img

    image = tf.cond(
      tf.constant(apply_preprocess),
      lambda: true_fn(image),
      lambda: image
    )

    return image

  def epoch_changed(self, epoch):
    self.epoch = epoch
    stage = self.change_stage_points.get(self.epoch, None)
    if stage:
      self.set_stage(stage)
    return

  def __call__(self):
    return self.tf_dataset()

  def preprocess_flip_left_right(self, image):
    # tf.print("DEBUG: preprocess_flip_left_right")
    return tf.image.flip_left_right(image)

  def preprocess_crop(self, image, top_prc=0.0, left_prc=0.0, bottom_prc=0.0, right_prc=0.0):
    # tf.print("DEBUG: preprocess_crop")
    tf_img_shape = tf.shape(image)
    tf_img_hw = tf.cast(tf.slice(tf_img_shape, [0], [2]), 'float32')
    kwargs = self._tf_get_crop_to_bounding_box_params(
      tf_img_hw,
      top_prc=top_prc, left_prc=left_prc,
      bottom_prc=bottom_prc, right_prc=right_prc
    )

    return tf.image.crop_to_bounding_box(image=image, **kwargs)

  def preprocess_brightness(self, image, max_delta=0.5):
    # tf.print("DEBUG: preprocess_brightness")
    return tf.image.random_brightness(image=image, max_delta=max_delta)

  def preprocess_rotation(self, image, max_degrees=15):
    # tf.print("DEBUG: preprocess_rotation")
    if tf.math.greater(tf.abs(max_degrees), 20):
      tf.print("WARNING! In rotation preproc, |max_degrees| is greater than 20. Please be sure that you want higher rotation")

    degrees = tf.random.uniform([], -max_degrees, max_degrees, seed=None)

    ### Info: we use tensorflow-addons!
    return tfa.image.rotate(
      images=image,
      angles=self._degrees_to_radians(degrees),
    )

  def preprocess_crop_v2(self, image, min_crop_prc=0.05, max_crop_prc=0.3):
    if tf.math.greater(max_crop_prc, 0.5):
      tf.print("WARNING! In crop preproc, max_proc_prc is greater than 0.5. Please be sure that you want to crop more than half of the images")

    prc = tf.random.uniform([], min_crop_prc, max_crop_prc)
    top_prc, left_prc, bottom_prc, right_prc = self._get_cropping_percents(prc)

    return self.preprocess_crop(
      image=image,
      top_prc=top_prc,
      left_prc=left_prc,
      bottom_prc=bottom_prc,
      right_prc=right_prc
    )

  @staticmethod
  def _get_cropping_percents(prc):
    if False:
      # 0 - crop top
      # 1 - crop left
      # 2 - crop bottom
      # 3 - crop right
      # 4 - crop top-left
      # 5 - crop bottom-right

      tf_option = tf.random.uniform([], 0, 6, dtype=tf.dtypes.int32)

      if tf.equal(tf_option, 0):
        t, l, b, r = prc, 0.0, 0.0, 0.0
      elif tf.equal(tf_option, 1):
        t, l, b, r = 0.0, prc, 0.0, 0.0
      elif tf.equal(tf_option, 2):
        t, l, b, r = 0.0, 0.0, prc, 0.0
      elif tf.equal(tf_option, 3):
        t, l, b, r = 0.0, 0.0, 0.0, prc
      elif tf.equal(tf_option, 4):
        t, l, b, r = prc, prc, 0.0, 0.0
      elif tf.equal(tf_option, 5):
        t, l, b, r = 0.0, 0.0, prc, prc
      else:
        t, l, b, r = 0.0, 0.0, 0.0, 0.0
    #endif

    t, l, b, r = 0.0, 0.0, prc, 0.0
    return t,l,b,r


  @staticmethod
  def _get_crop_to_bounding_box_params(H, W, top_prc=0, left_prc=0, bottom_prc=0, right_prc=0):
    top = int(top_prc * H)
    left = int(left_prc * W)
    bottom = H - int(bottom_prc * H)
    right = W - int(right_prc * W)

    target_H = bottom - top
    target_W = right - left

    return {
      'offset_height' : top,
      'offset_width' : left,
      'target_height' : target_H,
      'target_width' : target_W
    }

  @staticmethod
  def _tf_get_crop_to_bounding_box_params(tf_img_hw, top_prc, left_prc, bottom_prc, right_prc):
    tf_procents_tl = tf.stack([top_prc, left_prc])
    tf_procents_br = tf.stack([bottom_prc, right_prc])

    tf_tl = tf.cast(tf.round(tf.math.multiply(tf_procents_tl, tf_img_hw)), 'int32')
    tf_br = tf.cast(tf_img_hw - tf.round(tf.math.multiply(tf_procents_br, tf_img_hw)), 'int32')

    target_hw = tf_br - tf_tl

    return {
      'offset_height' : tf_tl[0],
      'offset_width' : tf_tl[1],
      'target_height' : target_hw[0],
      'target_width' : target_hw[1]
    }

  @staticmethod
  def _degrees_to_radians(degrees):
    return degrees * np.pi / 180


  @tf.function
  def _resize(self, image):
    image = tf.cond(
      tf.constant(self.resize_with_pad),
      lambda: tf.image.resize_with_pad(
        image,
        target_height=self.image_height,
        target_width=self.image_width,
      ),
      lambda: tf.image.resize(
        image,
        size=[self.image_height, self.image_width]
      )
    )
    return image

  @tf.function
  def read_staged_preprocess_image(self, file_name):
    # tf.print("!!!READ_IMAGE!!!")
    # self.log.start_timer('read_image')
    image = tf.io.read_file(file_name)
    image = tf.image.decode_jpeg(image, channels=3)
    # self.log.end_timer('read_image')
    # image = tf.cond(tf.constant(apply_preprocess), lambda: self.get_stage_image(image), lambda: image)
    # self.log.start_timer('maybe_process_image')
    image = self._staged_preprocess_image(image=image, apply_preprocess=self.apply_preprocess)
    # self.log.end_timer('maybe_process_image')
    # self.log.start_timer('resize')
    image = self._resize(image)
    # self.log.end_timer('resize')
    # self.log.start_timer('cast_and_label')
    image = tf.cast(image, tf.uint8)
    label = tf.strings.split(file_name, os.sep)[-2]
    label = tf.squeeze(tf.where(tf.equal(label, self.labels)), axis=1)
    # self.log.end_timer('cast_and_label')
    return image, label#, file_name
  # enddef

  def tf_dataset(self):
    p = '{}/*/*{}'.format(self.dataset_directory, self.files_extension)
    list_ds = tf.data.Dataset.list_files(p)
    labeled_ds = list_ds.map(self.read_staged_preprocess_image)
    labeled_ds = labeled_ds.batch(self.batch_size)
    return labeled_ds

if __name__ == '__main__':
  import matplotlib.pyplot as plt
  from naeural_core import Logger

  log = Logger(
    lib_name='GIDS',
    base_folder='.',
    app_folder='_local_cache',
    TF_KERAS=False
  )

  url = 'https://www.dropbox.com/sh/6iiec6xryd1nrxz/AADO2xCx_6gMcnp51TlUfPX7a?dl=1'
  ds_name = 'hard_cover_datasets'

  saved_files, _ =log.maybe_download(
    url=url,
    fn=ds_name,
    target='data',
    unzip=True
  )

  BATCH_SIZE = 9
  IMG_HEIGHT, IMG_WIDTH = 224, 112
  data_dir = saved_files[0]

  train_data_dir = os.path.join(data_dir, 'TRAIN')
  dev_data_dir = os.path.join(data_dir, 'DEV')
  test_data_dir = os.path.join(data_dir, 'TEST')

  preproc_w = TFImageDatasetsStagePreprocesserWrapper(
    log=log,
    batch_size=BATCH_SIZE, image_height=IMG_HEIGHT, image_width=IMG_WIDTH,
    train_dataset_directory=train_data_dir,
    dev_dataset_directory=dev_data_dir,
    test_dataset_directory=test_data_dir,
    apply_preprocess=True,
    change_stage_points={3: 1, 10: 2},
    resize_with_pad=False
  )

  dev_results = preproc_w.get_original_dev_dataset()
  dev_results_2 = preproc_w.get_stage1_dev_dataset(times=4)

  if False:
    for epoch in [2, 3, 4, 10, 11]:
      print("Epoch {}".format(epoch))
      preproc_w.train_epoch_changed(epoch)
      ds_trn = preproc_w.get_train_tf_dataset()
      images,labels,fns = next(iter(ds_trn))
      str_epoch = 'E{}'.format(epoch)
      if True:
        plt.figure(figsize=(10, 10))
        for i in range(9):
          label = preproc_w.get_dct_classes()[labels[i,0].numpy()]
          fn = fns[i].numpy().decode().split('/')[-1]
          ax = plt.subplot(3, 3, i + 1)
          plt.imshow(images[i].numpy().astype("uint8"))
          plt.title(label + '-' + fn + '-' + str_epoch)
          plt.axis("off")
        plt.show()


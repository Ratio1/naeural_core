

import tensorflow as tf
from tensorflow.python.framework.ops import EagerTensor
import random
from collections import OrderedDict
import numpy as np
import pandas as pd
import os
from functools import partial
from time import time
import itertools

from naeural_client import BaseDecentrAIObject

__VER__ = '0.2.0.0'


def time_exceeded(start_time, max_duration_seconds):
  """
  Util method that limits the amount of time of a grid search.

  Parameters:
  -----------
  start_time: float, mandatory
    The start time in seconds since the Epoch (should be computed using `time.time()`)

  max_duration_seconds: float, mandatory
    The number of seconds the training should run.
  """
  if time() - start_time >= max_duration_seconds:
    return True

  return False


percentage_metrics = [
  'acc',
  'recall', 'rec',
  'precision', 'pre',
]

train_descr = [
  'train', 'Train', 'TRAIN',
  'trn', 'Trn', 'TRN',
]

dev_descr = [
  'dev', 'Dev', 'DEV',
  'val', 'Val', 'VAL',
  'validation',
  'valid', 'Valid', 'VALID',
]

tst_descr = [
  'test', 'Test', 'TEST',
  'tst', 'Tst', 'TST',  
]


class DeviceUtils(BaseDecentrAIObject):
  def __init__(self, log, gpu=None, **kwargs):
    super().__init__(log=log, **kwargs)

    gpu_env = 'gpu'
    cpu_env = 'cpu'
    self.available_gpus = None

    if tf.test.gpu_device_name():
      self.env = gpu_env
      self.get_available_gpus()
    else:
      self.env = cpu_env

    self.P("Running on {} ...".format(self.env))
    if self.available_gpus is not None:
      self.P(" Available gpus={}".format(self.available_gpus))

    if gpu is not None:
      assert (gpu < len(self.available_gpus))
      self.set_dynamic_memory()
      self.device = self.available_gpus[gpu]
      self.P(" Chosen gpu: {}".format(self.available_gpus[gpu]))
    else:
      if self.env == gpu_env:
        self.device = self.available_gpus[0]
        self.P(" Chosen gpu: {}".format(self.available_gpus[0]))
      else:
        self.device = '/device:CPU:0'

    return

  @staticmethod
  def set_dynamic_memory():
    gpus = tf.config.experimental.list_physical_devices('GPU')

    for gpu in gpus:
      try:
        tf.config.experimental.set_memory_growth(gpu, True)
      except RuntimeError as e:
        print(e)

  def get_available_gpus(self):
    local_device_protos = tf.config.experimental.list_logical_devices('GPU')
    self.available_gpus = [x.name for x in local_device_protos if x.device_type == 'GPU']
    return


class Trainer(BaseDecentrAIObject):
  """
  DecentrAI generalised training utils. 
  """
  
  def __init__(self,
               log,
               monitor_key,
               monitor_key_mode,
               model_name='',
               epochs=10,
               train_loss_key='train_loss',
               key=None,
               key_mode='max',
               delete_if_key_worse=None,
               stop_at_fails=100,
               stop_at_plateaus=3,
               threshold_progress=None,
               eps_monitor=0,
               eps_key=0,
               max_patience=15,
               max_cooldown=3,
               lr_decay=0.5, 
               lr_min=1e-7,
               return_history=True,
               rev_hist_summary=False,
               hist_summary_size=5,
               save_checkpoint=True,
               delete_best_model=False,
               final_cleanup=False,
               gpu=None,
               models_subfolder_path=None,
               **kwargs):
    """
    Constructor.
    
    Parameters
    ----------
    log : Logger, mandatory

    monitor_key : str, mandatory
      The name of the key which will be used for monitorizing the models'
      convergence. In a standard way, `monitor_key` could be identically
      to `train_loss_key` or it could be other computed loss (dev loss,
      for example). If the standard way results in overfitting, then 
      we need to monitor other metrics and this name should coincide with one
      of the keys returned by the callbacks given in `train` method.
      
    monitor_key_mode : str, mandatory
      Specifies whether the `monitor_key` is best when its value is maximum
      or minimum. Possible values: 'min' or 'max'.
    
    model_name : str, optional
      The name of the model / ensemble of models that we want to train.
      The default is ''.
   
    epochs : int, optional
      Maximum number of training epochs.
      The default is 10.
      
    train_loss_key : str, optional
      The name of the train_loss_key. This name should coincide with one of the
      keys returned by callbacks given in `train` method. For `simple_train`
      should be let to be the default value.
      The default is 'train_loss'.
      
    key : str, optional
      The name of the key on which will be chosen the best model. This name
      should coincide with one of the keys returned by evaluate callbacks given 
      in `train` method. For `simple_train`, this key should be 'dev_' or 'test_'
      (does not matter) concatenated to the name of the main metric of the model.
      The default is None. If this parameter is None, then the training routine
      will save everytime the last epoch model.
      
    key_mode : str, optional
      Specifies whether the key is best when its value is maximum or minimum.
      Possible values: 'min' or 'max'.
      The default is 'max'.
      
    delete_if_key_worse : int, optional [None]
      Specifies the minimal `key` value required to leave the model on disk after
      full training. `None` will ignore and in case `key=='dev_acc'` and 
      `delete_if_key_worse == 0.8` if the best does not exceed 0.8 it will be deleted
      
    stop_at_fails : int, optional
      The number of monitor failures (after cooldown) after which the training
      will be stopped.
      The default is 100.

    stop_at_plateaus : int, optional
      The number of monitor plateaus after which the training will be stopped.
      The default is 3.
  
    threshold_progress : int, optional
      This threshold specifies which is the value of the key for which we take
      into account setting new best models.
      The default is None and sets internally threshold_progress to
      0 if key_mode='max' or to 1 if key_mode='min'.
      
    eps_monitor : float, optional
      At each epoch we compare the current monitored value with the
      best monitored value - eps_monitor.
      The default is 1e-3.
      
    eps_key : float, optional
      At each epoch we compare the current key value with the best key value +/- eps_key.
      The default is 5e-4.
      
    max_patience : int, optional
      The number of epochs after cooldown in which we are waiting for monitor progress.
      The default is 15.
      
    max_cooldown : int, optional
      The number of epochs in which we wait to heat the model to get monitor progress.
      The default is 3.
      
    lr_decay : float, optional
      Decaying factor for learning rate.
      The default is 0.95.
      
    lr_min : float, optional
      Minimum value that could be taken by the learning rate after multiple decays.
      The default is 1e-7.
      
    return_history : bool, optional
      Whether the `train` / `simple_train` methods return or not the full history.
      The default is True.
      
    rev_hist_summary : bool optional [False]
      Will reverse (newest to oldest) the history summary.
      
    hist_summary_size : int, optional [5]
      The number of epochs that are showed in the history summary @each epoch.
      
    save_checkpoint : bool, optional [True]
      Specifies whether the Trainer saves or not the checkpoint, even if
      the Trainer "decides" that we have a new better model.

    delete_best_model : bool, optional [True]
      Specifies whether the Trainer deletes or not the best model at the end of the process.
      May be useful when a grid search is completed and then the best model must be retrained
      on full data - so you need to know only the result!

    final_cleanup : bool, optional [False]
      Specifies whether after the training-evaluation loop, the cleanup is performed.

    gpu : int, optional [None]
      Specifies on which gpu resides the trained model.
      The default is None, which means that tensorflow adopts the default allocations strategies,
      depending on the environment (cpu or gpu).
      TODO In the future, we will add also strategies for distributed training.

    models_subfolder_path : str, optional [None]
      A path relative to '_models' where the trained models are saved

    **kwargs :
      Arguments for parent class - log should be specified.
      
    Simple example:

        trainer = Trainer(
          log=log,
          monitor_key='dev_acc',
          monitor_key_mode='max',
          model_name=model.name,
          epochs=200,
          key='dev_acc',
          key_mode='max',
          stop_at_fails=10,
          stop_at_pleateau=3,
          threshold_progress=0,
          eps_loss=1e-3,
          max_patience=5,
          max_cooldown=2,
          lr_decay=0.50,
          lr_min=1e-7,
          return_history=False,
          rev_hist_summary=False,
        )
        
      
        result = trainer.simple_train_gen(
          model=model,
          train_gen=train_gen,
          steps_per_epoch=x_train.shape[0] // BATCH_SIZE,
          X_test=x_dev, y_test=y_dev,
          X_train=x_trn, y_train=y_trn,
          no_model_prefix=True,
          eager=False
        )
      

    """
        
    if monitor_key is not None:
      assert monitor_key_mode.lower() in ['min', 'max'], 'We should specify monitor_key_mode on min/max'
    #endif

    if key is None:
      key = train_loss_key
      key_mode = 'min'
    else:
      assert key_mode.lower() in ['min', 'max'], 'We should specify key_mode on min/max'
    #endif

    if stop_at_fails < max_patience:
      raise ValueError("`max_patience` must be lower than `stop_at_fails` in order to accomodate at least one lr reduction")

    super().__init__(log=log, **kwargs)
    
    self.monitor_key = monitor_key
    self.monitor_key_mode = monitor_key_mode
    self.model_name = model_name
    self.epochs = epochs
    self.train_loss_key = train_loss_key
    self.key = key
    self.key_metric = self._get_metric(key=self.key)
    self.key_mode = key_mode.lower()
    self.stop_at_fails = stop_at_fails
    self.stop_at_plateaus = stop_at_plateaus
    self.threshold_progress = threshold_progress
    self.eps_monitor = eps_monitor
    self.eps_key = eps_key
    self.max_patience = max_patience
    self.max_cooldown = max_cooldown
    self.lr_decay = lr_decay
    self.lr_min = lr_min
    self.return_history = return_history
    self.rev_hist_summary = rev_hist_summary
    self.hist_summary_size = hist_summary_size
    self.delete_if_key_worse = delete_if_key_worse
    self.save_checkpoint = save_checkpoint
    self.delete_best_model = delete_best_model
    self.final_cleanup = final_cleanup
    device_utils = DeviceUtils(log=log, gpu=gpu, **kwargs)
    self.device = device_utils.device
    self.models_subfolder_path = models_subfolder_path
    if self.models_subfolder_path is not None:
      _dir = os.path.join(self.log.get_models_folder(), models_subfolder_path)
      if not os.path.exists(_dir):
        os.makedirs(_dir)
      #endif
    #endif

    self._model = None
    self._evaluate_callbacks, self._parametrized_evaluate_callbacks = None, None
    self._test_callbacks, self._parametrized_test_callbacks = None, None
    self.optimizers = None
    self.optimizers_class_name = None
    self.version = __VER__
    self.__version__ = self.version

    if self.threshold_progress is None:
      if self.key_mode.lower() == 'min':
        self.threshold_progress = 1
      elif self.key_mode.lower() == 'max':
        self.threshold_progress = 0
    #endif

    self.P("Initialized {}.".format(self.log.get_object_params(self, n=15)))
    if lr_decay > 0.7:
      self.P("*** WARNING: lr decay value of will results in a SLOW lr decay***")
    return

  @staticmethod
  def get_model_master_json_name():
    return 'model_master.json'

  @staticmethod
  def save_to_model_master_json(log, key, value, model_subfolder_path, override=True):
    _dct = log.load_models_json(
      Trainer.get_model_master_json_name(),
      subfolder_path=model_subfolder_path
    )

    if _dct is None:
      _dct = {}

    if key not in _dct or override:
      _dct[key] = value
      log.save_models_json(
        _dct,
        Trainer.get_model_master_json_name(),
        subfolder_path=model_subfolder_path
      )
    # endif
    return

  def startup(self):
    """
    Resets all the training variables.
    """
    super().startup()

    self.dct_hist = None

    self.best_epoch = 0
    self.current_epoch = 0
    self.best_key = np.nan
    self.best_monitor = np.nan
    self.train_dev_overfit_at_best_ep = np.nan
    self.dev_test_overfit_at_best_ep = np.nan
    self.best_label = ''
    self.best_label_optimizers = ''
    self.last_saved_model = None
    self.dct_test_at_best_ep = {}

    self.lr_reductions = 0
    self.patience = 0
    self.cooldown = 0
    self.fails_counter = 0
    self.plateaus_counter = 0

    self.train_maxes = {}
    return


  def reset_optimizer(self, optimizer):
    self.reset_optimizers(optimizer)


  def reset_optimizers(self, optimizers):
    """
    Resets the optimizers for the current instance

    Parameters
    ----------
    optimizers : tf.keras.optimizer or [tf.keras.optimizer]
      The new optimizers.

    """
    if type(optimizers) is not list:
      optimizers = [optimizers]
    self.optimizers = optimizers
    return

  def _simple_train_epoch_callback(self, train_gen, steps_per_epoch):
    epoch_losses = []
    for itr in range(1, steps_per_epoch+1):
      x_batch, y_batch = next(train_gen)
      loss_batch = self._model.train_on_batch(x_batch, y_batch)
      epoch_losses.append(loss_batch)
      print('\r Iteration {:.1f}% loss: {:.3f}'
            .format(itr/steps_per_epoch*100, np.mean(epoch_losses)), flush=True, end='')
    #print('', flush=True) # not needed anymore as Logger will overwrite
    return {self.train_loss_key: np.mean(epoch_losses)}


  def _simple_train_epoch_callback_fitgen(self, train_gen, steps_per_epoch=None,
                                          nr_out_keys=1):

    assert nr_out_keys >= 1

    hist = self._model.fit(
      train_gen,
      epochs=1,
      verbose=1,
      steps_per_epoch=steps_per_epoch
    )


    dct = {train_descr[0] + '_' + k : v[0]\
           for i,(k,v) in enumerate(hist.history.items())\
           if i < nr_out_keys}

    return dct


  def _simple_train_epoch_callback_fit(self, X_train, y_train,
                                       batch_size=None,
                                       nr_out_keys=1):

    assert nr_out_keys >= 1

    hist = self._model.fit(
      x=X_train,
      y=y_train,
      batch_size=batch_size,
      epochs=1,
      verbose=1
    )

    dct = {train_descr[0] + '_' + k : v[0]\
           for i,(k,v) in enumerate(hist.history.items())\
           if i < nr_out_keys}

    return dct



  def _simple_eager_train_epoch_callback(self, train_gen, steps_per_epoch):
    optimizer = self.optimizers[0]
    self._train_loss.reset_states()
    loss_fn = tf.keras.losses.get(self._model.loss)
    for itr, (x_batch, y_batch) in enumerate(train_gen):

      with tf.GradientTape() as tape:
        tf_preds = self._model(x_batch)
        tf_loss = loss_fn(y_batch, tf_preds)
      #endwith

      self._train_loss(tf_loss)

      lst_all_weights = self._model.trainable_weights
      lst_grads = tape.gradient(tf_loss, lst_all_weights)
      optimizer.apply_gradients(zip(lst_grads, lst_all_weights))

      print('\r Iteration {:.1f}% loss: {:.3f}'
            .format(itr/steps_per_epoch*100, self._train_loss.result()), flush=True, end='')

    #endfor

    #print('', flush=True) # not needed anymore as Logger will overwrite
    return {self.train_loss_key: self._train_loss.result()}


  def _simple_save_model_callback(self, epoch, str_result):
    label = self.model_name + '_e{:03d}'.format(epoch) + str_result
    if not self._no_model_prefix:
      label = self.log.file_prefix + '_' + label

    fn = self.log.save_keras_model(
      model=self._model, label=label,
      include_optimizer=True,
      use_single_prefix=False,
      use_prefix=False,
      subfolder_path=self.models_subfolder_path
    )
    return label, fn


  def _simple_load_model_callback(self, label, custom_objects=None):
    self._model = self.log.load_keras_model(
      label,
      custom_objects=custom_objects,
      subfolder_path=self.models_subfolder_path
    )
    self.reset_optimizers(self._model.optimizer)
    self._parametrize_callbacks()
    return


  def _get_shape(self, inp):
    if type(inp) is not list:
      return inp.shape
    else:
      return [x.shape for x in inp]


  def _simple_evaluate_callback(self, X, y, ds):
    assert ds in [train_descr[0], dev_descr[0], tst_descr[0]]

    metrics_names = self._model.metrics_names

    self.P("Evaluating '{}' set: X={}; y={} on {}..."
           .format(ds, self._get_shape(X), self._get_shape(y), metrics_names))
    metrics_values = self._model.evaluate(X, y, verbose=0)
    self.P(" ", t=True)

    dct_ret = dict(zip(metrics_names, metrics_values))
    dct_ret_modif = {ds + '_' + k : v for k,v in dct_ret.items()}
    return dct_ret_modif


  def simple_train(self, model,
                   X_train, y_train, batch_size=None,
                   learning_rate=None,
                   X_dev=None, y_dev=None,
                   X_test=None, y_test=None,
                   X_train_smpl=None, y_train_smpl=None,
                   evaluate_callbacks=None,
                   test_callbacks=None,
                   time_exceeded_callback=None,
                   no_model_prefix=True,
                   custom_objects=None,
                   eager=False,
                   save_model_callback=None,
                   load_model_callback=None,
                   nr_out_keys=1):

    """
    Simple train with (X_train, y_train) routine

    Parameters
    ----------
    model : tf.keras.Model
      The model that should be trained.

    X_train : np.ndarray / tf.Tensor
      Train data inputs.

    y_train : np.ndarray / tf.Tensor
      Train data outputs.

    batch_size : int
      The batch size used in the training routine.
      The default is None. In this case the default value of model.fit(batch_size=...) will be used

    learning_rate : float
      Parameter that forces the learning rate of the model to be refresed to that value.
      This parameter is useful when you have to deal with multiple retrainings and at each new retraining
      the model should be reinitialized with a big learning rate, not with a small one resulted after
      the previous retrainings.
      The default is None. In this case the learning rate remains unchanged.

    X_dev : np.ndarray / tf.Tensor, optional
      Validation input data.
      The default is None.

    y_dev : np.ndarray / tf.Tensor, optional
      Validation target data.
      The default is None.

    X_test : np.ndarray / tf.Tensor, optional
      Test input data.
      The default is None.

    y_test : np.ndarray / tf.Tensor, optional
      Test target data.
      The default is None.

    X_train_smpl :np.ndarray / tf.Tensor, optional
      Train sample imput data.
      The default is None.

    y_train_smpl : np.ndarray / tf.Tensor, optional
      Train sample target data.
      The default is None.

    evaluate_callbacks : list[function], optional
      Custom callbacks for evaluating the model.
      The default is None.

    test_callbacks : list[function], optional
      Custom callbacks that will receive the current model and will do some custom testing.
      The default is None.

    time_exceeded_callback : function, optional
      Callback that is called without any argument that returns True if the total training
      time was exceeded and False otherwise. If the time was exceeded, the training is stopped.

      The default is None.

    no_model_prefix :  bool, optional [True]
      Ommit model timestamp prefix from model name

    custom_objects : dict, optional [None]
      Dictionary that specifies which custom objects are used in loading model

    eager : bool, optional
      Whether the routine is executed in eager mode or not.
      The default is False.

    save_model_callback : function, optional
      Callback for saving the model(s).
      The default is None (_simple_save_model_callback is used)

    load_model_callback : function, optional
      Callback for loading the model(s).
      The default is None (_simple_load_model_callback is used)

    nr_out_keys : int, optional
      If there are multiple output heads for how many should the trainer log the loss
      The defaule is 1.

    Returns
    -------
    best_epoch (int), dictionary with all values @best_epoch (dict)

    or (if return_history is set to True in __init__):

    best_epoch (int), dictionary with all values @best_epoch (dict), training_history (dict)

    """

    if eager is True:
      raise ValueError("Eager mode with X_train and y_train not implemented yet.")

    self._model = model
    self._eager = eager
    if self.model_name == '':
      self.model_name = self._model.name

    self.optimizers = [self._model.optimizer]
    train_epoch_callback = None

    if not self._eager:
      train_epoch_callback = partial(self._simple_train_epoch_callback_fit,
                                     X_train=X_train,
                                     y_train=y_train,
                                     batch_size=batch_size,
                                     nr_out_keys=nr_out_keys)
    else:
      pass # not implemented yet

    assert train_epoch_callback is not None

    return self._simple_train_helper(
      train_epoch_callback=train_epoch_callback,
      learning_rate=learning_rate,
      X_dev=X_dev, y_dev=y_dev,
      X_test=X_test, y_test=y_test,
      X_train_smpl=X_train_smpl, y_train_smpl=y_train_smpl,
      evaluate_callbacks=evaluate_callbacks,
      test_callbacks=test_callbacks,
      time_exceeded_callback=time_exceeded_callback,
      no_model_prefix=no_model_prefix,
      custom_objects=custom_objects,
      save_model_callback=save_model_callback,
      load_model_callback=load_model_callback
    )

  def simple_train_gen(self, model,
                       train_gen, steps_per_epoch=None,
                       learning_rate=None,
                       X_dev=None, y_dev=None,
                       X_test=None, y_test=None,
                       X_train_smpl=None, y_train_smpl=None,
                       evaluate_callbacks=None,
                       test_callbacks=None,
                       time_exceeded_callback=None,
                       no_model_prefix=True,
                       custom_objects=None,
                       eager=False,
                       save_model_callback=None,
                       load_model_callback=None,
                       nr_out_keys=1):
    """
    Simple train with generator routine

    Parameters
    ----------
    model : tf.keras.Model
      The model that should be trained.

    train_gen : generator / tf.data.Dataset
      Train data generator.

    steps_per_epoch : int
      Passed to `self._model.fit`
      The default is None

    learning_rate : float
      Parameter that forces the learning rate of the model to be refresed to that value.
      This parameter is useful when you have to deal with multiple retrainings and at each new retraining
      the model should be reinitialized with a big learning rate, not with a small one resulted after
      the previous retrainings.
      The default is None. In this case the learning rate remains unchanged.

    X_dev : np.ndarray / tf.Tensor, optional
      Validation input data.
      The default is None.

    y_dev : np.ndarray / tf.Tensor, optional
      Validation target data.
      The default is None.

    X_test : np.ndarray / tf.Tensor, optional
      Test input data.
      The default is None.

    y_test : np.ndarray / tf.Tensor, optional
      Test target data.
      The default is None.

    X_train_smpl :np.ndarray / tf.Tensor, optional
      Train sample imput data.
      The default is None.

    y_train_smpl : np.ndarray / tf.Tensor, optional
      Train sample target data.
      The default is None.

    evaluate_callbacks : list[function], optional
      Custom callbacks for evaluating the model.
      The default is None.

    test_callbacks : list[function], optional
      Custom callbacks that will receive the current model and will do some custom testing.
      The default is None.

    time_exceeded_callback : function, optional
      Callback that is called without any argument that returns True if the total training
      time was exceeded and False otherwise. If the time was exceeded, the training is stopped.

      The default is None.

    no_model_prefix :  bool, optional [True]
      Ommit model timestamp prefix from model name

    custom_objects : dict, optional [None]
      Dictionary that specifies which custom objects are used in loading model

    eager : bool, optional
      Whether the routine is executed in eager mode or not.
      The default is False.

    save_model_callback : function, optional
      Callback for saving the model(s).
      The default is None (_simple_save_model_callback is used)

    load_model_callback : function, optional
      Callback for loading the model(s).
      The default is None (_simple_load_model_callback is used)

    nr_out_keys : int, optional
      If there are multiple output heads for how many should the trainer log the loss
      The defaule is 1.

    Returns
    -------
    best_epoch (int), dictionary with all values @best_epoch (dict)

    or (if return_history is set to True in __init__):

    best_epoch (int), dictionary with all values @best_epoch (dict), training_history (dict)

    """

    self._model = model
    self._eager = eager
    if self.model_name == '':
      self.model_name = self._model.name

    self.optimizers = [self._model.optimizer]

    # gen_type = str(type(train_gen))
    # if "tensorflow" in  gen_type and "Dataset" in gen_type:
    #   train_gen = self.log.tfdataset_to_generator(train_gen)

    if not self._eager:
      train_epoch_callback = partial(
        self._simple_train_epoch_callback_fitgen,
        train_gen=train_gen,
        steps_per_epoch=steps_per_epoch,
        nr_out_keys=nr_out_keys
      )
    else:
      with tf.device(self.device):
        self._train_loss = tf.keras.metrics.Mean(name='mean_train_loss')

      train_epoch_callback = partial(self._simple_eager_train_epoch_callback,
                                     train_gen=train_gen,
                                     steps_per_epoch=steps_per_epoch)

    return self._simple_train_helper(
      train_epoch_callback=train_epoch_callback,
      learning_rate=learning_rate,
      X_dev=X_dev, y_dev=y_dev,
      X_test=X_test, y_test=y_test,
      X_train_smpl=X_train_smpl, y_train_smpl=y_train_smpl,
      evaluate_callbacks=evaluate_callbacks,
      test_callbacks=test_callbacks,
      time_exceeded_callback=time_exceeded_callback,
      no_model_prefix=no_model_prefix,
      custom_objects=custom_objects,
      save_model_callback=save_model_callback,
      load_model_callback=load_model_callback
    )


  def _simple_train_helper(self,
                           train_epoch_callback,
                           learning_rate=None,
                           X_dev=None, y_dev=None,
                           X_test=None, y_test=None,
                           X_train_smpl=None, y_train_smpl=None,
                           evaluate_callbacks=None,
                           test_callbacks=None,
                           time_exceeded_callback=None,
                           no_model_prefix=True,
                           custom_objects=None,
                           save_model_callback=None,
                           load_model_callback=None):

    self._no_model_prefix = no_model_prefix

    if save_model_callback is None:
      save_model_callback = self._simple_save_model_callback

    if load_model_callback is None:
      load_model_callback = partial(self._simple_load_model_callback,
                                    custom_objects=custom_objects)

    standard_evaluate_callbacks = []
    standard_test_callbacks = []

    if X_dev is not None and y_dev is not None:
      standard_evaluate_callbacks.append(
        partial(self._simple_evaluate_callback,
                X=X_dev,
                y=y_dev,
                ds=dev_descr[0])
      )
    #endif

    if X_test is not None and y_test is not None:
      standard_test_callbacks.append(
        partial(self._simple_evaluate_callback,
                X=X_test,
                y=y_test,
                ds=tst_descr[0])
        )
    #endif

    if X_train_smpl is not None and y_train_smpl is not None:
      standard_evaluate_callbacks.append(
        partial(self._simple_evaluate_callback,
                X=X_train_smpl,
                y=y_train_smpl,
                ds=train_descr[0])
        )
    #endif

    if evaluate_callbacks is None:
      evaluate_callbacks = standard_evaluate_callbacks
    else:
      if len(standard_evaluate_callbacks) > 0:
        str_log = "[WARNING] When providing custom evaluate callbacks, "
        str_log += "(X_dev, y_dev, X_train, y_train) should be None. "
        str_log += "Otherwise, they will not be taken into account"
        self.P(str_log)
      #endif
    #endif

    if test_callbacks is None:
      test_callbacks = standard_test_callbacks
    else:
      if len(standard_test_callbacks) > 0:
        str_log = "[WARNING] When providing custom test callbacks, "
        str_log += "(X_test, y_test) should be None. "
        str_log += "Otherwise, they will not be taken into account"
        self.P(str_log)
      #endif
    #endif


    if self.rev_hist_summary:
      self.P("Un-reversing hist summary in simple_training")
      self.rev_hist_summary = False

    return self.train(optimizers=self._model.optimizer,
                      train_epoch_callback=train_epoch_callback,
                      save_model_callback=save_model_callback,
                      load_model_callback=load_model_callback,
                      learning_rate=learning_rate,
                      evaluate_callbacks=evaluate_callbacks,
                      test_callbacks=test_callbacks,
                      time_exceeded_callback=time_exceeded_callback)


  def _convert_seconds_to_log(self, start, end):
    nr_seconds = end - start
    str_log = "{:.2f}s".format(nr_seconds)

    if nr_seconds > 120:
      m, s = divmod(nr_seconds, 60)
      h, m = divmod(m, 60)

      m = int(m)
      h = int(h)
      s = int(s)

      if h == 0:
        str_log += " ({:02d}:{:02d})".format(m, s)
      else:
        str_log += " ({:02d}:{:02d}:{:02d})".format(h, m, s)

    return str_log

  def _convert_dct_results(self, dct_results):
    new_dct_results = {}
    for k,v in dct_results.items():
      if type(v) is EagerTensor:
        v = v.numpy()
      new_dct_results[k] = v
    return new_dct_results

  def _get_metric(self, key):
    metric = '_'.join(key.split('_')[1:])
    return metric

  def process_eval_dct_results(self, dct_results, epoch, show_loss=False):
    """
    Processes and displayes evaluation results

    Parameters
    ----------
    dct_results : dict
      the evaluation results (assumes floats as values)

    epoch : int
      current epoch number (will be displayed)

    steps_per_epoch : int
      Number of batches returned by the train_gen per epoch.

    show_loss : bool [False]
      Will hide or display loss analysis

    Returns
    -------

    """

    def _check_ds_key(descriptions, metric):
      has, idx = False, None
      for _idx, descr in enumerate(descriptions):
        has = (descr + '_' + metric) in dct_results
        if has:
          idx = _idx
          break
      return has, idx
    #enddef `_check_ds_key`

    def _get_overfits(metric, train, *idx_descr):
      keys, values = [], []
      idx, descr = list(zip(*idx_descr))
      assert len(idx) == len(descr)

      for i in range(len(idx)):
        k = descr[i][idx[i]] + '_' + metric
        v = dct_results[k]
        keys.append(k)
        values.append(v)
      #endfor

      is_min = ('loss' in keys[0]) or (self.key_mode != 'max')

      if train:
        trn = keys[0]
        val_trn = values[0]
        dct_prev = self.train_maxes.get(trn, {'VAL': np.inf if is_min else -np.inf})
        prev_val = dct_prev['VAL']
        if (is_min and (prev_val > val_trn)) or (not is_min and (prev_val < val_trn)):
          self.P("  NEW MAX TRAIN: {:.4f}{}{:.4f}".format(
              val_trn, '<' if is_min else '>', prev_val ))
          self.train_maxes[trn] = {'VAL':val_trn, 'EP':epoch}
        #endif
      #endif - train

      combinations = list(itertools.combinations(list(range(len(keys))), r=2))
      overfits = []
      for comb in combinations:
        val_ovr = values[comb[1]] - values[comb[0]] if is_min else values[comb[0]] - values[comb[1]]
        self.P("  {:<11} {:.4f}".format(keys[comb[0]][:10]+':', values[comb[0]]))
        self.P("  {:<11} {:.4f}".format(keys[comb[1]][:10]+':', values[comb[1]]))
        if val_ovr > 0:
          self.P("  {:<11} {:.4f}".format('OVERFIT:', val_ovr))
        else:
          val_ovr = 0
          self.P("  NO OVERFIT!")
        overfits.append(val_ovr)
      #endfor comb

      return overfits
    #enddef `_get_overfit`

    metrics = list(set([self._get_metric(x) for x in dct_results]))

    overfits = []
    printed = False
    for metric in metrics:
      if 'loss' in metric and not show_loss:
        continue

      has_trn, idx_trn = _check_ds_key(train_descr, metric)
      has_dev, idx_dev = _check_ds_key(dev_descr, metric)
      has_tst, idx_tst = _check_ds_key(tst_descr, metric)

      idx_descr = []
      if has_trn:
        idx_descr.append((idx_trn, train_descr))
      if has_dev:
        idx_descr.append((idx_dev, dev_descr))
      if has_tst:
        idx_descr.append((idx_tst, tst_descr))

      if len(idx_descr) > 1:
        if not printed:
          self.P("Epoch {} evaluation results:".format(epoch))
          printed = True
        tmp_overfits = _get_overfits(metric, has_trn, *idx_descr)

        # We register the overfit just for the key_metric
        if self.key_metric in metric:
          overfits = tmp_overfits
      #endif
    #endfor - metric
    return overfits


  def save_optimizers(self, epoch, str_result):
    if self.DEBUG:
      self.D("Save opt. debugging ...")
      self._display_opt_state()
    label = self.log.file_prefix + '_' + self.model_name + '_e{:03d}'.format(epoch) + str_result

    fns = []
    for i, opt in enumerate(self.optimizers):
      fn = self.log.save_optimizer_weights(
        optimizer=opt,
        fn=label+'_opt_{}'.format(i+1),
        subfolder_path=self.models_subfolder_path
      )
      fns.append(fn)

    return label, fns

  def _display_opt_state(self):
    for i,opt in enumerate(self.optimizers):
      weights = opt.weights
      str_log = "Optimizer {} - {} weights shape/avg: ".format(i, len(weights))
      for w in weights:
        str_log += "{}/{:.3e}; ".format(w.shape, w.numpy().mean())
      str_log = str_log[:-2]
      self.D(str_log)

    return


  def _reinstantiate_optimizer(self, class_name_optimizer):
    name = class_name_optimizer

    if name == 'Adadelta':
      optimizer = tf.keras.optimizers.Adadelta()
    elif name == 'Adagrad':
      optimizer = tf.keras.optimizers.Adagrad()
    elif name == 'Adam':
      optimizer = tf.keras.optimizers.Adam()
    elif name == 'Adamax':
      optimizer = tf.keras.optimizers.Adamax()
    elif name == 'Ftrl':
      optimizer = tf.keras.optimizers.Ftrl()
    elif name == 'Nadam':
      optimizer = tf.keras.optimizers.Nadam()
    elif name == 'RMSprop':
      optimizer = tf.keras.optimizers.RMSprop()
    elif name == 'SGD':
      optimizer = tf.keras.optimizers.SGD()

    return optimizer


  def load_optimizers(self, trainable_weights_callbacks, label):
    new_optimizers = []
    for i, class_name_optimizer in enumerate(self.optimizers_class_name):
      new_opt = self._reinstantiate_optimizer(class_name_optimizer)
      weight_values = self.log.load_pickle_from_models(
        fn=label+'_opt_{}'.format(i+1),
        subfolder_path=self.models_subfolder_path
      )
      lst_all_weights = trainable_weights_callbacks[i]()
      lst_grads = [tf.zeros_like(x) for x in lst_all_weights]
      new_opt.apply_gradients(zip(lst_grads, lst_all_weights))
      new_opt.set_weights(weight_values)
      new_optimizers.append(new_opt)
    #endfor

    self.optimizers = new_optimizers
    if self.DEBUG:
      self.D("Load opt. debugging ...")
      self._display_opt_state()
    return


  def _analyze_multiple_callbacks(self, callbacks):
    callbacks_with_kw = []
    for callback in callbacks:
      callbacks_with_kw.append(self._analyze_callback(callback))

    return callbacks_with_kw


  def _analyze_callback(self, callback):
    """
    Analyze a callback in order to find the specific kwargs.

    Parameters:
    -----------
    callback : func, mandatory
      A evaluate / test callback

    Returns:
    --------
    callback : func:
      The same as the argument

    kw : dict
      kwargs for the callback
    """
    if callback is None:
      return

    _, required_params, _ = self.log.get_function_parameters(callback)
    kw = {}
    if len(required_params) > 0:
      assert len(required_params) in [1,2],\
        "Callback could have 1 required param (the model) or 2 required params (the model and the epoch)."

      assert self._model is not None,\
        "Could not use callback with required params because `simple_train` or `simple_train_gen` is not activated"

      kw = {required_params[0]: self._model}
      if len(required_params) == 2:
        kw[required_params[1]] = self.current_epoch
    #endif

    return callback, kw

  def _parametrize_callbacks(self):
    # in these parametrized callbacks, each element is a tuple (callback, kwargs)
    self._parametrized_evaluate_callbacks = self._analyze_multiple_callbacks(
      callbacks=self._evaluate_callbacks
    )
    self._parametrized_test_callbacks = self._analyze_multiple_callbacks(
      callbacks=self._test_callbacks
    )
    return

  def _run_parametrized_callbacks(self, parametrized_callbacks):
    dct_res = {}
    for (function, kw) in parametrized_callbacks:
      with tf.device(self.device):
        tmp = function(**kw)
      if type(tmp) is not None:
        assert type(tmp) in [dict, OrderedDict], \
          "Function used in parametrized callback should return a dict/OrderedDict"

        dct_res = {**dct_res, **tmp}
      # endif
    # endfor

    dct_res = self._convert_dct_results(dct_res)
    return dct_res

  def _check_callbacks_validity(self, lst_callbacks):
    for callback in lst_callbacks:
      if type(callback) is partial:
        if 'model' in callback.keywords:
          raise ValueError(
            'Predefined `model` param found in callback {}. '
            'Please be aware that model param is assigned in the inner training loop!'.format(
              callback
            )
          )
    return

  def _set_lr(self, lr=None):
    if lr is None:
      lr = self.lr

    self.P("[DEBUG] _set_lr called with {}".format(lr))

    for opt in self.optimizers:
      with tf.device(self.device):
        tf.keras.backend.set_value(opt.lr, lr)

    return

  def _train__log_train_hist_summary(self, epoch):
    keys = list(self.dct_hist.keys())
    if self.rev_hist_summary:
      _rng = range(epoch, epoch - self.hist_summary_size, -1)
      if len(_rng) > 0:
        self.P("Model {} history summary (last {} recent to older):".format(
          self.model_name, self.hist_summary_size
        ))
      #endif
    else:
      _rng = range(max(1, epoch - self.hist_summary_size), epoch + 1)
      if len(_rng) > 0:
        self.P("Model {} history summary (last {}):".format(
          self.model_name, self.hist_summary_size
        ))
      #endif
    #endif

    for _itr in _rng:
      if _itr <= 0:
        break
      str_log = ""
      for k in keys:
        str_log += "{}: {:.4f} ".format(k, self.dct_hist[k][_itr - 1])
      self.P("  Epoch {:03}: {}".format(_itr, str_log))
    # endfor
    return

  def _train__run_optimization_step(self, train_epoch_callback, save_optimizers):
    # run train step
    tr_start = time()
    if not save_optimizers:
      with tf.device(self.device):
        dct_train_epoch = train_epoch_callback()
    else:
      opt = self.optimizers
      if len(self.optimizers) == 1:
        opt = self.optimizers[0]
      with tf.device(self.device):
        dct_train_epoch = train_epoch_callback(current_opt=opt)
    tr_end = time()
    self.P("  Optimization time: {:.2f}s".format(tr_end - tr_start))

    assert type(dct_train_epoch) in [dict, OrderedDict], \
      "Train epoch callback should return a dict/OrderedDict"
    assert self.train_loss_key in dct_train_epoch, \
      "Mean of all batch losses should be returned in train_epoch_callback"

    dct_train_epoch = self._convert_dct_results(dct_train_epoch)
    return dct_train_epoch

  def _train__properly_set_loss_key(self, dct_epoch_results):
    """
    Method that checks if other loss keys (dev, test) besides `self.train_loss_key` were returned,
    because they are better to be used in the models backtracking process.
    """

    loss_key = self.train_loss_key
    keys = list(dct_epoch_results.keys())
    lambda_filter = lambda k: 'loss' in k and \
                              self.train_loss_key not in k and \
                              all([descr not in k for descr in train_descr])

    filtered_keys = list(filter(lambda_filter, keys))

    if len(filtered_keys) > 0:
      self.P("[INFO]  Losses (besides {}) found in dct_returns: {}".format(
        self.train_loss_key, filtered_keys
      ))
      loss_key = random.choice(filtered_keys)
    # endif
    return loss_key

  def _train__maybe_use_monitor_key_default(self, loss_key):
    if self.monitor_key is None:
      self.monitor_key = loss_key
      self.monitor_key_mode = 'min'

    self.P("[INFO]  monitor_key='{}' / monitor_key_mode='{}'".format(
      self.monitor_key, self.monitor_key_mode
    ))

    if self.monitor_key_mode == 'min':
      self.best_monitor = np.inf
    elif self.monitor_key_mode == 'max':
      self.best_monitor = -np.inf

    return

  def _train__done_cooldown(self,
                            load_model_callback,
                            bool_load_optimizers,
                            trainable_weights_callbacks):
    self.P("  Cooldown is done then lose our patience with this lr:", color='b')
    self.patience += 1
    self.fails_counter += 1
    self.P("    Patience {}/{} ({}/{} fails)".format(
      self.patience, self.max_patience,
      self.fails_counter, self.stop_at_fails
    ), color='b')

    if self.patience >= self.max_patience:
      self.P("  Patience {} reached.".format(self.max_patience), color='b')
      if self.best_label != '':
        self.P("  Loading previous best model '{}'".format(self.best_label), color='b')
        self.cleanup()
        with tf.device(self.device):
          load_model_callback(label=self.best_label)
        if bool_load_optimizers:
          assert self.best_label_optimizers != ''
          self.P("  Loading previous best model optimizers '{}'".format(self.best_label_optimizers), color='b')
          with tf.device(self.device):
            self.load_optimizers(trainable_weights_callbacks, self.best_label_optimizers)
        #endif
      else:
        self.P("  No previous model saved, best_label empty", color='b')
      #endif
      self.lr_reductions += 1
      lr_new = self.lr_start * (self.lr_decay ** self.lr_reductions)
      lr_prev = self.lr
      self.lr = max(lr_new, self.lr_min)  # max(lr1 * lr_decay, lr_min)
      self._set_lr()
      self.P("  Reduced lr from {:.4e} to {:.4e} ({} reductions from lr_start={:.4e})".format(
        lr_prev, self.lr, self.lr_reductions, self.lr_start
      ), color='b')

      self.patience = 0
      self.cooldown = self.max_cooldown
    # endif self.patience > self.max_patience
    return

  def _train__check_convergence(self,
                                crt_monitor_value,
                                load_model_callback,
                                bool_load_optimizers,
                                trainable_weights_callbacks):

    self.P("Checking if better epoch...", color='b')
    if self.monitor_key_mode == 'min':
      bool_better_epoch = (crt_monitor_value < self.best_monitor - self.eps_monitor)
    elif self.monitor_key_mode == 'max':
      bool_better_epoch = (crt_monitor_value > self.best_monitor + self.eps_monitor)
    #endif

    bool_plateau = (crt_monitor_value == self.best_monitor)

    if bool_better_epoch:
      self.P("  New best monitor {}: {:.4f} passes {:.4f}. Patience & cooldown set to 0.".format(
        self.monitor_key, crt_monitor_value, self.best_monitor - self.eps_monitor
      ), color='b')
      self.best_monitor = crt_monitor_value
      self.patience, self.cooldown = 0, 0
    else:
      self.P("  Crt monit. val {:.4f} DID NOT pass {:.4f} (best monit. {:.4f} + eps_monitor {:.4f})".format(
        crt_monitor_value, self.best_monitor + self.eps_monitor,
        self.best_monitor, self.eps_monitor
      ), color='b')

      if self.cooldown > 0:
        # cooldown helps us not to penalize the model just after it was "rebooted"
        self.cooldown -= 1
        self.P("  Cooldown decreased: {}/{}".format(self.cooldown, self.max_cooldown), color='b')
      else:
        # cooldown is done then less "lose our patience" with this lr
        self._train__done_cooldown(
          load_model_callback=load_model_callback,
          bool_load_optimizers=bool_load_optimizers,
          trainable_weights_callbacks=trainable_weights_callbacks
        )
      # endif cooldown
    #endif

    if bool_plateau:
      self.plateaus_counter += 1
    else:
      self.plateaus_counter = 0

    return


  def _train__check_new_best(self,
                             epoch,
                             dct_train_epoch,
                             dct_evaluate_epoch,
                             crt_monitor_value):
    self.P("Checking if we have new best ...")

    str_result = ''
    save = True

    sgn_mode = '<' if self.key_mode == 'min' else '>'
    dct_returns = {**dct_train_epoch, **dct_evaluate_epoch}
    crt_key_value = dct_returns.get(self.key, None)

    if crt_key_value is not None:
      bool_new_best = False

      if self.best_key is np.nan:
        bool_new_best = True
      else:
        if self.key_mode == 'min':
          bool_new_best = (crt_key_value < self.best_key - self.eps_key)
        elif self.key_mode == 'max':
          bool_new_best = (crt_key_value > self.best_key + self.eps_key)
        #endif
      #endif

      save = bool_new_best

      if not bool_new_best:
        self.P("  Epoch {} '{}' {:.4f} (DID NOT PASS best {:.4f})".format(
          epoch, self.key, crt_key_value, self.best_key
        ))
      #endif

      if bool_new_best:
        self.P("  Epoch {} new BEST '{}' {:.4f} {} {:.4f} prev".format(
          epoch,self.key, crt_key_value, sgn_mode, self.best_key
        ))
        str_result = '_{}_{:.3f}'.format(self.key, crt_key_value)
        self.best_monitor = crt_monitor_value

        # we reset the patience and the cooldown
        self.patience, self.cooldown = 0, 0

        # but more important this is the place where we reset the fails counter
        # we can sometimes decrease a bit the 'loss' particularly when changeing the lr
        # so we must have a more robust fails_counter reset-er based on required 'key'
        # otherwise we can reset on 'loss' and the training can take quite a lot
        # without any use until we reach a low lr and the updates will be meaningless
        self.fails_counter = 0

        # run test functions
        dct_test_epoch = self._run_parametrized_callbacks(
          parametrized_callbacks=self._parametrized_test_callbacks
        )

        overfit_train_dev = self.process_eval_dct_results(
          dct_results=dct_returns,
          epoch=epoch
        )
        overfit_dev_test = []

        if len(dct_test_epoch) > 0:
          overfit_dev_test = self.process_eval_dct_results(
            dct_results={**dct_evaluate_epoch, **dct_test_epoch},
            epoch=epoch
          )
        #endif

        if len(overfit_dev_test) == 0 and len(overfit_train_dev) == 3:
          # this may happen when no test_callbacks are provided, but also 'test' keys are returned in evaluate_callbacks
          overfit_dev_test = [overfit_train_dev[-1]]
        #endif

        #####
        ##### so here is a pitfall: we should not save only when required 'key'
        ##### is better than a "great" one (such as acc=0.9 as a minimal baseline)
        ##### as this will not allow us to roll-back models tham might recover "later"
        ##### in optimization "journey". As a result we use a secondary much lower threshold
        #####
        if self.key_mode == 'min':
          save = crt_key_value < self.threshold_progress
        elif self.key_mode == 'max':
          save = crt_key_value > self.threshold_progress

        if not save:
          self.P("  Epoch {} NO save checkpoint on new best '{}' {:.4f} (DID NOT PASS the threshold_progress:{})".format(
            epoch, self.key, crt_key_value, self.threshold_progress
          ))
          self.P("    Best '{}' remains unchanged: {:.4f}".format(self.key, self.best_key))

        else:
          self.P("  Epoch {} SAVE checkpoint on new best '{}' {:.4f} (PASSED the threshold_progress:{})".format(
            epoch, self.key, crt_key_value, self.threshold_progress
          ))
          self.best_key = crt_key_value
          self.P("    Best '{}' becomes: {:.4f}".format(self.key, self.best_key))

          if len(overfit_train_dev) > 0:
            self.train_dev_overfit_at_best_ep = overfit_train_dev[0]
          if len(overfit_dev_test) > 0:
            self.dev_test_overfit_at_best_ep = overfit_dev_test[0]
          self.dct_test_at_best_ep = dct_test_epoch
        # endif not save
      # endif bool_new_best
    # endif crt_key_value is not None

    return save, str_result

  def _train__on_start(self,
                       evaluate_callbacks,
                       test_callbacks,
                       trainable_weights_callbacks,
                       optimizers,
                       learning_rate):
    self._check_callbacks_validity(evaluate_callbacks)
    self._check_callbacks_validity(test_callbacks)

    self._evaluate_callbacks = evaluate_callbacks
    self._test_callbacks = test_callbacks

    self._parametrize_callbacks()

    self.optimizers = optimizers
    self.optimizers_class_name = list(map(lambda o: o.__class__.__name__, self.optimizers))

    with tf.device(self.device):
      self.lr_start = tf.keras.backend.get_value(self.optimizers[0].lr)

    if learning_rate is not None:
      assert isinstance(learning_rate, (float, np.float32, np.float64))
      if learning_rate != self.lr_start:
        self.P("Provided learning rate {} which != learning rate of the optimizer(s) ({})".format(
          learning_rate, self.lr_start
        ), color='b')
        self.lr_start = learning_rate
        self._set_lr(self.lr_start)
      # endif
    # endif

    self.lr = self.lr_start
    self.startup()

    save_optimizers = False
    if len(trainable_weights_callbacks) > 0:
      assert len(self.optimizers) == len(trainable_weights_callbacks), \
        "If `trainable_weights_callbacks` are specified, then the number of optimizers should be the same"
      save_optimizers = True
    #endif

    return save_optimizers

  def _train__on_end(self, str_time, best_files_so_far):
    try:
      self.log.plot_keras_history(
        self.dct_hist,
        plot_all=False,
        model_name=self.model_name,
        include_prefix=True,
        use_single_prefix=True
      )
    except:
      pass

    dct_ret = {}
    if self.dct_hist is not None:
      for k, v in self.dct_hist.items():
        dct_ret[k] = v[self.best_epoch - 1]

    dct_ret = {**dct_ret, **self.dct_test_at_best_ep}
    try:
      fsize = os.path.getsize(self._best_fn)
    except:
      fsize = -1
    dct_ret['SZ_MB'] = fsize / 1024 ** 2

    self.P("\nTraining results:")
    self.P(" * Time: {}".format(str_time))
    self.P(" * Best epoch: {}".format(self.best_epoch))
    self.P(" * Results @best_epoch:")
    for k, v in dct_ret.items():
      self.P("     {:<12} {:.4f}".format(k + ':', v))
    self.P(" * Overfits @best_epoch:")

    if self.train_dev_overfit_at_best_ep is not np.nan:
      k = 'tr_dv_ovf_{}'.format(self.key_metric)
      self.P("     {:<18} {:.4f}".format(k + ':', self.train_dev_overfit_at_best_ep))
      dct_ret[k] = self.train_dev_overfit_at_best_ep
    #endif

    if self.dev_test_overfit_at_best_ep is not np.nan:
      k = 'dv_ts_ovf_{}'.format(self.key_metric)
      self.P("     {:<18} {:.4f}".format(k + ':', self.dev_test_overfit_at_best_ep))
      dct_ret[k] = self.dev_test_overfit_at_best_ep
    #endif

    self.P(" * Train maxes:")
    for k, v in self.train_maxes.items():
      self.P("     {:<12} {:.4f} @ ep:{:03}".format(k, v['VAL'], v['EP']))
      dct_ret[k + '_max'] = "{:.4f}@{:03}".format(v['VAL'], v['EP'])
    #endfor

    if self.delete_if_key_worse is not None:
      thr = self.delete_if_key_worse
      best_val = dct_ret[self.key]
      must_delete = thr > best_val if self.key_mode.lower() == 'max' else thr < best_val
      if must_delete:
        self.P("  DELETING final model '{}' as best {} of {:.4f} is worse than threshold {:.4f}".format(
          self.best_label, self.key, best_val, thr
        ))
        self._add_model_history(best_files_so_far)
    # endif

    if self.delete_best_model:
      self.P("  DELETING final model '{}' due to `delete_best_model` boolean".format(self.best_label))
      self._add_model_history(best_files_so_far)
    # endif

    # mandatory clean-up
    self._delete_model_history(force=True)

    # optional clean-up (based on how the Trainer is instantiated)
    if self.final_cleanup:
      self.cleanup()

    ret_tuple = [self.best_epoch, dct_ret, None]
    if self.return_history:
      ret_tuple[-1] = self.dct_hist

    return ret_tuple

  def _train__main_loop(self,
                        train_epoch_callback,
                        bool_save_optimizers,
                        load_model_callback,
                        save_model_callback,
                        trainable_weights_callbacks,
                        time_exceeded_callback):

    bool_load_optimizers = bool_save_optimizers
    dct_train = {}
    dct_evaluate = {}
    best_files_so_far = []

    for epoch in range(1, self.epochs + 1):
      self.current_epoch = epoch
      start = time()
      self.P("\nTraining epoch {} with lr={:.4e}...".format(epoch, self.lr))

      dct_train_epoch = self._train__run_optimization_step(train_epoch_callback, bool_save_optimizers)
      # combine train step results with global results
      if epoch == 1:
        dct_train = {k: [] for k in dct_train_epoch.keys()}

      for k in dct_train_epoch.keys():
        dct_train[k].append(dct_train_epoch[k])
      # end combining train step results

      dct_evaluate_epoch = self._run_parametrized_callbacks(
        parametrized_callbacks=self._parametrized_evaluate_callbacks
      )
      # combine evaluate step results with global results
      if epoch == 1:
        dct_evaluate = {k: [] for k in dct_evaluate_epoch.keys()}

      for k in dct_evaluate_epoch.keys():
        dct_evaluate[k].append(dct_evaluate_epoch[k])
      # end combining evaluate step results
      self.dct_hist = {**dct_train, **dct_evaluate}

      dct_returns = {**dct_train_epoch, **dct_evaluate_epoch}
      if self.key not in dct_returns:
        self.P("[WARNING] '{}' key not returned in training/evaluation callbacks!".format(self.key))

      self._train__log_train_hist_summary(epoch)
      if epoch == 1:
        loss_key = self._train__properly_set_loss_key(dct_returns)
        self._train__maybe_use_monitor_key_default(loss_key)
      # endif

      crt_monitor_value = dct_returns[self.monitor_key]
      self._train__check_convergence(
        crt_monitor_value=crt_monitor_value,
        load_model_callback=load_model_callback,
        bool_load_optimizers=bool_load_optimizers,
        trainable_weights_callbacks=trainable_weights_callbacks
      )

      if self.fails_counter >= self.stop_at_fails:
        self.P("REACHED {} FAILS. Cancelling training...".format(self.fails_counter))
        break
      # endif

      if self.plateaus_counter >= self.stop_at_plateaus:
        self.P("REACHED {} PLATEAUS. Cancelling training...".format(self.plateaus_counter))
        break
      # endif

      save, str_result = self._train__check_new_best(
        epoch=epoch,
        dct_train_epoch=dct_train_epoch,
        dct_evaluate_epoch=dct_evaluate_epoch,
        crt_monitor_value=crt_monitor_value
      )

      if save and self.save_checkpoint:
        self.best_epoch = epoch
        with tf.device(self.device):
          self.best_label, fn = save_model_callback(epoch=epoch, str_result=str_result)
        if type(fn) is not list:
          fn = [fn]

        fn_opt = []
        if bool_save_optimizers:
          with tf.device(self.device):
            self.best_label_optimizers, fn_opt = self.save_optimizers(
              epoch=epoch,
              str_result=str_result
            )

        self._add_model_history(best_files_so_far)
        best_files_so_far = fn + fn_opt
        self._best_fn = fn[0]
        self._delete_model_history()
      # endif save

      end = time()
      str_time = self._convert_seconds_to_log(start, end)
      self.P("Epoch {} finished in {}".format(epoch, str_time))

      if time_exceeded_callback is not None:
        if time_exceeded_callback():
          break
    # endfor - epoch

    return best_files_so_far

  def train(self,
            optimizers,
            train_epoch_callback,
            save_model_callback,
            load_model_callback,
            learning_rate=None,
            evaluate_callbacks=None,
            test_callbacks=None,
            trainable_weights_callbacks=None,
            time_exceeded_callback=None,
            ):
    """
    Generalised train routine

    Parameters
    ----------
    optimizers : tf.keras.optimizer / [tf.keras.optimizer]
      All the optimizers used for training the model(s).

    train_epoch_callback : function
      Callback for training the model(s).

    save_model_callback : function
      Callback for saving the model(s).

    load_model_callback : function
      Callback for loading the model(s).

    learning_rate : float
      Parameter that forces the learning rate of the model to be refresed to that value.
      This parameter is useful when you have to deal with multiple retrainings and at each new retraining
      the model should be reinitialized with a big learning rate, not with a small one resulted after
      the previous retrainings.
      The default is None. In this case the learning rate remains unchanged.

    evaluate_callbacks : list[function], optional
      Callbacks for evaluating the model(s).
      The default is None ([]).

    test_callbacks : list[function], optional
      Callbacks for testing the model(s) when one reach a maximum state.
      The default is None ([]).

    trainable_weights_callbacks : [function], optional
      If the optimizers cannot be serialized by save_model_callback (complex
      training routine), than for each optimizer we should specify a callback
      that returns the weights that are optimized by that optimizer.

      If specified, this list should have the same length as the number of optimizers
      and each callback in this list should correspond to an optimizer, i.e. the
      callback @position i should correspond to optimizer #i.

      The default is None ([]).

    time_exceeded_callback : function, optional
      Callback that is called without any argument that returns True if the total training
      time was exceeded and False otherwise. If the time was exceeded, the training is stopped.

      The default is None.

    Returns
    -------
    best_epoch (int), dictionary with all values @best_epoch (dict)

    or (if return_history is set to True in __init__):

    best_epoch (int), dictionary with all values @best_epoch (dict), training_history (dict)
    """

    if evaluate_callbacks is None:
      evaluate_callbacks = []

    if test_callbacks is None:
      test_callbacks = []

    if trainable_weights_callbacks is None:
      trainable_weights_callbacks = []

    if type(optimizers) is not list:
      optimizers = [optimizers]

    if type(trainable_weights_callbacks) is not list:
      trainable_weights_callbacks = [trainable_weights_callbacks]

    bool_save_optimizers = self._train__on_start(
      evaluate_callbacks=evaluate_callbacks,
      test_callbacks=test_callbacks,
      trainable_weights_callbacks=trainable_weights_callbacks,
      optimizers=optimizers,
      learning_rate=learning_rate
    )

    self.P("Training model {} for {} epochs starting with lr={:.4e}".format(
      self.model_name, self.epochs, self.lr_start
    ))

    start_train = time()
    best_files_so_far = self._train__main_loop(
      train_epoch_callback=train_epoch_callback,
      bool_save_optimizers=bool_save_optimizers,
      load_model_callback=load_model_callback,
      save_model_callback=save_model_callback,
      trainable_weights_callbacks=trainable_weights_callbacks,
      time_exceeded_callback=time_exceeded_callback
    )
    end_train = time()
    str_time = self._convert_seconds_to_log(start_train, end_train)

    return self._train__on_end(str_time, best_files_so_far)

  def _add_model_history(self, file_list):
    if self.last_saved_model is None:
      self.last_saved_model = file_list
    else:
      self.last_saved_model += file_list
    return

  def _delete_model_history(self, force=False):
    if self.last_saved_model is not None:
      while len(self.last_saved_model) > 0:
        not_saved = []
        for last_saved in self.last_saved_model:
          if os.path.isfile(last_saved):
            try:
              os.remove(last_saved)
              self.P(" File '...{}' deleted!".format(last_saved[-50:]))
            except:
              self.P(" File '...{}' NOT deleted!".format(last_saved[-50:]))
              not_saved.append(last_saved)
          else:
            self.P(" [WARNING] '{}' IS NOT A FILE!".format(last_saved))
        #endfor
        self.last_saved_model = not_saved
        if not force:
          break
      #endwhile
    #endif

  def cleanup(self):
    tf.keras.backend.clear_session()
    if hasattr(self, '_model') and self._model is not None:
      del self._model

    del self.optimizers
    tf.keras.backend.clear_session()
    self.P("Cleaned up the current Trainer ...")
    return


class GridSearcher(BaseDecentrAIObject):

  MODEL_NAME_DF_COL = 'model_name'

  def __init__(self,
               log,
               monitor_key,
               monitor_key_mode,
               grid_params,
               get_train_params_callback,
               one_folder_per_model=False,
               base_name=None,
               exclusions=None,
               append_to_model_name=None,
               allow_max_mem_growth=1024,
               time_exceeded_callback=None,
               models_subfolder_path=None,
               results_subfolder_path=None,
               **kwargs):
    """
    Constructor

    Parameters
    ----------
    log : Logger, mandatory

    monitor_key : str, mandatory
      Passed to `libraries.training.Trainer.__init__`

    monitor_key_mode : str, mandatory
      Passed to `libraries.training.Trainer.__init__`

    grid_params : dict / list
      If dictionary, it specifies all grid params. For each key of the dictionary,
      it should be specified a list of values that will be tried in the grid search.
      Example - if we want to try different nr embeddings / dense layers / regularizers for a model:
        grid_params = {'emb' : [128, 256],
                       'dense': [[600], [256, 64], [1024, 512]],
                       'reg': [0, 0.05]}

      If list, it specifies a subset of params combinations that should be tested. Not suited for exhaustive
      grid search.
      Example - we suppose that the above grid search resulted in best 2 models to be
        [{'emb': 128, 'dense': [600], 'reg': 0},
         {'emb': 256, 'dense': [256, 64], 'reg': 0.05}]. Now, grid_params can be exactly this list and just these 2
         models will be trained & optionally, validated.

    get_train_params_callback : function
      Callback that returns all pairs (param:value) for `train` / `simple_train` / `simple_train_gen` methods in Trainer.
      For example, if the grid search uses `simple_train_gen` as the training methodology,
      then this callback should return:
          {'model': ..., 'train_gen': ..., 'steps_per_epoch': ...,
           'evaluate_callbacks': ...,
           'eager': True
          }

    one_folder_per_model : boolean, optional
      Flag that specifies whether each model will have a separate folder in which its related files are saved, or everything
      will be saved in the same folder.
      This functionality arised because a model is not everytime composed of a single .h5 file. You may want to save metadata,
      or pickles along with that .h5 file. Thus, when you have a grid search of N models, instead having N files you find
      multiple files pointing to the same model.
      The default is False

    exclusions : list[dict], optional
      A list of dictionaries, where each dictionary specify the combination of (key: [values])
      that will be excluded from the grid search. Example:
        exclusions = [
            {'emb': [128], 'dense': [[600]]},
            {'dense': [[1024, 512]], 'reg': [0]}
        ] will exclude from grid search the combinations where:
          - (emb=128, dense=[600]);
          - (dense=[1024,512], reg=0)
      The default is None.


    base_name : str, optional
      The name of the grid searched model.
      The default is None ('')

    append_to_model_name : str, optional
      The key in the `grid_params` dict that will be added to the model name. For example,
      if `model_name` is 'ExampleClassifier' and `append_to_model_name` is `emb`, then
      in results, it will appear as `model_name` 'ExampleClassifier_XXX_emb_y', where x
      is the iteration index and y is the value of `emb`.
      The default is None.

    allow_max_mem_growth : int, optional [1024]
      The maximum MegaBytes that are allowed to be added to the current process from
      the first iteration.

    time_exceeded_callback : function, optional
      Callback that is called without any argument that returns True if the total training
      time was exceeded and False otherwise. If the time was exceeded, the grid search is stopped.
      The default is None.

    models_subfolder_path : str, optional
      A path relative to '_models' where the trained models are saved
      The default is None

    results_subfolder_path : str, optional
      A path relative to '_output' or '_data' where the grid search results are saved
      The default is None

    **kwargs :
      Arguments for Trainer - log should be specified, because they are passed also
      to the parent class (BaseDecentrAIObject)

    """
    assert type(grid_params) in [dict, OrderedDict, list]

    if base_name is None or base_name == '':
      raise ValueError("Please provide a valid and clear name for your models. Thank you.")

    self.grid_params = grid_params
    self.get_train_params_callback = get_train_params_callback
    self.one_folder_per_model = one_folder_per_model
    self.exclusions = exclusions
    self.base_name = base_name
    if self.base_name is None:
      self.base_name = ''
    self.append_to_model_name = append_to_model_name
    self.allow_max_mem_growth = allow_max_mem_growth
    self.time_exceeded_callback = time_exceeded_callback
    self.models_subfolder_path = models_subfolder_path
    self.results_subfolder_path = results_subfolder_path
    self.kwargs = kwargs
    self.kwargs['monitor_key'] = monitor_key
    self.kwargs['monitor_key_mode'] = monitor_key_mode

    self.grid_dicts = []
    self.histories = []
    self.dict_grid_results = OrderedDict({})
    self.nr_grid_iters = 0
    self.current_trainer = None
    self.mem_history = []
    self.best_model_label = None

    super().__init__(log=log, **kwargs)

    self.P("Initialized {}.".format(self.log.get_object_params(self, n=1)))
    return

  @staticmethod
  def simple_build_model_name(i, base_name):
    model_name = '{}_{:03d}'.format(base_name, i+1)
    return model_name

  def _build_model_name(self, i, grid_dict):
    model_name = self.simple_build_model_name(i, self.base_name)
    if self.append_to_model_name in grid_dict.keys():
      model_name += '_{}_{}'.format(
        self.append_to_model_name,
        grid_dict[self.append_to_model_name]
      )

    return model_name

  def _iterate_model_def(self):
    self.nr_grid_iters = len(self.grid_params)
    for i,grid_dict in enumerate(self.grid_params):
      if self.MODEL_NAME_DF_COL not in grid_dict:
        grid_dict[self.MODEL_NAME_DF_COL] = self._build_model_name(i, grid_dict)

      yield grid_dict

  def _generate_model_def(self, max_iters=None):
    self.P("Generating model definition(s) ...")

    full_grid = self.log.get_grid_iterations(
      self.grid_params,
      exceptions=self.exclusions,
      DEBUG=self.DEBUG
    )

    key_order = [self.MODEL_NAME_DF_COL] + list(self.grid_params.keys())
    if max_iters is None:
      max_int = np.iinfo(np.int32).max
      max_iters = max_int

    nr_all_iters = len(full_grid)
    self.P("  Nr iterations: {}".format(nr_all_iters))
    if nr_all_iters > max_iters:
      full_grid = np.random.choice(full_grid, size=max_iters, replace=False)
      self.P("  Nr iterations cut to: {}".format(max_iters))
    #endif

    self.nr_grid_iters = len(full_grid)

    for i, grid_dict in enumerate(full_grid):
      grid_dict[self.MODEL_NAME_DF_COL] = self._build_model_name(i, grid_dict)
      grid_dict_ordered = {k : grid_dict[k] for k in key_order}
      yield grid_dict_ordered


  def _check_memory_overflow(self):
    self.mem_history.append(int(self.log.get_current_process_memory(mb=True)))
    condition = False
    condition = condition or (self.mem_history[-1] > self.mem_history[0] + self.allow_max_mem_growth)
    if len(self.mem_history) > 1:
      condition = condition and (self.mem_history[-1] > self.mem_history[-2] + 0.1 * self.allow_max_mem_growth)

    if condition:
      self.P("*" * 50)
      self.P("WARNING! Memory overflow! History: {}".format(self.mem_history))
      self.P("*" * 50)
    return

  def _setup_model_subfld(self, model_subfolder_path):
    subfld = os.path.join(self.log.get_models_folder(), model_subfolder_path)
    if not os.path.exists(subfld):
      os.makedirs(subfld)
      return True

    return False

  @staticmethod
  def get_grid_name_suffix():
    return '_grid'

  def run(self, max_iters=None, return_top_k=1, save_df=True):
    """
    Grid search routine.
    Each Trainer will be initialized using:
      1. kwargs that were specified for `GridSearcher` that are passed to
         BaseDecentrAIObject (because Trainer is also a BaseDecentrAIObject)
      2. Trainer variable parameters specified in grid_dict.

    Parameters
    ----------

    max_iters : int, optional
      Cutoff value for nr iterations.
      The default value is None.

    return_top_k : int, optional
      how many best grid iterations dictionaries are returned
      The default value is 1

    save_df : bool, optional
      Whether to save the results dataframe after each grid iteration or not
      The default value is True
    """


    trainer_init_params, _, _ = self.log.get_function_parameters(Trainer.__init__)
    trainer_init_params = trainer_init_params[:-1]
    _, trainer_required_train_params, _ = self.log.get_function_parameters(Trainer.train)
    _, trainer_required_simple_train_params, _ = self.log.get_function_parameters(Trainer.simple_train)
    _, trainer_required_simple_train_gen_params, _ = self.log.get_function_parameters(Trainer.simple_train_gen)

    if type(self.grid_params) in [dict, OrderedDict]:
      gen = self._generate_model_def(max_iters=max_iters)
    elif type(self.grid_params) is list:
      gen = self._iterate_model_def()
    else:
      gen = None

    mean_time = np.inf
    lst_time = []

    files_and_results = [] # used to register all model names and their best result

    for idx, grid_dict in enumerate(gen):
      start = time()

      # Trainer kwargs composed of:
      # 1. kwargs that were specified for `GridSearcher` that are passed to
      # BaseDecentrAIObject (because Trainer is also a BaseDecentrAIObject)
      # 2. Trainer variable parameters specified in grid_dict.
      trainer_crt_kwargs = self.kwargs
      for k,v in grid_dict.items():
        if k in trainer_init_params:
          trainer_crt_kwargs[k] = v
      # end trainer kwargs

      self.grid_dicts.append(grid_dict)
      lst_grid_dict = ["{}:{}".format(k,v) for k,v in grid_dict.items()]
      str_grid_dict = "  ".join(lst_grid_dict)


      t_elapsed = idx * mean_time / 3600
      t_remain = (self.nr_grid_iters - idx) * mean_time / 3600
      t_total = t_elapsed + t_remain
      self.P("==================================")
      self.P("Training giter {}/{} - total/elapsed/remain: {:.1f}/{:.1f}/{:.1f} hrs (giter: {:.1f} mins)"
             .format(idx+1, self.nr_grid_iters, t_total, t_elapsed, t_remain, mean_time / 60))
      self.P(" * {}".format(str_grid_dict))
      self.P("==================================")

      crt_model_subfolder_path = self.models_subfolder_path
      if self.one_folder_per_model:
        crt_model_subfolder_path = os.path.join(crt_model_subfolder_path, grid_dict[self.MODEL_NAME_DF_COL])

      if crt_model_subfolder_path is not None:
        self._setup_model_subfld(crt_model_subfolder_path)

      self.current_trainer = Trainer(
        log=self.log,
        models_subfolder_path=crt_model_subfolder_path,
        **trainer_crt_kwargs
      )

      ### get_train_params_callback should return a dict having all required
      ### `train` / `simple_train` / `simple_train_gen` params
      callback_params = grid_dict
      if 'model_subfolder_path' in self.log.get_function_parameters(self.get_train_params_callback)[1]:
        callback_params['model_subfolder_path'] = crt_model_subfolder_path
      with tf.device(self.current_trainer.device):
        train_params = self.get_train_params_callback(**grid_dict)

      train_params_keys = set(list(train_params.keys()))

      if len(set(train_params_keys) & set(trainer_required_train_params)) == len(trainer_required_train_params):
        ret_train = self.current_trainer.train(
          **train_params,
          time_exceeded_callback=self.time_exceeded_callback
        )
      elif len(set(train_params_keys) & set(trainer_required_simple_train_params)) == len(trainer_required_simple_train_params):
        ret_train = self.current_trainer.simple_train(
          **train_params,
          time_exceeded_callback=self.time_exceeded_callback
        )
      elif len(set(train_params_keys) & set(trainer_required_simple_train_gen_params)) == len(trainer_required_simple_train_gen_params):
        ret_train = self.current_trainer.simple_train_gen(
          **train_params,
          time_exceeded_callback=self.time_exceeded_callback
        )
      else:
        raise ValueError("Unknown train params!")

      self._check_memory_overflow()

      best_epoch, dct_best_epoch, dct_hist = ret_train
      if self.current_trainer.return_history:
        self.histories.append(dct_hist)

      ### Add to `dict_grid_results` all the keys specified in grid_dict, and also
      ### trainer constant params if the `add_constant_params_to_results`
      ### boolean is True.
      ###
      if idx == 0:
        self.dict_grid_results[self.MODEL_NAME_DF_COL] = []
        for k in dct_best_epoch.keys():
          self.dict_grid_results[k] = []
        self.dict_grid_results['best_ep'] = []

        for k in grid_dict.keys():
          if k != self.MODEL_NAME_DF_COL:
            self.dict_grid_results[k] = []
      #endif

      for k,v in dct_best_epoch.items():
        if any([x in k for x in percentage_metrics]) and type(v) != str:
          self.dict_grid_results[k].append(v * 100)
        else:
          self.dict_grid_results[k].append(v)
      self.dict_grid_results['best_ep'].append(best_epoch)

      for k,v in grid_dict.items():
        self.dict_grid_results[k].append(v)
      ### end add to `dict_grid_results`

      ###
      last_best_val = dct_best_epoch[self.current_trainer.key]
      files_and_results.append((self.current_trainer.best_label, last_best_val))
      key_mode = self.current_trainer.key_mode
      ###

      df = pd.DataFrame.from_dict(self.dict_grid_results).sort_values(self.current_trainer.key)
      if save_df:
        try:
          self.log.save_dataframe(
            df,
            '{}{}'.format(self.base_name, self.get_grid_name_suffix()),
            show_prefix=True,
            subfolder_path=self.results_subfolder_path
          )
        except:
          self.log.save_pickle_to_data(
            self.dict_grid_results,
            '_debug_dict_grid_results.pickle',
            subfolder_path=self.results_subfolder_path
          )
          raise ValueError("")
      #endif - save_df

      stop = time()
      lap_time = stop - start
      lst_time.append(lap_time)
      mean_time = np.mean(lst_time)

      self.P('Finished lap in {:.1f}s'.format(lap_time))
      self.P("Results:\n{}".format(self.log.drop_constant_columns(df)))

      # also cleanup generators & other memory stuff
      self._cleanup()

      if self.time_exceeded_callback is not None:
        if self.time_exceeded_callback():
          break
    #endfor - grid iterations
    sorted_results = sorted(files_and_results, key=lambda x:x[1])
    if key_mode =='max':
      self.best_model_label = sorted_results[-1][0]
      top_k_grid_iterations = df.tail(return_top_k)[list(grid_dict.keys())].to_dict('records')
    else:
      self.best_model_label = sorted_results[0][0]
      top_k_grid_iterations = df.head(return_top_k)[list(grid_dict.keys())].to_dict('records')
    return df, self.best_model_label, top_k_grid_iterations

  def _cleanup(self):
    self.current_trainer.cleanup()
    del self.current_trainer
    self.P("Current trainer deleted.")
    return

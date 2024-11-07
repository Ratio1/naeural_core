import numpy as np
import torch as th

from collections import OrderedDict
import textwrap
import os
from time import time

from naeural_core import DecentrAIObject
from .utils import Pr


_LOSSES = {
  'mse'                 : th.nn.MSELoss,
  'binary_crossentropy' : th.nn.BCELoss,
  'mae'                 : th.nn.L1Loss,
  'logits_sparse_categorical_crossentropy': th.nn.CrossEntropyLoss,
  'smooth_mae'          : th.nn.SmoothL1Loss, # this is similary to HuberLoss
  # TODO: Must add HuberLoss
  # TODO: Must add quantile loss
  }

_OPTIMS  = {
  'adam': th.optim.Adam,
  'sgd': th.optim.SGD,
  'rmsprop':th.optim.RMSprop,
  }

  
  
class ModelTrainer(DecentrAIObject):
  def __init__(self,
               model,
               losses,
               optimizer='adam',
               lr=0.001,
               reload_init_callback=None,
               predict_callback=None,
               evaluate_callback=None,
               evaluate_train_callback=None,
               train_on_batch_callback=None,
               max_patience=10,
               max_fails=30,
               cooldown=2,
               lr_decay=0.5,
               batch_size=32,
               score_mode='max',
               score_key='dev_acc',
               device=None,
               min_score_thr=None,
               min_score_eps=1e-4,
               validation_data=None,
               model_name=None,
               evaluate_train_best_model=True,
               evaluate_train_at_new_best=False,
               base_folder=None,
               **kwargs,
               ):
    
    """
    TODO: Refactor to match tf trainer
    OBS: This is actually the proto-trainer from which emerged the tf trainer

    Parameters
    ----------
    
    model: th.Module
      the model object that must be already instantiated

    loss: callable or str
      loss module class or function. 

    optimizer : str or th.optim, optional
      proposed optimizer class. The default is th.optim.Adam.

    lr : float, optional
      proposed starting learning rate. The default is 0.001.
      
    reload_init_callback: function, optional
      a function that needs to be called after a checkpoint has been called
      
    predict_callback: function, optional
      custom predict function, default is just a forward pass
    
    evaluate_callback: function, optional
      custom evaluate callback, default uses predict and computes accuracy

    train_on_batch_callback: function, optional
      custom train callback, default uses _train_on_batch

    max_patience : int, optional
      max number of epochs for patience. The default is 10.
      
    max_fails : int, optional
      max number of consecutive fails before stopping. The default is 30.
      
    cooldown : int, optional
      how many epochs to skip patience counting. The default is 2.
      
    lr_decay : float, optional
      scaling factor when reducing lr. The default is 0.5.
      
    batch_size : int, optional
      batch size to be used with th.utils.data. The default is 32.
      
    score_mode : str, optional
      how to compare the score key. The default is 'max'.
      
    score_key : str, optional
      the name of the score key. The default is 'acc'.
      
    device : tf.device, optional
      proposed device where all the training will be done. If (default) is None 
      then 'CUDA' will be used.
      
    validation_data : list or tuple of ndarrays, optional
      validation data. The default is None.

    min_score_thr : float, optional
      minimal score to consider the result as valid. The default is None.
      
    min_score_eps : float, optional
      minimal score delta to consider model is better

    base_folder : str, optional
      Folder in which to save the model weights. Defaults to None (self.log.models_folder)

    Returns
    -------
    None.

    """
    self.model = model
    self.model_name = model_name
    self.reload_init = reload_init_callback if reload_init_callback is not None else self._reload_init
    self.predict = predict_callback if predict_callback is not None else self._predict
    self.evaluate = evaluate_callback if evaluate_callback is not None else self._evaluate
    self.evaluate_train = evaluate_train_callback if evaluate_train_callback is not None else self._evaluate
    self._evaluate_train_best_model = evaluate_train_best_model
    self._evaluate_train_at_new_best = evaluate_train_at_new_best
    self.train_on_batch = train_on_batch_callback if train_on_batch_callback is not None else self._train_on_batch

    self._loss_call = losses
    self.losses = None
    self._optimizer_class = optimizer
    self.optimizer = None
    
    self.score_mode = score_mode
    self.score_key = score_key
    self.batch_size = batch_size
    self.device = th.device("cuda" if th.cuda.is_available() else "cpu") if device is None else device
    self.lr = lr        
    self.max_patience = max_patience
    self.max_fails = max_fails
    self.cooldown = cooldown
    self.lr_decay = lr_decay
    self.validation_data = validation_data
    self.min_score_eps = min_score_eps

    if min_score_thr is None:
      self.min_score_thr =  -np.inf if self.score_mode == 'max' else np.inf
    else:
      self.min_score_thr = min_score_thr
    
    self.lr_decay_iters = 0
    self.no_remove=False
    self.errors= []
    self.training_status = {}
    self._files_for_removal = []
    self._debug_data = None
    self.epochs_data = {}
    self.in_training = False
    self.current_epoch = None
    self.score_eval_func = max if self.score_mode == 'max' else min

    self.average_loss = None
    self.base_folder = base_folder

    self._timers_section = 'THModelTrainer'
    super().__init__(**kwargs)

    if self.evaluate_train is None:
      self.P("Warning! No evaluate train callback set!", color='r')
    return
  
  def startup(self):
    super().startup()
    self.__name__ = 'THT'

    if self.base_folder is None:
      self.base_folder = self.log.get_models_folder()
    return

  def P(self, s, **kwargs):
    super().P(s, prefix=True, **kwargs)
    return
  
  def __repr__(self):
    _s  = "  Model: '{}'+\n".format(self.model_name)
    _s += textwrap.indent(str(self.model), " " * 4) + '\n'
    _s += textwrap.indent("Loss: {}".format(self.losses), " " * 4)
    return _s
  
    
  def _evaluate(self, epoch, verbose=False, key='dev', **kwargs):
    ### TODO: Check all list fctions
    if self.validation_data is None:
      raise ValueError('Default evaluate failed due to no validation data')
    dct_results = OrderedDict()
    x_eval = self.validation_data[0]
    if type(x_eval) != list:
      x_eval = [x_eval]
    np_y_hat = self.predict(x_eval)
    if self.score_key.lower() == 'acc':
      n_outs = np_y_hat.shape[-1]
      if n_outs > 1:
        np_y_pred = np.argmax(np_y_hat, -1)
      else:
        np_y_pred = np_y_hat >= 0.5
      y_true = self.validation_data[1]
      dct_results['{}_acc'.format(key)] = (np_y_pred == y_true).sum() / y_true.shape[0]
    else:
      raise ValueError("No default implementation for score type '{}'".format(self.score_key))
    return dct_results
    
    
  def _predict(self, x_data):
    self.model.eval()
    with th.no_grad():
      th_X = [th.tensor(x, device=self.device) for x in x_data]
      if not self.input_type_list:
        th_X = th_X[0]
      th_preds = self.model(th_X)
      np_preds = [x.cpu().numpy() for x in th_preds]
    self.model.train()
    return np.array(np_preds)

    
  def _reload_init(self, dct_epoch_data):
    self.P("Reload Init: {}".format(dct_epoch_data))
    return
  
  def _train_on_batch(self, model, optimizer, losses, batch, y_index=1, batch_index=False):
    # batch_index: flag that specifies if last value of the batch is the batch index
    if batch_index:
      batch = batch[:-1]

    if len(batch) == 1:
      lst_th_x = [batch[0]]
      lst_th_y = None
    else:
      lst_th_x = batch[:y_index]
      lst_th_y = batch[y_index:]
    #endif

    if not self.input_type_list:
      lst_th_x = lst_th_x[0]

    if type(lst_th_x[0]) is list:
      data_device = lst_th_x[0][0].device
    else:
      data_device = lst_th_x[0].device
    #endif
    model_device = next(model.parameters()).device

    if data_device != model_device:
      if type(lst_th_x[0]) is list:
        lst_th_x = [[_x.to(model_device) for _x in x] for x in lst_th_x]
      else:
        lst_th_x = [x.to(model_device) for x in lst_th_x]
      #endif
    #endif


    th_yh = model(lst_th_x)
    if lst_th_y[0].device != th_yh.device:
      lst_th_y = [y.to(th_yh.device) for y in lst_th_y]


    losses_vals = []
    if type(th_yh) is not list:
      th_yh = [th_yh]

    if lst_th_y is not None:
      ### TODO: CHECK If ok
      for i, loss in enumerate(losses):
        losses_vals.append(losses[loss](th_yh[i], lst_th_y[i]))
    else:
      for i, loss in enumerate(losses):
        losses_vals.append(losses[loss](th_yh[i]))
    #endif
    ### TODO: sanity check
    th_loss = th.stack(losses_vals, dim=0).sum(dim=0)#th.sum(*losses_vals, keepdim=True)

    optimizer.zero_grad()
    th_loss.mean().backward()
    optimizer.step()
    err = th_loss.detach().cpu().numpy()
    return err
    

  def _maybe_define_loss(self):
    if self.losses is not None:
      return
    loss_call = None
    if isinstance(self._loss_call, str):
      s_loss = self._loss_call.lower()
      if s_loss in _LOSSES:
        loss_call = _LOSSES[s_loss]
      else:
        raise ValueError("loss '{}' not defined. Available options are: {}".format(
          s_loss, list(_LOSSES.keys())))
    else:
      loss_call = self._loss_call

    if type(loss_call) is dict:
      self.losses = loss_call
    else:
      self.losses = {'loss': loss_call}
    return
  
  def _maybe_define_optimizer(self):
    if self.optimizer is not None:
      return
    optimizer_class = None
    if isinstance(self._optimizer_class,str):
      s_optim = self._optimizer_class.lower()
      if s_optim in _OPTIMS:
        optimizer_class = _OPTIMS[s_optim]
      else:
        raise ValueError('{} not available. Available optimizers are: {}'.format(
          s_optim, list(_OPTIMS.keys())))
    else:
      optimizer_class = self._optimizer_class
    
    self.optimizer = optimizer_class(self.model.parameters(), lr=self.lr)
    return

  def is_new_best(self, dct_score):
    if self.score_key not in dct_score: ## TODO This is in case of train eval
      return False
    score = dct_score[self.score_key]
    test_score = score - self.min_score_eps if self.score_mode.lower() == 'max' else score + self.min_score_eps
    new_best = self.score_eval_func(test_score, self.training_status['best_score']) != self.training_status['best_score']
    return new_best

  def start_timer(self, tmr_id):
    if self.log.is_main_thread:
      section = None
    else:
      section = self._timers_section

    self.log.start_timer(tmr_id, section=section)
    return

  def end_timer(self, tmr_id, skip_first_timing=False, periodic=False):
    if self.log.is_main_thread:
      section = None
    else:
      section = self._timers_section

    self.log.end_timer(tmr_id, skip_first_timing=skip_first_timing, section=section, periodic=periodic)
    return

  def stop_timer(self, tmr_id, skip_first_timing=False, periodic=False):
    self.end_timer(tmr_id=tmr_id, skip_first_timing=skip_first_timing, periodic=periodic)
    return

  def fit(self, x_train=None, th_dl=None, y_train=None, batch_index=False, epochs=10000, verbose=False):
    """
    The training method

    Parameters
    ----------
    x_train : ndarray # TODO: must support list of tensors
      the actual training data.
      
    y_train : ndarray, optional
      y_hat data. The default is None.
      
    epochs : int, optional
      max number of epochs. The default is 10000.
      
    verbose : bool, optional
      show verbose information. The default is False.

    Raises
    ------
    ValueError
      DESCRIPTION.

    Returns
    -------
    TYPE
      DESCRIPTION.

    """    
    if self.model_name is None:
      self.model_name = self.model.__class__.__name__
         
    self._maybe_define_loss()
    self._maybe_define_optimizer()
      
    if self.score_key is None:
      raise ValueError("Scoring config is incomplete!")

    if x_train is None:
      assert th_dl is not None
    if th_dl is None:
      assert x_train is not None
      
    self.P("Moving {} and data to device {}".format(
      self.model.__class__.__name__, self.device))
    self.model.to(self.device)
    if type(x_train) != list:
      x_train = [x_train]
      self.input_type_list = False
    else:
      self.input_type_list = True

    if type(y_train) != list:
      y_train = [y_train]

    if th_dl is None:
      x_tensors = [th.tensor(x, device=self.device) for x in x_train if x is not None]
      y_tensors = [th.tensor(y, device=self.device) for y in y_train if y is not None]

      tensors = x_tensors + y_tensors
      y_index = len(x_tensors)

      th_ds = th.utils.data.TensorDataset(*tensors)
      th_dl = th.utils.data.DataLoader(
          th_ds,
          batch_size=self.batch_size,
          shuffle=True)
    else:
      y_index = -1
    #endif
    self.th_dl = th_dl # TODO: Validate with L
    n_batches = len(th_dl)

    self.P("\nTraining model:\n  {}\n    Training on {} observations with batch size {}.\n".format(
      self,
      n_batches * th_dl.batch_size,
      self.batch_size,
      ))
    patience = 0
    fails = 0
    self.training_status['best_score'] = -np.inf if self.score_mode == 'max' else np.inf
    self.log.P("Training for {} epochs with bs={} and {} steps per epoch - total {} training examples".format(
      epochs, self.batch_size, len(th_dl),  self.batch_size*len(th_dl))
    )
    for epoch in range(1, epochs + 1):
      self.P("Starting epoch {:03d} / {:03d}".format(epoch, epochs))
      epoch_errors = []
      self.in_training = epoch
      self.current_epoch = epoch
      self.start_timer('epoch_time')
      ### TODO: REDO
      enum = enumerate(th_dl)

      mean_time_per_batch = 0
      for i in range(len(th_dl)):
        start_time = time()
        self.start_timer('get_data')
        batch_iter, batch_data = next(enum)
        self.end_timer('get_data')

        self.start_timer('batch_train')
        err = self.train_on_batch(self.model, self.optimizer, self.losses, batch_data, y_index=y_index, batch_index=batch_index)
        epoch_errors.append(err)
        if len(err.shape) > 0:
          self.average_loss = np.mean(np.concatenate(epoch_errors))
        else:
          self.average_loss = np.mean(epoch_errors)
        #endif
        _tmp = batch_iter * mean_time_per_batch
        _tmp += time()-start_time
        _tmp /= (batch_iter + 1)
        mean_time_per_batch = _tmp

        str_log = "Training epoch {:03d} - {:.1f}% (e:{:.1f}s r:{:.1f}s) - avg loss: {:.3f},  Patience {:02d}/{:02d},  Fails {:02d}/{:02d}".format(
          epoch,
          (batch_iter + 1) / n_batches * 100,
          mean_time_per_batch * (batch_iter+1),
          mean_time_per_batch * (n_batches - batch_iter - 1),
          self.average_loss,
          patience, self.max_patience,
          fails, self.max_fails,
        )

        if i == len(th_dl) - 1:
          self.P(str_log)
        else:
          if self.log.is_main_thread:
            Pr(str_log)
        #endif

        self.end_timer('batch_train')
      # end epoch
      self.end_timer('epoch_time', skip_first_timing=False)
      # self.log.show_timers()

      dct_score = self.evaluate(owner=self, epoch=epoch, verbose=verbose)
      # dct_score = {**dct_score_train, **dct_score_eval}
      dct_score['ep'] = epoch
      self.epochs_data[epoch] = dct_score
      score = dct_score[self.score_key]

      if self.is_new_best(dct_score):
        self.P("Found new best score {:.5f} better than {:.5f} at epoch {}.".format(
            score, self.training_status['best_score'], epoch), color='g')

        if self._evaluate_train_at_new_best:
          dct_train_score = self.evaluate_train(owner=self, epoch=epoch, verbose=verbose)
          self.epochs_data[epoch] = {**self.epochs_data[epoch], **dct_train_score}

        self.save_best_model_and_track(epoch, score)
        fails = 0
        patience = 0
      else:
        patience += 1
        if patience > 0:
          fails += 1
        self.P("Finished epoch {:03d}, loss: {:.4f}. Current score {:.5f} < {:.5f}. Patience {:02d}/{:02d},  Fails {:02d}/{:02d}".format(
            epoch, self.average_loss, score, self.training_status['best_score'], patience, self.max_patience, fails, self.max_fails))
        self.P('')
        if patience >= self.max_patience:
          self.P("Patience reached {}/{} - reloading from epoch {} and reducting lr".format(
              patience, self.max_patience, self.training_status['best_epoch']), color='y')
          self.reload_best()
          self.reduce_lr()
          patience = -self.cooldown
        if fails >= self.max_fails:
          self.P("\nMax fails {}/{} reached!".format(fails, self.max_fails))
          break
        #endif
      #endif
    #endfor
    self.restore_best_and_cleanup()

    if self._evaluate_train_best_model:
      dct_train_score = self.evaluate_train(owner=self, epoch=self.training_status['best_epoch'], verbose=verbose)
      self.epochs_data[self.training_status['best_epoch']] = {
        **self.epochs_data[self.training_status['best_epoch']],
        **dct_train_score
      }
    #endif

    self.in_training = None
    return self

  def save_best_model_and_track(self, epoch, score, cleanup=True, verbose=True):
    if not os.path.isdir(self.base_folder):
      os.mkdir(self.base_folder)

    weights_fn = "{}_weights_e{:03}_F{:.2f}.th".format(
            self.model_name, epoch, score
    )

    best_fn = os.path.join(self.base_folder, weights_fn)
    self.save_model(best_fn)
    if verbose:
      self.P("  Saved: '{}'".format(best_fn))
      self.P("  Saved: '{}'".format(best_fn + '.optim'))
    last_best =  self.training_status.get('best_file')
    if last_best != None:
      self._files_for_removal.append(last_best)
      self._files_for_removal.append(last_best + '.optim')
    self.training_status['best_file'] = best_fn
    self.training_status['best_epoch'] = epoch
    self.training_status['best_score'] = score
    if cleanup:
      self.cleanup_files()
    return    
  
  
  def save_model(self, fn):
    th.save(self.model.state_dict(), fn)
    th.save(self.optimizer.state_dict(), fn + '.optim')
    return
   
  def load_model(self, fn):
    self.model.load_state_dict(th.load(fn))
    self.optimizer.load_state_dict(th.load(fn + '.optim'))
    return
    
  
  def reload_best(self, verbose=True):
    self.load_model(self.training_status['best_file'])
    self.reload_init(self.epochs_data[self.training_status['best_epoch']])
    if verbose:
      self.P("  Reloaded '{}' data: {}".format(
          self.training_status['best_file'], 
          dict(self.epochs_data[self.training_status['best_epoch']])))
    return
  
  
  def reduce_lr(self, verbose=True):
    self.lr_decay_iters += 1
    factor = self.lr_decay ** self.lr_decay_iters
    for i, param_group in enumerate(self.optimizer.param_groups):
      lr_old = param_group['lr'] 
      param_group['lr'] = param_group['lr'] * factor
      lr_new = param_group['lr']
    if verbose:
      self.P("  Reduced lr from {:.1e} to {:.1e}".format(lr_old, lr_new))
    return
    


  def cleanup_files(self):
    atmps = 0
    while atmps < 10 and len(self._files_for_removal) > 0:   
      removed = []
      for fn in self._files_for_removal:
        if os.path.isfile(fn):
          try:
            os.remove(fn)
            removed.append(fn)
            self.P("  Removed '{}'".format(fn))
          except:
            pass
        else:
          removed.append(fn)            
      self._files_for_removal = [x for x in self._files_for_removal if x not in removed]
    return
  
  
  def restore_best_and_cleanup(self):
    self.reload_best()
    if self.score_eval_func(self.min_score_thr, self.training_status['best_score']) != self.training_status['best_score']:
      self.P("Best score {:.4f} did not pass minimal threshold of {} - model file will be deleted".format(
          self.training_status['best_score'], self.min_score_thr))
      self._files_for_removal.append(self.training_status['best_file'])
    else:
      self.P("Best score {:.4f} passed minimal threshold of {}".format(
          self.training_status['best_score'], self.min_score_thr))
    self._files_for_removal.append(self.training_status['best_file'] + '.optim')
    self.cleanup_files()
    return
  

if __name__ == '__main__':
  #############################################################################
  #############################################################################
  #############################################################################
  ############################## EXAMPLE ######################################
  #############################################################################
  #############################################################################
  #############################################################################

  import torchvision as tv
  
  from naeural_core import Logger
  from naeural_core.local_libraries.nn.th.layers import InputPlaceholder
  from naeural_core.local_libraries.nn.utils import conv_output_shape

  class SimpleModel(th.nn.Module):
    def __init__(self, 
                 input_shape=(1, 28, 28), 
                 convs=[(8, 3, 2), (32, 3, 2), (64, 3, 2)], 
                 dense=32,
                 **kwargs):
      super().__init__()
      assert len(input_shape) == 3
      self.layers = []
      self.add_layer('input', InputPlaceholder(input_shape))
      input_size = input_shape[0]
      prev_h, prev_w = input_shape[1:]
      for i, (f, k, s) in enumerate(convs):
        cnv = th.nn.Conv2d(
          in_channels=input_size, 
          out_channels=f, 
          kernel_size=k,
          stride=s
          )
        input_size = f
        new_h, new_w = conv_output_shape(
          h_w=(prev_h, prev_w),
          kernel_size=k,
          stride=s,
          )
        self.add_layer('conv_{}'.format(i+1), cnv)
        self.add_layer('reul_{}'.format(i+1), th.nn.ReLU6())
        prev_h = new_h
        prev_w = new_w

      self.add_layer('flatten', th.nn.Flatten())
      input_size = new_h * new_w * f

      # self.add_layer('pre_read', th.nn.Linear(input_size, dense))
      # self.add_layer('pre_read_relu', th.nn.ReLU6())
      # input_size = dense

      self.add_layer('drop',th.nn.Dropout(0.5))
      self.add_layer('readout', th.nn.Linear(input_size, 10))
      return
    
    def forward(self, inputs):
      x = inputs
      for layer in self.layers:
        x = layer(x)
      return x
    
    def predict(self, inputs):
      # we dont have softmax in output above so we make a nice "predict" function
      self.eval()
      with th.no_grad():
        out = self(inputs)
        sm_out = th.nn.Softmax(out)
      self.train()
      return sm_out
    

    def add_layer(self, name, module):
      self.add_module(name, module)      
      self.layers.append(getattr(self, name))
      return
  

  l = Logger('THTST', base_folder='.', app_folder='_local_cache', TF_KERAS=False) 
  
  data_path = l.get_data_folder()
  
  mnist_train = tv.datasets.MNIST(root=data_path, download=True, train=True)
  mnist_test = tv.datasets.MNIST(root=data_path, download=True, train=False)
  
  x_train = mnist_train.data.numpy()
  y_train = mnist_train.targets.numpy()
  
  x_dev = mnist_test.data.numpy()
  y_dev = mnist_test.targets.numpy()

  x_train = (x_train.reshape((-1, 1, 28, 28)) / 255.).astype('float32')
  x_dev = (x_dev.reshape((-1, 1, 28, 28)) / 255.).astype('float32')
  y_train = y_train.astype('int64')
  y_dev =y_dev.astype('int64')
  
  l.gpu_info(True)
  
  model = SimpleModel()
  ###TODO: REMOVE DCT
  #model_loss = {'cross_entropy': th.nn.CrossEntropyLoss()}
  model_loss = th.nn.CrossEntropyLoss()
  trainer = ModelTrainer(
    log=l, 
    model=model, 
    losses=model_loss,
    validation_data=(x_dev, y_dev),
    batch_size=256
    )
  trainer.fit(x_train, y_train)
  
  
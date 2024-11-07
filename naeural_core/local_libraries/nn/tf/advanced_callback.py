import tensorflow as tf
import os
import numpy as np

class AdvancedCallback(tf.keras.callbacks.Callback):
  """
      'Swiss Army Callback Knife'
  """

  def __init__(self,
               log,
               model_name=None,
               lr_monitor='val_loss',
               lr_mode='min',
               lr_factor=0.1,
               lr_patience=3,
               lr_min_delta=1e-4,
               lr_min_lr=1e-7,
               save_monitor='val_loss',
               save_mode='min',
               save_weights=False,
               save_last=2,
               metric_calc=None,
               calc_MAPE_TS=False,
               calc_MAD_TS=None,
               allow_neg_test=False,
               log_validate_each=1,
               nr_series_debug=None,
               y_trans=None,
               y_scale=None,
               DEBUG=False,
               **kwargs):
    """
    model_name : if None the model name will be inferred from tf.keras.models.Model.name
    lr_* : parameters for learning rate monitor

    save_monitor : what should be measured
    save_mode : how the checkpoint monitor should be measured
    save_last : the monitor will only keep best 'save_last' files

    metric_calc: function with signature func(model, data1, data2) -> dict
                 with "metric":value pairs

    y_scale, y_tras: transform y if needed

    log_validate_each :  will log chosen validation metrics every x epochs (default 1)

    calc_MAPE_TS: (default None) used in TS prediction calcs MAPE on last
                  time-step on validation data
    calc_MAD_TS: (default None) value x used in TS prediction to calculate
                  "period average deviation" for last x steps period
    *****

    `log` :  obiectul Logger din care se apeleaza - este automat dat de Logger - dont bother
    `model_name=None` : optional numele modelului urmarit - va lua numele Keras if None
    `lr_monitor='val_loss'` : ce monitorizam pentru scaderea ratei de invatare
    `lr_mode='min'` : cum monitorizam - min inseamna ca urmarim minimizarea lui lr_monitor
    `lr_factor=0.1` : cu ce scalam rata daca nu se respecta lr_mode pentru lr_patience pasi
    `lr_patience=3` : cati pasi avem 'rabdare' cu rata curenta
    `lr_min_delta=1e-4` : care este minimum de scadere/crestere a lr_monitor
    `lr_min_lr=1e-7`  : pana unde scadem de mult rata
    `save_monitor='val_loss'` : ce monitorizam ca sa salvam cele mai bune epoci
    `save_mode='min'` : cum monitorizam ...
    `save_last=2` : top epoci salvate
    `metric_calc=None` :  functie cu semnatura f(model,x_val,y_val)->dict(metric:val)
    `calc_MAPE_TS=False` : calcularea val_MAPE pe ultimul pas al series - doar pentru TS
    `calc_MAD_TS=None` : calculeaza val_MAD pe ultimii `calc_MAD_TS` pasi din TS
    `log_validate_each=1` : printeaza date validare la fiecare nr de epoci
    `y_trans=None` : variabila de translatare a output-ului de validare (daca a fost transf inainte)
    `y_scale=None` : variabile de scalare --//----
    `DEBUG=False` : True ca sa arate informatii extra la fiecare epoca
    `allow_neg_test=False` : default va face pozitive toate valorile pentru teste
    `save_weights=False` : true ca sa salveze doar weights

    """
    super().__init__(**kwargs)
    self.DEBUG = DEBUG
    self.K = tf.keras.backend

    if lr_factor >= 1.0:
      raise ValueError('ReduceLROnPlateau ' 'does not support a factor >= 1.0.')
    self.log = log
    self.__version__ = '1.1.7'
    self.model_name = model_name
    self.name = "ACb[{}]".format(self.model_name)
    self.lr_monitor = lr_monitor
    self.lr_factor = lr_factor
    self.lr_patience = lr_patience
    self.lr_min_delta = lr_min_delta
    self.lr_mode = lr_mode
    self.lr_min_lr = lr_min_lr
    self.log_validate_each = log_validate_each
    self.y_scale = y_scale
    self.y_trans = y_trans
    self.history = {}
    self.recorded_epochs = 0
    self.lr_monitor_op = None
    self.validation_funcs = {}
    self.save_monitor = save_monitor
    self.save_mode = save_mode
    self.save_last = save_last
    self.save_history = []
    self.allow_neg_test=allow_neg_test
    self.nr_series_debug = nr_series_debug
    self.__x_val = None
    self.__y_val = None
    self.save_weights = save_weights

    if metric_calc:
      func_name = metric_calc.__name__
      self.validation_funcs[func_name] = metric_calc

    if calc_MAPE_TS:
      func_name = "MAPE LAST STEP"
      self.validation_funcs[func_name] = self.calc_MAPE_TS

    if calc_MAD_TS:
      self.MAD_last_steps = calc_MAD_TS
      func_name = "MAD LAST {} STEPS".format(calc_MAD_TS)
      self.validation_funcs[func_name] = self.calc_MAD_TS

    self._reset()
    self.PrintSelf()
    return

  def PrintSelf(self):
    self.P(" v{}: {}".format(self.__version__, self.log.get_object_params(self)))

  def P(self, s, t=False, prefix=''):
    return self.log.P("{}{}: {}".format(
        prefix, self.name,s),show_time=t)


  def D(self, s, t=False, prefix=''):
    _r = -1
    if self.DEBUG:
      _r = self.log.P("{}[DEBUG] {}: {}".format(
                      prefix, self.name,s),show_time=t)
    return _r


  def _save_model(self, config_json=None):
    del_hist = max(1, self.save_last)-1
    for fn in self.save_history[:-del_hist]:
      if os.path.isfile(fn):
        try:
          os.remove(fn)
        except:
          pass
    label = "{}_ep{:03d}_{}_{:.3f}".format(self.model_name,
                                           self.recorded_epochs,
                                           self.save_monitor,
                                           self.save_best)

    self.last_save = self.log.save_keras_model(model=self.model,
                                               label=label,
                                               use_prefix=True,
                                               cfg=config_json,
                                               DEBUG=self.DEBUG,
                                               only_weights=self.save_weights)

    self.save_history.append(self.last_save)
    return


  def _reset(self):
    self.P("Resetting AdvancedCallback...")
    self.recorded_epochs = 0
    self.history = {}
    self.train_sessions = []
    self.last_save = None
    if self.lr_mode not in ['min', 'max']:
      raise ValueError("Unknown lr monitoring mode")
    if self.save_mode not in ['min', 'max']:
      raise ValueError("Unknown save monitoring mode")

    if self.lr_mode == 'min':
      self.lr_monitor_op = lambda a, b: np.less(a, b - self.lr_min_delta)
      self.lr_best = np.Inf
    else:
      self.lr_monitor_op = lambda a, b: np.greater(a, b + self.lr_min_delta)
      self.lr_best = -np.Inf

    if self.save_mode == 'min':
      self.save_monitor_op = lambda a, b: np.less(a, b - self.lr_min_delta)
      self.save_best = np.Inf
    else:
      self.save_monitor_op = lambda a, b: np.greater(a, b + self.lr_min_delta)
      self.save_best = -np.Inf

    self.cooldown_counter = 0
    self.wait = 0
    self.n_vals = len(self.validation_funcs)
    return


  def _lr_monitor(self, epoch, logs):
    if (self.lr_monitor is None) or (self.lr_monitor == ''):
      # we dont need lr monitoring
      return
    current = logs.get(self.lr_monitor)
    if current is None:
      raise ValueError("Learning rate monitoring metric {} missing!".format(
          self.lr_monitor))
    else:
      if self.lr_monitor_op(current, self.lr_best):
        _outcome = 'decreased'
        self.lr_best = current
        self.wait = 0
      else:
        _outcome = 'increased'
        self.wait += 1
        if self.wait >= self.lr_patience:
          old_lr = float(self.K.get_value(self.model.optimizer.lr))
          if old_lr > self.lr_min_lr:
            new_lr = old_lr * self.lr_factor
            new_lr = max(new_lr, self.lr_min_lr)
            self.K.set_value(self.model.optimizer.lr, new_lr)
            self.P("Epoch {}({}): {} {} from {:.4f} to {:.4f} ".format(
                              epoch + 1, self.recorded_epochs,
                              self.lr_monitor, _outcome,
                              self.lr_best, current,), prefix='\n')
            self.P('             => reducing lr from {:.7f} to {:.7f}'.format(
                          old_lr, new_lr))
            self.wait = 0
            self.lr_best = current # reset the best
    return


  def _checkpoint_monitor(self, epoch, logs):
    if (self.save_monitor is None) or (self.save_monitor == ''):
      # no checkpoint
      return
    current = logs.get(self.save_monitor)
    if current is None:
      raise ValueError("Checkpoint save monitoring metric {} missing!".format(
          self.save_monitor))
    else:
      if self.save_best > current:
        _outcome = 'decreased'
      else:
        _outcome = 'increased (NO SAVING)'
      self.D("Epoch {}({}): {} {} from {:.4f} to {:.4f} ".format(
                         epoch + 1, self.recorded_epochs,
                         self.save_monitor, _outcome,
                         self.save_best, current,), prefix='\n')
      if self.save_monitor_op(current, self.save_best):
        self.save_best = current
        self._save_model()
    return


  def on_train_begin(self, logs=None):
    if self.model_name is None:
      self.model_name = self.model.name
    self.nr_inputs = len(self.model.inputs)
    self.nr_outputs = len(self.model.outputs)
    self.output_names = [x.name.split('/')[0] for x in self.model.outputs]
    self.name = "ACb[{}]".format(self.model_name)
    self.train_sessions.append(
          {self.name : 0}
        )
    c_lr = self.K.get_value(self.model.optimizer.lr)
    prefix = ""
    if self.recorded_epochs > 0:
      prefix = "(Re)"
    self.P("{}Starting train with lr={:.7f}. So far trained {} epochs".format(
        prefix, c_lr, self.recorded_epochs))
    return


  def on_epoch_end(self, epoch, logs=None):
    if (self.n_vals > 0)  and (self.validation_data) and (self.__x_val == None):
      # setup validation only if required by validation functions
      # so check if we have functions, input data and if we did not setup already
      if ((type(self.validation_data) == tuple) and (len(self.validation_data) == 2)
           and self.nr_inputs > 1):
        # now we have to re-arrange the validation data
        val_data = [x for x in self.validation_data[0]]
        if self.validation_data[1] is not None:
          val_data += [x for x in self.validation_data[1]]
        self.validation_data = val_data
      self.__x_val = self.validation_data[:self.nr_inputs]
      self.__y_val = self.validation_data[self.nr_inputs:(self.nr_inputs+self.nr_outputs)]
      if len(self.__x_val) == 1:
        self.__x_val = self.__x_val[0]
      if len(self.__y_val) == 1:
        self.__y_val = self.__y_val[0]
      self.P("")
      self.P("Validation at ep {} on data len={}".format(self.recorded_epochs,
                                                         len(self.validation_data)))
      yv = self.__y_val
      xv = self.__x_val
      if type(yv) in [np.ndarray, list, tuple]:
        yvs = yv.shape if type(yv) == np.ndarray else [y.shape for y in yv
                                                      if type(y) == np.ndarray]
      if type(xv) in [np.ndarray, list, tuple]:
        xvs = xv.shape if type(xv) == np.ndarray else [x.shape for x in xv
                                                      if type(x) == np.ndarray]
      self.P(" y_val={}".format(yvs))
      self.P(" x_val={}".format(xvs))

    self.train_sessions[-1][self.name] += 1
    self.recorded_epochs += 1
    logs = logs or {}
    # this will update global log
    logs['lr'] = self.K.get_value(self.model.optimizer.lr)

    if (self.recorded_epochs % self.log_validate_each) == 0:
      self.P("")
      self.P("Validation tests on model '{}' at epoch {}".format(
          self.model_name, self.recorded_epochs))
    for func_name in self.validation_funcs:
      func_val = self.validation_funcs[func_name]
      assert self.__y_val is not None, "Missing validation data for validation func '{}'!"\
      " This is probably due to the version of keras/tensorflow that removed the "\
      "validation_data variables setup. Please assign validation_data manually".format(
                          func_name)
      dict_res = func_val(self.model, self.__x_val, self.__y_val)
      for res_key in dict_res:
        # this will update global log
        logs[res_key] = dict_res[res_key]

    for log_key in logs:
      key_hist = self.history.get(log_key) or []
      key_hist.append(logs[log_key])
      self.history[log_key] = key_hist

    self._lr_monitor(epoch=epoch, logs=logs)
    self._checkpoint_monitor(epoch=epoch, logs=logs)
    return


  def calc_MAPE_TS(self, model, x_val, y_val, stand_alone=False):
    y_pred = model.predict(x_val)
    if type(y_val) == list:
      y_pred = np.concatenate(y_pred, axis=-1)
      y_val = np.concatenate(y_val, axis=-1)
    if self.y_scale:
      self.P("  Scaling y data by {}".format(self.y_scale))
      y_pred = y_pred * self.y_scale
      y_val = y_val * self.y_scale
    if self.y_trans:
      self.P("  Translating y data by {}".format(self.y_trans))
      y_pred = y_pred + self.y_trans
      y_val = y_val + self.y_trans

    if not self.allow_neg_test:
      y_val = np.clip(y_val,self.K.epsilon(), None)
      y_pred = np.clip(y_pred, self.K.epsilon(), None)

    y_pred_last = y_pred[:,-1,:]
    y_val_last = y_val[:,-1,:]

    residual = y_pred_last - y_val_last

    if hasattr(model,"loss"):
      loss = model.loss
    else:
      loss = "MSE"

    loss_type = 'MSE'
    if isinstance(loss, list):
      loss = str(loss[0])
    if 'MAE' in loss.upper():
      loss_type = 'MAE'
      MAPE_loss = np.mean(np.abs(residual))
    elif 'LOG' in loss.upper():
      loss_type = 'LOGCOSH'
      MAPE_loss = np.mean(np.log(np.cosh(residual)))
    else:
      # MSE
      MAPE_loss = np.mean(residual ** 2)

    self.P("  Using {} for val_MAPE_loss calculation".format(loss_type))
    mape_series = np.abs(residual) / (
                  np.clip(np.abs(y_val_last), self.K.epsilon(), None))
    mape_series = np.clip(mape_series, 0, 2)

    if self.nr_series_debug:
      nr_series = self.nr_series_debug
    else:
      nr_series = min(10, mape_series.shape[0])

    mape = np.mean(mape_series)
    res_dict = {"val_MAPE" : mape, "val_MAPE_loss" : MAPE_loss}
    self.P("  Using {} for val_MAPE_loss={:.4f}".format(loss_type, MAPE_loss))
    if (self.recorded_epochs % self.log_validate_each) == 0:
      self.P(" Validation MAPE (last step) on {} outputs {}:".format(
          y_val.shape[-1], self.output_names))
      self.P("  y_val({} series): {}".format(nr_series,
                                            y_val[:nr_series,-1,:].ravel().round(2)))
      self.P("  y_hat({} series): {}".format(nr_series,
                                            y_pred[:nr_series,-1,:].ravel().round(2)))
      self.P("  MAPEs({} series): {}".format(nr_series,
                                            mape_series[:nr_series].ravel().round(2)))
      self.P("  Mean Average Percentual ERROR: {:.1f}%".format(mape*100))

    if not stand_alone:
      _return = res_dict
    else:
      _return = res_dict, mape_series

    return _return


  def calc_MAD_TS(self, model, x_val, y_val):
    assert self.MAD_last_steps > 0
    lstp = self.MAD_last_steps
    y_pred = model.predict(x_val)
    if type(y_val) == list:
      y_pred = np.concatenate(y_pred, axis=-1)
      y_val = np.concatenate(y_val, axis=-1)
    assert len(y_val.shape) == 3, "The time-series must be in (B,T,F) format!"

    sum_axis = 1
    if (lstp == 1) and (y_val.shape[-1] > 1):
      sum_axis = 2

    if self.y_scale:
      self.P("  Scaling y data by {}".format(self.y_scale))
      y_pred = y_pred * self.y_scale
      y_val = y_val * self.y_scale
    if self.y_trans:
      self.P("  Translating y data by {}".format(self.y_trans))
      y_pred = y_pred + self.y_trans
      y_val = y_val + self.y_trans

    if not self.allow_neg_test:
      y_val = np.clip(y_val,self.K.epsilon(), None)
      y_pred = np.clip(y_pred, self.K.epsilon(), None)

    yp = y_pred[:,-lstp:,:]
    yt = y_val[:,-lstp:,:]
    yp_s = yp.sum(axis=sum_axis)
    yt_s = yt.sum(axis=sum_axis)
    yp_s = np.clip(yp_s, self.K.epsilon(), None)
    np_mad = np.abs(yp_s-yt_s) / yp_s
    np_mad = np.clip(np_mad, 0, 2)
    mad = np_mad.mean()

    if self.nr_series_debug:
      nr_series = self.nr_series_debug
    else:
      nr_series = min(2, np_mad.shape[0])

    res_dict = {"val_MAD" : mad}
    if (self.recorded_epochs % self.log_validate_each) == 0:
      yvl = "".join(['{}'.format(x.ravel().round(2)) for x in y_val[:nr_series,-lstp:,:]])
      yhl = "".join(['{}'.format(x.ravel().round(2)) for x in y_pred[:nr_series,-lstp:,:].astype('float64')])
      self.P(" Validation MAD last {} steps on {} outputs {}:".format(
           self.MAD_last_steps, y_val.shape[-1], self.output_names,))
      self.P("  y_val({} series): {}".format(nr_series, yvl))
      self.P("  y_hat({} series): {}".format(nr_series, yhl))
      self.P("  MADs({} series):  {}".format(nr_series,
                                            np_mad[:nr_series].ravel().round(2)))
      self.P("  Mean Period({}) Average Deviation ERROR: {:.1f}%".format(lstp, mad*100))
    return res_dict
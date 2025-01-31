import numpy as np
from collections import deque

from naeural_core.utils.sensors.virtual_time_series import VirtualTimeSeries

class BasicSeriesModel:
  """
  Basic timeseries prediction with constant bias (no variable step-id)
  """  
  def __init__(self, series_min=100, train_hist=None, train_periods=None):
    self.reset_params(
      train_hist=train_hist,
      series_min=series_min,
      train_periods=train_periods
    )
    return
  
  def reset_params(self, series_min=50, train_hist=None, train_periods=None):
    MIN_STEPS = 8
    assert series_min > MIN_STEPS, f"Minimal series config must be {MIN_STEPS + 1} steps. Config set to {series_min}"
    if train_hist is None:
      train_hist = int(series_min / (MIN_STEPS / 2))
    if train_periods is None:
      train_periods = series_min - (train_hist + 1)
    assert train_periods <= (series_min - train_hist)
    assert train_hist < train_periods
    self._min_series = series_min    
    self._train_steps = train_periods
    self._train_hist = train_hist
    return
  
  def fit(self, vect):
    # convert & add bias
    np_x = np.array(vect).reshape(-1)
    _min = np_x.min()
    _max = np_x.max()
    self._scale = _max - _min
    self._loc = _min
    np_x = (np_x - self._loc) / self._scale
    self._x_series = np_x
    l = np_x.shape[0]
    assert l >= self._min_series, "Minimal time-series len must be {}. Received {}".format(
      self._min_series, l)
    
    x_data = []
    y_data = []
    ts = self._train_steps
    th = self._train_hist
    for i in range(ts):
      start = -(th + i + 1)
      end = -(i+1)
      x_vect = np_x[start:end]
      y_val = np_x[end]
      x_data.append(x_vect)
      y_data.append(y_val)
    np_Xnb = np.array(x_data)
    np_b = np.ones((len(x_data), 1))
    np_X = np.concatenate((np_b, np_Xnb), axis=1)
    np_y = np.array(y_data).reshape((-1,1))
    self.theta = np.linalg.pinv(np_X.T.dot(np_X)).dot(np_X.T).dot(np_y)
    self.np_X = np_X
    self.np_y = np_y
    return
  
  def predict(self, nr_steps):
    x_data = deque(maxlen=self._train_hist)
    x_data.extend(self._x_series[-self._train_hist:])
    y_preds = []
    for i in range(nr_steps):
      np_x = np.array([1] + list(x_data))
      y_pred = np_x.dot(self.theta.ravel())
      y_preds.append(y_pred * self._scale + self._loc)
      x_data.append(y_pred.round(2))
    return y_preds
      
                       
    
if __name__ == '__main__':
  sensor = VirtualTimeSeries(n_steps=30, test_size=0.3, no_random=6)
  import matplotlib.pyplot as plt
  import seaborn as sns
  sns.set()
  
  np.set_printoptions(linewidth=200)
  np.set_printoptions(precision=1)
  
  train = sensor.get_train_data()
  xtrn = range(train.shape[-1])
  test = sensor._data_test
  xtst = range(train.shape[-1], train.shape[-1] + test.shape[-1])
  plt.figure(figsize=(26,8))
  plt.plot(xtrn, train.ravel(), c='black')
  plt.plot(xtst, test.ravel(), c='red')
  
  model = BasicSeriesModel(series_min=train.shape[-1])
  model.fit(train)
  yh = model.predict(nr_steps=test.ravel().shape[0])
  plt.plot(xtst, yh, c='green')
  
  
  plt.title(str(sensor._random_seed))
        
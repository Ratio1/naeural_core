import numpy as np

from naeural_core.utils.sensors.base import AbstractSensor
from naeural_core.local_libraries.ts.artificial_data import generate_artificial_series


_SEED_HARD = 1785

_SEED_SIMPLE =  4318


class VirtualTimeSeries(AbstractSensor):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    return
  
  
  def _setup_connection(self, 
                        n_series=1,
                        n_steps=400, 
                        freq='D',
                        test_size=0.1,
                        max_value=30,
                        random_peaks=False,
                        encode_date_time=_SEED_SIMPLE,
                        no_random=None,
                        noise_size=10,
                        **kwargs,
                        ):
    if no_random is not None:
      self._random_seed = no_random
      np.random.seed(self._random_seed)
      
    else:
      self._random_seed= np.random.randint(1, 9999)
      np.random.seed(self._random_seed)
      
    dct_data = generate_artificial_series(
      n_series=n_series, 
      n_steps=n_steps, 
      freq=freq, 
      max_sales=max_value,
      noise_size=noise_size,
      random_peaks=random_peaks,
      )
    
    np_series = dct_data['SIGNAL']
    n_test = int(np_series.shape[-1] * test_size)    
    self._covar = dct_data['COVAR']
    self._periods = dct_data['PERIODS']
    self._data_train = np_series[:,:-n_test]
    self._data_test = np_series[:,-n_test:]
    self._pos = 0
    return
  
  
  def _get_single_observation(self):
    data = self._data_test[:, self._pos]
    self._pos += 1
    if self._pos >= self._data_test.shape[1]:
      self._pos = 0
    return data
  
    
  def _has_training_data(self):
    return True
    
  
  def _get_training_data(self):
    return self._data_train
  
  

if __name__ == '__main__':
  
  import matplotlib.pyplot as plt

  data_type = 'D'
  n_steps = 400 if data_type == 'D' else 1500
  n_series = 1 
  max_value = 30
  sensor = VirtualTimeSeries( 
    no_random=608, #   # Tests: 3306, 608, 5679, 7981
    n_steps=n_steps,
    n_series=n_series,
    freq=data_type,
    random_peaks=True,
    max_value=max_value,
    test_size=0.5,
    ) 
  
  
  
  train = sensor.get_train_data()
  xtrn = range(train.shape[-1])
  alert = train.max() * 1.2

  test = sensor._data_test
  xtst = range(train.shape[-1], train.shape[-1] + test.shape[-1])
  plt.figure(figsize=(26,8))
  plt.plot(xtrn, train.ravel(), 'D-', c='blue', markersize=12)
  plt.plot(xtst, test.ravel(), 'D-', c='green', markersize=12)
  # plt.bar(xtrn, train.ravel(), color='blue')
  # plt.bar(xtst, test.ravel(), color='green')
  plt.hlines(y=alert, xmin=train.shape[-1], xmax=n_steps, color='red')
  plt.title(str(sensor._random_seed))  
  plt.show()
  print(sensor._random_seed)
  
    
  
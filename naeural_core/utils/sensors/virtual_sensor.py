from sklearn.datasets import make_blobs
from sklearn.model_selection import train_test_split
from naeural_core.utils.sensors.base import AbstractSensor

class VirtualSensor(AbstractSensor):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    return
  
  def _setup_connection(self, 
                        n_max_samples=1000, 
                        n_feats=2, 
                        centers=1, 
                        test_size=0.5,
                        no_random=False,
                        **kwargs,
                        ):
    random_state = 1234 if no_random else None
    data, centers = make_blobs(
      n_samples=n_max_samples,
      n_features=n_feats,
      centers=centers,
      return_centers=False,
      random_state=random_state,
      center_box=(-3,3)
      )
    x_train, x_test = train_test_split(data, test_size=test_size, random_state=random_state)
    self._data_train = x_train
    self._data_feed = x_test
    self._datastream_len = x_test.shape[0]
    return
  
  def _get_single_observation(self):
    data = self._data_feed[self._pos]
    self._pos += 1
    if self._pos >= self._datastream_len:
      self._pos = 0
    return data
  
    
  def _has_training_data(self):
    return True
    
  def _get_training_data(self):
    return self._data_train
  
  
    
  
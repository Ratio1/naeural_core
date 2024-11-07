import numpy as np
import abc
from naeural_core.local_libraries.nn.th.training.callbacks.base import TrainingCallbacks
from naeural_core.local_libraries.nn.th.training.callbacks.mixins import _ClassificationMetricsMixin

class ClassificationTrainingCallbacks(TrainingCallbacks, _ClassificationMetricsMixin, abc.ABC):
  def __init__(self, **kwargs):
    super(ClassificationTrainingCallbacks, self).__init__(**kwargs)
    return

  def get_class_names(self, dataset_info: dict = None, **kwargs) -> list[str]:
    """
    Get the class names from the dataset info
    Parameters
    ----------
    dataset_info : dict
      Dataset info dictionary
    kwargs : dict
      Additional keyword arguments

    Returns
    -------
    list[str]
      List of class names
    """
    return list(dataset_info['class_to_id'].keys())


  def _evaluate_callback(self, epoch: int, dataset_info: dict, y: np.ndarray, y_hat: np.ndarray, idx: np.ndarray, key:str = 'dev') -> dict:
    classes = self.get_class_names(dataset_info=dataset_info)
    dct_global_metrics = self.basic_metrics(y=y, y_hat=y_hat, classes=classes, key=key)


    new_best = self._owner.is_new_best(dct_global_metrics)
    if new_best or epoch % 10 == 0 or key == 'train': # TODO: Change key == 'train'
      dct_advanced_metrics = self.advanced_metrics(
        y=y, y_hat=y_hat, idx=idx, dataset_info=dataset_info, classes=classes, key=key
      )
      for k, dct_metrics in dct_advanced_metrics.items():
        self.P(".......... Category={} ..........".format(k))
        self.log_metrics(dct_metrics=dct_metrics, classes=classes, log_cm=new_best)
      #endfor
    #endfor

    self.log_metrics(dct_metrics=dct_global_metrics, classes=classes, log_cm=new_best)

    keys = list(dct_global_metrics.keys())
    lst_cm_keys = []
    for k in keys:
      if isinstance(dct_global_metrics[k], np.ndarray):
        lst_cm_keys.append(k)
    #endfor

    for k in lst_cm_keys:
      dct_global_metrics.pop(k)
    # endfor
    return dct_global_metrics

  def _test_callback(self, dataset_info: dict, y: np.ndarray, y_hat: np.ndarray, idx: np.ndarray) -> dict:
    classes = self.get_class_names(dataset_info=dataset_info)
    dct_metrics = self.basic_metrics(y=y, y_hat=y_hat, classes=classes, key='test')
    dct_advanced_metrics = self.advanced_metrics(
      y=y, y_hat=y_hat, idx=idx, dataset_info=dataset_info, classes=classes, key='test'
    )
    for k, subset_dct_metrics in dct_advanced_metrics.items():
      self.P(".......... Category={} ..........".format(k))
      self.log_metrics(dct_metrics=subset_dct_metrics, classes=classes, log_cm=True)
    #endfor
    self.log_metrics(dct_metrics=dct_metrics, classes=classes, log_cm=True)
    return dct_metrics

  def _dev_callback(self, dataset_info: dict, y: np.ndarray, y_hat: np.ndarray, idx: np.ndarray) -> dict:
    classes = self.get_class_names(dataset_info=dataset_info)
    dct_metrics = self.basic_metrics(y=y, y_hat=y_hat, classes=classes, key='dev')
    dct_advanced_metrics = self.advanced_metrics(
      y=y, y_hat=y_hat, idx=idx, dataset_info=dataset_info, classes=classes, key='dev'
    )
    for k, subset_dct_metrics in dct_advanced_metrics.items():
      self.P(".......... Category={} ..........".format(k))
      self.log_metrics(dct_metrics=subset_dct_metrics, classes=classes, log_cm=True)
    #endfor
    self.log_metrics(dct_metrics=dct_metrics, classes=classes, log_cm=True)
    return dct_metrics

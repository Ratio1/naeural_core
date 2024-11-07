from typing import List
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, recall_score, precision_score
import numpy as np
import torch as th

class _ClassificationMetricsMixin(object):

  def __init__(self):
    super(_ClassificationMetricsMixin, self).__init__()
    return

  def get_classes_accuracies(self, cm, classes=None, key='dev'):
    n_classes = max(cm.shape)
    if classes is None:
      classes = range(n_classes)

    cls_acc = [cm[i, i] for i in range(n_classes)]
    normalized_accuracies = cls_acc / np.sum(cm, axis=1)
    acc_dict = {f'{key}_acc_{classes[i]}': round(normalized_accuracies[i], 2) for i in range(n_classes)}
    return acc_dict

  def basic_metrics(self, y, y_hat, classes, key):
    acc = accuracy_score(y, y_hat)
    cm_labels = list(range(len(classes)))
    cm = confusion_matrix(y, y_hat, labels=cm_labels)
    f1 = f1_score(y, y_hat, average='macro')
    rec = recall_score(y, y_hat, average='macro')
    prec = precision_score(y, y_hat, average='macro')
    classes_accuracies = self.get_classes_accuracies(cm, classes, key=key)

    return {
      '{}_acc'.format(key): acc,
      '{}_cm'.format(key): cm,
      '{}_f1'.format(key) : f1,
      '{}_rec'.format(key) : rec,
      '{}_prec'.format(key) : prec,
      **classes_accuracies
    }

  def advanced_metrics(self, y, y_hat, idx, dataset_info, classes=None, key='dev'):
    dct_metrics_per_categ = {}
    classes = classes if classes is not None else self.get_class_names(dataset_info=dataset_info)

    try:
      dct_level_categs = dataset_info['categ_to_id_per_lvl'][self._level_analysis]
      idpaths = dataset_info['path_to_idpath'][idx][:, self._level_analysis]
    except:
      return dct_metrics_per_categ

    for categ_name, cidx in dct_level_categs.items():
      crt_idx = np.where(idpaths == cidx)[0]
      if isinstance(y, (np.ndarray, th.Tensor)):
        y_crt = y[crt_idx]
        y_hat_crt = y_hat[crt_idx]
      else:
        y_crt = [_y[crt_idx] for _y in y]
        y_hat_crt = [_y[crt_idx] for _y in y_hat]
      #endif

      dct_metrics_per_categ['{}_{}'.format(key, categ_name)] = self.basic_metrics(
        y=y_crt, y_hat=y_hat_crt,
        classes=classes,
        key=key
      )
    #endfor

    return dct_metrics_per_categ

  def log_metrics(self, dct_metrics: dict,
                  classes: List[str],
                  log_cm: bool = True):

    keys = list(dct_metrics.keys())
    lst_cm_keys = []
    str_log = ""
    for k in keys:
      if not isinstance(dct_metrics[k], np.ndarray):
        str_log += "{}: {:.4f} / ".format(k, dct_metrics[k])
      else:
        lst_cm_keys.append(k)

    self.log.P(str_log)
    if log_cm:
      for k in lst_cm_keys:
        cm = dct_metrics[k]
        self.log.P("Printing confusion matrix key '{}'".format(k))
        self.log.log_confusion_matrix(cm, classes=classes)
      #endfor
    #endif
    return

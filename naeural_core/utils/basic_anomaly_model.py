import numpy as np

_BASE_ANOMALY_PRC = 0.02

__VER__ = '1.0.1.0'

class BasicAnomalyModel:
  def __init__(self, data_validation_callback=None, **kwargs):
    """
    simple multi-variate Gaussian anomaly detector
    """
    self.trained = False
    self.version = __VER__
    self.data_validation_callback = data_validation_callback # Used to validate if there is sufficient data to train
    return
  
  def _calc_eps_by_prc(self, prc=None, max_eps=0.02):
    """
    compute eps using cut-off percentage
    """
    if prc is None:
      prc = _BASE_ANOMALY_PRC
    pval = self._pdf(self._x_train)
    eps = np.quantile(pval, prc)
    return min(eps, max_eps)
  
  def _calc_simple_proba(self, pvals):
    n = (self._eps - pvals) / self._eps
    p = n * 0.50 + 0.50
    return np.maximum(p, 0)
    
  
  def _calc_eps(self, y_true):
    """
    given a labeled dataset (1 anomaly, 0 not anomaly) we compute the optimal thr 
    without residing on a pre-defined cut-off percentage
    """
    pval = self._pdf(self._train_data) #  we compute pdf value for all observations
    best_eps = 0
    best_F1 = 0
    
    stepsize = (max(pval) -min(pval))/1000
    eps_range = np.arange(pval.min(),pval.max(),stepsize)
    for eps in eps_range: 
      predictions = (pval < eps)[:,np.newaxis] # now we find optimal eps
      tp = np.sum(predictions[y_true==1]==1)   # given that we know which are the outliers
      fp = np.sum(predictions[y_true==0]==1)
      fn = np.sum(predictions[y_true==1]==0)
      
      # compute precision, recall and F1
      prec = tp/(tp+fp)
      rec = tp/(tp+fn)
      
      F1 = (2*prec*rec)/(prec+rec)
      
      if F1 > best_F1:
          best_F1 =F1
          best_eps = eps
      
    return best_eps, best_F1  
  
  @staticmethod
  def calc_pdf(x, loc=None, scale=None):
    if loc is None:
      loc = x.mean()
    if scale is None:
      scale = x.std()
    if len(x.shape) == 1:
      x = x.reshape(-1,1)
    n_feats = x.shape[1]
    if n_feats == 1:
      v = (1 / (scale  * np.sqrt(2 * np.pi))) * np.exp(-0.5 * ((x - loc)/scale)**2)
    else:
      k = n_feats
      sigma2 = np.diag(scale ** 2)
      Xs = x - loc.T
      s_det = np.linalg.det(sigma2)
      s_det_sqrt_inv = 1 / (s_det ** 0.5)
      t1 = 1/((2 * np.pi)**(k/2)) * s_det_sqrt_inv
      t2 = np.exp(-0.5* np.sum(Xs @ np.linalg.pinv(sigma2) * Xs,axis=1))
      v = t1 * t2
    return v  
  
  def _pdf(self, x):   
    """
    Calculates proba density func values given some data and assuming stats are
    already computed
    """
    res = self.calc_pdf(x, loc=self._mean, scale=self._std)
    return res
  
  def fit(self, x_train, y_train=None, prc=None):
    if self.data_validation_callback is not None:
      if not self.data_validation_callback(x_train, y_train):
        # Model will not train; insufficient data
        return
      #endif
    #endif

    if not isinstance(x_train, np.ndarray) and len(x_train.shape) != 2:
      raise ValueError("{} training data must be ndarray with shape [n_obs, n_features] - received {}".format(
        self.__class__.__name__, type(x_train)))
    self._n_feats = x_train.shape[1]
    self._n_obs = x_train.shape[0]
    self._mean = x_train.mean(axis=0)
    self._std = x_train.std(axis=0)  
    self._x_train = x_train
    if y_train is None:
      if prc is None:
        prc = _BASE_ANOMALY_PRC
      _eps = self._calc_eps_by_prc(prc=prc)
    else:
      _eps, _ = self._calc_eps(y_train)
    self._eps = _eps
    self.trained = True
    return
  
  def predict(self, x_test, proba=False):
    if not self.trained: ## TODO: maybe add maybe_predict?
      return []
    if not isinstance(x_test, np.ndarray) or x_test.shape[-1] != self._n_feats:
      raise ValueError("{} input for prediction must be of type np.ndarray [None, N_FEATS]".format(
        self.__class__.__name__))
    x_test = x_test.reshape((-1, self._n_feats))
    pval = self._pdf(x_test)
    if proba:
      return self._calc_simple_proba(pval)
    is_anom = pval <= self._eps
    return is_anom


if __name__ == '__main__':
  from naeural_core.utils.sensors.virtual_sensor import VirtualSensor
  from sklearn.covariance import EllipticEnvelope
  from naeural_core import Logger
  import matplotlib.pyplot as plt
  import seaborn as sns
  sns.set(style="ticks", context="talk")
  plt.style.use('dark_background')

  
  l = Logger('ANOM', base_folder='.', app_folder='_cache', TF_KERAS=False)

  
  for itr in range(1):
    sensor = VirtualSensor(n_max_samples=1000, centers=1)
    x_train = sensor.get_train_data()
    x_test = sensor._data_feed
    n_samples = x_test.shape[0]
    clf = EllipticEnvelope(contamination=0.02)
    clf.fit(x_train)
    
    yh_train1 = clf.predict(x_train) == -1
    yh_test1 = clf.predict(x_test) == -1
  
    model = BasicAnomalyModel()
    model.fit(x_train)
    _yh_train2 = model.predict(x_train, proba=True)
    yh_train2 = _yh_train2 >= 0.5
    _yh_test2 = model.predict(x_test, proba=True)
    yh_test2 = _yh_test2 >= 0.5
    
    
    trn = yh_train1==yh_train2
    tst = yh_test1==yh_test2
    trn_title = "{} Train SKLearn/BasicAnom/match {:>2}/{:>2}/{:>1}".format(
      "GOOD:" if np.all(trn) else "BAD:",
      yh_train1.sum(), yh_train2.sum(), np.all(trn),
      )

    tst_title = "{} Test SKLearn/BasicAnom/match {:>2}/{:>2}/{:>1}".format(
      "GOOD:" if np.all(tst) else "BAD:",
      yh_test1.sum(), yh_test2.sum(), np.all(tst)
      )
    
    
    g_trn = ~yh_train1 & trn     
    a_1 = yh_train1 & (~trn)        
    a_2 = yh_train2 &(~trn)    
    a_12 = yh_train1 & trn
    
    plt.figure(figsize=(13,8))
    plt.scatter(x_train[g_trn][:,0], x_train[g_trn][:,1], marker='o', color='b', label='good')
    plt.scatter(x_train[a_1][:,0], x_train[a_1][:,1], marker='D', color='y', label='clf_bad')
    plt.scatter(x_train[a_2][:,0], x_train[a_2][:,1], marker='x', color='r', label='our_bad')
    plt.scatter(x_train[a_12][:,0], x_train[a_12][:,1], marker='x', color='g', label='both_bad')
    for i, (_x,_y) in enumerate(x_train[yh_train2]):
      plt.annotate("{:.2f}".format(_yh_train2[yh_train2][i]), (_x,_y), fontsize=12)
    for i, (_x,_y) in enumerate(x_train[a_1]):
      plt.annotate("{:.2f}".format(_yh_train2[a_1][i]), (_x,_y), fontsize=12)

    plt.legend()
    plt.title(trn_title)
    plt.show()

    g_tst = ~yh_test1 & tst
    a_1 = yh_test1 & (~tst)        
    a_2 = yh_test2 &(~tst)    
    a_12 = yh_test1 & tst

    plt.figure(figsize=(13,8))
    plt.scatter(x_test[g_tst][:,0], x_test[g_tst][:,1], marker='o', color='b', label='good')
    plt.scatter(x_test[a_1][:,0], x_test[a_1][:,1], marker='D', color='y', label='clf_bad')
    plt.scatter(x_test[a_2][:,0], x_test[a_2][:,1], marker='x', color='r', label='our_bad')
    plt.scatter(x_test[a_12][:,0], x_test[a_12][:,1], marker='x', color='g', label='both_bad')

    for i, (_x,_y) in enumerate(x_test[yh_test2]):
      plt.annotate("{:.2f}".format(_yh_test2[yh_test2][i]), (_x,_y), fontsize=12)
    for i, (_x,_y) in enumerate(x_test[a_1]):
      plt.annotate("{:.2f}".format(_yh_test2[a_1][i]), (_x,_y), fontsize=12)

    plt.title(tst_title)
    plt.legend()
    plt.show()
    
    for i in range(n_samples):
      obs = sensor.get_observation()
      yh1 = clf.predict(obs.reshape((1,-1)))
      yh2 = model.predict(obs)
      if yh1 != yh2:
        yh1 = clf.predict(obs.reshape((1,-1)))
        yh2 = model.predict(obs)
  

from naeural_client import BaseDecentrAIObject

import numpy as np
import matplotlib.pyplot as plt

from sklearn.cluster import KMeans
from collections import Counter

class ElbowKmeans(BaseDecentrAIObject):
  def __init__(self, log, feats, min_nr_clusters=2, max_nr_clusters=30):
    if log is None or (type(log).__name__ != 'Logger'):
      raise ValueError("Loggger object is invalid: {}".format(log))
    super().__init__(log=log)
    
    self.X = feats
    self.errors  = []
    self.min_nr_clusters = min_nr_clusters
    self.max_nr_clusters = max_nr_clusters
    self.P("Initialized SearchKmeans class with the following params:")
    self.P(" * feats shape:     {}".format(feats.shape))
    self.P(" * min_nr_clusters: {}".format(min_nr_clusters))
    self.P(" * max_nr_clusters: {}".format(max_nr_clusters))
    return


  def _dist_line(self, x_A, y_A, x_B, y_B, x0, y0):
    m = (y_A - y_B) / (x_A - x_B)
    b = (y_B * x_A - y_A * x_B) / (x_A - x_B)
    
    distance = np.abs(m * x0 - y0 + b) / np.sqrt(1 + m**2);
    return distance
    
    
  def _check_stop_condition(self):
    if len(self.errors) < 3:
      return False, None

    sol_iter = []
    for i in range(len(self.errors)-2):
      tmp_error = self.errors[0:i+3]

      sol_x = 0
      d_max = 0

      for j in range(1, len(tmp_error)-1):
        dist = self._dist_line(0, tmp_error[0], i+2, tmp_error[i+2], j, tmp_error[j])

        if dist > d_max:
          d_max = dist
          sol_x = j
        #endif
      #endfor
      sol_iter.append(sol_x)
    #endfor 
    
    histogram = Counter(sol_iter)
    no_of_occurence = 5
    if no_of_occurence in histogram.values():
      return True, list(histogram.keys())[list(histogram.values()).index(no_of_occurence)]
    else:
      return False, None
   
      
  def get_optimum_nr_clusters(self, n_init=5, max_iter=300, verbose=0):
    self.P("Finding the optimum nr of clusters ...")
    self.P("KMeans params:")
    self.P(" * n_init={}".format(n_init))
    self.P(" * max_iter={}".format(max_iter))
    self.P(" * verbose={}".format(verbose))
    range_ = range(self.min_nr_clusters, self.max_nr_clusters)
    for i, nr_clusters in enumerate(range_):
      self.P("Iteration {}/{} - {} clusters".format(i+1, len(range_), nr_clusters))
      self.kmeans_object = KMeans(nr_clusters, n_init=n_init,
                                  max_iter=max_iter, verbose=verbose)
      self.kmeans_object.fit(self.X)
      self.P(" ", t=True)
      self.errors.append(self.kmeans_object.inertia_)
      ret, iter_sol = self._check_stop_condition()
      if ret is True:
        optimum_nr = iter_sol + self.min_nr_clusters
        self.kmeans_object = KMeans(optimum_nr)
        self.kmeans_object.fit(self.X)

        plt.figure()
        plt.plot(np.arange(len(self.errors)) + self.min_nr_clusters, self.errors)
        plt.scatter(optimum_nr, self.errors[iter_sol])
        self.P(" the system found {} optimum clusters.".format(optimum_nr))
        return optimum_nr
    
  def get_labels(self):
    return self.kmeans_object.labels_
# -*- coding: utf-8 -*-
"""
Created on Thu Nov 14 20:26:49 2019

@author: Andrei
"""

import tensorflow as tf
import numpy as np

if __name__ == '__main__':
  
  # load the graph
  model_wrapper = tf.saved_model.load('xperimental/tf_function_wrapper/saved_model')
  
  inp = np.zeros((5,1024,1024,1))
  
  # inp_single = np.zeros((1,300,300,1))
  
  # call the graph 
  res = model_wrapper.batch_predict(inp)
  
  # there you go
  pred = res[0].numpy()
  timing = [x.numpy() for x in res[1]]
  print('Pred: {}'.format(pred))
  for i, p in enumerate(timing):
    print("Timing {}:\n{}\n".format(i,p))
    
    
  # model_server.graph_get_top_k_dae.get_concrete_function().graph.get_operations()    
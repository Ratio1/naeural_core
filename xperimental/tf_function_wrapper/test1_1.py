# -*- coding: utf-8 -*-
"""
Created on Thu Nov 14 20:08:34 2019

@author: Andrei
"""

import tensorflow as tf
import numpy as np


class CustomModelWrapper(tf.Module):
  def __init__(self, model):
    self._model = model
    
  @tf.function(input_signature=(tf.TensorSpec(shape=(None,None, None, 1), dtype=tf.float32),))
  def batch_predict(self, inputs):
    
    # some arbitrary pre-processing
    t0 = tf.timestamp()
    tf_inputs = inputs + 0.1
    tf_known_size = 10
    tf_input_size = tf.size(tf_inputs)
    tf_input_batch = tf_inputs.shape[0]
    tf_reshaped = tf.image.resize(tf_inputs, size=(tf_known_size,tf_known_size))
    tf_overall_size = tf.size(tf_reshaped)
    tf_n_feats = tf_known_size**2
    tf_b_size =  tf_overall_size // tf_n_feats
    # some debug stuff
    tf.print('DEBUG: input shape ', tf_inputs.shape)
    tf.print('DEBUG: input batch ', tf_input_batch)
    tf.print('DEBUG: input size ', tf_input_size)
    tf.print('DEBUG: reshaped shape ', tf_reshaped.shape)
    tf.print('DEBUG: reshaped size ', tf_overall_size)
    tf.print('DEBUG: feat size ', tf_n_feats  )
    tf.print('DEBUG: actual bsize', tf_b_size)
    t1 = tf.timestamp()    
    # now we process each image at a time as the model is restricted at 1 image only
    tf_preds = tf.TensorArray(tf.float32, size=tf_b_size)
    tf_pred = tf.zeros([1,], dtype=tf.float32)
    for i in tf.range(tf_b_size):
      # now we slice each image
      tf_c_pre_input = tf_reshaped[i]
      tf.print('DEBUG: pre inp shape: ', tf_c_pre_input.shape)
      tf_c_input = tf.expand_dims(tf_c_pre_input, axis=0)
      tf.print('DEBUG: inp shape: ', tf_c_input.shape)
      # this a model call - remember the model assumes batch-size == 1 !
      tf_pred = self._model(tf_c_input)
      tf.print('DEBUG: pred shape: ', tf_pred.shape, ' value: ', tf_pred)
      tf_preds = tf_preds.write(i, tf_pred)      
    t2 = tf.timestamp()
    # now we make some arbitrary post-processing    
    tf_preds = tf_preds.stack()
    tf.print('DEBUG: all preds: ', tf_preds)
    t3 = tf.timestamp()
    # finally return tuple of results
    return tf_preds, (t1-t0, t2-t1, t3-t2)
  
  
  @tf.function(input_signature=(tf.TensorSpec(shape=(1,None, None, 1), dtype=tf.float32),))
  def single_predict(self, inputs):
    tf_res = self._model(inputs)
    return tf_res

if __name__ == '__main__':
  
  # this simulates a conv net that can not receive more than 1 grayscale image at a time
  tf_inp = tf.keras.layers.Input(batch_size=1, shape=(None,None,1), dtype=tf.float32)
  tf_flat = tf.keras.layers.Flatten()(tf_inp)
  tf_res = tf.keras.layers.Lambda(lambda x:tf.keras.backend.sum(x, axis=-1), 
                                  output_shape=(1,1))(tf_flat)
  model = tf.keras.models.Model(tf_inp, tf_res)
    
  to_export = CustomModelWrapper(model)
  
  tf.saved_model.save(to_export,"xperimental/tf_function_wrapper/saved_model")
  
  
  # now lets try the custom coded graph
  np_inp = np.array([np.ones((100,100,1)) * x for x in range(1,4)])

  resp = to_export.batch_predict(np_inp)
  
  pred = resp[0].numpy()
  timing = [x.numpy() for x in resp[1]]
  print('results\n',resp[0].numpy())
  print("Timings - prep: {:.5f}s  loop_predict: {:.5f}  post-proc: {:.5f}".format(
      timing[0], timing[1], timing[2]))
  
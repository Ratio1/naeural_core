import tensorflow as tf
import tensorflow.keras.backend as K
from functools import partial


def quantile_loss(y_true, y_pred, Q):
  """
  I = y_pred <= y_true
  loss = Q * (y_true - y_pred) * I + (1 - Q) * (y_pred - y_true) * (1 - I)  
  """
  tf_res = y_true - y_pred
  tf_max = K.maximum(Q * tf_res, (Q-1) * tf_res)
  tf_loss = K.mean(tf_max, axis=-1)
  return tf_loss


def quantile_loss_05(y_true, y_pred):
  return quantile_loss(y_true,y_pred, Q=0.05)


def quantile_loss_95(y_true, y_pred):
  return quantile_loss(y_true,y_pred, Q=0.95)


def focal_loss_softmax(y_true, y_pred, gamma=2.0, alpha=0.25):
  """
  this is the fixed params focal loss for softmax outputs
  
  paper: loss = -alpha*((1-p)^gamma)*log(p) (https://arxiv.org/pdf/1708.02002.pdf)
  """
  
  eps = K.epsilon()
  
  y_pred = K.clip(y_pred, eps, 1 - eps)
  xentopy = - y_true * K.log(y_pred)
  focal_weights = alpha * y_true * K.pow((1 - y_pred), gamma)
  _loss = focal_weights * xentopy
  loss = K.sum(_loss, axis=-1)
  return loss


def focal_loss_sigmoid(y_true, y_pred, gamma=2.0, alpha=0.25):
  """
  this is the fixed params focal loss for binary xentropy

  OBSERVATION: alpha MUST BE inverse-proportional to the frequency of the class. So if
  class 1 has 25% presence then alpha is 0.25
  
  please see also https://github.com/vandit15/Class-balanced-loss-pytorch  
  
    Formula: https://arxiv.org/pdf/1708.02002.pdf
        loss = -alpha_t*((1-p_t)^gamma)*log(p_t)
        
        p_t = y_pred, if y_true = 1
        p_t = 1-y_pred, otherwise
        
        alpha_t = alpha, if y_true=1
        alpha_t = 1-alpha, otherwise
        
  def focal_loss_fixed(y_true, y_pred):
    pt_1 = tf.where(tf.equal(y_true, 1), y_pred, tf.ones_like(y_pred))
    pt_0 = tf.where(tf.equal(y_true, 0), y_pred, tf.zeros_like(y_pred))

    pt_1 = K.clip(pt_1, 1e-3, .999)
    pt_0 = K.clip(pt_0, 1e-3, .999)

    return -K.sum(alpha * K.pow(1. - pt_1, gamma) * K.log(pt_1))-K.sum((1-alpha) * K.pow( pt_0, gamma) * K.log(1. - pt_0))        

  """
  
  eps = K.epsilon()  
  y_pred = K.clip(y_pred, eps, 1 - eps)
  # Calculate p_t
  p_t = tf.where(K.equal(y_true, 1), y_pred, 1-y_pred)
  # Calculate alpha_t
  alpha_factor = K.ones_like(y_true) * alpha
  alpha_t = tf.where(K.equal(y_true, 1), alpha_factor, 1-alpha_factor)
  # Calculate cross entropy
  cross_entropy = -K.log(p_t)
  weight = alpha_t * K.pow((1-p_t), gamma)
  # Calculate focal loss
  loss = weight * cross_entropy
  # Sum the losses in mini_batch
  loss = K.sum(loss, axis=1)
  return loss

  

def get_focal_loss_softmax(gamma=2.0, alpha=0.25):
  return partial(focal_loss_softmax, gamma=gamma, alpha=alpha)

def get_focal_loss_sigmoid(gamma=2.0, alpha=0.25):
  return partial(focal_loss_sigmoid, gamma=gamma, alpha=alpha)




def huber_loss(y_true, y_pred, d=1.0):
  """
  0.5 * x^2                  if |x| <= d
  0.5 * d^2 + d * (|x| - d)  if |x| > d
  """
  tf_res = y_pred - y_true
  cond1 = K.cast((K.abs(tf_res) <= d), tf.float32)
  cond2 = K.cast((K.abs(tf_res) > d), tf.float32)
  tf_batch_loss1 = cond1 * (0.5 * (tf_res**2))
  tf_batch_loss2 = cond2 * (d * (K.abs(tf_res) - 0.5 * d))
  tf_batch_loss = tf_batch_loss1 + tf_batch_loss2
  tf_loss = K.mean(tf_batch_loss)
  return tf_loss

def K_huber_loss(y_true, y_pred, d=1.0):
  return huber_loss(y_true=y_true, y_pred=y_pred, d=d)


def contrastive_loss(y_true, y_pred, margin=1):
  tf_sqpred = K.square(y_pred)
  tf_margin = K.square(K.maximum(margin - y_pred, 0))
  tf_mean = K.mean(y_true * tf_sqpred + (1 - y_true) * tf_margin)
  return tf_mean


def MAD_loss(y_true, y_pred):
  tf_res = K.abs(y_true - y_pred)
  tf_mad = tf_res / (K.abs(y_pred) + K.epsilon())
  return K.mean(tf_mad)


def MADSQ_loss(y_true, y_pred):
  tf_res_sq = K.pow(y_true - y_pred, 2)
  tf_mad_sq = tf_res_sq / (K.pow(y_pred, 2) + K.epsilon())
  return K.mean(tf_mad_sq)

def triplet_loss_variable_margin(y_pred):
  anchor = y_pred[0]
  positive = y_pred[1]
  negative = y_pred[2]
  beta = y_pred[3]

  similar_dist = K.sum(K.square(anchor - positive), axis=1)
  diff_dist = K.sum(K.square(anchor - negative), axis=1)
  dist = K.expand_dims(similar_dist - diff_dist)
  loss = K.maximum(dist + beta, 0.0)
  return loss

def triplet_loss(y_pred, beta=0.5,):

  anchor = y_pred[0]
  positive = y_pred[1]
  negative = y_pred[2]

  similar_dist = K.sum(K.square(anchor - positive), axis=1)
  diff_dist = K.sum(K.square(anchor - negative), axis=1)
  loss = K.maximum(similar_dist - diff_dist + beta, 0.0)
  loss = K.expand_dims(loss)
  return loss


def identity_loss(y_true, y_pred):
  return K.mean(y_pred - 0 * y_true)


def quad_loss(y_pred, beta1, beta2, ):
  
  anchor = y_pred[0]
  positive = y_pred[1]
  negative = y_pred[2]
  negative2 = y_pred[3]

  similar_dist = K.sum(K.square(anchor - positive), axis=1)
  diff_dist1 = K.sum(K.square(anchor - negative), axis=1)
  diff_dist2 = K.sum(K.square(negative - negative2), axis=1)
  basic_loss_1 = similar_dist - diff_dist1 + beta1
  basic_loss_2 = similar_dist - diff_dist2 + beta2
  # now finally max operator but NO summing as this is a layer not a
  # actual loss function
  loss_1 = K.maximum(basic_loss_1, 0.0)
  loss_2 = K.maximum(basic_loss_2, 0.0)
  loss = loss_1 + loss_2
  loss = K.expand_dims(loss)
  return loss


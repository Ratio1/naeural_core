def K_rec(y_true, y_pred, threshold=0.5):
  import tensorflow as tf
  K = tf.keras.backend
  y_pred = tf.cast(y_pred >= threshold, dtype=tf.int32)
  y_true = tf.cast(y_true, dtype=tf.int32)
  recall = K.sum(y_true * y_pred) / K.sum(y_true)
  return recall

def K_MAPE_TS_LAST(y_true, y_pred):
  import tensorflow as tf
  K = tf.keras.backend
  tf_res = K.abs(y_true[:, -1, :] - y_pred[:, -1, :])
  tf_yt = K.clip(K.abs(y_true[:, -1, :]), K.epsilon(), None)
  tf_mape = tf_res / tf_yt
  mape_series = K.clip(tf_mape, K.epsilon(), 2)
  return K.mean(mape_series)

def K_pre(y_true, y_pred, threshold=0.5, ):
  import tensorflow as tf
  K = tf.keras.backend
  y_pred = tf.cast(y_pred >= threshold, dtype=tf.int32)
  y_true = tf.cast(y_true, dtype=tf.int32)
  precision = K.sum(y_true * y_pred) / K.sum(y_pred)
  return precision

def K_f2(y_true, y_pred):
  precision = K_pre(y_true, y_pred)
  recall = K_rec(y_true, y_pred)
  return 5 * (precision * recall) / (4 * precision + recall)

def K_f1(y_true, y_pred):
  precision = K_pre(y_true, y_pred)
  recall = K_rec(y_true, y_pred)
  return 2 * (precision * recall) / (precision + recall)

def get_K_clf_metrics():
  return ['accuracy', K_rec, K_pre, K_f2]

def get_K_metrics():
  metrics = get_K_clf_custom_dict()
  return metrics

def get_K_clf_custom_dict():
  return {'K_rec': K_rec, 'K_pre': K_pre, 'K_f2': K_f2}

def K_r2_1D(y_true, y_pred):
  import tensorflow as tf
  K = tf.keras.backend
  SS_res = K.sum(K.square(y_true - y_pred))
  SS_tot = K.sum(K.square(y_true - K.mean(y_true)))
  return 1 - SS_res / (SS_tot + K.epsilon())

def K_r2_TS(y_true, y_pred, return_series=False):
  import tensorflow as tf
  K = tf.keras.backend
  tf_SS_res = K.sum(K.square(y_true - y_pred), axis=1)
  tf_SS_tot = K.sum(K.square(y_true - K.mean(y_true, axis=1,
                                             keepdims=True)),
                    axis=1)
  tf_SS_tot = K.clip(tf_SS_tot, K.epsilon(), None)
  tf_all_R2 = 1 - tf_SS_res / tf_SS_tot
  tf_all_R2 = K.clip(tf_all_R2, -1.5, None)
  if return_series:
    return tf_all_R2
  else:
    return K.mean(tf_all_R2)

def K_MAD(y_true, y_pred, relu=True, std=None, mean=None):
  import tensorflow as tf
  K = tf.keras.backend
  if std is not None and mean is not None:
    y_true = y_true * std + mean
    y_pred = y_pred * std + mean

  if relu:
    y_pred = K.relu(y_pred)

  y_true_s = K.sum(y_true, axis=1)
  y_pred_s = K.sum(y_pred, axis=1)

  y_true_s = K.reshape(y_true_s, shape=[-1, ])
  y_pred_s = K.reshape(y_pred_s, shape=[-1, ])

  tf_zeros = K.zeros(shape=K.shape(y_pred_s))
  not_zero_indices = K.not_equal(y_pred_s, tf_zeros)

  y_true_s = tf.boolean_mask(y_true_s, not_zero_indices)
  y_pred_s = tf.boolean_mask(y_pred_s, not_zero_indices)

  mad = K.abs(y_true_s - y_pred_s) / y_pred_s
  return K.mean(mad)

def K_MAD_TS(y_true, y_pred, relu=True, ERR_MAX=2,
             std=None, mean=None):
  import tensorflow as tf
  K = tf.keras.backend

  if std is not None and mean is not None:
    y_true = y_true * std + mean
    y_pred = y_pred * std + mean

  if relu:
    y_pred = K.relu(y_pred)

  y_true_s = K.sum(y_true, axis=1)
  y_pred_s = K.sum(y_pred, axis=1)

  y_pred_s = K.clip(y_pred_s, K.epsilon(), None)

  tf_mad_pre = K.abs(y_true_s - y_pred_s) / y_pred_s
  tf_mad = K.clip(tf_mad_pre, 0, ERR_MAX)
  return K.mean(tf_mad)


def generate_K_MAD(mean, std, relu=True):
  from functools import partial
  generated = partial(K_MAD, std=std, mean=mean, relu=relu)
  generated.__name__ = K_MAD.__name__
  return generated

def generate_K_MAD_TS(mean, std, relu=True, ERR_MAX=2):
  from functools import partial
  generated = partial(K_MAD_TS, std=std, mean=mean,
                      relu=relu, ERR_MAX=ERR_MAX)
  generated.__name__ = K_MAD_TS.__name__
  return generated

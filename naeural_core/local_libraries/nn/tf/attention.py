import tensorflow as tf

def luong_attention(q, k, v, ffn=None):
  """
  Calculate the attention weights.
  q, k, v must have matching leading dimensions.
  k, v must have matching penultimate dimension, i.e.: seq_len_k = seq_len_v.

  Args:
    q: query shape == (..., seq_len_q, depth_q)
    k: key shape == (..., seq_len_k, depth_k)
    v: value shape == (..., seq_len_v, depth_v)
    ffn: a feed-forward network object that transforms the query (q) in order to be
         dot-multiplied with the key (k). The last layer of the `ffn` should have
         `units=depth_k`.
         The default value is None. In this case, `ffn` is a Dense layer with `units=depth_k`

    Returns:
      attn, attn_sm
    """

  depth_k = k.shape[-1]
  if ffn is None:
    layers = [tf.keras.layers.Dense(units=depth_k,
                                    name='q_transform_{}'.format(depth_k))]
    ffn = tf.keras.models.Sequential(layers)
  #endif

  assert ffn.layers[-1].units == depth_k

  q_transformed = ffn(q) # (batch_size, depth_k)
  q_exp = tf.expand_dims(q_transformed, axis=1) # (batch_size, 1, depth_k)
  query_keys = tf.keras.backend.batch_dot(k, q_exp, axes=2) # (batch_size, seq_len_k, 1)

  attn_logits = query_keys
  attn_sm = tf.keras.activations.softmax(attn_logits, axis=1)
  attn = tf.reduce_sum(attn_sm * v, axis=1, keepdims=False) # (batch_size, depth_v)

  return attn, attn_sm

def bahdanau_one_attention(q, k, v, ffn=None):
  """
  Calculate the attention weights.
  q, k, v must have matching leading dimensions.
  k, v must have matching penultimate dimension, i.e.: seq_len_k = seq_len_v.

  Args:
    q: query shape == (..., seq_len_q, depth_q)
    k: key shape == (..., seq_len_k, depth_k)
    v: value shape == (..., seq_len_v, depth_v)
    ffn: a feed-forward network object that transforms the result of query (q)
         applied on keys (k). The last layer of the `ffn` should have `units=1`.
         The default value is None. In this case, `ffn` is a Dense layer with `units=1`

    Returns:
      attn, attn_sm
    """
  if ffn is None:
    layers = [tf.keras.layers.Dense(units=1,
                                    name='qk_transform_{}'.format(1))]
    ffn = tf.keras.models.Sequential(layers)
  #endif

  assert ffn.layers[-1].units == 1

  seq_len_k = k.shape[-2]

  q_exp = tf.expand_dims(q, axis=1) # (batch_size, 1, depth_q)
  repeated_query = q_exp + tf.zeros((seq_len_k, 1)) # (batch_size, seq_len_k, depth_q)
  query_keys = tf.concat([repeated_query, k], axis=-1) # (batch_size, seq_len_k, depth_q + depth_k)

  attn_logits = ffn(query_keys) # (batch_size, seq_len_k, 1)
  attn_sm = tf.keras.activations.softmax(attn_logits, axis=1)
  attn = tf.reduce_sum(attn_sm * v, axis=1, keepdims=False)

  return attn, attn_sm

def bahdanau_two_attention(q, k, v, ffn=None, W1=None, W2=None):
  """
  Calculate the attention weights.
  q, k, v must have matching leading dimensions.
  k, v must have matching penultimate dimension, i.e.: seq_len_k = seq_len_v.

  Args:
    q: query shape == (..., seq_len_q, depth_q)
    k: key shape == (..., seq_len_k, depth_k)
    v: value shape == (..., seq_len_v, depth_v)
    ffn: a feed-forward network object that transforms the result of query (q)
         applied on keys (k). The last layer of the `ffn` should have `units=1`.
         The default value is None. In this case, `ffn` is a Dense layer with `units=1`
    W1: a Dense object that transforms the key in order to be summed.
        The default value is None. In this case, `W1` is a Dense layer with `units=depth_k`.
    W2: a Dense object that transforms the query in order to be summed.
        The default value is None. In this case, `W1` is a Dense layer with `units=depth_k`.

    Returns:
      attn, attn_sm
    """
  if ffn is None:
    layers = [tf.keras.layers.Dense(units=1,
                                    name='qk_transform_{}'.format(1))]
    ffn = tf.keras.models.Sequential(layers)
  #endif

  assert ffn.layers[-1].units == 1

  depth_k = k.shape[-1]
  if W1 is None:
    W1 = tf.keras.layers.Dense(units=depth_k, use_bias=False,
                               name='k_transform_{}'.format(depth_k))
  if W2 is None:
    W2 = tf.keras.layers.Dense(units=depth_k, use_bias=False,
                               name='q_transform_{}'.format(depth_k))
  query_keys = tf.nn.tanh(W1(k) + W2(q)) # (batch_size, seq_len_k, depth_k)
  attn_logits = ffn(query_keys) # (batch_size, seq_len_k, 1)
  attn_sm = tf.keras.activations.softmax(attn_logits, axis=1)
  attn = tf.reduce_sum(attn_sm * v, axis=1, keepdims=False)

  return attn, attn_sm


class LuongAttention(tf.keras.layers.Layer):
  def __init__(self, key_depth, **kwargs):
    super(LuongAttention, self).__init__(**kwargs)
    self.key_depth = key_depth

    layers = [tf.keras.layers.Dense(units=self.key_depth,
                                    name='q_transform_{}'.format(self.key_depth))]
    self.ffn = tf.keras.models.Sequential(layers)
    return

  def call(self, inputs):
    q,k,v = inputs
    return luong_attention(q=q, k=k, v=v, ffn=self.ffn)

  def get_config(self):
    config = {
      'key_depth' : self.key_depth,
    }
    base_config = super(LuongAttention, self).get_config()
    cfg =  dict(list(base_config.items()) + list(config.items()))
    return cfg


class BahdanauAttention(tf.keras.layers.Layer):
  def __init__(self, key_depth, mode=1, **kwargs):
    assert mode in [1,2]

    super(BahdanauAttention, self).__init__(**kwargs)
    self.key_depth = key_depth
    self.mode = mode

    layers = [tf.keras.layers.Dense(units=1,
                                    name='qk_transform_{}'.format(1))]
    self.ffn = tf.keras.models.Sequential(layers)

    self.W1 = tf.keras.layers.Dense(units=self.key_depth, use_bias=False,
                                    name='k_transform_{}'.format(self.key_depth))
    self.W2 = tf.keras.layers.Dense(units=self.key_depth, use_bias=False,
                                    name='q_transform_{}'.format(self.key_depth))
    return

  def call(self, inputs):
    q,k,v = inputs
    if self.mode == 1:
      return bahdanau_one_attention(q=q, k=k, v=v, ffn=self.ffn, W1=self.W1, W2=self.W2)
    elif self.mode == 2:
      return bahdanau_two_attention(q=q, k=k, v=v, ffn=self.ffn, W1=self.W1, W2=self.W2)

  def get_config(self):
    config = {
      'key_depth': self.key_depth,
      'mode' : self.mode,
    }
    base_config = super(BahdanauAttention, self).get_config()
    cfg =  dict(list(base_config.items()) + list(config.items()))
    return cfg


def scaled_dot_product_attention(q, k, v, mask=None):
  """
  Calculate the attention weights.
  q, k, v must have matching leading dimensions.
  k, v must have matching penultimate dimension, i.e.: seq_len_k = seq_len_v.
  The mask has different shapes depending on its type (padding or look ahead)
  but it must be broadcastable for addition.

  Args:
    q: query shape == (..., seq_len_q, depth)
    k: key shape == (..., seq_len_k, depth)
    v: value shape == (..., seq_len_v, depth_v)
    mask: Float tensor with shape broadcastable
          to (..., seq_len_q, seq_len_k). Defaults to None.

  Returns:
    output, attention_weights
  """

  matmul_qk = tf.matmul(q, k, transpose_b=True)  # (..., seq_len_q, seq_len_k)

  # The dot-product attention is scaled by a factor of square root of the depth.
  # This is done because for large values of depth, the dot product grows large
  # in magnitude pushing the softmax function where it has small gradients
  # resulting in a very hard softmax.
  dk = tf.cast(tf.shape(k)[-1], tf.float32)
  scaled_attention_logits = matmul_qk / tf.math.sqrt(dk)

  # add the mask to the scaled tensor.
  if mask is not None:
    scaled_attention_logits += (mask * -1e9)

  # softmax is normalized on the last axis (seq_len_k)
  attention_weights = tf.nn.softmax(scaled_attention_logits, axis=-1)  # (..., seq_len_q, seq_len_k)

  output = tf.matmul(attention_weights, v)  # (..., seq_len_q, depth_v)

  return output, attention_weights


class MultiHeadAttention(tf.keras.layers.Layer):
  def __init__(self, d_model, num_heads):
    super(MultiHeadAttention, self).__init__()
    self.num_heads = num_heads
    self.d_model = d_model

    assert d_model % self.num_heads == 0

    self.depth = d_model // self.num_heads

    self.wq = tf.keras.layers.Dense(d_model)
    self.wk = tf.keras.layers.Dense(d_model)
    self.wv = tf.keras.layers.Dense(d_model)

    self.dense = tf.keras.layers.Dense(d_model)
    return

  def split_heads(self, x, batch_size):
    """Split the last dimension into (num_heads, depth).
    Transpose the result such that the shape is (batch_size, num_heads, seq_len, depth)
    """
    x = tf.reshape(x, (batch_size, -1, self.num_heads, self.depth))
    return tf.transpose(x, perm=[0, 2, 1, 3])

  def call(self, q, k, v, mask=None):
    batch_size = tf.shape(q)[0]

    # Instead of one single attention head, Q, K, and V are split into multiple heads
    # because it allows the model to jointly attend to information at different
    # positions from different representational spaces. After the split each
    # head has a reduced dimensionality, so the total computation cost is the same
    # as a single head attention with full dimensionality.
    q = self.wq(q)  # (batch_size, seq_len, d_model)
    k = self.wk(k)  # (batch_size, seq_len, d_model)
    v = self.wv(v)  # (batch_size, seq_len, d_model)

    q = self.split_heads(q, batch_size)  # (batch_size, num_heads, seq_len_q, depth)
    k = self.split_heads(k, batch_size)  # (batch_size, num_heads, seq_len_k, depth)
    v = self.split_heads(v, batch_size)  # (batch_size, num_heads, seq_len_v, depth)

    # scaled_attention.shape == (batch_size, num_heads, seq_len_q, depth)
    # attention_weights.shape == (batch_size, num_heads, seq_len_q, seq_len_k)
    scaled_attention, attention_weights = scaled_dot_product_attention(
      q, k, v, mask)

    scaled_attention = tf.transpose(scaled_attention, perm=[0, 2, 1, 3])  # (batch_size, seq_len_q, num_heads, depth)

    concat_attention = tf.reshape(scaled_attention,
                                  (batch_size, -1, self.d_model))  # (batch_size, seq_len_q, d_model)

    output = self.dense(concat_attention)  # (batch_size, seq_len_q, d_model)

    return output, attention_weights


if __name__ == '__main__':
  import numpy as np
  query = tf.constant(np.array([[0.23, 0.25, 1.43, 1.45, 0.01, 0.45, 0.73, 0.88],
                                [0.15, 0.77, 1.41, 0.03, 0.12, 0.17, 1.17, 1.12]]))
  enc_states = np.array([[[1, 2, 3, 4, 5], [0, 0, 0, 0, 0], [1, 2, 3, 4, 5], [0, 0, 0, 0, 0]],
                         [[1, 2, 3, 4, 5], [0, 0, 0, 0, 0], [1, 2, 3, 4, 5], [0, 0, 0, 0, 0]]])
  key = tf.constant(enc_states, dtype=tf.float32)
  value = tf.constant(enc_states, dtype=tf.float32)

  a, b = luong_attention(q=query, k=key, v=value)
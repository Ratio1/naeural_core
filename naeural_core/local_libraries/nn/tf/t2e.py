import tensorflow as tf
from tensorflow.keras import initializers, regularizers, constraints, activations
from tensorflow.keras.initializers import Constant
from tensorflow.keras import backend as K


__VER__ = '0.2.0.1'

class Time2Embedding(tf.keras.layers.Layer):
  """
  
  TODO: 
      The general assumption when working with time series is that a embedding 
      based identification of each time-series could encode the local patterns 
      of each series and thus allow the overall function approximator to 
      predict individual patterns. This behavior is partially a wishful assumption 
      due to the nature of the optimization process than cannot ensure the fact that
      embeddings will actually encode the local series patterns such as seasonality 
      and/or even non-linear individual patterns. 
      Nevertheless we do have the option to enfoce the optimization process to adapt 
      each individual embedding vector to time signals by encoding the time variable 
      in embeddings activated by sinuidal functions such as sine and cosine. This will 
      also drop the need of having a individual data stream in the computational graph 
      that deals with the time-series indentification.
      
      Time2Embedding(n_series, embedding_size) -> call(series_id, time_variable):
        
        n_series        :  number of time-series required to construct embedding 
                           matrix
                           
        embedding_size  :  size of each time-series embedding vector that is 
                           actually the transformation matrix of the time_variable
                           
        series_id       :  the unique id of series (1..n_series)
        
        time_variable   :  the variable or vector that represents the time that 
                           could be either [step], [month, week, day, weekday], etc
        
  
  """
  def __init__(self, nr_series, emb_size, 
               act='sin',
               embed_initializer='random_normal',
               DEBUG=False,
               **kwargs):
    assert act in ['sin','cos'], "Activation '{}' unknown!".format(act)
    self.__version__ = __VER__  
    self.emb_size = emb_size
    self.nr_series = nr_series
    self.act = act
    self.DEBUG = DEBUG
    self.embed_initializer = initializers.get(embed_initializer)
    print("Time2Embedding ver. {} initializing.".format(self.__version__))
    super(Time2Embedding, self).__init__(**kwargs)
    return
  
  
  def build(self, input_shape):
    """
     the omega/phi must be selected from embeddings matrix based on time-series id
     input_shape is tuple (series_embedding_shape, time_vector_shape)
    """
    if len(input_shape) != 2:
      raise ValueError("Time2Embedding must receive (series_tensor, time_tensor) input")
    input_dim_sers = int(input_shape[0][-1])
    input_dim_time = int(input_shape[1][-1])
    
    
    if input_dim_sers != 1:
      raise ValueError("Time2Embedding must receive [batch, steps, 1] tensor for time-series identification")
    
    if self.DEBUG:
      initializer_emb = Constant(1)
    else:
      initializer_emb = self.embed_initializer    
      
    embed_real_size = (input_dim_time + 1) * self.emb_size
    
    self.time_embeds = tf.keras.layers.Embedding((self.nr_series, embed_real_size),
                                                 name='{}_embeds'.format(self.name),
                                                 embeddings_initializer=initializer_emb)

    self.input_dim_time = input_dim_time
    
    embed_build_shape = input_shape[0]
    self.time_embeds.build(embed_build_shape)
    
    super(Time2Embedding, self).build(input_shape)
    return
  
  
  
  def call(self, x, mask=None):
    
    x_ser = x[0]
    x_time = x[1]
    
    x_ser_emb = self.time_embeds[x_ser]    
    offset = self.input_dim_time * self.emb_size
    
    tf_omega = x_ser_emb[:,:,:offset]
    tf_phi = x_ser_emb[:,:,offset:]
        
    
    tf_x_k1n = K.dot(x_time, tf_omega) + self.tf_phi
    if self.act == 'sin':
      tf_x_k1n = K.sin(tf_x_k1n)
    elif self.act == 'cos':
      tf_x_k1n = K.cos(tf_x_k1n)
    else:
      raise ValueError("Uknown activation '{}'".format(self.activation))
    
    tf_x_k0 = K.dot(x, self.W_omega0) +  self.W_phi0 ### TODO ???
    
    tf_x = K.concatenate([tf_x_k0, tf_x_k1n], axis=-1)
    
    output = tf_x
    
    return output
  
  
  
  def compute_output_shape(self, input_shape):
    return (input_shape[0:-1] + [self.emb_size])  



  def get_config(self):
    config = {
        'emb_size' : self.emb_size,
        'act' : self.act,
        'kernel_initializer': initializers.serialize(self.kernel_initializer),
        'kernel_regularizer': regularizers.serialize(self.kernel_regularizer),
        'kernel_constraint': constraints.serialize(self.kernel_constraint),
        }
    base_config = super(Time2Embedding, self).get_config()
    cfg =  dict(list(base_config.items()) + list(config.items()))
    return cfg


class SimpleTime2Embedding(tf.keras.layers.Layer):
  """
  
  """
  def __init__(self, emb_size, 
               act='sin',
               omega_initializer='random_normal',
               phi_initializer='random_normal',
               DEBUG=False,
               **kwargs):
    assert act in ['sin','cos'], "Activation '{}' unknown!".format(act)
    self.__version__ = '0.1.0.0'
    self.emb_size = emb_size
    self.act = act
    self.DEBUG = DEBUG
    self.omega_initializer = initializers.get(omega_initializer)
    self.phi_initializer = initializers.get(phi_initializer)
    print("SimpleTime2Embedding ver. {} initializing.".format(self.__version__))
    super(SimpleTime2Embedding, self).__init__(**kwargs)
    return
  
  
  def build(self, input_shape):
    """
     the omega/phi must be selected from embeddings matrix based on time-series id
     input_shape is tuple (series_embedding_shape, time_vector_shape)
    """
    input_dim = int(input_shape[-1])
   
    if self.DEBUG:
      initializer_omega = Constant([1] * self.emb_size)
      initializer_phi = Constant([1] * self.emb_size)
      initializer_omega0 = Constant([1])
      initializer_phi0 = Constant([1])
    else:
      initializer_omega = self.omega_initializer
      initializer_phi = self.phi_initializer
      initializer_omega0 = self.omega_initializer
      initializer_phi0 = self.phi_initializer
      
    
    self.W_omega = self.add_weight(shape=(input_dim, self.emb_size),
                                   name='{}_W_omega'.format(self.name),
                                   trainable=True,
                                   initializer=initializer_omega,
                                   )
    
    self.W_phi = self.add_weight(shape=(self.emb_size, ),
                                 name='{}_W_phi'.format(self.name),
                                 trainable=True,
                                 initializer=initializer_phi,
                                 )
    
    self.W_omega0 = self.add_weight(shape=(input_dim, 1),
                                   name='{}_W_omega0'.format(self.name),
                                   trainable=True,
                                   initializer=initializer_omega0,
                                   )
    
    self.W_phi0 = self.add_weight(shape=(1,),
                                  name='{}_W_phi0'.format(self.name),
                                  trainable=True,
                                  initializer=initializer_phi0,
                                  )
    
    super(SimpleTime2Embedding, self).build(input_shape)
    return
  
  
  def call(self, x, mask=None):
    
    tf_x_k1n = K.dot(x, self.W_omega) + self.W_phi
    if self.act == 'sin':
      tf_x_k1n = K.sin(tf_x_k1n)
    elif self.act == 'cos':
      tf_x_k1n = K.cos(tf_x_k1n)
    else:
      raise ValueError("Uknown activation '{}'".format(self.activation))
    
    tf_x_k0 = K.dot(x, self.W_omega0) +  self.W_phi0
    
    tf_x = K.concatenate([tf_x_k0, tf_x_k1n], axis=-1)
    
    output = tf_x
    
    return output
  
  
  def compute_output_shape(self, input_shape):
    return (input_shape[0:-1] + [self.emb_size])  


  def get_config(self):
    config = {
        'emb_size' : self.emb_size,
        'act' : self.act,
        'kernel_initializer': initializers.serialize(self.kernel_initializer),
        'kernel_regularizer': regularizers.serialize(self.kernel_regularizer),
        'kernel_constraint': constraints.serialize(self.kernel_constraint),
        }
    base_config = super(SimpleTime2Embedding, self).get_config()
    cfg =  dict(list(base_config.items()) + list(config.items()))
    return cfg

  
  
  
if __name__ == '__main__':
  import numpy as np
  np.set_printoptions(edgeitems=30, linewidth=100000)
  epochs = 5
  
  
  ###
  x = np.array([[[1],[1]], [[2],[2]]])
  tf_time_vector = tf.keras.layers.Input((None,4))
  tf_series_id = tf.keras.layers.Input((None,1))
  inputs = [tf_series_id, tf_time_vector]
  tf_m = Time2Embedding(4)([tf_series_id, tf_time_vector])
  m = tf.keras.models.Model(inputs, tf_m)
  m.summary()
  p=m.predict(x)
  print(p.shape, '\n',p)
  

  
  ###


  def rec(y_true, y_pred, threshold=0.5):
    tf_TP = K.sum(K.round(K.clip(y_true * y_pred, 0, 1)))
    tf_AP = K.sum(y_true)
    recall = tf_TP / tf_AP
    return recall
  
  n_d = 30
  stp_size = 5
  n_stp = n_d * 2
  series = []
  starts = []
  steps = [stp_size*x for x in [1,2,3]]
  n_ser = len(steps)
  for step in steps:
    s = np.zeros(n_stp, dtype=np.float32)
    s[::step] = 1
    series.append(s)
    starts.append(np.random.randint(365))
  
  y_ts = np.array(series).reshape((n_ser,-1,1))
  x_id_ts = np.array([[x]*n_stp for x in range(len(series))])
  x_id_ts = x_id_ts.reshape((n_ser,-1,1))
  x_tm_ts = np.array([[x % n_d for x in range(y,y+n_stp,1)] for y in starts])
  x_tm_ts = x_tm_ts.reshape((n_ser,-1,1))
  
  y_fc = y_ts.reshape((-1,1))
  x_id_fc = x_id_ts.reshape((-1,1))
  x_tm_fc = x_tm_ts.reshape((-1,1))
  ##################
  if True:
    print("Base FC test")
    
    tf_id = tf.keras.layers.Input((1,))
    tf_tm = tf.keras.layers.Input((1,), dtype=tf.int32)
    tf_id_emb = tf.keras.layers.Embedding(n_ser, 2)(tf_id)
    tf_id_emb = tf.keras.layers.Lambda(lambda x: K.squeeze(x, axis=1))(tf_id_emb)
    tf_tm_emb = tf.keras.layers.Lambda(
        lambda x: K.one_hot(tf_tm, num_classes=n_d))(tf_tm)
    tf_tm_emb = tf.keras.layers.Lambda(lambda x: K.squeeze(x, axis=1))(tf_tm_emb)
    tf_x = tf.keras.layers.concatenate([tf_id_emb, tf_tm_emb])
    tf_x = tf.keras.layers.Dense(64, activation='selu')(tf_x)
    tf_x = tf.keras.layers.Dense(1, activation='sigmoid')(tf_x)
    model_fc = tf.keras.models.Model([tf_id, tf_tm], tf_x)
    model_fc.compile(optimizer='adam', loss='binary_crossentropy',
                     metrics=['acc',rec])
    model_fc.summary()
    model_fc.fit([x_id_fc, x_tm_fc], y_fc, epochs=epochs, batch_size=256)
    preds = model_fc.predict([x_id_fc, x_tm_fc])
    chk = np.concatenate([preds, y_fc], axis=-1)
    print(chk[:10])
  
  ###############
  if True:
    print("Base LSTM test")
    tf_id = tf.keras.layers.Input((None,1))
    tf_tm = tf.keras.layers.Input((None,1), dtype=tf.int32)
    tf_id_emb = tf.keras.layers.Embedding(n_ser, 2)(tf_id)
    tf_id_emb = tf.keras.layers.Lambda(lambda x: K.squeeze(x, axis=2))(tf_id_emb)
    tf_tm_emb = tf.keras.layers.Lambda(
        lambda x: K.one_hot(tf_tm, num_classes=n_d))(tf_tm)
    tf_tm_emb = tf.keras.layers.Lambda(lambda x: K.squeeze(x, axis=2))(tf_tm_emb)
    tf_x = tf.keras.layers.concatenate([tf_id_emb, tf_tm_emb])
    tf_x = tf.keras.layers.CuDNNLSTM(256, return_sequences=True)(tf_x)
    tf_x = tf.keras.layers.Dense(64, activation='selu')(tf_x)
    tf_x = tf.keras.layers.Dense(1, activation='sigmoid')(tf_x)
    model_ts = tf.keras.models.Model([tf_id, tf_tm], tf_x)
    model_ts.compile(optimizer='adam', loss='binary_crossentropy',
                     metrics=['acc',rec])
    model_ts.summary()
    model_ts.fit([x_id_ts, x_tm_ts], y_ts, epochs=epochs, batch_size=256)
    preds = model_ts.predict([x_id_ts, x_tm_ts])
    chk = np.concatenate([preds, y_ts], axis=-1)
    print(chk[0,:10])
  
  
  
  ##################
  if True:
    print("T2E FC test")
    
    tf_id = tf.keras.layers.Input((1,))
    tf_tm = tf.keras.layers.Input((1,))
    tf_id_emb = tf.keras.layers.Embedding(n_ser, 2)(tf_id)
    tf_id_emb = tf.keras.layers.Lambda(lambda x: K.squeeze(x, axis=1))(tf_id_emb)
    tf_tm_emb = Time2Embedding(2)(tf_tm)
    tf_x = tf.keras.layers.concatenate([tf_id_emb, tf_tm_emb])
    tf_x = tf.keras.layers.Dense(64, activation='selu')(tf_x)
    tf_x = tf.keras.layers.Dense(1, activation='sigmoid')(tf_x)
    model_fc = tf.keras.models.Model([tf_id, tf_tm], tf_x)
    model_fc.compile(optimizer='adam', loss='binary_crossentropy',
                     metrics=['acc',rec])
    model_fc.summary()
    model_fc.fit([x_id_fc, x_tm_fc], y_fc, epochs=epochs, batch_size=256)
    preds = model_fc.predict([x_id_fc, x_tm_fc])
    chk = np.concatenate([preds, y_fc], axis=-1)
    print(chk[:10])


  ##################
  if True:
    print("T2E LSTM test")
    
    tf_id = tf.keras.layers.Input((None,1))
    tf_tm = tf.keras.layers.Input((None,1))
    tf_id_emb = tf.keras.layers.Embedding(n_ser, 2)(tf_id)
    tf_id_emb = tf.keras.layers.Lambda(lambda x: K.squeeze(x, axis=2))(tf_id_emb)
    tf_tm_emb = Time2Embedding(2)(tf_tm)
    tf_x = tf.keras.layers.concatenate([tf_id_emb, tf_tm_emb])
    tf_x = tf.keras.layers.CuDNNLSTM(256, return_sequences=True)(tf_x)
    tf_x = tf.keras.layers.Dense(64, activation='selu')(tf_x)
    tf_x = tf.keras.layers.Dense(1, activation='sigmoid')(tf_x)
    model_fc = tf.keras.models.Model([tf_id, tf_tm], tf_x)
    model_fc.compile(optimizer='adam', loss='binary_crossentropy',
                     metrics=['acc',rec])
    model_fc.summary()
    model_fc.fit([x_id_ts, x_tm_ts], y_ts, epochs=epochs, batch_size=256)
    preds = model_fc.predict([x_id_ts, x_tm_ts])
    chk = np.concatenate([preds, y_ts], axis=-1)
    print(chk[0,:10])
  
  
import tensorflow as tf
from tensorflow.keras import initializers, regularizers, constraints, activations
from tensorflow.keras.initializers import Constant
from tensorflow.keras import backend as K


__VER__ = '2.0.0.0'



BIAS_CONSTANT = -1.9
 
    

class GatedDense(tf.keras.layers.Layer):
  """
  Modified Highway network.

  Intuition: instead of transfering/skip connections we lear a transformation gate

  output = Gate * Layer + (1- Gate) * TransformedInput
  
  

  # Arguments
      units: number of output units
      
      batch_norm: apply batch norm after linearity 
      
      activation: Activation function to use (after BN if specified)
          (see [activations](../activations.md)).
          Default: `ReLU` is applied          
          
      transform_activation: Activation function to use
          for the transform (gate signal) unit - recommended as `sigmoid`
          (see [activations](../activations.md)).
          Default: sigmoid (`sigmoid`).
          If you pass `None`, no activation is applied
          (ie. "linear" activation: `a(x) = x`).x
          
      kernel_initializer: Initializer for the `kernel` weights matrix,
          used for the linear transformation of the inputs
          (see [initializers](../initializers.md)).
          
      transform_initializer: Initializer for the `transform` weights matrix,
          used for the linear transformation of the inputs
          (see [initializers](../initializers.md)).
          
      bias_initializer: Initializer for the bias vector
          (see [initializers](../initializers.md)).
          
      transform_bias_initializer: Initializer for the bias vector
          (see [initializers](../initializers.md)).
          Default: -2 constant.
          
      kernel_regularizer: Regularizer function applied to
          the `kernel` weights matrix
          (see [regularizer](../regularizers.md)).
          
      transform_regularizer: Regularizer function applied to
          the `transform` weights matrix
          (see [regularizer](../regularizers.md)).
          
      bias_regularizer: Regularizer function applied to the bias vector
          (see [regularizer](../regularizers.md)).
          
      transform_bias_regularizer: Regularizer function applied to the transform bias vector
          (see [regularizer](../regularizers.md)).
          
      kernel_constraint: Constraint function applied to
          the `kernel` weights matrix
          (see [constraints](../constraints.md)).
          
      bias_constraint: Constraint function applied to the bias vector
          (see [constraints](../constraints.md)).
          
  # Input shape
      2D/3D tensor with shape: `(nb_samples, input_dim)` / `(nb_samples, timesteps, input_dim)`.
  # Output shape
      2D/3D tensor with shape: `(nb_samples, units)` / `(nb_samples, timesteps, units)`..
  # References
      - [Highway Networks](http://arxiv.org/pdf/1505.00387v2.pdf)
  """

  def __init__(self,
               units,
               batch_norm=True,
               activation='relu',               
               transform_activation='sigmoid',
               kernel_initializer='glorot_uniform',
               transform_initializer='glorot_uniform',
               bias_initializer='zeros',
               transform_bias_initializer=BIAS_CONSTANT, #init gate bias with negative so to force carry behaviour
               kernel_regularizer=None,
               transform_regularizer=None,
               bias_regularizer=None,
               transform_bias_regularizer=None,
               kernel_constraint=None,
               bias_constraint=None,
               version='',
               **kwargs):
    print("WARNING: `GatedDense` is deprecated. Please use `MultiGatedUnit(Dense())`.")
    self.units = units
    self.__version__ = __VER__
    self.version = self.__version__
    self.batch_norm = batch_norm
    self.activation = activations.get(activation)
    self.transform_activation = activations.get(transform_activation)

    self.kernel_initializer = initializers.get(kernel_initializer)
    self.transform_initializer = initializers.get(transform_initializer)
    self.bias_initializer = initializers.get(bias_initializer)
    if isinstance(transform_bias_initializer, int) or isinstance(transform_bias_initializer, float):
      self.transform_bias_initializer = Constant(value=transform_bias_initializer)
    else:
      self.transform_bias_initializer = initializers.get(transform_bias_initializer)

    self.kernel_regularizer = regularizers.get(kernel_regularizer)
    self.transform_regularizer = regularizers.get(transform_regularizer)
    self.bias_regularizer = regularizers.get(bias_regularizer)
    self.transform_bias_regularizer = regularizers.get(transform_bias_regularizer)

    self.kernel_constraint = constraints.get(kernel_constraint)
    self.bias_constraint = constraints.get(bias_constraint)
    if version == '':
      if transform_bias_initializer != BIAS_CONSTANT:
        print("GatedDense ver. {} initializing from a previous (0.9.8) saved instance.".format(self.__version__))
      else:
        print("GatedDense ver. {} initializing (default).".format(self.__version__))
    else:
      print("GatedDense ver. {} initializing from {} saved instance.".format(
          self.version, version))
    super(GatedDense, self).__init__(**kwargs)

  def build(self, input_shape):
    #assert len(input_shape) == 2
    input_dim = int(input_shape[-1])
    
    self.W = self.add_weight(shape=(input_dim, self.units),
                             name='{}_W'.format(self.name),
                             trainable=True,
                             initializer=self.kernel_initializer,
                             regularizer=self.kernel_regularizer,
                             constraint=self.kernel_constraint)
    
    self.W_transform = self.add_weight(shape=(input_dim, self.units),
                                       name='{}_W_transform'.format(self.name),
                                       trainable=True,
                                       initializer=self.transform_initializer,
                                       regularizer=self.transform_regularizer,
                                       constraint=self.kernel_constraint)

    self.input_transform = self.add_weight(shape=(input_dim, self.units),
                                           name='{}_input_transform'.format(self.name),
                                           trainable=True,
                                           initializer=self.transform_initializer,
                                           regularizer=self.transform_regularizer,
                                           constraint=self.kernel_constraint)

    self.bias = self.add_weight(shape=(self.units,),
                                name='{}_bias'.format(self.name),
                                trainable=True,
                                initializer=self.bias_initializer,
                                regularizer=self.bias_regularizer,
                                constraint=self.bias_constraint)

    self.bias_transform = self.add_weight(shape=(self.units,),
                                          name='{}_bias_transform'.format(self.name),
                                          trainable=True,
                                          initializer=self.transform_bias_initializer,
                                          regularizer=self.transform_bias_regularizer)
    
    if self.batch_norm:
      self.bn_layer = tf.keras.layers.BatchNormalization(name='bn_gated')
      bn_shape = input_shape.as_list()[:-1] + [self.units]
      self.bn_layer.build(bn_shape)
     
    super(GatedDense, self).build(input_shape)

  def call(self, x, mask=None):
    x_linear = K.dot(x, self.W) + self.bias
    if self.batch_norm:
      x_linear = self.bn_layer(x_linear) # will use `learning_phase`
    x_h = self.activation(x_linear)
    x_trans = self.transform_activation(K.dot(x, self.W_transform) + self.bias_transform)
    x_inp_trans = K.dot(x, self.input_transform)
    output = x_trans * x_h  + (1 - x_trans) * x_inp_trans
    return output
  
  def compute_output_shape(self, input_shape):
    return (input_shape[0:-1] + [self.units])  

  def get_config(self):
    config = {
        'units' : self.units,
        'activation': activations.serialize(self.activation),
        'transform_activation': activations.serialize(self.transform_activation),
        'kernel_initializer': initializers.serialize(self.kernel_initializer),
        'transform_initializer': initializers.serialize(self.transform_initializer),
        'bias_initializer': initializers.serialize(self.bias_initializer),
        'transform_bias_initializer': initializers.serialize(self.transform_bias_initializer),
        'kernel_regularizer': regularizers.serialize(self.kernel_regularizer),
        'transform_regularizer': regularizers.serialize(self.transform_regularizer),
        'bias_regularizer': regularizers.serialize(self.bias_regularizer),
        'transform_bias_regularizer': regularizers.serialize(self.transform_bias_regularizer),
        'kernel_constraint': constraints.serialize(self.kernel_constraint),
        'bias_constraint': constraints.serialize(self.bias_constraint),
        'version' : self.version,
        }
    base_config = super(GatedDense, self).get_config()
    cfg =  dict(list(base_config.items()) + list(config.items()))
    return cfg



if __name__ == '__main__':
  import numpy as np
  TEST_GATED = True

  
  if TEST_GATED:
    x = np.random.rand(2, 4, 32)
    print("x {}:\n{}".format(x.shape,x))
    
    tf_inp = tf.keras.layers.Input(x.shape[1:])
    gd_layer = GatedDense(units=8)
    tf_x = gd_layer(tf_inp)
    tf_out = tf_x
    m = tf.keras.models.Model(inputs=tf_inp, outputs=tf_out)
    y1 = m.predict(x)
    print(tf_x)
    print("y {}: {} \n{}".format(y1.shape,y1.sum(),y1))
    m.summary()
    if True:
      print("Saving..")
      m.save("gated_save_test.h5")
      
    print("\n\nLoading model...")
    m2 = tf.keras.models.load_model("gated_save_test.h5",
                                    custom_objects={'GatedDense':GatedDense})
    m2.summary()
    y2 = m2.predict(x)
    print("y {}: {}".format(y2.shape,y2.sum()))
    print("ok" if y1.sum()==y2.sum() else "NOT OK! {}".format(y1.sum()-y2.sum()))
    
    if False:
      print("Saving weights...")
      m.save_weights('gated_save_test_weights.h5')
    
      tf_inp = tf.keras.layers.Input(x.shape[1:])
      gd_layer = GatedDense(units=8)
      tf_x = gd_layer(tf_inp)
      tf_out = tf_x
      m3 = tf.keras.models.Model(inputs=tf_inp, outputs=tf_out)
    
      print("\n\nLoading weights...")
      m3.load_weights('gated_save_test_weights.h5')
      y3 = m3.predict(x)
      print("y {}: {}".format(y3.shape,y3.sum()))
      print("ok" if y2.sum()==y3.sum() else "NOT OK! {}".format(y2.sum()-y3.sum()))
    
    if False:  
      import os
      os.remove("gated_save_test.h5")
      os.remove('gated_save_test_weights.h5')
    

    
    
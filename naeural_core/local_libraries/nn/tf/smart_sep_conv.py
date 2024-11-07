import tensorflow as tf
from tensorflow.keras import initializers, activations
from tensorflow.python.keras.applications import imagenet_utils

CONV_KERNEL_INITIALIZER = {
    'class_name': 'VarianceScaling',
    'config': {
        'scale': 2.0,
        'mode': 'fan_out',
        'distribution': 'truncated_normal'
    }
}

class SmartSeparableConv2D(tf.keras.layers.Layer):
  def __init__(self,
               filters,
               dw_kernel_size,
               dw_strides,
               activation,
               expand_ratio=0,
               se_ratio=0.25,
               kernel_initializer=CONV_KERNEL_INITIALIZER,
               use_pointwise_batch_norm=True,
               **kwargs):
    super(SmartSeparableConv2D, self).__init__(**kwargs)    
    assert se_ratio > 0 and se_ratio <= 1
    
    if kernel_initializer != CONV_KERNEL_INITIALIZER:
      kernel_initializer = initializers.get(kernel_initializer)
      
    self.filters = filters
    self.dw_kernel_size = dw_kernel_size
    self.dw_strides = dw_strides
    self.expand_ratio = expand_ratio
    self.se_ratio = se_ratio
    self.activation = activations.get(activation)
    self.kernel_initializer = kernel_initializer
    self.use_pointwise_batch_norm = use_pointwise_batch_norm
    
  def _correct_pad(self, input_shape, kernel_size):
    """Returns a tuple for zero-padding for 2D convolution with downsampling.
      Arguments:
      inputs: Input tensor.
      kernel_size: An integer or tuple/list of 2 integers.
    
      Returns:
        A tuple.
    """
    input_size = input_shape[1:3]
    if isinstance(kernel_size, int):
      kernel_size = (kernel_size, kernel_size)
    if input_size[0] is None:
      adjust = (1, 1)
    else:
      adjust = (1 - input_size[0] % 2, 1 - input_size[1] % 2)
    correct = (kernel_size[0] // 2, kernel_size[1] // 2)
    return ((correct[0] - adjust[0], correct[0]),
            (correct[1] - adjust[1], correct[1]))
  
  def build(self, input_shape):
    filters_in  = int(input_shape[-1])    
    filters_se  = max(1, int(filters_in * self.se_ratio))
    
    #expand component layers
    if self.expand_ratio > 0:
      filters_in = filters_in * self.expand_ratio
      self.exp_conv   = tf.keras.layers.Conv2D(filters=filters_in,
                                               kernel_size=1,
                                               padding='same',
                                               use_bias=False,
                                               kernel_initializer=self.kernel_initializer,
                                               name=self.name + 'expand_conv')
      self.exp_bn     = tf.keras.layers.BatchNormalization(name=self.name + 'expand_bn')
      self.exp_act    = tf.keras.layers.Activation(self.activation, name=self.name + 'expand_act')    
    #endif
    
    #depthwise component layers
    dw_padding = 'same'
    if self.dw_strides == 2:
      self.dwconv_pad = tf.keras.layers.ZeroPadding2D(padding=self._correct_pad(input_shape, self.dw_kernel_size),
                                                      name=self.name + 'dwconv_pad')
      dw_padding = 'valid'
    #endif
    self.dwconv     = tf.keras.layers.DepthwiseConv2D(kernel_size=self.dw_kernel_size,
                                                      strides=self.dw_strides,
                                                      padding=dw_padding,
                                                      use_bias=False,
                                                      depthwise_initializer=self.kernel_initializer,
                                                      name=self.name + 'dwconv')
    self.dwconv_bn  = tf.keras.layers.BatchNormalization(name=self.name + 'bn')
    self.dwconv_act = tf.keras.layers.Activation(self.activation, name=self.name + 'act')
    
    #squeeze-excite component
    self.se_gap     = tf.keras.layers.GlobalAveragePooling2D(name=self.name + 'se_squeeze')
    self.se_res     = tf.keras.layers.Reshape(target_shape=(1, 1, filters_in), 
                                              name=self.name + 'se_reshape')
    self.se_reduce  = tf.keras.layers.Conv2D(filters=filters_se,
                                             kernel_size=1,
                                             padding='same',
                                             kernel_initializer=self.kernel_initializer,
                                             activation=self.activation,
                                             name=self.name + 'se_reduce')
    self.se_expand  = tf.keras.layers.Conv2D(filters=filters_in,
                                             kernel_size=1,
                                             padding='same',
                                             kernel_initializer=self.kernel_initializer,
                                             activation='sigmoid',
                                             name=self.name + 'se_expand')
    self.se_excite  = tf.keras.layers.Multiply(name=self.name + 'se_excite')
    
    #pointwise component
    self.pw_conv    = tf.keras.layers.Conv2D(filters=self.filters,
                                             kernel_size=1,
                                             padding='same',
                                             use_bias=False,
                                             kernel_initializer=self.kernel_initializer,
                                             name=self.name + 'project_conv')
    if self.use_pointwise_batch_norm:
      self.pw_bn      = tf.keras.layers.BatchNormalization(name=self.name + 'project_bn')
    
    super(SmartSeparableConv2D, self).build(input_shape)
    
  def call(self, inputs):
    tf_x  = inputs
    if self.expand_ratio > 0:
      tf_x = self.exp_conv(tf_x)
      tf_x = self.exp_bn(tf_x)
      tf_x = self.exp_act(tf_x)
    if self.dw_strides > 1:
      tf_x = self.dwconv_pad(tf_x)
    tf_x  = self.dwconv(tf_x)
    tf_x  = self.dwconv_bn(tf_x)
    tf_x  = self.dwconv_act(tf_x)
    tf_se = self.se_gap(tf_x)
    tf_se = self.se_res(tf_se)
    tf_se = self.se_reduce(tf_se)
    tf_se = self.se_expand(tf_se)
    tf_x  = self.se_excite([tf_x, tf_se])
    tf_x  = self.pw_conv(tf_x)
    if self.use_pointwise_batch_norm:
      tf_x  = self.pw_bn(tf_x)
    tf_out = tf_x
    return tf_out
  
  def get_config(self):
    config = {
        'activation'              : self.activation,
        'kernel_initializer'      : self.kernel_initializer,
        'filters'                 : self.filters,
        'dw_kernel_size'          : self.dw_kernel_size,
        'dw_strides'              : self.dw_strides,
        'expand_ratio'            : self.expand_ratio,
        'se_ratio'                : self.se_ratio,
        'use_pointwise_batch_norm': self.use_pointwise_batch_norm
        }
    base_config = super(SmartSeparableConv2D, self).get_config()
    cfg =  dict(list(base_config.items()) + list(config.items()))
    return cfg   

  def model(self):
    x = tf.keras.layers.Input(shape=(100, 200, 3), name='inp')
    model = tf.keras.models.Model(inputs=[x], outputs=self.call(x))
    return model
    
if __name__ == '__main__':
  tf_inp = tf.keras.layers.Input(shape=(300, 300, 3), name='inp')
  tf_x = tf_inp
  tf_x = SmartSeparableConv2D(filters=32, 
                              dw_kernel_size=3, 
                              dw_strides=2,
                              expand_ratio=6,
                              activation='relu',
                              name='ssc2d')
  # model = tf.keras.models.Model(tf_inp, tf_x)
  # model.summary()
  
  tf_x.build(input_shape=(None, 100, 200, 3))
  tf_x.model().summary()
  
  
  
  
  
  
  
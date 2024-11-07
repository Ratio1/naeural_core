from functools import partial
from collections import OrderedDict
import numpy as np

import tensorflow as tf
import tensorflow.keras.backend as K
import pickle

from tensorflow.keras import initializers, regularizers, constraints

VER = '0.7.5'


class SinCosEncodingLayer(tf.keras.layers.Layer):

  def __init__(self, nr_classes, **kwargs):
    super(SinCosEncodingLayer, self).__init__(**kwargs)
    self.nr_classes = nr_classes
    return

  def call(self, inputs):
    tf_sin_enc = tf.expand_dims(
      tf.keras.backend.sin(2 * np.pi * inputs / self.nr_classes),
      -1
    )

    tf_cos_enc = tf.expand_dims(
      tf.keras.backend.cos(2 * np.pi * inputs / self.nr_classes),
      -1
    )

    tf_x = tf.keras.layers.concatenate([tf_sin_enc, tf_cos_enc])
    return tf_x


  def get_config(self):
    config = {
        'nr_classes' : self.nr_classes,
        }
    base_config = super(SinCosEncodingLayer, self).get_config()
    cfg = {**base_config, **config}
    return cfg


class RepeatElements(tf.keras.layers.Layer):
  def __init__(self, rep, axis, **kwargs):
    super(RepeatElements, self).__init__(**kwargs)
    self.rep = rep
    self.axis = axis
    return

  # def compute_output_shape(self, input_shape):
  #   input_shape = tensor_shape.TensorShape(input_shape).as_list()
  #   return tensor_shape.TensorShape([input_shape[0], self.n, input_shape[1]])

  def call(self, inputs):
    return K.repeat_elements(inputs, self.rep, self.axis)

  def get_config(self):
    config = {
        'rep' : self.rep,
        'axis': self.axis
        }
    base_config = super(RepeatElements, self).get_config()
    cfg = {**base_config, **config}
    return cfg


class SplitLayer(tf.keras.layers.Layer):
  def __init__(self, num_or_size_splits, axis=0, **kwargs):
    super(SplitLayer, self).__init__(**kwargs)
    self.num_or_size_splits = num_or_size_splits
    self.axis = axis
    return

  def call(self, inputs):
    return tf.split(
      inputs,
      num_or_size_splits=self.num_or_size_splits,
      axis=self.axis
    )

  def get_config(self):
    config = {
        'num_or_size_splits' : self.num_or_size_splits,
        'axis': self.axis
        }
    base_config = super(SplitLayer, self).get_config()
    cfg = {**base_config, **config}
    return cfg


class OneHotLayer(tf.keras.layers.Layer):

  def __init__(self, units, **kwargs):
    self.units = units
    super(OneHotLayer, self).__init__(**kwargs)

  def call(self, inputs):
    x = tf.keras.backend.one_hot(inputs, num_classes=self.units)
    return x

  def get_config(self):
    config = {
        'units' : self.units,
        }
    base_config = super(OneHotLayer, self).get_config()
    cfg = {**base_config, **config}
    return cfg
  
  
class SqueezeLayer(tf.keras.layers.Layer):

  def __init__(self, axis, **kwargs):
    self.axis = axis
    super(SqueezeLayer, self).__init__(**kwargs)

  def call(self, inputs):
    x = tf.keras.backend.squeeze(inputs, axis=self.axis)
    return x

  def get_config(self):
    config = {
        'axis' : self.axis,
        }
    base_config = super(SqueezeLayer, self).get_config()
    cfg = {**base_config, **config}
    return cfg
  
  
class SliceAxis1Layer(tf.keras.layers.Layer):
  def __init__(self, pos, **kwargs):
    self.pos = pos
    super(SliceAxis1Layer, self).__init__(**kwargs)
    
    
  def call(self, inputs):
    x = inputs[:, self.pos]
    return x
  
  
  def get_config(self):
    config = {
        'pos': self.pos,
        }
    base_config = super(SliceAxis1Layer, self).get_config()
    cfg = {**base_config, **config}
    return cfg
  

class SliceLayer(tf.keras.layers.Layer):
  """
   this layer is a universal slicer as it employs tf.gather and determines at 
   runtime the dimension of `None` declared tensors
  """
  def __init__(self, pos, axis, keepdims=False, **kwargs):
    self.pos = pos
    self.axis = axis
    self.keepdims = keepdims
    super(SliceLayer, self).__init__(**kwargs)
    
  def compute_output_shape(self, input_shape):
    output_shape = list(input_shape)
    if self.keepdims:
      output_shape[self.axis] = 1
    else:
      del output_shape[self.axis]
    return output_shape
    
  def call(self, inputs):
    pos = self.pos

    if not isinstance(pos, list):
      if pos < 0:
        shape = tf.shape(inputs)[self.axis]
        pos = shape + self.pos

      if self.keepdims:
        pos = [pos]
    #endif

    x = tf.gather(inputs, indices=pos, axis=self.axis)
    return x
  
  
  def get_config(self):
    config = {
        'pos': self.pos,
        'axis' : self.axis,
        'keepdims' : self.keepdims
        }
    base_config = super(SliceLayer, self).get_config()
    cfg = {**base_config, **config}
    return cfg


class SampledSoftmax(tf.keras.layers.Layer):
  def __init__(self, num_classes, num_sampled=100, num_true=1, 
               kernel_initializer='glorot_uniform',
               bias_initializer='zeros',
               kernel_regularizer=None,
               bias_regularizer=None,
               kernel_constraint=None,
               bias_constraint=None,
               **kwargs):
    self.num_sampled = num_sampled
    self.num_classes = num_classes
    self.num_true = num_true
    
    self.kernel_initializer = initializers.get(kernel_initializer)
    self.bias_initializer = initializers.get(bias_initializer)
    
    self.kernel_regularizer = regularizers.get(kernel_regularizer)
    self.bias_regularizer = regularizers.get(bias_regularizer)
    
    self.kernel_constraint = constraints.get(kernel_constraint)
    self.bias_constraint = constraints.get(bias_constraint)
    
    super(SampledSoftmax, self).__init__(**kwargs)

  def build(self, input_shape):
    dense_shape, _ = input_shape
    self.kernel = self.add_weight(name='kernel',
                                  shape=(self.num_classes, dense_shape[1]),
                                  trainable=True,
                                  initializer=self.kernel_initializer,
                                  regularizer=self.kernel_regularizer,
                                  constraint=self.kernel_constraint)
    self.bias = self.add_weight(name='bias',
                                shape=(self.num_classes,),
                                trainable=True,
                                initializer=self.bias_initializer,
                                regularizer=self.bias_regularizer,
                                constraint=self.bias_constraint)

    super(SampledSoftmax, self).build(input_shape)

  def call(self, inputs_and_labels, training=True):
      inputs, labels = inputs_and_labels

      if training:
        return tf.nn.sampled_softmax_loss(
                 weights=self.kernel,
                 biases=self.bias,
                 labels=labels,
                 inputs=inputs,
                 num_sampled=self.num_sampled,
                 num_classes=self.num_classes,
                 num_true=self.num_true)
      else:
        logits = tf.matmul(inputs, tf.transpose(self.kernel))
        logits = tf.nn.bias_add(logits, self.bias)
        labels_one_hot = tf.one_hot(labels, self.num_classes)
        loss = tf.nn.softmax_cross_entropy_with_logits(
            labels=labels_one_hot,
            logits=logits)
        return [loss, logits]
        
  
  def get_config(self):
    config = {
        'num_sampled' : self.num_sampled,
        'num_classes' : self.num_classes,
        'num_true' : self.num_true,
        'kernel_initializer': initializers.serialize(self.kernel_initializer),
        'bias_initializer': initializers.serialize(self.bias_initializer),
        'kernel_regularizer': regularizers.serialize(self.kernel_regularizer),
        'bias_regularizer': regularizers.serialize(self.bias_regularizer),
        'kernel_constraint': constraints.serialize(self.kernel_constraint),
        'bias_constraint': constraints.serialize(self.bias_constraint),
        }
    base_config = super(SampledSoftmax, self).get_config()
    cfg = {**base_config, **config}
    return cfg




class InputTensorsPreparator:
  """
  An alternative to prepare_input_tensors function.
  """
  EMBEDDING = 'Embedding'
  ONEHOT = 'OneHotLayer'
  SINCOS = 'SinCosEncodingLayer'

  def __init__(self, log):
    self.logger = log
    self.EmbLayers = {}
    self.InputTensors = {}

    self.prepare_calls = 0
    return
  
  @staticmethod
  def _convert_shape(shape):
    new_shape = []
    for dim in shape:
      if dim <= -1: dim = None
      new_shape.append(dim)
    return tuple(new_shape)

  @staticmethod
  def _get_dtype(input_name, config_embeddings):
    for emb in config_embeddings:
      if input_name == emb['CONNECTED_TO']:
        mode = InputTensorsPreparator._check_mode(emb['MODE'])
        if mode[0] in [InputTensorsPreparator.EMBEDDING, InputTensorsPreparator.ONEHOT]:
          return tf.int32

    return tf.float32


  @staticmethod
  def _check_is_inp_for_embedding(input_name, config_embeddings):
    for emb in config_embeddings:
      if input_name == emb['CONNECTED_TO']:
        return True
    return False

  @staticmethod
  def _get_dict_key_index(dct, key):
    return list(dct).index(key)

  @staticmethod
  def _check_mode(mode):
    assert ',' in mode
    mode = mode.split(',')

    if mode[0] == InputTensorsPreparator.EMBEDDING:
      assert len(mode) == 3
    if mode[0] in [InputTensorsPreparator.ONEHOT, InputTensorsPreparator.SINCOS]:
      assert len(mode) == 2

    return mode
    
  def prepare(self, config_inputs, config_embeddings=None):
    """
    this function returns the model inputs and the post-inputs concatenated
    tensor with all the pre-req for each individual input (one-hots, embeddings etc.)
    
    inputs:
      config_inputs: list that contains definition of graph inputs where each element is a 
                     different tensor (in the order of the actual inputs) represented by a dict
                     with the folllowing possible keys:
                       - "NAME"
                       - "SHAPE" / "BATCH_SHAPE": specified as a list, in which -1 elements
                                                  will be converted to None
                       - "IS_FEEDABLE": [optional] whether the tensor will be fed with data or not
                       
      config_embeddings: list that contains the definition of the embeddings. Each element is a different layer
                         represented by a dict with the following possible keys:
                           - "CONNECTED_TO": the name of the input to which is connected
                           - "NAME" : [optional]. If not specified the name of the emb layer will be
                                      `config_embeddings['CONNECTED_TO'] + '_emb'`
                           - "MODE" :
                              - if embeddings: 'Embedding,<input_dim>,<output_dim>';
                              - if one-hot   : 'OneHotLayer,<nr_classes>';
                              - if sincos    : 'SinCosEncodingLayer,<nr_classes>';

                           - "TRAINABLE": [optional] [used only for embeddings].
                                          Whether the embeddings matrix is trainable or not
                           - "EMB_MATRIX_PATH": [optional] [used only for embeddings].
                                                Path to pre-trained embeddings matrix
                           - "USE_DRIVE": [optional] whether to load the embeddings matrix from the '_data' folder or not
                           - "SQUEEZE_AXIS": [optional] specified if it is necessary to squeeze the tensor
                                             after the encoding
                           - "REUSE": [optional] the name of reused embedding layer. It is used manily when the
                                      architecture is more complex (encoder-decoder, for example)
                           - "BOTTLENECK": [optional] the number of units of the Dense layer that bottlenecks the embedding
    """
    
    all_feats = []
    self.prepare_calls += 1
    if config_embeddings is None:
      config_embeddings = []
    
    ################## INPUTS PROCESSING ##################
    crt_inputs = OrderedDict()
    for _idx, cfg_inp in enumerate(config_inputs):
      name = cfg_inp['NAME']
      is_feedable = cfg_inp.get('IS_FEEDABLE', True)
      dtype = self._get_dtype(name, config_embeddings)
      is_input_for_emb = self._check_is_inp_for_embedding(name, config_embeddings)

      has_batch_shape = 'BATCH_SHAPE' in cfg_inp
      if has_batch_shape:
        tf_x = tf.keras.layers.Input(
          name=name, dtype=dtype,
          batch_shape=self._convert_shape(cfg_inp['BATCH_SHAPE']),
        )
      else:
        tf_x = tf.keras.layers.Input(
          name=name, dtype=dtype,
          shape=self._convert_shape(cfg_inp['SHAPE']),
        )
      #endif
      
      crt_inputs[name] = tf_x
      
      if is_feedable:
        k = len(self.InputTensors) + 1
        self.InputTensors[k] = tf_x
      
      if not is_input_for_emb:
        all_feats.append((_idx,tf_x))
    #endfor
    
    ################## EMBEDDINGS PROCESSING ##################


    for cfg_emb in config_embeddings:
      weights = None
      connected_to = cfg_emb['CONNECTED_TO']
      trainable = cfg_emb.get('TRAINABLE', True)
      squeeze_axis = cfg_emb.get('SQUEEZE_AXIS', None)
      bottleneck_units = cfg_emb.get('BOTTLENECK', None)
      reuse = cfg_emb.get('REUSE', None)
      bool_reuse = not ((reuse is None) or (reuse == ''))

      _idx = self._get_dict_key_index(dct=crt_inputs, key=connected_to)
      mode = self._check_mode(cfg_emb['MODE'])

      name = cfg_emb.get('NAME', None)

      if name is None:
        if mode[0] == InputTensorsPreparator.EMBEDDING:
          name = connected_to + '_emb_{}_{}'.format(mode[1], mode[2])
        elif mode[0] == InputTensorsPreparator.ONEHOT:
          name = connected_to + '_oh_{}'.format(mode[1])
        else: #mode[0] == InputTensorsPreparator.SINCOS
          name = connected_to + '_sincos_{}'.format(mode[1])
      #endif

      lyr_name = name.lower()
  
      if 'EMB_MATRIX_PATH' in cfg_emb and mode[0] == InputTensorsPreparator.EMBEDDING:
        if cfg_emb['EMB_MATRIX_PATH'] != '':
          path = cfg_emb['EMB_MATRIX_PATH']
          use_drive = cfg_emb.get('USE_DRIVE', False)
          if use_drive:
            path = self.logger.get_data_file(path)

          weights = np.load(path)
          assert len(weights.shape) == 2
        #endif
      #endif
      
      if not bool_reuse:
        if mode[0] == InputTensorsPreparator.EMBEDDING:
          if weights is None:
            Emb = tf.keras.layers.Embedding(
              input_dim=int(mode[1]), output_dim=int(mode[2]),
              trainable=trainable, name=lyr_name
            )
          else:
            initializer = tf.keras.initializers.Constant(weights)
            Emb = tf.keras.layers.Embedding(
              input_dim=int(mode[1]), output_dim=int(mode[2]),
              embeddings_initializer=initializer,
              trainable=trainable, name=lyr_name
            )
        elif mode[0] == InputTensorsPreparator.ONEHOT:
          Emb = OneHotLayer(units=int(mode[1]), name=lyr_name)
        else:  # mode[0] == InputTensorsPreparator.SINCOS
          Emb = SinCosEncodingLayer(nr_classes=int(mode[1]), name=lyr_name)
        
        tf_emb = Emb(crt_inputs[connected_to])
        self.EmbLayers[name] = Emb
      else:
        Emb = self.EmbLayers[reuse]
        lyr_name = Emb.name
        tf_emb = Emb(crt_inputs[connected_to])
      #endif

      if squeeze_axis is not None:
        tf_emb = SqueezeLayer(axis=squeeze_axis, name=lyr_name+'_sqz_{}'.format(self.prepare_calls))(tf_emb)
      #endif

      if bottleneck_units is not None:
        tf_emb = tf.keras.layers.Dense(
          units=bottleneck_units,
          name=lyr_name+'_bneck{}'.format(bottleneck_units),
          use_bias=False
        )(tf_emb)
      #endif

      all_feats.append((_idx, tf_emb))
    #endfor


    all_feats = sorted(all_feats, key=lambda x: x[0])
    all_feats = list(zip(*all_feats))
    all_feats = list(all_feats[1])

    return list(crt_inputs.values()), all_feats


def prepare_input_tensors(log, lst_inputs, oh_bneck=None, DEBUG=False):
  """
  this function returns the model inputs and the post-inputs concatenated
  tensor with all the pre-req for each individual input (one-hots, etc)
  
  lst_inputs: list that contains definition of graph inputs where each element is a 
  different tensor (in the order of the actual inputs) represented by a dict where 
  and each key defines the name, tf dtype, number of classes if one-hot is required. 
    For example lets suppose we have three different inputs in the following order: 
        ID that will be embedding encoded (by default if no number of classes
                                           is provided 'EMB_SIZE' tuple is provided), 
        LOCATION that will be one-hot encoded
        VALUE that is a simple float
            lst_inputs = [
                            {
                                'NAME': 'ID',
                                'SHAPE' : [-1] # -1 means None
                                'DTYPE':tf.int32 # this can also be a string such as "int32"
                                'EMB_SIZE': (500, 16),
                            },
                            {
                                'NAME': 'LOCATION',
                                'SHAPE' : [-1, 1] # (None, 1)
                                'DTYPE': tf.int32,
                                'NUM_CLASSES': 30,
                            },
                            {
                                'NAME': 'VALUE',
                                'SHAPE' : [-1, 2] # (None, 2)
                                'DTYPE': tf.float32,
                            } 
                          ]
            
        oh_bneck : (None) if a number it will put a bottleneck dense(units=oh_bneck) after each one-hot encoder
            
    Returns:
      tuple (inputs, post_inputs) the list of input tensors and the post_input ones
      
      
      
  shared-embeddings inputs are handled in InputTensorsPreparator
  """
  if (log is None) or not hasattr(log, '_logger'):
    raise ValueError("Loggger object is invalid: {}".format(log))

  input_list = []
  post_input_tensors = []
  if oh_bneck is not None:
    if type(oh_bneck) is not int or oh_bneck <= 0:
      raise ValueError("One-hot bottleneck param `ohb` must be positive integer. Received {}".format(oh_bneck))
  for dct_inp in lst_inputs:
    inp_type = dct_inp['DTYPE']
    inp = dct_inp['NAME']
    inp_shape = dct_inp['SHAPE']
    inp_tuple = ()
    for pos in inp_shape:
      inp_tuple += (None,) if pos == -1 else (pos,)
    if type(inp_type) is str:
      inp_type = tf.as_dtype(inp_type)
    last_pos_inp = inp_tuple[-1]
    inp_name = 'inp_'+inp
    if DEBUG:
      log.P("  prepare_input_tensors: name:{}  type:{}  shape:{}  ".format(
          inp_name, inp_type, inp_tuple))
    tf_inp = tf.keras.layers.Input(inp_tuple, dtype=inp_type, name=inp_name)
    input_list.append(tf_inp)
    tf_x = tf_inp
    if inp_type == tf.int32:
      if 'NUM_CLASSES' in dct_inp:
        num_cls = dct_inp['NUM_CLASSES']
        #tf.keras.layers.Lambda(function=lambda x: one_hot(x, num_classes=num_cls), name=inp_name+'_oh{}'.format(num_cls))(tf_x)
        tf_x = OneHotLayer(units=num_cls, name=inp_name+'_oh{}'.format(num_cls))(tf_x)
      elif 'EMB_SIZE' in dct_inp:
        vocab_size, emb_size = dct_inp['EMB_SIZE']
        if 'TRAINABLE' not in dct_inp:
          dct_inp['TRAINABLE'] = True
        if 'WEIGHTS' in dct_inp:
          with open(dct_inp['WEIGHTS'], 'rb') as f:
            weights = pickle.load(f)
            initializer = tf.keras.initializers.Constant(weights)
          l_emb = tf.keras.layers.Embedding(vocab_size, emb_size, embeddings_initializer=initializer, trainable=dct_inp['TRAINABLE'],
              name=inp_name+'_emb_{}_{}'.format(vocab_size, emb_size))
        else:
          l_emb = tf.keras.layers.Embedding(vocab_size, emb_size, trainable=dct_inp['TRAINABLE'],
              name=inp_name+'_emb_{}_{}'.format(vocab_size, emb_size))
        tf_x = l_emb(tf_x)
      else:
        raise ValueError('INT32 input received without classes/embeds definition')  
      if last_pos_inp == 1:
        # we should squeeze the singleton dim
        # tf.keras.layers.Lambda(function=lambda x: squeeze(x, axis=-2), name=inp_name+'_sqz')(tf_x)
        tf_x = SqueezeLayer(axis=-2, name=inp_name+'_sqz')(tf_x)
        if oh_bneck is not None:
          tf_x = tf.keras.layers.Dense(units=oh_bneck, 
                                       name=inp_name+'_oh_bneck{}'.format(oh_bneck),
                                       use_bias=False)(tf_x)
        
    post_input_tensors.append(tf_x)
        
  return input_list, post_input_tensors


def _ts_causal_conv_res_module_he(tf_x, kernel, filters, dilation, bn, activ, drop, bname, nr_reps=2):
  tf_skip = tf_x
  inp_filters = tf_x.shape.as_list()[-1]
  if filters != inp_filters:
    tf_skip = tf.keras.layers.Conv1D(filters, 
                                     kernel_size=1, 
                                     padding='causal',
                                     name=bname+'_ident')(tf_skip)
    tf_x = tf_skip

  recv_field = 0
  bname += '_d'+str(dilation)
  for L in range(1, nr_reps+1):
    if bn:
      tf_x = tf.keras.layers.BatchNormalization(name=bname+'_bnpre{}'.format(L))(tf_x)
    tf_x = tf.keras.layers.Activation(activ, name=bname+'_{}{}'.format(activ,L))(tf_x)
    if drop > 0:
      tf_x = tf.keras.layers.Dropout(rate=drop, name=bname+'_drp{}_{:.1f}'.format(L,drop))(tf_x)
    tf_x = tf.keras.layers.Conv1D(filters=filters,
                                  kernel_size=kernel,
                                  padding='causal',
                                  dilation_rate=dilation,
                                  name=bname+'_cnv{}'.format(L))(tf_x)
    recv_field += (kernel - 1) * dilation
  tf_out = tf.keras.layers.add([tf_skip, tf_x], name=bname+'_res_rf_{}'.format(recv_field))
  return tf_out, recv_field


def _ts_causal_conv_res_module_res(tf_x, kernel, filters, dilation, bn, activ, drop, bname, nr_reps=2):

  tf_skip = tf_x
  inp_filters = tf_x.shape.as_list()[-1]
  recv_field = 0
  if filters != inp_filters:
    tf_skip = tf.keras.layers.Conv1D(filters, 
                                     kernel_size=1, 
                                     padding='causal',
                                     name=bname+'_ident')(tf_skip)
  
  bname += '_d'+str(dilation)

  for L in range(1, nr_reps):
    tf_x = tf.keras.layers.Conv1D(filters=filters,
                                  kernel_size=kernel,
                                  padding='causal',
                                  dilation_rate=dilation,
                                  name=bname+'_cnv{}'.format(L))(tf_x)
    recv_field += (kernel - 1) * dilation
    if bn:
      tf_x = tf.keras.layers.BatchNormalization(name=bname+'_bnpre{}'.format(L))(tf_x)
    tf_x = tf.keras.layers.Activation(activ, name=bname+'_{}{}'.format(activ,L))(tf_x)
  
  tf_x = tf.keras.layers.Conv1D(filters=filters,
                                kernel_size=kernel,
                                padding='causal',
                                dilation_rate=dilation,
                                name=bname+'_cnv{}'.format(nr_reps))(tf_x)
  recv_field += (kernel - 1) * dilation
  if bn:
    tf_x = tf.keras.layers.BatchNormalization(name=bname+'_bnpre{}'.format(nr_reps))(tf_x)
    
  tf_x = tf.keras.layers.add([tf_skip, tf_x], name=bname+'_res')
  
  tf_x = tf.keras.layers.Activation(activ, name=bname+'_{}{}_rf_{}'.format(activ,'out',recv_field))(tf_x)
  
  if drop > 0:
    tf_x = tf.keras.layers.Dropout(rate=drop, name=bname+'_drp_{:.1f}'.format(drop))(tf_x)
  tf_out = tf_x  
  return tf_out, recv_field


def _ts_causal_conv_res_module_bai(tf_x, kernel, filters, dilation, bn, activ, drop, bname, nr_reps=2):
  tf_skip = tf_x
  inp_filters = tf_x.shape.as_list()[-1]
  recv_field = 0
  if filters != inp_filters:
    tf_skip = tf.keras.layers.Conv1D(filters, 
                                     kernel_size=1, 
                                     padding='causal',
                                     name=bname+'_ident')(tf_skip)
  
  bname += '_d'+str(dilation)
  for L in range(1, nr_reps+1):
    tf_x = tf.keras.layers.Conv1D(filters=filters,
                                  kernel_size=kernel,
                                  padding='causal',
                                  dilation_rate=dilation,
                                  name=bname+'_cnv{}'.format(L))(tf_x)
    recv_field += (kernel - 1) * dilation
    if bn:
      tf_x = tf.keras.layers.BatchNormalization(name=bname+'_bnpre{}'.format(L))(tf_x)
    tf_x = tf.keras.layers.Activation(activ, name=bname+'_{}{}'.format(activ,L))(tf_x)
    if drop > 0:
      tf_x = tf.keras.layers.Dropout(rate=drop, name=bname+'_drp{}_{:.1f}'.format(L,drop))(tf_x)
  
    
  tf_x = tf.keras.layers.add([tf_skip, tf_x], name=bname+'_res')
  
  tf_x = tf.keras.layers.Activation(activ, name=bname+'_{}{}_rf'.format(activ,'out',recv_field))(tf_x)
  
  if drop > 0:
    tf_x = tf.keras.layers.Dropout(rate=drop, name=bname+'_drp_{}'.format(drop))(tf_x)

  tf_out = tf_x  
  return tf_out, recv_field


def _ts_causal_conv_res_module(tf_x, 
                                        kernel, 
                                        filters, 
                                        dilation, 
                                        bn, 
                                        activ, 
                                        drop, 
                                        bname, 
                                        nr_reps,
                                        res,
                                        default_dilation=0,
                                        progressive_dilation=False,
                                        ):
  if default_dilation != 0:
    dilation = default_dilation
  
  if progressive_dilation:
    _pow = int(np.log2(dilation))
  recv_field = 0
  if res:
    tf_skip = tf_x
    inp_filters = tf_x.shape.as_list()[-1]
    if filters != inp_filters:
      tf_skip = tf.keras.layers.Conv1D(filters, 
                                       kernel_size=1, 
                                       padding='causal',
                                       name=bname+'_ident')(tf_skip)
  
  for L in range(1, nr_reps+1):
    if progressive_dilation:
      dilation = 2 ** _pow
      _pow += 1
    recv_field += (kernel - 1) * dilation
    tf_x = tf.keras.layers.Conv1D(filters=filters,
                                  kernel_size=kernel,
                                  padding='causal',
                                  dilation_rate=dilation,
                                  name=bname+'_cnv{}_d{}_rf{}'.format(L,dilation,recv_field))(tf_x)
    

    if bn:
      tf_x = tf.keras.layers.BatchNormalization(name=bname+'_bnpre{}'.format(L))(tf_x)
      
    if activ not in [None, '', 'none', 'linear']:
      tf_x = tf.keras.layers.Activation(activ, name=bname+'_{}{}'.format(activ,L))(tf_x)

    if drop > 0:
      tf_x = tf.keras.layers.Dropout(rate=drop, name=bname+'_drp{}_{:.1f}'.format(L,drop))(tf_x)
  
    
  if res:
    tf_x = tf.keras.layers.add([tf_skip, tf_x], name=bname+'_res')
  
  tf_out = tf_x  
  return tf_out, recv_field



dct_FUNCS = {
    'he' : _ts_causal_conv_res_module_he,
    'ba' : _ts_causal_conv_res_module_bai,
    'rs' : _ts_causal_conv_res_module_res,
    'Lf' : partial(_ts_causal_conv_res_module, res=True),
    'Lr' : partial(_ts_causal_conv_res_module, res=True, default_dilation=1),
    'Ld' : partial(_ts_causal_conv_res_module, res=False),
    'Lc' : partial(_ts_causal_conv_res_module, res=False, default_dilation=1),
    'Lp' : partial(_ts_causal_conv_res_module, res=True, progressive_dilation=1),
    }
  
def _ts_causal_conv_res_module(tf_x, kernel, filters, dilation, bn, activ, drop, bname, 
                               architecture='ba',
                               nr_reps=2):
  func = dct_FUNCS[architecture]
  tf_out, recv_field = func(tf_x, kernel, filters, dilation, bn, activ, drop, bname, nr_reps)
  return tf_out, recv_field
  


def timeseries_causal_conv_module(
                        log,
                        tf_input,
                        kernels=[  31,  7,    7,   365], 
                        filters=[ 512, 256,  512,  256,],
                        factors=[ 1.0, 1.0,  1.0,  1.0,],
                        depths= [   1,   6,   1,    1],
                        drops=  [   0,   0,   0,    0],
                        repeats=[   1,   2,   1,    1], # do not repeat many times!
                        bns=    [   0,   0,   0,    0], 
                        layouts=['Lc','Lf',  'Lc', 'Lc'],
                        activs=['selu', 'selu', 'selu', 'selu'], 
                        return_model=False,
                        skip=True,
                        name_suffix='',
                        DEBUG=False,
                        verbose=2,
                ):
  """
   Creates a causal convolution inception module with `len(kernels)` columns 
   for time-series modeling
   
   OBSERVATION: please run libraries/nn/utils.py `__main__` code to get some 
   intuition on the functionalities of this method
   
   inputs:
     
     `log`  :  Logger object
     
     `tf_input` : input tensor for the module
     
     `filters`: list of filters for each kernel default

     `factors`: the nr filters on each column should be increased with the depth
                (i.e. each residual block should have more filters than the previous one).
                This param specifies for each column the scale factor for the filters param.

                Could be:
                  * list - a factor for each column
                  * float - same factor is applied on all columns

     
     `kernels`: kernel size (temporal window) 
     
     `depths` : how many residual blocks per column
     
             `kernels=[  2,  3,  4,  4,  7,],`  31  / 61 / 91 / 373  days receptive fields
             `filters=[128,128,128,128,128,],` 
             `depths= [  4,  4,  4,  5,  5,],`
             `drops=  [0.2,0.2,0.2,0.2,0.2,],`
             `repeats=[  2,  2,  2,  2,  2,],`
     
     `repeats` : how many convolutions per residual block
     
     `layouts` : architecture definition of each column!
     
     `drops`  : dropout post activation for each column
     
     `bns` :  BatchNorms - True or False for each column     
           
     `activs` : activation defaults to `selu` for each column
     
     `skip` : skip from input

     `verbose` : int, optional
        Logging verbosity. Set 0 for no prints.
        Default is 1.

  returns : 
    
      concatenated tf tensor or the whole model
      
  comments:
    # k/d/r:rcv
    # 2/4/2:31, 2/5/2:63,
    # 3/3/2:29, 3/4/2:61, 
    # 4/4/2:91, 4/5/2:187,
    # 7/2/2:37, 7/3/2:85, 7/4/2:181, 7/5/2:373    
  """
  if DEBUG:
    if tf_input.name[:5].lower() != 'input':
      raise ValueError("In debug mode input tensor must me an actual 'Input' tensor. Received {}".format(
          tf_input.name))
  if type(layouts) not in [list, tuple]:
    raise ValueError("Param architecture [list] must define architecture for each column!")
  if type(activs) not in [list, tuple]:
    raise ValueError("Param activs [list] must define activation for each column!")

  if isinstance(factors, float):
    factors = [factors]
  elif isinstance(factors, (list, tuple)):
    pass
  else:
    raise ValueError("Param factors not defined correctly. Please provide `list` or `float`")


  _AVAIL_ARCHITECTURES = list(dct_FUNCS.keys())
  if (log is None) or not hasattr(log, '_logger'):
    raise ValueError("Loggger object is invalid: {}".format(log))
  if len(filters) < len(kernels):
    filters += [filters[-1]] * (len(kernels) - len(filters))
  if len(factors) < len(kernels):
    factors += [factors[-1]] * (len(kernels) - len(factors))
  if len(depths) < len(kernels):
    depths += [depths[-1]] * (len(kernels) - len(depths))
  if len(drops) < len(kernels):
    drops += [drops[-1]] * (len(kernels) - len(drops))
  if len(repeats) < len(kernels):
    repeats += [repeats[-1]] * (len(kernels) - len(repeats))
  if len(bns) < len(kernels):
    bns += [bns[-1]] * (len(kernels) - len(bns))
  if len(layouts) < len(kernels):
    layouts += [layouts[-1]] * (len(kernels) - len(layouts))
  if len(activs) < len(kernels):
    activs += [activs[-1]] * (len(kernels) - len(activs))

  suf = name_suffix
  outputs = []
  columns = []
  summary = []
  lst_receptive_fields = []
  s_recept=''

  if verbose >= 2:
    log.P("  Creating {}{} column Time Convolution Inception Module...".format(
        len(kernels),
        "+1 (skip)" if skip else " (no skip)"), color='y')
  #endif

  lst_par = []
  for i in range(len(kernels)):
    layout = layouts[i]
    s_basename = 'TC_{}'.format(layout)
    if layout not in _AVAIL_ARCHITECTURES:
      raise ValueError("Unknown residual module layout '{}'. Available designs are: {}".format(
          layout, _AVAIL_ARCHITECTURES))
    #endif

    f = filters[i]
    fact = factors[i]
    k = kernels[i]
    drop = drops[i]
    depth = depths[i]
    bn = bns[i]
    nr_reps = repeats[i]
    activ = activs[i]
    tf_x = tf_input
    receptive_field = 1
    s_col_name = '{}{}_k{}_f{}_Col{}'.format(suf, s_basename, k, f, i+1)
    columns.append(s_col_name)
    for j in range(depth):
      dilation = (2 ** j)
      bname = '{}_B{}'.format(s_col_name,j+1)
      #if nr_reps < 2 and architecture.lower() in ['res']:
      #  log.P("  Causal block '{}' must have at least 2 repeats. Increasing to 2.".format(nr_reps))
      #  nr_reps = 2
      tf_x, recv_field = _ts_causal_conv_res_module(
        tf_x=tf_x,
        kernel=k,
        filters=int(f * np.power(fact, j)),
        dilation=dilation,
        bn=bn,
        activ=activ,
        drop=drop,
        bname=bname,
        architecture=layout,
        nr_reps=nr_reps
      )
      receptive_field += recv_field
    #endfor - j in range(depth)

    lst_receptive_fields.append(receptive_field)
    outputs.append(tf_x)    
    n_par = 0

    if DEBUG:
      _m = tf.keras.models.Model(tf_input, tf_x, name='{}block_test'.format(suf))
      n_par = _m.count_params()
      lst_par.append(n_par)
    #endif

    summary.append("    Column '{}' recv field: {} @ {:,} params".format(s_col_name, receptive_field, n_par))
    s_recept += str(receptive_field)+'_'
  #endfor - i in range(len(kernels))
  
  if skip:
    outputs.append(tf_input)
  
  if len(outputs) > 1:
    tf_output = tf.keras.layers.concatenate(outputs, name='{}blk_out_rcf_{}'.format(
                                                  suf, 'skp_' if skip else '')+s_recept)
  else:
    tf_output = tf_x

  if verbose >= 2:
    for _msg in summary:
      log.P(_msg, color='y')

  if DEBUG:
    log.P("    Total {:,} parameters".format(sum(lst_par)), color='y')

  if return_model:
    s_model = 'Model_{}_{}_{}'.format(s_basename, len(kernels), s_recept[:-1])
    m = tf.keras.models.Model(inputs=tf_input, outputs=tf_output,
                              name=s_model)
    return m
  else:
    return tf_output, lst_receptive_fields, outputs
  
  

##### PLEASE PUT MAIN IN DIFFERENT SCRIPT AND IMPORT UTIL METHODS FROM THIS MODULE!
# if __name__ == '__main__':
#   import sys
#   sys.path.append("../")
#  # from naeural_core import Logger
#  # l = Logger(lib_name='LYRUTLS', config_file='libraries/config.txt')
#  # l.clear_all_results()
#   TEST_INPUTS = False
#   TEST_TC1 = False
#   TEST_TC2 = False
#   TEST_SLICE = False
#
#   import cloudpickle
#   with open('oh.dat', 'wb') as f:
#     cloudpickle.dump(OneHotLayer, f)
#
#   if TEST_SLICE:
#     a = np.arange(200).reshape(2,25,4)
#     pos = 0
#     tf_inp = tf.keras.layers.Input((None,4))
#     tf_x = SliceLayer(pos=pos,axis=1)(tf_inp)
#     m = tf.keras.models.Model(tf_inp, tf_x)
#     y = m.predict(a)
#     l.P("output:\n{}".format(y))
#     l.P("truth:\n{}".format(a[:,pos]))
#
#   if TEST_INPUTS:
#     import json
#     with open('inputs_json.txt', 'r') as f:
#       dict_inputs = json.load(f)
#     lst_inputs = dict_inputs['MODEL_INPUTS']
#     if True:
#       inputs, post_inputs = prepare_input_tensors(lst_inputs, DEBUG=True)
#     else:
#       inputs = [tf.keras.layers.Input((None,1), dtype=tf.as_dtype("int32")) for x in range(4)]
#       post_inputs1 = [tf.keras.layers.Embedding(100,16)(x) for x in inputs[:2]]
#       post_inputs2 = [tf.keras.layers.Lambda(lambda x: K.one_hot(x, num_classes=10))(x)
#                       for x in inputs[2:]]
#       post_inputs = post_inputs1 + post_inputs2
#       post_inputs = [tf.keras.layers.Lambda(lambda x: K.squeeze(x, axis=-2))(x)
#                       for x in post_inputs]
#
#     if len(post_inputs) > 1:
#       tf_x = tf.keras.layers.concatenate(post_inputs, name='concat_inputs')
#     else:
#       tf_x = post_inputs[0]
#     tf_out = tf.keras.layers.Dense(1)(tf_x)
#     model = tf.keras.models.Model(inputs=inputs, outputs=tf_out)
#     model.summary()
#     model.save("test_2nd")
#
#   if TEST_TC1:
#
#     tf_inp = tf.keras.layers.Input((10,1))
#     lyr_c1 = tf.keras.layers.Conv1D(filters=1,
#                                    kernel_size=3,
#                                    padding='causal',
#                                    use_bias=False)
#     lyr_c2 = tf.keras.layers.Conv1D(filters=1,
#                                    kernel_size=3,
#                                    padding='causal',
#                                    dilation_rate=2,
#                                    use_bias=False)
#     layers = [lyr_c1, lyr_c2]
#     tf_x1 = lyr_c1(tf_inp)
#     tf_x2 = lyr_c2(lyr_c1(tf_inp))
#     model1 = tf.keras.models.Model(tf_inp, tf_x1)
#     model2 = tf.keras.models.Model(tf_inp, tf_x2)
#     for layer in layers:
#       _w = layer.get_weights()[0]
#       layer.set_weights([np.ones(_w.shape)])
#     x = np.arange(10).reshape(1,10,1)
#     x[:,::3,:] = 0
#     y1 = model1.predict(x)
#     y2 = model2.predict(x)
#     l.P("Demonstrating temporal convolution with `ones` kernels: {}".format(
#       layer.get_weights()[0].tolist()))
#     l.P("Input:\n{}".format(x.ravel()))
#     l.P("First step: output of simple model (k=3):\n{}".format(y1.ravel()))
#     l.P("Second step: output of two lyrs (prev + second layer dilation=2):\n{}".format(y2.ravel()))
#
#   if TEST_TC2:
#
#     # 2/4/2:31, 2/5:63,
#     # 3/3/2:29, 3/4/2:61,
#     # 4/4/2:91, 4/5/2:187,
#     # 7/2/2:37, 7/3/1: 43,  7/3/2:85, 7/4/2:181, 7/5/2: 373
#     # 32/2/1:94
#     # 64/2/1: 190
#     # 128/2/1: 382
#     # 256/2/1: 766
#
#     GOOD = [
#               {
#                   "NAME" : "1x6_full_946",
#                   "PARAMS" :
#                         { # recv field: 946 @ 5,288,192 params
#                             "kernels" : [ 16,],
#                             "filters" : [256,],
#                             "depths"  : [  6,],
#                             "drops"   : [0.2,],
#                             "repeats" : [  1,],
#                             "layouts" : ['Lf'],
#                             "bns"     : [   0],
#                             "activs"  : ['selu'],
#                         },
#               },
#
#               {
#                   "NAME" : "1x2_full_382",
#                   "PARAMS" :
#                         { # recv field: 382 @ 8,719,616 params
#                             "kernels" : [128,],
#                             "filters" : [256,],
#                             "depths"  : [  2,],
#                             "drops"   : [0.2,],
#                             "repeats" : [  1,],
#                             "layouts" : ['Lf'],
#                             "bns"     : [   0],
#                             "activs"  : ['selu'],
#                         },
#               },
#
#
#
#               {
#                   "NAME" : "1x2_res_nd_31",
#                   "PARAMS" :
#                         { # recv field: 31 @ 1,092,864 params
#                             "kernels" : [ 16,],
#                             "filters" : [256,],
#                             "depths"  : [  2,],
#                             "drops"   : [0.2,],
#                             "repeats" : [  1,],
#                             "layouts" : ['Lr'],
#                             "bns"     : [   0],
#                             "activs"  : ['selu'],
#                         },
#               },
#
#
#
#               {
#                   "NAME" : "1x1_cc_nd_31",
#                   "PARAMS" :
#                         { # selu 31 step selu conv
#                             "kernels" : [ 31,],
#                             "filters" : [256,],
#                             "depths"  : [  1,],
#                             "drops"   : [  0,],
#                             "repeats" : [  1,],
#                             "layouts" : ['Lc'],
#                             "bns"     : [   0],
#                             "activs"  : ['selu'],
#                         },
#               },
#
#
#               {
#                   "NAME" : "1x1_cc_nd_14",
#                   "PARAMS" :
#                         { #  14 step selu conv
#                             "kernels" : [ 14,],
#                             "filters" : [256,],
#                             "depths"  : [  1,],
#                             "drops"   : [  0,],
#                             "repeats" : [  1,],
#                             "layouts" : ['Lc'],
#                             "bns"     : [   0],
#                             "activs"  : ['selu'],
#                         },
#               },
#
#
#               {
#                   "NAME" : "1x1_cc_nd_365",
#                   "PARAMS" :
#                         { #  365 step selu conv
#                             "kernels" : [ 365,],
#                             "filters" : [256,],
#                             "depths"  : [  1,],
#                             "drops"   : [  0,],
#                             "repeats" : [  1,],
#                             "layouts" : ['Lc'],
#                             "bns"     : [   0],
#                             "activs"  : ['selu'],
#                         },
#               },
#
#               {
#                   "NAME" : "1x1_cc_nd_732",
#                   "PARAMS" :
#                         { #  732 step linear conv
#                             "kernels" : [ 732,],
#                             "filters" : [256,],
#                             "depths"  : [  1,],
#                             "drops"   : [  0,],
#                             "repeats" : [  1,],
#                             "layouts" : ['Lc'],
#                             "bns"     : [   0],
#                             "activs"  : ['selu'],
#                         },
#               },
#
#               {
#                   "NAME"   : "4xX_cc_nd_732_good",
#                   "PARAMS" :
#                     {
#                             #
#                             "kernels" : [  14,  31,  365, 732,],
#                             "filters" : [ 256, 256, 256, 256,],
#                             "depths"  : [   1,   1,   1,   1,],
#                             "drops"   : [   0,   0,   0,   0,],
#                             "repeats" : [   1,   1,   1,   1,],
#                             "layouts" : ['Lc','Lc','Lc','Lc',],
#                             "bns"     : [   0,   0,   0,   0,],
#                             "activs"  : ['selu','selu','selu','selu']
#                     }
#               },
#         ]
#
#     conv_grid = [
#
#               {
#                   "NAME" : "2X732",
#                   "PARAMS" :
#                         { #  732 step linear conv
#                             "kernels" : [ 31,   7],
#                             "filters" : [512, 256],
#                             "depths"  : [  1,   6],
#                             "drops"   : [  0,   0],
#                             "repeats" : [  1,   2],
#                             "layouts" : ['Lc','Lf'],
#                             "bns"     : [   0,  0],
#                             "activs"  : ['selu','selu'],
#                         },
#               },
#
#               {
#                   "NAME"   : "4cNd732",
#                   "PARAMS" :
#                     {
#                             #
#                             "kernels" : [  14,  31,  365, 732,],
#                             "filters" : [ 256, 256, 256, 256,],
#                             "depths"  : [   1,   1,   1,   1,],
#                             "drops"   : [   0,   0,   0,   0,],
#                             "repeats" : [   1,   1,   1,   1,],
#                             "layouts" : ['Lc','Lc','Lc','Lc',],
#                             "bns"     : [   0,   0,   0,   0,],
#                             "activs"  : ['selu','selu','selu','selu']
#                     }
#               },
#
#
#         ]
#
#     get_model = False
#     bn=True
#     l.P("")
#     l.P("")
#     for i, dparams in enumerate(conv_grid):
#       s_model = "Model__{}".format(dparams['NAME'])
#       l.P(s_model + ':')
#       tf_inp = tf.keras.layers.Input((None,100),)
#       params = dparams['PARAMS']
#       if get_model:
#         m1 = timeseries_causal_conv_module(log=l, tf_input=tf_inp,
#                                            return_model=True,
#                                            DEBUG=True,
#                                            **params
#                                            )
#         tf_x = m1(tf_inp)
#       else:
#         tf_x = timeseries_causal_conv_module(log=l,
#                                              tf_input=tf_inp,
#                                              DEBUG=True,
#                                              **params
#                                              )
#       m = tf.keras.models.Model(tf_inp, tf_x, name=s_model)
#       l.plot_keras_model(m, verbose=False)
#     l.P("Standard :")
#     tf_x = timeseries_causal_conv_module(log=l, tf_input=tf_inp, DEBUG=True)
#     m = tf.keras.models.Model(tf_inp, tf_x, name='std')
#     l.plot_keras_model(m, verbose=False)
#
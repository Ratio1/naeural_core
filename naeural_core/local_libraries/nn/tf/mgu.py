"""
TODOs: 
    - experiment with various biases for each individual gate. Example: gate for
      bypass opened 50%, etc
    
    - dropout on inputs might force the GateLayer to learn relevant features. Apply
      different dropouts for different gates
    
    - experiment with different gating FC and different gating inputs (maybe 
      gate dependent more than just inputs)
    
    - replace last gate with residual connection ?
    
    - add layer self-analysis (explain each gate learned features)


Original text:

Multi-Gated Layer

pass_through = linear_nobias(input)
dense_bn_post = bn(f(linear(input)) OR layer_norm(....
dense_bn_pre = f(bn(linear(input))

BN = bn_pos * dense_bn_pre + (1 - bn_pos) * dense_bn_post

out1= has_bn * BN + (1- has_bn) * dense

final = uses_layer * out1 + (1- uses_layer) * pass_through


"""

import numpy as np
import tensorflow as tf


__VER__ = '0.3.6.1'


def get_input_shape(lyr):
  ## assume batch first
  return tuple(lyr.input.shape)[1:]

def explain_model(model, log, np_input):
  _layers = model.layers
  np_temp_input = np_input
  log.P("Self-explainability of MGU layers in {} layer graph".format(
    len(_layers)), 
    color='y')
  for _layer in _layers:
    if type(_layer) is MultiGatedUnit:
      _input_shape = tuple(_layer.input.shape)[1:]
      if np_temp_input.shape[1:] == _input_shape:
        # 1. create model from tf_input up to current MGU inpu
        # 2. run inference on np_input
        # 3. pass np_input to current MGU explain
        _layer.explain(
          log=log,
          np_input=np_temp_input,
          )
        _temp_model = _layer.get_model()
        np_temp_input = _temp_model.predict(np_temp_input)
      else:
        log.P("  Layer {} skipped - input {} vs layer input {}".format(
          _layer.name,
          np_input.shape[1:],
          _input_shape,
          ), color='y')


class GatingUnit(tf.keras.layers.Layer):
  def __init__(self, 
               layer,
               activation='sigmoid',
               **kwargs):
    self.gate_trans = layer
    
    self._name = kwargs.get('name', 'gu')
    # replaced tf.keras.activations.get(activation)
    self.gate_activ = tf.keras.layers.Activation(
      activation, 
      name=self._name + '_gact',
      )
    
    super().__init__(**kwargs)
    return
    
    
  def call(self, inputs):
    """
    

    Parameters
    ----------
    inputs : tuple(3) tensors
      inputs[0] - initial input in the module that generates the gate status
      inputs[1] - first path (gate open)
      inputs[2] - second path (gate closed)

    Returns
    -------
    tf_out : TYPE
      DESCRIPTION.

    """
    return self._forward(inputs)
  ############################################################################

  def _forward(self, inputs, for_model=False):
    tf_source = inputs[0]
    tf_value1 = inputs[1]
    tf_value2 = inputs[2]
    
    tf_gate_x = self.gate_activ(
      self.gate_trans(
        tf_source
        ))
    
    if not for_model:
      tf_out = tf_gate_x * tf_value1 + (1 - tf_gate_x) * tf_value2
    else:
      tf_v1 = tf.keras.layers.multiply(
        [tf_gate_x,tf_value1], 
        name=self._name + '_open_go_s1')
      tf_not_gate_x = tf.keras.layers.Lambda(
        lambda x: 1-x, 
        name=self._name + '_neg_gate',
        )(tf_gate_x)
      tf_v2 = tf.keras.layers.multiply(
        [tf_not_gate_x,tf_value2], 
        name=self._name + '_close_go_s2')
      tf_out = tf.keras.layers.add(
        [tf_v1, tf_v2],
        name=self._name + '_add')
    return tf_out
  
  
  def _get_gate_response_tf(self, tf_source):
    return self.gate_activ(
      self.gate_trans(
        tf_source
        ))
  
  def _get_gate_response_model(self):
    input_shape = get_input_shape(self.gate_trans)
    tf_inp = tf.keras.layers.Input(input_shape)
    tf_x = self._get_gate_response_tf(tf_inp)
    m = tf.keras.models.Model(
      inputs=tf_inp,
      outputs=tf_x,
      )
    return m


class SqueezeGatingUnit(tf.keras.layers.Layer):
  def __init__(self,
               input_shape,
               output_units,
               base_layer_activation,
               activation,
               name,
               squeeze_type,
               **kwargs):

    assert base_layer_activation not in [None, 'linear']
    nr_dim = len(input_shape[1:])
    input_units = input_shape[-1]
    self.squeeze_type = squeeze_type
    self.prepare = None
    self.out_shape = None
    self.embed = None
    self.expand = None
    self.activate = None
    self.reshape = None

    if nr_dim == 4:
      self.prepare = tf.keras.layers.GlobalAveragePooling3D(name=name + '_gap3d')
      self.out_shape = (1,1,1,output_units)
    elif nr_dim == 3:
      self.prepare = tf.keras.layers.GlobalAveragePooling2D(name=name + '_gap2d')
      self.out_shape = (1,1,output_units)
    elif nr_dim == 2:
      self.prepare = tf.keras.layers.GlobalAveragePooling1D(name=name + '_gap1d')
      self.out_shape = (1,output_units)
    elif nr_dim == 1:
      self.prepare = tf.keras.layers.Lambda(lambda x: x, name=name + '_ident')
      self.out_shape = (output_units,)
    
    self.embed = tf.keras.layers.Dense(
      units=self._infer_embed_units(
        input_units=input_units, 
        adaptive=squeeze_type == 'adaptive'
        ),
      activation=base_layer_activation,
      name=name + '_embed_lin_'+str(base_layer_activation)
    )

    self.expand = tf.keras.layers.Dense(units=output_units, name=name+'_expand')
    self.activate = tf.keras.layers.Activation(activation=activation, name=name+'_activ')
    self.reshape = tf.keras.layers.Reshape(self.out_shape, name=name+'_reshape')
    super().__init__(**kwargs)
    return

  def call(self, inputs):
    return self._forward(inputs)

  @staticmethod
  def _infer_embed_units(input_units, adaptive=True):
    if adaptive:
      stages  = [ 32, 128, 512]
      factors = [  4,   8,  16] 
      assert len(stages) == len(factors)
      factor = 2
      for i in range(len(stages)):
        if input_units > stages[i]:
          factor = factors[i]    
      embed_units = max(input_units // factor, 2)
    else:
      embed_units = max(input_units // 16, 2)
    return embed_units

  def _forward(self, inputs, for_model=False):
    tf_source = inputs[0]
    tf_value1 = inputs[1]
    tf_value2 = inputs[2]

    tf_gate_x = self.prepare(tf_source)
    tf_gate_x = self.embed(tf_gate_x)
    tf_gate_x = self.expand(tf_gate_x)
    tf_gate_x = self.activate(tf_gate_x)
    tf_gate_x = self.reshape(tf_gate_x)

    if not for_model:
      tf_out = tf_gate_x * tf_value1 + (1 - tf_gate_x) * tf_value2
    else:
      tf_v1 = tf.keras.layers.multiply(
        [tf_gate_x,tf_value1],
        name=self._name + '_open_go_s1')
      tf_not_gate_x = tf.keras.layers.Lambda(
        lambda x: 1-x,
        name=self._name + '_neg_gate',
        )(tf_gate_x)
      tf_v2 = tf.keras.layers.multiply(
        [tf_not_gate_x,tf_value2],
        name=self._name + '_close_go_s2')
      tf_out = tf.keras.layers.add(
        [tf_v1, tf_v2],
        name=self._name + '_add')
    return tf_out

  def _get_gate_response_tf(self, tf_source):
    tf_gate_x = self.prepare(tf_source)
    tf_gate_x = self.embed(tf_gate_x)
    tf_gate_x = self.expand(tf_gate_x)
    tf_gate_x = self.activate(tf_gate_x)
    tf_gate_x = self.reshape(tf_gate_x)
    return tf_gate_x

  def _get_gate_response_model(self):
    input_shape = get_input_shape(self.prepare)
    tf_inp = tf.keras.layers.Input(input_shape)
    tf_x = self._get_gate_response_tf(tf_inp)
    m = tf.keras.models.Model(
      inputs=tf_inp,
      outputs=tf_x,
    )
    return m

class MultiGatedUnit(tf.keras.layers.Layer):
  """
  MultiGatedUnit implements a universal advanced version of highway networks that can be applied
  to almost any layer as a wrapper.

  The usage is as simple as:
    ```
    # example 1
    # here we apply a activation without specifying one in base layer - the Conv1D is linear
    lyr_m1 = MultiGatedUnit(tf.keras.layers.Conv1D(64,3), activation='relu')

    # example 2
    # we specify the activation in the FC layer
    lyr2 = tf.keras.layers.Dense(10, activation='sigmoid')
    # we wrap it inside the MultiGatedUnit
    lyr_m2 = MultiGatedUnit(lyr2)
    ```
  """
  def __init__(self,
               layer,
               gates_initializer='glorot_uniform',
               bypass_initializer='glorot_uniform',
               gating_activation='sigmoid',
               add_residual_connection=True,
               use_squeeze_gating='adaptive', # None/False, 'adaptive', 'fixed'
               version=None,
               **kwargs,
               ):
    assert isinstance(layer, tf.keras.layers.Layer), "`layer` must be a `tf.keras.layers.Layer` not `{}`".format(
      layer.__class__.__name__)
    assert use_squeeze_gating in [None, False, 'adaptive','fixed'], "`use_squeeze_gating` must be either disabled or fixed or adaptive based on input size"
    if version is None:
      version = __VER__

    self.__version__ = version
    self.version = self.__version__
      
    self.gates_initializer = gates_initializer
    self.bypass_initializer = bypass_initializer
    self.gating_activation = gating_activation
    self.layer_class = layer.__class__
    self.add_residual_connection = add_residual_connection
    self.use_squeeze_gating = use_squeeze_gating
    
    base_layer_config = layer.get_config()
    name = kwargs.get('name')
    base_name = base_layer_config.get('name','')
    if name is None:
      name = 'MGU_' + base_name

    kwargs['name'] = name    
    if base_name[:3] != 'MGU':
      base_layer_config['name'] = 'MGU_' + base_name + '_base'
    
    self._name = name
    self._original_layer = layer
      
    self.activation = kwargs.get('activation') if 'activation' in kwargs else base_layer_config.get('activation')
    if base_layer_config.get('activation') not in [None, 'linear']:
      base_layer_config['activation'] = 'linear'
      
    if self.activation == 'linear':
      raise ValueError('Cannot have MGU with linear activation. Either set host MGU or the layer activation')

    if 'activation' in kwargs:
      kwargs.pop('activation')
    
    self.base_layer_config = base_layer_config

    ########## CLASS ATTRIBUTRES #############
    self.layer = None
    self.act_pre_bn = None
    self.act_post_bn = None
    self.bypass = None
    self.bn_pre_act = None
    self.bn_pos_act = None
    self.ln_pos_act = None
    self.residual = None

    self.g_bpre_bpos = None
    self.g_bn_ln = None
    self.g_norm_non = None
    self.g_residual = None
    self.g_proc_skip = None
    ##########################################

    self._create_layers()

    super().__init__(**kwargs)
    return

  def call(self, inputs):
    return self._forward(inputs)

  def build(self, input_shape):
    super().build(input_shape)
    self._create_all_gates(input_shape)
    self.built = True
    return

  def get_config(self):
    config = {
        'layer' :  tf.keras.layers.serialize(self.layer),
        # no tf.keras.activations.serialize for string props
        'activation' : self.activation,
        'gating_activation': self.gating_activation, 
        'gates_initializer': self.gates_initializer, 
        'bypass_initializer': self.bypass_initializer,
        'version' : self.version,
        'add_residual_connection' : self.add_residual_connection,
        'use_squeeze_gating' : self.use_squeeze_gating
    }
    base_config = super().get_config()
    cfg =  dict(list(base_config.items()) + list(config.items()))
    return cfg

  @classmethod
  def from_config(cls, config, custom_objects=None):
    from tensorflow.python.keras.layers import deserialize as deserialize_layer  # pylint: disable=g-import-not-at-top
    # Avoid mutating the input dict
    config = config.copy()
    v = config.get('version', 'UNK')
    print("\x1b[1;33mLoading MGU from {} config into v{}\x1b[0m".format(
      v, __VER__))
    layer = deserialize_layer(
        config.pop('layer'), custom_objects=custom_objects)
    return cls(layer, **config)  
  
  #############################################################################


  def _create_layer(self, name, kernel_initializer, use_bias):
    layer_config = self.base_layer_config.copy()
    layer_config['activation'] = 'linear'
    layer_config['name'] = self._name + '_' + name
    layer_config['kernel_initializer'] = tf.keras.initializers.get(kernel_initializer)
    return self.layer_class.from_config(layer_config)

  def _create_gate(self, gate_name):
    return GatingUnit(
      layer=self._create_layer(gate_name+'_gtrans', self.gates_initializer, use_bias=True),
      activation=self.gating_activation,
      name=self._name + '_' + gate_name
    )

  def _create_all_gates(self, input_shape):
    if not self.use_squeeze_gating:
      self.g_bpre_bpos = self._create_gate(gate_name='gu1_bpre_bpos')
      self.g_bn_ln = self._create_gate(gate_name='gu2_bn_ln')
      self.g_norm_non = self._create_gate(gate_name='gu3_norm_non')
      self.g_residual = self._create_gate(gate_name='gu4_res_or_not')
      self.g_proc_skip = self._create_gate(gate_name='gu5_proc_skip')
    else:
      self.g_bpre_bpos = self._create_squeeze_gate(
        input_shape=input_shape, gate_name='gu1_bpre_bpos',
        squeeze_type=self.use_squeeze_gating,
      )
      self.g_bn_ln = self._create_squeeze_gate(
        input_shape=input_shape, gate_name='gu2_bn_ln',
        squeeze_type=self.use_squeeze_gating,
      )
      self.g_norm_non = self._create_squeeze_gate(
        input_shape=input_shape, gate_name='gu3_norm_non',
        squeeze_type=self.use_squeeze_gating,
      )
      self.g_residual = self._create_squeeze_gate(
        input_shape=input_shape, gate_name='gu4_res_or_not',
        squeeze_type=self.use_squeeze_gating,
      )
      self.g_proc_skip = self._create_squeeze_gate(
        input_shape=input_shape, gate_name='gu5_proc_skip',
        squeeze_type=self.use_squeeze_gating,
      )
    return

  def _infer_output_shape(self, input_shape):
    keys = ['units', 'filters']

    for k in keys:
      if self.base_layer_config.get(k, None) is not None:
        return self.base_layer_config.get(k)

    return input_shape[-1]

  def compute_output_shape(self, input_shape):
    output_shape = self.layer.compute_output_shape(input_shape)
    return output_shape

  def _create_squeeze_gate(self, input_shape, gate_name, squeeze_type):
    output_units = self._infer_output_shape(input_shape)
    base_layer_activation = self.activation

    return SqueezeGatingUnit(
      input_shape=input_shape,
      output_units=output_units,
      base_layer_activation=base_layer_activation,
      activation=self.gating_activation,
      squeeze_type=squeeze_type,
      name=self._name + '_' + gate_name
    )

  def _create_layers(self):
    self.layer = self._original_layer.__class__.from_config(self.base_layer_config)
    assert self.layer.activation.__name__ == 'linear'
    
    # tf.keras.activations.get(self.activation)
    self.act_pre_bn = tf.keras.layers.Activation(
      self.activation, 
      name=self._name + '_{}_pre_bn'.format(self.activation))

    self.act_post_bn = tf.keras.layers.Activation(
      self.activation, 
      name=self._name + '_{}_post_bn'.format(self.activation))
    
    # bypass
    self.bypass = self._create_layer(
      'linear_bypass', 
      self.bypass_initializer, 
      use_bias=False
      )
    
    self.bn_pre_act = tf.keras.layers.BatchNormalization(name=self._name + '_bn_pre_act')
    self.bn_pos_act = tf.keras.layers.BatchNormalization(name=self._name + '_bn_post_act')    
    self.ln_pos_act = tf.keras.layers.LayerNormalization(name=self._name + '_ln_post_act')
    self.residual   = tf.keras.layers.Add(name=self._name + '_residual')
    return
  
  def _forward(self, inputs, for_model=False):
    tf_bypass = self.bypass(inputs)
    tf_x = self.layer(inputs)
    tf_x_act = self.act_pre_bn(tf_x)
    
    tf_x_bn_act = self.act_post_bn(self.bn_pre_act(tf_x))
    tf_x_act_bn = self.bn_pos_act(tf_x_act)
    tf_x_act_ln = self.ln_pos_act(tf_x_act)
    
    if not for_model:    
      tf_bpre_bpos = self.g_bpre_bpos([inputs, tf_x_bn_act, tf_x_act_bn])
      tf_bn_ln = self.g_bn_ln([inputs, tf_bpre_bpos, tf_x_act_ln])
      tf_norm_non = self.g_norm_non([inputs, tf_bn_ln, tf_x_act])
      tf_processed = tf_norm_non

      if self.add_residual_connection:
        tf_processed_res = tf_processed + tf_bypass
        tf_final_processed = self.g_residual([inputs, tf_processed, tf_processed_res])
      else:
        tf_final_processed = tf_processed

      tf_proc_noproc = self.g_proc_skip([inputs, tf_bypass, tf_final_processed])
    else:
      tf_bpre_bpos  = self.g_bpre_bpos._forward(
        inputs=[inputs, tf_x_bn_act, tf_x_act_bn],
        for_model=True)      
      tf_bn_ln = self.g_bn_ln._forward(
        inputs=[inputs, tf_bpre_bpos, tf_x_act_ln],
        for_model=True)
      tf_norm_non = self.g_norm_non._forward(
        inputs=[inputs, tf_bn_ln, tf_x_act],
        for_model=True)
      tf_processed = tf_norm_non

      if self.add_residual_connection:
        tf_processed_res = self.residual([tf_processed, tf_bypass])
        tf_final_processed = self.g_residual._forward(
          inputs=[inputs, tf_processed, tf_processed_res],
          for_model=True)
      else:
        tf_final_processed = tf_processed

      tf_proc_noproc = self.g_proc_skip._forward(
        inputs=[inputs, tf_bypass, tf_final_processed],
        for_model=True)      
    
    tf_out = tf_proc_noproc
    return tf_out      

  
  def get_model(self):
    inp_shape = get_input_shape(self.bypass)
    inp_name = "INPUT_" + self.name
    tf_inp = tf.keras.layers.Input(inp_shape, name=inp_name)
    tf_x = self._forward(tf_inp, for_model=True)
    return tf.keras.models.Model(tf_inp, tf_x, name=self.name + '_Model')
    

  def explain(self, log, np_input, save_fig=True):
    import os
    def P(s, color='y'):
      if log is not None:
        log.P(str(s), color=color)
      else:
        print("\x1b[1;33m" + str(s) + "\x1b[0m")
      return
    _layers = {
      self.g_bpre_bpos : 
        {
          'desc'  : "Gate1 - BN pre or post activation",
          'left'  : 'BN-pre-{}'.format(self.activation),
          'right' : 'BN-post-{}'.format(self.activation),
        },
      self.g_bn_ln : 
        {
          'desc'  : "Gate2 - BN or LayerNorm",
          'left'  : 'BN',
          'right' : 'LayerNorm',
        },
      self.g_norm_non :
        {
          'desc'  : "Gate3 - any norm or no norming at all",
          'left'  : 'Norming',
          'right' : 'No-norming',
        },
      self.g_proc_skip : 
        {
          'desc'  : "Gate5 - direct linear bypass or processed",
          'left'  : 'Bypass',
          'right' : 'Processed',
        },
      }

    if self.add_residual_connection:
      _layers[self.g_residual] = {
          'desc'  : 'Gate4 - residual or not',
          'left'  : 'No-residual',
          'right' : 'Residual',
      }

    input_shape = get_input_shape(self.layer)
    assert np_input.shape[1:] == input_shape, "np_input {} must match shape of MGU input {}".format(
      np_input.shape[:1], input_shape)
    P("  Layer: {} self-explainability analysis based on {} input".format(
      self.name,
      input_shape))
    activations = []
    for i, gate in enumerate(_layers):
      _desc = _layers[gate]['desc']
      _left = _layers[gate]['left']
      _right = _layers[gate]['right']
      _desc_formula = 'gate * ' + _left + "  +  (1 - gate) * "+_right
      P("    Analysing '{}'".format(
        _desc, 
        ))
      P("      Gate rule: `{}`".format(
        _desc_formula))
      gate_model = gate._get_gate_response_model()
      np_gate_activ = gate_model.predict(np_input)
      _gate_mean = np.mean(np_gate_activ)
      P("      Gate mean: {:.2f}, median: {:.2f}, min/max: {:.2f}/{:.2f}".format(
        _gate_mean,
        np.median(np_gate_activ),
        np.min(np_gate_activ),
        np.max(np_gate_activ),
        ))
      P("      Gate opened for: {}".format(
        _left if _gate_mean > 0.5 else _right
        ), color='g')
      activations.append(np_gate_activ.ravel())
    bins = 30
    _labels = [x['desc'] + '\n Low activations:'+ x['right']+ '; High activations:' + x['left'] 
               for x in list(_layers.values())]
    fn = os.path.join(log.get_output_folder(), self.name)
    log.plot_histogram(
      activations, 
      figsize=(13,8), 
      bins=bins,
      colors=['b','r','c','g'],
      labels=_labels,
      logscale=True,
      save_img_path=None if not save_fig else fn,
      xticks=[np.linspace(0,1, bins).round(2) for _ in range(len(_layers))],
      vline=0.5,
      )      
    return
    
            

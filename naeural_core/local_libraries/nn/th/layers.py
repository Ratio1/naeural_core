import torch as th

from naeural_core.local_libraries.nn.utils import conv_output_shape
from naeural_core.local_libraries.nn.th.utils import get_activation, get_dropout

###############################################################################
##################               BASIC LAYERS                ##################        
###############################################################################

class Conv2dExt(th.nn.Module):
  def __init__(
        self,
        input_shape=None,
        show_input=True,
        **kwargs
    ) -> None:
    super().__init__()
    self._show_input = show_input
    self.output_shape = None
    self.input_shape = None
    self.conv = th.nn.Conv2d(**kwargs)
    if input_shape is not None:
      if len(input_shape) == 2:
        input_shape = (kwargs['in_channels'], ) + input_shape
      self.input_shape = tuple(input_shape)
      self.output_shape = (self.conv.out_channels,)
      self.output_shape += conv_output_shape(
        h_w=self.input_shape[1:],
        **kwargs,
      )
    return
  
  def forward(self, inputs):
    return self.conv(inputs)
  
  
  def __repr__(self):
    s = str(self.conv)
    if self.output_shape is not None:
      if self._show_input:
        s = s + ' [Input:{}'.format(self.input_shape)  
      s = s + ' => Output:{}{}'.format(self.output_shape, ']' if self._show_input else '')
    return s
    

class ConvTranspose2dExt(th.nn.Module):
  def __init__(
        self,
        input_shape=None,
        show_input=True,
        **kwargs
    ) -> None:
    super().__init__()
    self._show_input = show_input
    self.output_shape = None
    self.input_shape = None
    self.conv = th.nn.ConvTranspose2d(**kwargs)
    if input_shape is not None:
      self.input_shape = tuple(input_shape)
      self.output_shape = (self.conv.out_channels,)
      self.output_shape += conv_output_shape(
        h_w=self.input_shape[1:],
        transpose=True,
        **kwargs,
      )
    return
  
  def forward(self, inputs):
    return self.conv(inputs)
  
  
  def __repr__(self):
    s = str(self.conv)
    if self.output_shape is not None:
      if self._show_input:
        s = s + ' [Input:{}'.format(self.input_shape)  
      s = s + ' => Output:{}{}'.format(self.output_shape, ']' if self._show_input else '')
    return s
  
  
class GlobalMaxPool2d(th.nn.Module):
  def __init__(self):
    super().__init__()
    return
  
  def forward(self, inputs):
    th_x = th.nn.functional.max_pool2d(
      inputs, 
      kernel_size=inputs.size()[2:]
      )
    th_x = th.squeeze(th.squeeze(th_x, -1), -1)
    return th_x


class GlobalAvgPool2d(th.nn.Module):
  def __init__(self):
    super().__init__()
    return

  def forward(self, inputs):
    th_x = th.nn.functional.avg_pool2d(
      inputs,
      kernel_size=inputs.size()[2:]
    )
    th_x = th.squeeze(th.squeeze(th_x, -1), -1)
    return th_x
    

class L2_Normalizer(th.nn.Module):
  def __init__(self,):
    super().__init__()
    
  def forward(self, inputs):
    return th.nn.functional.normalize(inputs, p=2, dim=1)  
  
  
class TripletLoss(th.nn.Module):
  def __init__(self, device, beta=0.2):
    super().__init__()
    self.beta = th.tensor(beta, device=device)
    self.offset = th.tensor(0.0, device=device)
    return
    
    
  def forward(self, triplet):
    th_anchor = triplet[0]
    th_positive = triplet[1]
    th_negative = triplet[2]
    th_similar_dist = th.pow(th_anchor - th_positive, 2).sum(1)
    th_diff_dist = th.pow(th_anchor - th_negative, 2).sum(1)
    th_batch_pre_loss = th_similar_dist - th_diff_dist + self.beta
    th_batch_loss = th.max(input=th_batch_pre_loss, other=self.offset)
    th_loss = th_batch_loss.mean()
    return th_loss
  
  def __repr__(self):
    s = self.__class__.__name__ + "(beta={:.3f})".format(
        self.beta,
        )
    return s    


class InputPlaceholder(th.nn.Module):
  def __init__(self, input_dim):
    super().__init__()
    self.input_dim = input_dim
    return
    
  def forward(self, inputs):
    return inputs
  
  def __repr__(self):
    s = self.__class__.__name__ + "(input_dim={})".format(
        self.input_dim,
        )
    return s


class IdentityLayer(th.nn.Module):
  def __init__(self):
    super().__init__()
    return

  def forward(self, inputs):
    return inputs


class ConcatenateLayer(th.nn.Module):
  # TODO: REVIEW repr
  def __init__(self, dim=0, lst_input_dims=[]):
    super().__init__()
    self.dim = dim
    self.lst_input_dims = lst_input_dims
    if len(self.lst_input_dims) > 0:
      self.out_shape = (*lst_input_dims[:dim], sum([input_dims[dim] for input_dims in lst_input_dims]), *lst_input_dims[dim+1:])
    return

  def forward(self, inputs):
    return th.cat(inputs, dim=self.dim)
  
  def __repr__(self):
    model_repr = super().__repr__() 
    if len(self.lst_input_dims) > 0:
      model_repr += " [Input={} => Output={}]".format(
        self.lst_input_dims,
        self.out_shape
      )
    return model_repr
  
class ReshapeLayer(th.nn.Module):
  def __init__(self, dim=None, input_shape=None):
    super().__init__()
    assert dim is not None and len(dim) >= 1
    self.dim = list(dim)
    self.input_shape = input_shape
    return

  def forward(self, inputs):
    return inputs.reshape([-1] + self.dim)
  
  def __repr__(self):
    s_inp = 'input='
    if self.input_shape is not None:
      s_inp += str(tuple(self.input_shape))
    else:
      s_inp += 'None'
    s_out ='output=' + str(tuple(self.dim))
    s = self.__class__.__name__ + "({}, {})".format(s_inp, s_out)
    return s
  


class CausalConv1d(th.nn.Module):
  def __init__(self, f_in, f_out, k_size):
    super().__init__()
    self.conv = th.nn.Conv1d(
      f_in, 
      f_out, 
      kernel_size=k_size, 
      padding=k_size-1,
      stride=1,
      )    
    return
    
  def forward(self, x):
    th_x = self.conv(x)
    s = x.shape[2]
    return th_x[:,:, :s]  


class GAPGMPTransform(th.nn.Module):
  def __init__(self):
    super(GAPGMPTransform, self).__init__()
    self.gap = GlobalAvgPool2d()
    self.gmp = GlobalMaxPool2d()

  def forward(self, x):
    th_x_gap = self.gap(x)
    th_x_gmp = self.gmp(x)
    th_x_concat = th.cat([th_x_gap, th_x_gmp], dim=-1)

    return th_x_concat

###############################################################################
##################             ADVANCED LAYERS               ##################        
###############################################################################

class SQE(th.nn.Module):
  ## TODO: REVIEW 
  def __init__(self, gate_input_dim, gated_dim, squeeze_dim, gate_input_indexes=None, gated_indexes=None, suffix=''):

    super(SQE, self).__init__()
    self.gated_indexes = gated_indexes
    self.gate_input_indexes = gate_input_indexes
    self.add_module('squeeze_{}'.format(suffix),  th.nn.Linear(gate_input_dim, squeeze_dim))
    self.add_module('squeeze_act_{}'.format(suffix), th.nn.ReLU())
    self.add_module('excite_{}'.format(suffix), th.nn.Linear(squeeze_dim, gated_dim))
    self.add_module('excite_act_{}'.format(suffix), th.nn.Sigmoid())

  def forward(self, input_x):
    if type(input_x) == list:
      ### TODO: if indexes are None
      if self.gate_input_indexes is not None:
        gate_inputs_x = th.cat([x for i, x in enumerate(input_x) if i in self.gate_input_indexes], axis=-1)
        gated_x = th.cat([x for i, x in enumerate(input_x) if i in self.gated_indexes], axis=-1)

        bypass_inputs = [x for i, x in enumerate(input_x) if i not in self.gate_input_indexes + self.gated_indexes]
      else:
        ### TODO: CHECK IF CORRECT
        gate_inputs_x = th.cat(input_x)
        gated_x = th.cat(input_x)
        bypass_inputs = []
    else:
      ### TODO: CHECK IF CORRECT
      gate_inputs_x = input_x
      gated_x = input_x
      bypass_inputs = []

    x = gate_inputs_x
    for module in self._modules.values():
      x = module(x)
    th_x = gated_x * x

    th_x = th.cat(bypass_inputs + [th_x], axis=-1)

    return th_x

class AffineTransform(th.nn.Module):
  def __init__(self, input_shape:list=[], output_shape:list=None, padding_mode='reflection'):
    super().__init__()
    assert len(input_shape) == 3, "Please define CHW `input_shape` for the {} layer".format(self.__class__.__name__)
    if output_shape is None:
      output_shape = input_shape
    self._input_shape = input_shape
    self._output_shape = output_shape
    self._padding_mode = padding_mode
    return
  
  def forward(self, images, thetas):
    shape = (images.shape[0],) + self._output_shape
    th_transform_matrix = th.nn.functional.affine_grid(thetas, shape, align_corners=False)

    th_transformed = th.nn.functional.grid_sample(
      images, th_transform_matrix,
      padding_mode=self._padding_mode, align_corners=False
      )
    th_out = th_transformed
    return th_out
  
  def __repr__(self):
    s = "{}({} => {})".format(self.__class__.__name__, self._input_shape, self._output_shape)
    return s
        

class SpatialTransformerNet(th.nn.Module):    
  def __init__(
      self, 
      input_shape:list=[], 
      loc_layers=[(8,7), (10,5)], 
      drop_input=0,
      drop_fc=0,
      fc_afine=[32],
      reflection=True,
      affine_reshape=None, 
      scale_min=0,
      scale_max=1,
    ) -> None:
    """
    A Spatial Transformer Net https://arxiv.org/abs/1506.02025
    
    For more advanced version see `AdvancedSpatialTransformerNet` in encoders

    Parameters
    ----------
    input_shape : list
      Input shape in CHW format
    loc_layers : list, optional
      List of (filters, kernel) layers for the conv part of the STN. The default is [(8,7), (10,5)].
    drop_input : int, optional
      Maybe apply dropout on input. The default is 0.
    drop_fc : int, optional
      Maybe apply dropout on fc layers. The default is 0.
    fc_afine : list, optional
      List of units per layer in the fully connected post-conv processor. The default is [32].
    reflection : bool, optional
      If True then use "reflection" for `grid_sample` padding mode. The default is True.
    affine_reshape : list, optional
      Either CHW or HW for the transform if another output shape is required. The default is None.

  
    """
    
    super().__init__()
    assert len(input_shape) == 3, "Please define CHW input for the {}".format(self.__class__.__name__)
    locs = []
    prev_f, prev_h, prev_w = input_shape
    channels = prev_f
    prev_output = input_shape
    self._scale_min = scale_min
    self._scale_max = scale_max
    if affine_reshape is None:
      affine_reshape = input_shape[1:]
      
    if len(affine_reshape) == 2:
      affine_reshape = (channels, ) + tuple(affine_reshape)
    
    self.affine_reshape = affine_reshape
    
    if drop_input > 0:
      locs.append(th.nn.Dropout(drop_input))
    for loc, k in loc_layers:
      locs.append(Conv2dExt(input_shape=prev_output, in_channels=prev_f, out_channels=loc, kernel_size=k))
      prev_output= locs[-1].output_shape
      prev_f, prev_h, prev_w = prev_output
      locs.append(th.nn.MaxPool2d(2, stride=2))
      prev_h = prev_h // 2
      prev_w = prev_w // 2
      prev_output = prev_f, prev_h, prev_w
      locs.append(th.nn.ReLU(True)) # inplace saves a little memory as no tensor is allocated    
    
    fcs = []
    prev_u = prev_f  * prev_h * prev_w
    fcs.append(ReshapeLayer(input_shape=(prev_f, prev_h, prev_w), dim=(prev_u,)))
    for fc in fc_afine:
      fcs.append(th.nn.Linear(prev_u, fc))
      prev_u = fc
      fcs.append(th.nn.ReLU(True))
      if drop_fc > 0:
        fcs.append(th.nn.Dropout(drop_fc))
    
    fcs.append(th.nn.Linear(prev_u, 3 * 2))
        
    self.loc_net = th.nn.Sequential(*locs)
    self.fc_afine = th.nn.Sequential(*fcs)
    self.fc_afine[-1].weight.data.zero_()
    self.fc_afine[-1].bias.data.copy_(th.tensor([1, 0, 0, 0, 1, 0], dtype=th.float))
    
    self.readout_image = AffineTransform(
      input_shape=input_shape,
      output_shape=self.affine_reshape,
      padding_mode='reflection' if reflection else 'zeros'
    )
    self.readout_theta = ReshapeLayer(dim=(2,3), input_shape=(6,))
    return
  
  
  def forward(self, inputs):
    th_proc = (inputs - inputs.min()) / (inputs.max() - inputs.min())
    th_proc = th_proc * (self._scale_max - self._scale_min) + self._scale_min      
    
    t_x = th.where(inputs.max() > 1, th_proc, inputs)
    t_x = t_x.to(next(self.parameters()).dtype)
    
    th_x_conv = self.loc_net(t_x)
    
    th_x_fc = self.fc_afine(th_x_conv)
      
    th_theta = self.readout_theta(th_x_fc)
    th_affine_input = th.where(inputs.max() > 1, inputs.to(next(self.fc_afine.parameters()).dtype), inputs)
    th_image = self.readout_image(images=th_affine_input, thetas=th_theta)
    
    return th_theta, th_image
  
  
  def transform(self, images, shape=None):
    if shape is None:
      shape = images.shape
    assert images.shape[1] == 3
    
    th_thetas, _ = self(images)
    
    th_transform_matrix = th.nn.functional.affine_grid(th_thetas, shape=shape)

    th_transformed = th.nn.functional.grid_sample(images, th_transform_matrix)
    return th_transformed
    
    
      



class SiamesePathsCombiner(th.nn.Module):
  def __init__(self, input_dim, method, activ=None):
    super().__init__()
    self.method = method
    self.input_dim = input_dim
    self.activ = self.get_activation(activ)
    if method in ['sub','abs','sqr', 'add']:
      self.output_dim = input_dim
    elif method == 'cat':
      self.output_dim = input_dim * 2
    elif method == 'eucl':
      self.output_dim = 1
    else:
      raise ValueError("Unknown combine method '{}'".format(method))
    return
    
  def forward(self, paths):
    path1 = paths[0]
    path2 = paths[1]
#    if self.norm_each:
#      path1 = th.nn.functional.normalize(path1, p=2, dim=1)
#      path2 = th.nn.functional.normalize(path2, p=2, dim=1)
      
    if self.method == 'sub':
      th_x = path1 - path2
    elif self.method == 'add':
      th_x = path1 + path2
    elif self.method == 'cat':
      th_x = th.cat((path1, path2), dim=1)
    elif self.method == 'abs':
      th_x = (path1 - path2).abs()
    elif self.method == 'sqr':
      th_x = th.pow(path1 - path2, 2)
    elif self.method == 'eucl':
      th_x = th.pairwise_distance(path1, path2, keepdim=True)    
    
    if self.activ is not None:
      th_x = self.activ(th_x)
      
    return th_x
  


class DenseBlock(th.nn.Module):
  def __init__(self, in_channels, n_layers, dropout=0.0, use_batch_norm='pre', activation=None):
    super(DenseBlock, self).__init__()
    layers = []
    if use_batch_norm.lower() == 'pre-linear':
      layers.append(th.nn.BatchNorm1d(in_channels))

    layers.append(
      th.nn.Linear(
        in_features=in_channels,
        out_features=n_layers
      )
    )

    if use_batch_norm.lower() == 'pre':
      layers.append(th.nn.BatchNorm1d(n_layers))

    if activation is not None:
      layers.append(get_activation(activation))

    if use_batch_norm.lower() == 'post':
      layers.append(th.nn.BatchNorm1d(n_layers))

    if dropout > 0:
      layers.append(th.nn.Dropout(dropout))

    self.layers = th.nn.Sequential(
      *layers
    )

  def forward(self, th_x):
    return self.layers(th_x)


class DSepConv2d(th.nn.Module):
  def __init__(self, 
               in_channels, 
               out_channels, 
               kernel_size=3, 
               depth_multiplier=1,
               padding=0, 
               stride=1, 
               dilation=1, 
               bias=False,
               input_dim=None
               ):
    super().__init__()

    self.input_dim = input_dim
    self.output_h, self.output_w = None, None

    if self.input_dim is not None:
      output_dim = conv_output_shape(
        h_w=self.input_dim,
        kernel_size=kernel_size,
        stride=stride,
        pad=padding,
        dilation=dilation
      )
      self.output_h, self.output_w = output_dim
    #endif

    groups_depth = in_channels
    depth_out_channels = in_channels * depth_multiplier
    self.depthwise = th.nn.Conv2d(
      in_channels=in_channels,
      out_channels=depth_out_channels, 
      kernel_size=kernel_size, 
      groups=groups_depth,
      padding=padding, 
      stride=stride,
      dilation=dilation,
      bias=False,
      )
    
    self.pointwise = th.nn.Conv2d(
      in_channels=depth_out_channels,
      out_channels=out_channels, 
      kernel_size=1, 
      groups=1,
      padding=0, 
      stride=1,
      dilation=1,
      bias=bias,
      )
    return

  @property
  def output_dim(self):
    return self.output_h, self.output_w

  def forward(self, inputs):
    th_x = self.depthwise(inputs)
    out = self.pointwise(th_x)
    return out

  def __repr__(self):
    s = super().__repr__()

    if self.input_dim is None:
      return s

    s += "\n {}(input_dim={}, output_dim={})".format(
      self.__class__.__name__,
      self.input_dim,
      self.output_dim
    )
    return s



class EmbeddingTransform(th.nn.Module):
  def __init__(self, type, input_dim):
    """
    @param type in ['gmp/gap', 'flatten'] or self.transform will always be IdentityLayer
    @param input_dim = (C, H, W)
    """
    super(EmbeddingTransform, self).__init__()
    self.type = type
    self.input_dim = input_dim
    if type == 'gmp/gap':
      self.transform = GAPGMPTransform()
    elif type == 'flatten':
      self.transform = th.nn.Flatten()
    else:
      self.transform = IdentityLayer()
    return

  def forward(self, x):
    return self.transform(x)

  @property
  def output_dim(self):
    if self.type == 'gmp/gap':
      return 2 * self.input_dim[0]
    elif self.type == 'flatten':
      return self.input_dim[0] * self.input_dim[1] * self.input_dim[2]
    else:
      return self.input_dim

  def __repr__(self):
    s = super().__repr__()

    s += " [Input={} => Output={}]".format(
      self.input_dim,
      self.output_dim
    )
    return s

if False:
  # This is done in order for us to be able to use both the old version and the new version of DSepConv2DModule
  # with as little code as possible.
  # In the future this will be deleted.
  class DSepConv2DModule(th.nn.Module):
    """
    TODO: Docs: accept both in_channels, or input_dim

    """

    def __init__(self,
                 in_channels=None,
                 out_channels=None,
                 input_dim=None,
                 n_convs=2,
                 kernel_size=3,
                 patching=True,
                 depth_multiplier=1,
                 padding=0,
                 stride=1,
                 dilation=1,
                 bn=True,
                 activation=th.nn.ReLU6(),
                 bias=False,
                 dropout=0,
                 dropout_type='classic',  # classic or spatial
                 ext_conv=False,
                 residual=True
                 ):
      super().__init__()

      if isinstance(in_channels, (list, tuple)):
        # maybe someone used in_channels as input_dim
        input_dim = in_channels
        in_channels = input_dim[0]
      #endif
      if input_dim is not None:
        in_channels = input_dim[0]

      assert in_channels is not None, ValueError("DSepConv2DModule expects `in_channels` or `input_dim` parameters")
      assert out_channels is not None, ValueError("DSepConv2DModule expects `out_channels` param")

      self.input_dim = input_dim
      self.residual = residual
      self.channel_factor = 1 if residual else 2
      self.compose = self.compose_residual if self.residual else self.compose_concat
      output_dim = None
      self.out_channels, self.output_h, self.output_w = out_channels, None, None

      if ext_conv:  # this will be used after further checking
        self.reducer_skip = Conv2dExt(
          input_shape=input_dim,
          in_channels=in_channels,
          out_channels=out_channels,
          stride=kernel_size if patching else stride,
          kernel_size=(kernel_size, kernel_size),
          bias=bias,
          dilation=dilation,
          padding=padding
        )

        output_dim = self.reducer_skip.output_shape
        if output_dim is not None:
          output_dim = output_dim[1:]
          self.output_h, self.output_w = output_dim
        # endif has input dim
      else:
        self.reducer_skip = th.nn.Conv2d(
          in_channels=in_channels,
          out_channels=out_channels,
          stride=kernel_size if patching else stride,
          kernel_size=(kernel_size, kernel_size),
          bias=bias,
          dilation=dilation,
          padding=padding
        )

        if self.input_dim is not None:
          output_dim = conv_output_shape(
            h_w=self.input_dim[1:],
            stride=kernel_size if patching else stride,
            kernel_size=(kernel_size, kernel_size),
            pad=padding,
            dilation=dilation
          )
          self.output_h, self.output_w = output_dim
        # endif has input dim
      # endif use Conv2dExt or normal nn.Conv2d

      self.sep_convs = th.nn.ModuleList()
      for idx_cnv in range(n_convs):
        conv = DSepConv2d(
          in_channels=out_channels,
          out_channels=out_channels,
          kernel_size=(kernel_size, kernel_size),
          stride=1,
          padding=int((kernel_size - 1) / 2),
          bias=bias,
          input_dim=output_dim
        )
        self.sep_convs.append(conv)

        if bn:
          self.sep_convs.append(th.nn.BatchNorm2d(out_channels))

        self.sep_convs.append(activation)

        if dropout > 0:
          if dropout_type == 'classic':
            self.sep_convs.append(th.nn.Dropout(p=dropout))
          elif dropout_type == 'spatial':
            self.sep_convs.append(th.nn.Dropout2d(p=dropout))
          else:
            raise NotImplementedError("Invalid dropout type '{}'".format(dropout_type))
      # endfor
      return

    def compose_residual(self, th_x, th_y):
      return th_x + th_y

    def compose_concat(self, th_x, th_y):
      return th.cat([th_x, th_y], dim=1)

    @property
    def output_dim(self):
      return self.channel_factor * self.out_channels, self.output_h, self.output_w

    def forward(self, inputs):
      th_x_reduced = self.reducer_skip(inputs)
      th_x = th_x_reduced
      for layer in self.sep_convs:
        th_x = layer(th_x)

      out = self.compose(th_x, th_x_reduced)
      return out

    def __repr__(self):
      s = super().__repr__()

      if self.input_dim is None:
        return s

      s += " [Input={} => Output={}]".format(
        self.input_dim,
        self.output_dim
      )
      return s


  class DSepConv2DSkipModule(DSepConv2DModule):
    def __init__(self, **kwargs):
      super(DSepConv2DSkipModule, self).__init__(**kwargs)
      return

    def forward(self, inputs):
      th_x_reduced = self.reducer_skip(inputs)
      th_x = th_x_reduced
      for layer in self.sep_convs:
        th_x = layer(th_x)

      out = self.compose(th_x, th_x_reduced)
      return out, th_x_reduced
else:
  class DSepConv2DModule(th.nn.Module):
    """
    TODO: Replace DSepConv2DModule ddl 25.07.2023
    Depthwise Separable Convolution module with optional batch normalization and activation function.
    """

    def __init__(self,
                 in_channels=None,
                 out_channels=None,
                 input_dim=None,
                 n_convs=2,
                 kernel_size=3,
                 patching=True,
                 depth_multiplier=1,
                 padding=0,
                 stride=1,
                 dilation=1,
                 bn=True,
                 activation=th.nn.ReLU6(),
                 bias=False,
                 dropout=0,
                 dropout_type='classic',  # classic or spatial
                 ext_conv=False,
                 residual=True
                 ):
      """

      Parameters
      ----------
      in_channels : int, list, tuple or None, shape of the input channels
      out_channels : int, shape of the output channels
      input_dim : list, tuple or None, shape of the input tensor
      n_convs : int, optional, number of depthwise separable convolutions. The default is 2.
      kernel_size : int, optional, kernel size. The default is 3.
      patching : bool, optional, if True then stride is kernel_size. The default is True.
      depth_multiplier : int, optional, depth multiplier. The default is 1.
      padding : int, optional, padding. The default is 0.
      stride : int, optional, stride. The default is 1.
      dilation : int, optional, dilation. The default is 1.
      bn : bool, optional, if True then use batch normalization. The default is True.
      activation : th.nn.Module, optional, activation function. The default is th.nn.ReLU6().
      bias : bool, optional, if True then use bias. The default is False.
      dropout : float, optional, dropout rate. The default is 0.
      dropout_type : str, optional, dropout type. The default is 'classic'.
      ext_conv : bool, optional, if True then use Conv2dExt. The default is False.
      residual : bool, optional, if True then use residual connection. The default is True.
      """
      super().__init__()

      if isinstance(in_channels, (list, tuple)):
        # maybe someone used in_channels as input_dim
        input_dim = in_channels
        in_channels = input_dim[0]
      #endif
      if input_dim is not None:
        in_channels = input_dim[0]

      assert in_channels is not None, ValueError("DSepConv2DModule expects `in_channels` or `input_dim` parameters")
      assert out_channels is not None, ValueError("DSepConv2DModule expects `out_channels` param")

      self.input_dim = input_dim
      self.residual = residual
      self.channel_factor = 1 if residual else 2
      self.compose = self.compose_residual if self.residual else self.compose_concat
      output_dim = None
      self.out_channels, self.output_h, self.output_w = out_channels, None, None

      if ext_conv:  # this will be used after further checking
        reducer = Conv2dExt(
          input_shape=input_dim,
          in_channels=in_channels,
          out_channels=out_channels,
          stride=kernel_size if patching else stride,
          kernel_size=(kernel_size, kernel_size),
          bias=bias,
          dilation=dilation,
          padding=padding
        )

        output_dim = reducer.output_shape
        if output_dim is not None:
          output_dim = output_dim[1:]
          self.output_h, self.output_w = output_dim
        # endif has input dim
      else:
        reducer = th.nn.Conv2d(
          in_channels=in_channels,
          out_channels=out_channels,
          stride=kernel_size if patching else stride,
          kernel_size=(kernel_size, kernel_size),
          bias=bias,
          dilation=dilation,
          padding=padding
        )

        if self.input_dim is not None:
          output_dim = conv_output_shape(
            h_w=self.input_dim[1:],
            stride=kernel_size if patching else stride,
            kernel_size=(kernel_size, kernel_size),
            pad=padding,
            dilation=dilation
          )
          self.output_h, self.output_w = output_dim
        # endif has input dim
      # endif use Conv2dExt or normal nn.Conv2d

      if self.residual:
        self.reducer_res = reducer
        self._reduce_method = self._reduce_method_residual
      else:
        self.reducer_skip = reducer
        self._reduce_method = self._reduce_method_skip

      self.sep_convs = th.nn.ModuleList()
      for idx_cnv in range(n_convs):
        conv = DSepConv2d(
          in_channels=out_channels,
          out_channels=out_channels,
          kernel_size=(kernel_size, kernel_size),
          stride=1,
          padding=int((kernel_size - 1) / 2),
          bias=bias,
          input_dim=output_dim
        )
        self.sep_convs.append(conv)

        if bn:
          self.sep_convs.append(th.nn.BatchNorm2d(out_channels))

        self.sep_convs.append(activation)

        if dropout > 0:
          self.sep_convs.append(get_dropout(dropout=dropout, dropout_type=dropout_type))
      # endfor
      return

    def compose_residual(self, th_x, th_y):
      return th_x + th_y

    def compose_concat(self, th_x, th_y):
      return th.cat([th_x, th_y], dim=1)

    @property
    def output_dim(self):
      return self.channel_factor * self.out_channels, self.output_h, self.output_w

    def _reduce_method_residual(self, th_inputs):
      return self.reducer_res(th_inputs)

    def _reduce_method_skip(self, th_inputs):
      return self.reducer_skip(th_inputs)

    def reduce(self, th_inputs):
      return self._reduce_method(th_inputs)

    def forward(self, inputs):
      th_x_reduced = self.reduce(inputs)
      th_x = th_x_reduced
      for layer in self.sep_convs:
        th_x = layer(th_x)

      out = self.compose(th_x, th_x_reduced)
      return out

    def __repr__(self):
      s = super().__repr__()

      if self.input_dim is None:
        return s

      s += " [Input={} => Output={}]".format(
        self.input_dim,
        self.output_dim
      )
      return s


  class DSepConv2DSkipModule(DSepConv2DModule):
    def __init__(self, **kwargs):
      super(DSepConv2DSkipModule, self).__init__(**kwargs)
      return

    def forward(self, inputs):
      th_x_reduced = self.reduce(inputs)
      th_x = th_x_reduced
      for layer in self.sep_convs:
        th_x = layer(th_x)

      out = self.compose(th_x, th_x_reduced)
      return out, th_x_reduced


class ConvResizer(th.nn.Module):
  def __init__(self, input_dim, output_dim, apply_conv=True, **kwargs):
    super(ConvResizer, self).__init__(**kwargs)
    self.input_dim = input_dim
    self.output_dim = output_dim
    self.output_size = output_dim[-2:]
    self.out_channels = output_dim[-3] if apply_conv else input_dim[0]
    if apply_conv:
      self.conv = th.nn.Conv2d(
        in_channels=input_dim[0],
        out_channels=self.out_channels,
        kernel_size=1,
        stride=1
      )
    else:
      self.conv = IdentityLayer()
    # endif apply_conv
    self.resizer = th.nn.AdaptiveAvgPool2d(output_size=output_dim[-2:])

    return

  def __repr__(self):
    s = super().__repr__()

    if self.input_dim is None:
      return s

    s += " [Input={} => Output={}]".format(
      self.input_dim,
      self.output_dim
    )
    return s

  def forward(self, th_x):
    th_x_resized = self.resizer(th_x)
    th_x = self.conv(th_x_resized)
    return th_x


class XceptionBlock(th.nn.Module):
  def __init__(self,
               in_filters,
               out_filters,
               reps,
               strides=1,
               start_with_relu=True,
               grow_first=True
               ):
    super().__init__()

    if out_filters != in_filters or strides != 1:
      self.skip = th.nn.Conv2d(in_filters, out_filters, 1, stride=strides, bias=False)
      self.skipbn = th.nn.BatchNorm2d(out_filters)
    else:
      self.skip = None
    
    self.relu = th.nn.ReLU(inplace=True)
    rep = []

    filters = in_filters
    if grow_first:
      rep.append(self.relu)
      rep.append(DSepConv2d(in_filters, out_filters, 3, stride=1, padding=1, bias=False))
      rep.append(th.nn.BatchNorm2d(out_filters))
      filters = out_filters

    for i in range(reps-1):
      rep.append(self.relu)
      rep.append(DSepConv2d(filters, filters, 3, stride=1, padding=1, bias=False))
      rep.append(th.nn.BatchNorm2d(filters))
    
    if not grow_first:
      rep.append(self.relu)
      rep.append(DSepConv2d(in_filters, out_filters, 3, stride=1, padding=1, bias=False))
      rep.append(th.nn.BatchNorm2d(out_filters))

    if not start_with_relu:
      rep = rep[1:]
    else:
      rep[0] = th.nn.ReLU(inplace=False)

    if strides != 1:
      rep.append(th.nn.MaxPool2d(3, strides, 1))
    self.reps = th.nn.Sequential(*rep)

  def forward(self, inputs):
    th_x = self.reps(inputs)

    if self.skip is not None:
      th_skip = self.skip(inputs)
      th_skip = self.skipbn(th_skip)
    else:
      th_skip = inputs

    th_x += th_skip
    
    out = th_x
    return out

  
if __name__ == '__main__':
  
  TEST1 = False
  TEST2 = True
  
  if TEST1: 
      
    class Test(th.nn.Module):
      def __init__(self, input_dim):
        super().__init__()
        self.m1 = DSepConv2DModule(out_channels=32, input_dim=input_dim)
        self.m2 = DSepConv2DModule(out_channels=64, in_channels=self.m1.output_dim[0])
        self.gmp = GlobalMaxPool2d()
      
      def forward(self, inputs):
        th_x = inputs
        th_x = self.m1(th_x)
        th_x = self.m2(th_x)
        out = self.gmp(th_x)
        return out
      
    
    m = Test((3, 512, 512))
    print(m)
    th_in = th.rand((1, 3, 512, 512))
    th_out = m(th_in)
    print(th_out.shape)
  
  if TEST2:
    # example of LP tranforming
    mst = SpatialTransformerNet(
      input_shape=(1,120,200), 
      loc_layers=[(8,7), (10,5)], 
      affine_reshape=(1, 24, 94),
    )
    inp = th.rand((1, 1, 120, 200))
    print(mst)  
    o = mst(inp)
    print(o[0].shape)
    print(o[1].shape)
    
  

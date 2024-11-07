import torch as th
from naeural_core.local_libraries.nn.th.layers import (
  ConcatenateLayer,
  Conv2dExt,
  DSepConv2DModule,
  DSepConv2DSkipModule,
  ConvResizer,
  IdentityLayer,
  EmbeddingTransform,
)
from naeural_core.local_libraries.nn.utils import get_dropout_rate
from naeural_core.local_libraries.nn.th.utils import l2distance, get_optimizer_class, get_activation, get_dropout


class CNNColumn(th.nn.Module):
  """
  Convolutional Neural Network Column class that applies a series of convolutional layers.
  """
  def __init__(
      self, lst_filters, layer_norm,
      in_channels=None, input_dim=None,
      nconv=1, stride_multiplier=1,
      act='relu', dropout=0, dropout_type='classic',
      dropout_value_type='constant',
      skipping='residual', resize_conv=False
  ):
    """
    Parameters
    ----------
    lst_filters : list(tuple(int, int)), list of tuples defining the number of filters and kernel size for each layer
    layer_norm : bool, whether to apply layer normalization
    in_channels : int or list or tuple, number of input channels
    input_dim : tuple of int, input dimensions
    nconv : int, number of times to apply a certain convolutional layer
    stride_multiplier : int, stride multiplier for the first layer
    act : str, activation function
    dropout : float, dropout rate
    dropout_type : str, type of dropout
    dropout_value_type : str, type of dropout value
    skipping : str, type of skipping connection
    resize_conv : bool, whether to apply a convolutional resizer
    """
    super().__init__()
    if (input_dim is None or not all(input_dim)) and layer_norm is True:
      raise ValueError("Cannot apply layer norm without knowing input shapes")

    if input_dim is None or not any(input_dim):
      input_dim = (in_channels, None, None)

    assert any(input_dim), ValueError("CNNColumn expects 'in_channels' or 'input_dim' parameters")

    if not isinstance(act, th.nn.Module):
      act = get_activation(act)
    # endif
    self.input_dim = input_dim
    self.residual = False if skipping == 'skip' else True
    self.backbone = self.backbone_skip_forward if skipping == 'both' else self.backbone_forward
    conv_module = DSepConv2DSkipModule if skipping == 'both' else DSepConv2DModule
    reduced_dim = None

    self.column = th.nn.ModuleList()
    for i in range(len(lst_filters)):
      dropout_rate = get_dropout_rate(
        dropout=dropout,
        step=i,
        max_step=len(lst_filters) - 1,
        value_type=dropout_value_type
      )
      self.column.append(
        conv_module(
          input_dim=input_dim,
          out_channels=lst_filters[i][0],
          n_convs=nconv,
          kernel_size=lst_filters[i][1],
          stride=lst_filters[i][2] if len(lst_filters[i]) > 2 else (
            lst_filters[i][1] * stride_multiplier if i == 0 else 1),
          dropout=dropout_rate,
          dropout_type=dropout_type,
          activation=act,
          patching=False
        )
      )
      input_dim = self.column[-1].output_dim
      if reduced_dim is None:
        reduced_dim = input_dim
      # endif reduced_dim
    # endfor
    if skipping == 'both':
      self.linear_resizer = ConvResizer(
        input_dim=reduced_dim,
        output_dim=input_dim,
        apply_conv=resize_conv
      )
      input_dim = (input_dim[0] + self.linear_resizer.out_channels, *input_dim[1:])
    else:
      self.linear_resizer = None
    # endif skipping

    self.output_dim = input_dim
    if layer_norm:
      self.layer_norm = th.nn.LayerNorm(input_dim)
    else:
      self.layer_norm = IdentityLayer()
    # endif layer_norm
    return

  def backbone_forward(self, th_x):
    for layer in self.column:
      th_x = layer(th_x)

    return th_x

  def backbone_skip_forward(self, th_x):
    th_x, th_x_reduced = self.column[0](th_x)
    for layer in self.column[1:]:
      th_x, _ = layer(th_x)
    th_x_reduced = self.linear_resizer(th_x_reduced)

    return th.cat([th_x, th_x_reduced], dim=1)

  def forward(self, th_x):
    th_x = self.backbone(th_x)

    th_x = self.layer_norm(th_x)

    return th_x

  def __repr__(self):
    s = super().__repr__()

    if self.input_dim is None:
      return s

    s += " [Input={} => Output={}]".format(
      self.input_dim,
      self.output_dim
    )
    return s


class ImageEncoder(th.nn.Module):
  def __init__(
      self, filters, stride_multiplier, cnn_act, layer_norm, nconv,
      in_channels=None, embedding_transform='gmp/gap', input_dim=None,
      dropout=0, dropout_type='classic', dropout_value_type='constant',
      resize_conv=False, skipping='residual', equalize_columns=False,
      equalize_method='min'
  ):
    """
    TODO: Docstrings

    TODO: Implement output_dim
    """
    super().__init__()

    if input_dim is None or not any(input_dim):
      input_dim = (in_channels, None, None)

    assert any(input_dim), ValueError("ImageEncoder expects 'in_channels' or 'input_dim' parameters")
    self.input_dim = input_dim

    self.output_filters = 0
    columns = []
    for i in range(len(filters)):
      tf_x_column = CNNColumn(
        input_dim=input_dim,
        lst_filters=filters[i],
        act=cnn_act,
        stride_multiplier=stride_multiplier,
        layer_norm=layer_norm,
        nconv=nconv,
        dropout=dropout,
        dropout_type=dropout_type,
        dropout_value_type=dropout_value_type,
        skipping=skipping,
        resize_conv=resize_conv
      )
      transform = EmbeddingTransform(
        type=embedding_transform,
        input_dim=tf_x_column.output_dim
      )
      columns.append([tf_x_column, transform])
    # endfor columns
    embedding_sizes = [transform.output_dim for _, transform in columns]
    if equalize_columns:
      # Equalize the number of output filters for each column
      # First, decide the embedding size
      if equalize_method == 'min':
        embedding_size = min(embedding_sizes)
      elif equalize_method == 'max':
        embedding_size = max(embedding_sizes)
      else:
        raise ValueError(f"Equalize method {equalize_method} not implemented")
      # endif equalize_method
      # Second, add a linear layer to each column to equalize the number of filters
      for i in range(len(columns)):
        columns[i].append(th.nn.Linear(embedding_sizes[i], embedding_size))
      # endfor columns
      self.output_filters = len(columns) * embedding_size
    else:
      self.output_filters = sum(embedding_sizes)
    # endif equalize_columns
    columns = [th.nn.Sequential(*column) for column in columns]
    self.columns = th.nn.ModuleList(columns)
    # TODO: fix verbosity
    return

  def forward(self, x):
    results = []

    # x  self.stem(x)

    for column in self.columns:
      results.append(column(x))

    results = th.cat(results, dim=1)
    return results

  @property
  def output_dim(self):
    return self.output_filters

  def __repr__(self):
    s = super().__repr__()

    if self.input_dim is None:
      return s

    s += " [Input={} => Output={}]".format(
      self.input_dim,
      self.output_dim
    )
    return s


class StemLayer(th.nn.Module):
  def __init__(
    self, filters, in_channels=None, input_dim=None, act='relu', skip_last_act=False, **kwargs
  ):
    """
    Neural Network Stem Layer class that applies a series of convolutional layers
    for the initial part of the network with the purpose of feature extraction.
    Parameters
    ----------
    filters : list(int) or list(tuple(int)), list of filters for each layer
    in_channels : int, number of input channels
    input_dim : tuple of int, input dimensions
    act : str, activation function
    skip_last_act : bool, whether to skip the last activation function
    kwargs : dict, additional parameters
    """
    super().__init__()
    if input_dim is None or not any(input_dim):
      input_dim = (in_channels, None, None)
    assert any(input_dim), ValueError("StemLayer expects 'in_channels' or 'input_dim' parameters")

    self.input_dim = input_dim
    current_input_shape = input_dim
    stem_layers = []
    for i, layer_filter in enumerate(filters):
      if isinstance(layer_filter, int):
        layer_filter = [layer_filter]
      current_layer = Conv2dExt(
        input_shape=current_input_shape,
        in_channels=current_input_shape[0],
        out_channels=layer_filter[0],
        kernel_size=layer_filter[1] if len(layer_filter) > 1 else 3,
        stride=layer_filter[2] if len(layer_filter) > 2 else 2
      )
      stem_layers.append(current_layer)
      if not skip_last_act or i < len(filters) - 1:
        stem_layers.append(get_activation(act))
      current_input_shape = current_layer.output_shape
    # endfor layers
    self.stem = th.nn.Sequential(*stem_layers)
    self.output_dim = current_input_shape
    return

  def forward(self, x):
    return self.stem(x)

  def __repr__(self):
    s = super().__repr__()

    if self.input_dim is None:
      return s

    s += " [Input={} => Output={}]".format(
      self.input_dim,
      self.output_dim
    )
    return s


class ImageEncoderWithStem(th.nn.Module):
  def __init__(self, filters, stride_multiplier, cnn_act, layer_norm, nconv, stem_layer, use_stem_activation, in_channels=None, embedding_transform=None, input_dim=None):
    """
    TODO: Docstrings

    TODO: Implement output_dim
    """
    super().__init__()

    if input_dim is None or not any(input_dim):
      input_dim = (in_channels, None, None)

    assert any(input_dim), ValueError("ImageEncoderWithStem expects 'in_channels' or 'input_dim' parameters")

    self.input_dim = input_dim
    stem_layers = []
    stem_layers.append(
      Conv2dExt(
        input_shape=input_dim,

        in_channels=input_dim[0],
        out_channels=stem_layer[0],
        kernel_size=stem_layer[1],
        stride=stem_layer[2] if len(stem_layer) > 2 else 2,
      )
    )

    if use_stem_activation:
      stem_layers.append(th.nn.ReLU())
    self.stem = th.nn.Sequential(*stem_layers)

    self.output_filters = 0
    columns = []
    for i in range(len(filters)):
      tf_x_column = CNNColumn(
        input_dim=stem_layers[0].output_shape,
        lst_filters=filters[i],
        act=cnn_act,
        stride_multiplier=stride_multiplier,
        layer_norm=layer_norm,
        nconv=nconv
      )
      transform = EmbeddingTransform(
          type=embedding_transform,
          input_dim=tf_x_column.output_dim
        )
      columns.append(th.nn.Sequential(
        tf_x_column,
        transform
      ))
      self.output_filters += transform.output_dim
    self.columns = th.nn.ModuleList(columns)

  def forward(self, x):
    results = []

    x = self.stem(x)

    for column in self.columns:
      results.append(column(x))

    results = th.cat(results, dim=1)
    return results

  @property
  def output_dim(self):
    return self.output_filters

  def __repr__(self):
    s = super().__repr__()

    if self.input_dim is None:
      return s

    s += " [Input={} => Output={}]".format(
      self.input_dim,
      self.output_dim
    )
    return s


class ReadoutConv(th.nn.Module):
  """
  Readout Convolutional Neural Network class that applies a series of depth separable convolutional layers.
  """
  def __init__(
      self, lst_convs, in_channels, input_dim,
      act='relu6', dropout=0, embedding_type='flatten',
      dropout_type='classic', dropout_value_type='constant'
  ):
    """

    Parameters
    ----------
    lst_convs : list(int), list of number of filters for each layer
    in_channels : int, number of input channels
    input_dim : tuple of int, input dimensions
    act : str, activation function
    dropout : float, dropout rate
    embedding_type : str, type of embedding
    dropout_type : str, type of dropout
    dropout_value_type : str, type of dropout value applied after each depthwise separable convolutional layer.
      This is set to '' by default for backward compatibility.
    """
    super(ReadoutConv, self).__init__()

    self.lst_convs = lst_convs

    lst_blocks = []
    self.output_dim = input_dim
    self.in_channels = in_channels
    for step, out_channels in enumerate(lst_convs):
      dropout_rate = get_dropout_rate(
        dropout=dropout,
        step=step,
        max_step=len(lst_convs) - 1,
        value_type=dropout_value_type
      )
      conv = DSepConv2DModule(
        in_channels=in_channels,
        out_channels=out_channels,
        n_convs=1,
        kernel_size=3,
        input_dim=input_dim,
        activation=get_activation(act),
        patching=False,
        stride=1,
        dropout=dropout_rate,
        dropout_type=dropout_type
      )
      input_dim = conv.output_dim
      self._output_dim = conv.output_dim
      lst_blocks.append(conv)
      in_channels = out_channels
    # endfor

    self.blocks = th.nn.Sequential(*lst_blocks)
    self.embedding = EmbeddingTransform(
      type=embedding_type,
      input_dim=self._output_dim
    )
    self.dropout_rate = dropout
    self.dropout = get_dropout(dropout=self.dropout_rate, dropout_type=dropout_type)
    return

  @property
  def output_size(self):
    return self.embedding.output_dim
    # if len(self.lst_convs) > 0:
    #   nr_channels = self.lst_convs[-1]
    # else:
    #   nr_channels = self.in_channels
    # # if self.transformation == 'gmp/gap':
    # #   size = 2 * nr_channels
    # # elif self.transformation == 'flatten':
    # h, w = self._output_dim
    # size = h * w * nr_channels
    # return size

  def forward(self, th_x):
    th_x = self.blocks(th_x)
    th_x = self.embedding(th_x)
    th_x = self.dropout(th_x)
    return th_x


class ReadoutFC(th.nn.Module):
  def __init__(
      self, lst_fc_sizes, in_features, dropout, act='relu',
      dropout_type='classic', dropout_value_type='constant'
  ):
    super(ReadoutFC, self).__init__()
    self.in_features = in_features
    self.fc_sizes = lst_fc_sizes

    lst_fc = []
    for step, fc_size in enumerate(lst_fc_sizes):
      lst_fc.append(
        th.nn.Linear(
          in_features=in_features,
          out_features=fc_size
        )
      )
      in_features = fc_size

      lst_fc.append(get_activation(act))
      lst_fc.append(get_dropout(
        dropout=dropout,
        dropout_type=dropout_type,
        step=step,
        max_step=len(lst_fc_sizes) - 1,
        value_type=dropout_value_type
      ))
    # endfor
    self.fcs = th.nn.Sequential(*lst_fc)
    return

  @property
  def output_size(self):
    if len(self.fc_sizes) > 0:
      return self.fc_sizes[-1]
    return self.in_features

  def forward(self, th_x):
    th_out = self.fcs(th_x)
    return th_out

  def __repr__(self):
    s = super().__repr__()
    s += " [Input={} => Output={}]".format(
      self.in_features,
      self.output_size
    )
    return s

class Readout(th.nn.Module):
  def __init__(
      self, lst_coords_sizes, input_shape, dropout, act='relu',
      readout_act=None, nr_outputs=1, use_conv_dropout=False,
      embedding_type='flatten', lst_fc_preredout=None, dropout_type='classic',
      dropout_value_type='constant'
  ):
    super(Readout, self).__init__()

    transformation = lst_coords_sizes[0]
    assert transformation in ['gmp/gap', 'flatten', 'conv', 'identity']

    self.pre_readout = None
    if transformation in ['gmp/gap', 'flatten', 'identity']:
      transformation, lst_sizes = lst_coords_sizes

      self.pre_readout = ReadoutFC(
        lst_fc_sizes=lst_sizes,
        in_features=input_shape[0] if isinstance(input_shape, (list, tuple)) else input_shape,
        dropout=dropout,
        act=act,
        dropout_type=dropout_type,
        dropout_value_type=dropout_value_type
      )
    else:
      transformation, lst_sizes = lst_coords_sizes
      conv_dropout = dropout if use_conv_dropout else 0
      self.pre_readout = ReadoutConv(
        lst_convs=lst_sizes,
        in_channels=input_shape[0],
        input_dim=input_shape[1:],
        act=act,
        dropout=conv_dropout,
        dropout_type=dropout_type,
        dropout_value_type=dropout_value_type,
        embedding_type=embedding_type
      )

    lst_coords = []
    prev_units = self.pre_readout.output_size
    if lst_fc_preredout is None:
      lst_fc_preredout = []
    for nr_out, dropout in lst_fc_preredout:
      lst_coords.append(
        th.nn.Linear(
          in_features=prev_units,
          out_features=nr_out
        )
      )
      lst_coords.append(th.nn.ReLU())
      if dropout > 0:
        lst_coords.append(th.nn.Dropout(dropout))
      prev_units = nr_out
    lst_coords.append(
      th.nn.Linear(
        in_features=prev_units,
        out_features=nr_outputs
      )
    )

    if readout_act is not None and readout_act != 'linear':
      lst_coords.append(get_activation(readout_act))

    self.readout = th.nn.Sequential(*lst_coords)
    return

  def forward(self, th_x):
    th_x = self.pre_readout(th_x)
    th_res = self.readout(th_x)
    return th_res


class ClassificationHead(th.nn.Module):
  def __init__(
      self, nr_outputs, lst_coords_sizes, input_shape,
      dropout=0, individual_heads=False, act='relu',
      readout_act=None, use_conv_dropout=False, **kwargs
  ):
    """
    TODO: maybe add ordinal regression support
    Parameters
    ----------
    nr_outputs
    lst_coords_sizes
    input_shape
    dropout
    individual_heads
    act
    readout_act
    use_conv_dropout
    kwargs
    """
    super(ClassificationHead, self).__init__()
    self.individual_heads = individual_heads
    readout_kwargs = {
      'lst_coords_sizes': lst_coords_sizes,
      'input_shape': input_shape,
      'dropout': dropout,
      'act': act,
      'readout_act': readout_act,
      'use_conv_dropout': use_conv_dropout,
      **kwargs
    }
    if not individual_heads:
      readout_kwargs['nr_outputs'] = nr_outputs
      self.readout = Readout(**readout_kwargs)
      self.apply_readout = self._apply_single_readout
    else:
      readout_heads = []
      readout_kwargs['nr_outputs'] = 1
      for _ in range(nr_outputs):
        readout_heads.append(Readout(**readout_kwargs))
      # endfor
      self.readout_heads = th.nn.ModuleList(readout_heads)
      self.apply_readout = self._apply_multiple_readouts
    # endif

  def _apply_single_readout(self, x):
    return self.readout(x)

  def _apply_multiple_readouts(self, x):
    results = []
    for head in self.readout_heads:
      results.append(head(x))
    return th.cat(results, dim=-1)

  def forward(self, x):
    yh = self.apply_readout(x)  # (bs, 1)
    return yh


class OrdinalRegressionHead(th.nn.Module):
  def __init__(self, input_size, output_size, units_dense=None) -> None:
    super(OrdinalRegressionHead, self).__init__()
    
    
    if units_dense is None:
      units_dense = []

    layers = []
    for unit in units_dense:
      layers.append(th.nn.Linear(input_size, unit))
      layers.append(th.nn.ReLU())
      input_size = unit

    self.fc = th.nn.Sequential(*layers)
    
    self.W = th.nn.Linear(input_size, 1, bias=False)
    self.b = th.nn.Parameter(th.zeros(output_size - 1))

    self.activation = th.nn.Sigmoid()

  def forward(self, x):
    """
    From:
    (bs, X) -> (bs, nr_out) ==> M(X, nr_out)

    To:
    (bs, X) -> (bs, nr_out) ==> M(X, 1) + W(1, nr_out)
    """
    yh = self.fc(x)
    yh = self.W(yh)
    yh = yh + self.b
    return yh, self.activation(yh)


class SiameseClassifier(th.nn.Module):
  def __init__(self, nr_outputs, filters, units_dense, drop_pre_readout, merge_mode, nconv,
               cnn_act='relu', stride_multiplier=1, individual_heads=False, layer_norm=False,
               in_channels=None, image_height=None, image_width=None, input_dim=None, ordinal_regression=False, **kwargs):
    """

    :param nr_outputs: int, Mandatory
      Number of classes
    :param filters: list(list(tuple(int,int))) mandatory

    :param units_dense:
    :param drop_pre_readout:
    :param merge_mode:
    :param nconv:
    :param cnn_act:
    :param stride_multiplier:
    :param individual_heads:
    :param layer_norm:
    :param in_channels:
    :param image_height:
    :param image_width:
    :param input_dim:
    """
    super().__init__()

    if input_dim is None:
      input_dim = (in_channels, image_height, image_width)

    self.image_encoder = ImageEncoder(
      filters=filters,
      stride_multiplier=stride_multiplier,
      cnn_act=cnn_act,
      layer_norm=layer_norm,
      nconv=nconv,
      input_dim=input_dim,
      embedding_transform='gmp/gap',
    )

    self.merge_mode = merge_mode

    output_filters = self.image_encoder.output_filters

    if self.merge_mode == 'CONCAT':
      self.diff_squeeze = None
      no_feats = 2 * output_filters
    elif self.merge_mode == 'DIFFERENCE':
      self.diff_squeeze = None
      no_feats = output_filters
    elif self.merge_mode == 'DIFF_CONCAT':
      self.diff_squeeze = None
      no_feats = 2 * output_filters
    elif self.merge_mode == 'DIFF_SQ_CONCAT':
      self.diff_squeeze = th.nn.Linear(
        in_features=output_filters,
        out_features=output_filters // 8
      )
      no_feats = output_filters + output_filters // 8
    else:
      raise NotImplementedError("Merge mode {} not implemented".format(self.merge_mode))
    # end

    if ordinal_regression:
      self.readout = OrdinalRegressionHead(
        input_size=no_feats,
        output_size=nr_outputs,
        units_dense=units_dense,
      )
    else:
      self.readout = ClassificationHead(
        nr_outputs=nr_outputs,
        lst_coords_sizes=('gmp/gap', units_dense),
        input_shape=(no_feats,),
        dropout=drop_pre_readout,
        individual_heads=individual_heads
      )

  def forward(self, *args, **kwargs):
    if len(args) > 0:
      anchor_imgs, test_imgs = args[0]
    else:
      anchor_imgs, test_imgs = kwargs['anchor_imgs'], kwargs['test_imgs']

    anch_enc = self.image_encoder(anchor_imgs)
    test_enc = self.image_encoder(test_imgs)

    if self.merge_mode == 'CONCAT':
      th_x = th.cat([anch_enc, test_enc], dim=-1)
    elif self.merge_mode == 'DIFFERENCE':
      th_x = l2distance(anch_enc, test_enc)
    elif self.merge_mode == 'DIFF_CONCAT':
      th_diff = l2distance(anch_enc, test_enc)
      th_x = th.cat([th_diff, test_enc], dim=-1)
    elif self.merge_mode == 'DIFF_SQ_CONCAT':
      th_diff = l2distance(anch_enc, test_enc)
      tf_diff_sq = self.diff_squeeze(th_diff)
      th_x = th.cat([tf_diff_sq, test_enc], dim=-1)
    else:
      raise NotImplementedError("Merge mode {} not implemented".format(self.merge_mode))
    # end

    th_y_hat = self.readout(th_x)

    return th_y_hat


class SiameseClassifierWithStem(th.nn.Module):
  def __init__(self, nr_outputs, filters, units_dense, drop_pre_readout, merge_mode, nconv, stem_layer, use_stem_activation=False,
               cnn_act='relu', stride_multiplier=1, individual_heads=False, layer_norm=False,
               in_channels=None, image_height=None, image_width=None, input_dim=None, ordinal_regression=False, **kwargs):
    """

    :param nr_outputs: int, Mandatory
      Number of classes
    :param filters: list(list(tuple(int,int))) mandatory

    :param units_dense:
    :param drop_pre_readout:
    :param merge_mode:
    :param nconv:
    :param cnn_act:
    :param stride_multiplier:
    :param individual_heads:
    :param layer_norm:
    :param in_channels:
    :param image_height:
    :param image_width:
    :param input_dim:
    """
    super().__init__()

    if input_dim is None:
      input_dim = (in_channels, image_height, image_width)

    self.image_encoder = ImageEncoderWithStem(
      filters=filters,
      stride_multiplier=stride_multiplier,
      cnn_act=cnn_act,
      layer_norm=layer_norm,
      nconv=nconv,
      input_dim=input_dim,
      stem_layer=stem_layer,
      use_stem_activation=use_stem_activation,
      embedding_transform='gmp/gap',
    )

    self.merge_mode = merge_mode
    self.merge = None

    output_filters = self.image_encoder.output_filters

    if self.merge_mode == 'CONCAT':
      self.diff_squeeze = None
      no_feats = 2 * output_filters
      self.merge = self._merge_mode_concat
    elif self.merge_mode == 'DIFFERENCE':
      self.diff_squeeze = None
      no_feats = output_filters
      self.merge = self._merge_mode_difference
    elif self.merge_mode == 'DIFF_CONCAT':
      self.diff_squeeze = None
      no_feats = 2 * output_filters
      self.merge = self._merge_mode_diff_concat
    elif self.merge_mode == 'DIFF_SQ_CONCAT':
      self.diff_squeeze = th.nn.Linear(
        in_features=output_filters,
        out_features=output_filters // 8
      )
      no_feats = output_filters + output_filters // 8
      self.merge = self._merge_mode_diff_sq_concat
    else:
      raise NotImplementedError("Merge mode {} not implemented".format(self.merge_mode))
    # end

    if ordinal_regression:
      self.readout = OrdinalRegressionHead(
        input_size=no_feats,
        output_size=nr_outputs,
        units_dense=units_dense,
      )
    else:
      self.readout = ClassificationHead(
        nr_outputs=nr_outputs,
        lst_coords_sizes=('gmp/gap', units_dense),
        input_shape=(no_feats,),
        dropout=drop_pre_readout,
        individual_heads=individual_heads
      )
  # Merge modes
  if True:
    def _merge_mode_concat(self, anch_enc, test_enc):
      return th.cat([anch_enc, test_enc], dim=-1)

    def _merge_mode_difference(self, anch_enc, test_enc):
      return l2distance(anch_enc, test_enc)

    def _merge_mode_diff_concat(self, anch_enc, test_enc):
      th_diff = l2distance(anch_enc, test_enc)
      return th.cat([th_diff, test_enc], dim=-1)

    def _merge_mode_diff_sq_concat(self, anch_enc, test_enc):
      th_diff = l2distance(anch_enc, test_enc)
      tf_diff_sq = self.diff_squeeze(th_diff)
      return th.cat([tf_diff_sq, test_enc], dim=-1)

  def forward(self, *args, **kwargs):
    if len(args) > 0:
      anchor_imgs, test_imgs = args[0]
    else:
      anchor_imgs, test_imgs = kwargs['anchor_imgs'], kwargs['test_imgs']

    anch_enc = self.image_encoder(anchor_imgs)
    test_enc = self.image_encoder(test_imgs)

    th_x = self.merge(anch_enc, test_enc)

    th_y_hat = self.readout(th_x)

    return th_y_hat


class SiameseClassifierRefactorBackbone(th.nn.Module):
  def __init__(self, filters, merge_mode, nconv, cnn_act='relu', stride_multiplier=1, layer_norm=False,
               in_channels=None, image_height=None, image_width=None, input_dim=None,
               **kwargs):
    """

    :param nr_outputs: int, Mandatory
      Number of classes
    :param filters: list(list(tuple(int,int))) mandatory

    :param units_dense:
    :param drop_pre_readout:
    :param merge_mode:
    :param nconv:
    :param cnn_act:
    :param stride_multiplier:
    :param individual_heads:
    :param layer_norm:
    :param in_channels:
    :param image_height:
    :param image_width:
    :param input_dim:
    """
    super().__init__()

    if input_dim is None:
      input_dim = (in_channels, image_height, image_width)

    self.image_encoder = ImageEncoder(
      filters=filters,
      stride_multiplier=stride_multiplier,
      cnn_act=cnn_act,
      layer_norm=layer_norm,
      nconv=nconv,
      input_dim=input_dim
    )

    self.merge_mode = merge_mode

    self.transform = EmbeddingTransform(
      type='gmp/gap',
      input_dim=(self.image_encoder.output_filters, None, None)
    )
    output_filters = self.transform.output_dim

    self.merge = None

    if self.merge_mode == 'CONCAT':
      self.diff_squeeze = None
      self.no_feats = 2 * output_filters
      self.merge = self._merge_mode_concat
    elif self.merge_mode == 'DIFFERENCE':
      self.diff_squeeze = None
      self.no_feats = output_filters
      self.merge = self._merge_mode_difference
    elif self.merge_mode == 'DIFF_CONCAT':
      self.diff_squeeze = None
      self.no_feats = 2 * output_filters
      self.merge = self._merge_mode_diff_concat
    elif self.merge_mode == 'DIFF_SQ_CONCAT':
      self.diff_squeeze = th.nn.Linear(
        in_features=output_filters,
        out_features=output_filters // 8
      )
      self.no_feats = output_filters + output_filters // 8
      self.merge = self._merge_mode_diff_sq_concat
    else:
      raise NotImplementedError("Merge mode {} not implemented".format(self.merge_mode))
    # end

  # Merge modes
  if True:
    def _merge_mode_concat(self, anch_enc, test_enc):
      return th.cat([anch_enc, test_enc], dim=-1)

    def _merge_mode_difference(self, anch_enc, test_enc):
      return l2distance(anch_enc, test_enc)

    def _merge_mode_diff_concat(self, anch_enc, test_enc):
      th_diff = l2distance(anch_enc, test_enc)
      return th.cat([th_diff, test_enc], dim=-1)

    def _merge_mode_diff_sq_concat(self, anch_enc, test_enc):
      th_diff = l2distance(anch_enc, test_enc)
      tf_diff_sq = self.diff_squeeze(th_diff)
      return th.cat([tf_diff_sq, test_enc], dim=-1)

  def get_encodings(self, th_images):
    return self.transform(self.image_encoder(th_images))

  def forward(self, th_anchor_encodings, th_images):
    th_image_encodings = self.get_encodings(th_images)

    th_x = self.merge(th_anchor_encodings, th_image_encodings)

    return th_x


class SiameseClassifierRefactor(SiameseClassifierRefactorBackbone):
  def __init__(self, nr_outputs, units_dense, drop_pre_readout, individual_heads, *args, **kwargs):
    super().__init__(
      nr_outputs=nr_outputs,
      units_dense=units_dense,
      drop_pre_readout=drop_pre_readout,
      individual_heads=individual_heads,
      *args,
      **kwargs
    )
    self.readout = ClassificationHead(
        nr_outputs=nr_outputs,
        lst_coords_sizes=('gmp/gap', units_dense),
        input_shape=(self.no_feats,),
        dropout=drop_pre_readout,
        individual_heads=individual_heads
      )

    return

  def predict(self, th_readout):
    with th.no_grad():
      th_preds = th.argmax(th_readout, axis=-1)
    return th_preds

  def get_debug_info(self, th_readout):
    return th_readout

  def forward(self, th_anchor_encodings, th_images):
    th_x = super()(th_anchor_encodings, th_images)
    th_readout = self.readout(th_x)

    return th_readout


class SiameseClassifierRefactorOrdinal(SiameseClassifierRefactorBackbone):
  def __init__(self, nr_outputs, *args, threshold=0.5, **kwargs):
    super().__init__(
      nr_outputs=nr_outputs,
      *args,
      **kwargs
    )
    self.threshold = threshold
    self.readout = OrdinalRegressionHead(
      input_size=self.no_feats,
      output_size=nr_outputs
    )

  def predict(self, th_readout):
    with th.no_grad():
      th_preds = th.sum(th_readout[1] > self.threshold, axis=-1) + 1
    return th_preds

  def get_debug_info(self, th_readout):
    return th_readout[1]

  def forward(self, th_anchor_encodings, th_images):
    th_x = super().forward(th_anchor_encodings, th_images)
    th_readout = self.readout(th_x)

    return th_readout


class SiameseClassifierRefactorWithStemBackbone(th.nn.Module):
  def __init__(self, nr_outputs, filters, units_dense, drop_pre_readout, merge_mode, nconv, stem_layer, use_stem_activation=False,
               cnn_act='relu', stride_multiplier=1, individual_heads=False, layer_norm=False,
               in_channels=None, image_height=None, image_width=None, input_dim=None, ordinal_regression=False, **kwargs):
    """

    :param nr_outputs: int, Mandatory
      Number of classes
    :param filters: list(list(tuple(int,int))) mandatory

    :param units_dense:
    :param drop_pre_readout:
    :param merge_mode:
    :param nconv:
    :param cnn_act:
    :param stride_multiplier:
    :param individual_heads:
    :param layer_norm:
    :param in_channels:
    :param image_height:
    :param image_width:
    :param input_dim:
    """
    super().__init__()

    if input_dim is None:
      input_dim = (in_channels, image_height, image_width)

    self.image_encoder = ImageEncoderWithStem(
      filters=filters,
      stride_multiplier=stride_multiplier,
      cnn_act=cnn_act,
      layer_norm=layer_norm,
      nconv=nconv,
      input_dim=input_dim,
      stem_layer=stem_layer,
      use_stem_activation=use_stem_activation,
      embedding_transform='gmp/gap',
    )

    self.merge_mode = merge_mode
    self.merge = None

    output_filters = self.image_encoder.output_filters

    if self.merge_mode == 'CONCAT':
      self.diff_squeeze = None
      self.no_feats = 2 * output_filters
      self.merge = self._merge_mode_concat
    elif self.merge_mode == 'DIFFERENCE':
      self.diff_squeeze = None
      self.no_feats = output_filters
      self.merge = self._merge_mode_difference
    elif self.merge_mode == 'DIFF_CONCAT':
      self.diff_squeeze = None
      self.no_feats = 2 * output_filters
      self.merge = self._merge_mode_diff_concat
    elif self.merge_mode == 'DIFF_SQ_CONCAT':
      self.diff_squeeze = th.nn.Linear(
        in_features=output_filters,
        out_features=output_filters // 8
      )
      self.no_feats = output_filters + output_filters // 8
      self.merge = self._merge_mode_diff_sq_concat
    else:
      raise NotImplementedError("Merge mode {} not implemented".format(self.merge_mode))
    # end

  # Merge modes
  if True:
    def _merge_mode_concat(self, anch_enc, test_enc):
      return th.cat([anch_enc, test_enc], dim=-1)

    def _merge_mode_difference(self, anch_enc, test_enc):
      return l2distance(anch_enc, test_enc)

    def _merge_mode_diff_concat(self, anch_enc, test_enc):
      th_diff = l2distance(anch_enc, test_enc)
      return th.cat([th_diff, test_enc], dim=-1)

    def _merge_mode_diff_sq_concat(self, anch_enc, test_enc):
      th_diff = l2distance(anch_enc, test_enc)
      tf_diff_sq = self.diff_squeeze(th_diff)
      return th.cat([tf_diff_sq, test_enc], dim=-1)

  def get_encodings(self, th_images):
    return self.image_encoder(th_images)

  def forward(self, th_anchor_encodings, th_images):
    th_image_encodings = self.get_encodings(th_images)

    th_x = self.merge(th_anchor_encodings, th_image_encodings)

    return th_x


class SiameseClassifierRefactorWithStemOrdinal(SiameseClassifierRefactorWithStemBackbone):
  def __init__(self, nr_outputs, *args, threshold=0.5, **kwargs):
    super().__init__(
      nr_outputs=nr_outputs,
      *args,
      **kwargs
    )
    self.threshold = threshold
    self.readout = OrdinalRegressionHead(
      input_size=self.no_feats,
      output_size=nr_outputs
    )

  def predict(self, th_readout):
    with th.no_grad():
      th_preds = th.sum(th_readout[1] > self.threshold, axis=-1) + 1
    return th_preds

  def get_debug_info(self, th_readout):
    return th_readout[1]

  def forward(self, th_anchor_encodings, th_images):
    th_x = super().forward(th_anchor_encodings, th_images)
    th_readout = self.readout(th_x)

    return th_readout


class SiameseClassifierRefactorWithStem(SiameseClassifierRefactorWithStemBackbone):
  def __init__(self, nr_outputs, units_dense, drop_pre_readout, individual_heads, *args, **kwargs):
    super().__init__(
      nr_outputs=nr_outputs,
      units_dense=units_dense,
      drop_pre_readout=drop_pre_readout,
      individual_heads=individual_heads,
      *args,
      **kwargs
    )
    self.readout = ClassificationHead(
        nr_outputs=nr_outputs,
        lst_coords_sizes=('gmp/gap', units_dense),
        input_shape=(self.no_feats,),
        dropout=drop_pre_readout,
        individual_heads=individual_heads
      )

    return

  def predict(self, th_readout):
    with th.no_grad():
      th_preds = th.argmax(th_readout, axis=-1)
    return th_preds

  def get_debug_info(self, th_readout):
    return th_readout

  def forward(self, th_anchor_encodings, th_images):
    th_x = super()(th_anchor_encodings, th_images)
    th_readout = self.readout(th_x)

    return th_readout

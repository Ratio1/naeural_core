import torch as th
from naeural_core.local_libraries.nn.th.layers import DenseBlock
from naeural_core.local_libraries.nn.utils import get_dropout


class DenseEncoder(th.nn.Module):
  def __init__(self, input_size, layer_sizes, dropout=('constant', 0.3), use_batch_norm='pre', activation=None):
    super(DenseEncoder, self).__init__()
    if isinstance(dropout, tuple):
      self.dropout_type, self.dropout = dropout
    else:
      self.dropout_type = 'constant'
      self.dropout = dropout
    self.output_size = layer_sizes[-1]
    n_layers = len(layer_sizes)

    layers = []
    current_input = input_size
    for i in range(n_layers):
      current_dropout = get_dropout(
        dropout_type=self.dropout_type,
        dropout=self.dropout,
        step=i,
        max_step=n_layers - 1
      )
      layers.append(
        DenseBlock(
          in_channels=current_input,
          n_layers=layer_sizes[i],
          dropout=current_dropout,
          use_batch_norm=use_batch_norm,
          activation=activation
        )
      )
      current_input = layer_sizes[i]

    self.layers = th.nn.Sequential(*layers)

    return

  def forward(self, th_x):
    return self.layers(th_x)


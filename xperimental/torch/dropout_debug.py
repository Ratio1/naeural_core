import torch as th


if __name__ == '__main__':
  shape = (4, 4, 4, 4, 2)
  inp = th.randn(shape)
  dropout_layer = th.nn.Dropout(p=0.5)
  flatten_layer = th.nn.Flatten()
  th.manual_seed(0)
  inp_drop = dropout_layer(inp)
  inp_drop_flatten = flatten_layer(inp_drop)

  inp_flatten = flatten_layer(inp)
  th.manual_seed(0)
  inp_flatten_drop = dropout_layer(inp_flatten)

  print(th.abs(inp_drop_flatten - inp_flatten_drop))

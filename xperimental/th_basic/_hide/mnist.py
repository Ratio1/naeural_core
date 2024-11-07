"""
[THTST][2021-10-06 18:08:11] Dev acc: 0.9880
[THTST][2021-10-06 18:08:11] Timing results:
[THTST][2021-10-06 18:08:11]  eager_run = 0.0008s, max: 0.0030s, curr: 0.0010s

"""
import os
import numpy as np
import torch as th
import torchvision as tv

from naeural_core import Logger
from naeural_core.local_libraries.nn.th.trainer import ModelTrainer
from naeural_core.local_libraries.nn.th.layers import InputPlaceholder
from naeural_core.local_libraries.nn.th.utils import conv_output_shape



if __name__ == '__main__':

  class ConvBlock(th.nn.Module):
    def __init__(self, input_shape, k, f, s , act):
      super().__init__()
      prev_ch, prev_h, prev_w = input_shape
      self.conv = th.nn.Conv2d(
        in_channels=prev_ch,
        out_channels=f,
        kernel_size=k,
        stride=s)
      self.bn = th.nn.BatchNorm2d(num_features=f)
      self.output_shape = conv_output_shape(
          h_w=(prev_h, prev_w),
          kernel_size=k,
          stride=s,
          )
      self.act = th.nn.LeakyReLU()
      return
    
    def forward(self, inputs):
      th_x = self.conv(inputs)
      th_x = self.bn(th_x)
      th_x = self.act(th_x)
      return th_x

  class SimpleModel(th.nn.Module):
    def __init__(self, 
                 input_shape=(1, 28, 28), 
                 convs=[(8, 3, 2), (16, 3, 2), (32, 3, 1)], 
                 dense=32,
                 **kwargs):
      super().__init__()
      assert len(input_shape) == 3
      self.layers = th.nn.ModuleList()
      self.layers.append(InputPlaceholder(input_shape))
      input_size = input_shape[0]
      prev_h, prev_w = input_shape[1:]
      for i, (f, k, s) in enumerate(convs):
        cnv = th.nn.Conv2d(
          in_channels=input_size, 
          out_channels=f, 
          kernel_size=k,
          stride=s
          )
        input_size = f
        new_h, new_w = conv_output_shape(
          h_w=(prev_h, prev_w),
          kernel_size=k,
          stride=s,
          )
        self.layers.append(cnv)
        self.layers.append(th.nn.ReLU6())
        prev_h = new_h
        prev_w = new_w

      self.layers.append(th.nn.Flatten())
      post_flatten_size = new_h * new_w * f
      self.layers.append(th.nn.Linear(post_flatten_size, dense))
      self.layers.append(th.nn.ReLU6())
      self.layers.append(th.nn.Dropout(0.5))
      self.layers.append(th.nn.Linear(dense, 10))
      return
    
    def forward(self, inputs):
      x = inputs
      for layer in self.layers:
        x = layer(x)
      return x
    
    
    def add_layer(self, name, module):
      self.add_module(name, module)      
      self.layers.append(getattr(self, name))
      # we can use `for layer in self.layers...`
      return
  

  l = Logger('THTST', base_folder='.', app_folder='_cache', TF_KERAS=False)
  
  train = tv.datasets.MNIST(root=l.get_data_folder(), train=True, download=True)
  test = tv.datasets.MNIST(root=l.get_data_folder(), train=False, download=True)
  
  
  (x_train, y_train), (x_dev, y_dev) = (train.data.numpy(), train.targets.numpy()), (test.data.numpy(), test.targets.numpy())
  x_train = (x_train.reshape((-1, 1, 28, 28)) / 255.).astype('float32')
  x_dev = (x_dev.reshape((-1, 1, 28, 28)) / 255.).astype('float32')
  y_train = y_train.astype('int64')
  y_dev = y_dev.astype('int64')
  
  l.gpu_info(True)
  
  model = SimpleModel()
  model_loss = th.nn.CrossEntropyLoss()
    
  trainer = ModelTrainer(
    log=l, 
    model=model, 
    losses=model_loss, 
    validation_data=(x_dev, y_dev),
    batch_size=256
    )
  trainer.fit(x_train, y_train)
  
  model.eval()
  dev = next(model.parameters()).device
  
  with th.no_grad():
    for _ in range(100):
      _slice = np.random.randint(0, y_dev.shape[0])
      x_dev_1 = x_dev[:_slice]
      x_dev_2 = x_dev[_slice:]
      th_xd1 = th.tensor(x_dev_1, device=dev)
      th_xd2 = th.tensor(x_dev_2, device=dev)  
      
      l.start_timer('eager_run')
      th_yp1 = model(th_xd1)
      th_yp2 = model(th_xd2)
      l.stop_timer('eager_run')
    np_y1 = th_yp1.cpu().numpy().argmax(-1).ravel()
    np_y2 = th_yp2.cpu().numpy().argmax(-1).ravel()
    np_y = np.concatenate((np_y1, np_y2))
  
  res = (np_y == y_dev).sum() / y_dev.shape[0]
  l.P("Dev acc: {:.4f}".format(res), color='g')
  l.show_timers()
  
  sm = th.jit.script(model)
  fn = os.path.join(l.get_models_folder(), 'simple_model_ts.pt')
  th.jit.save(sm, fn)
  
  
  
  
  
  
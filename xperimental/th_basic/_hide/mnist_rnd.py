"""
[THTST][2021-10-06 14:51:47] Timing results:
[THTST][2021-10-06 14:51:47]  cuda_fp32 = 0.0003s, max: 0.0010s, curr: 0.0010s
[THTST][2021-10-06 15:10:06]  cuda_fp32 = 0.0016s, max: 0.0070s, curr: 0.0009s
[THTST][2021-10-06 15:14:06]  cuda_fp32 = 0.0015s, max: 0.0070s, curr: 0.0010s

[THTST][2021-10-06 15:14:07] 0.001545557627329478

Script:
[THTST][2021-10-06 15:15:07]  torch_script = 0.0015s, max: 0.0060s, curr: 0.0010s
[THTST][2021-10-06 15:15:39]  torch_script = 0.0015s, max: 0.0070s, curr: 0.0021s
[THTST][2021-10-06 15:15:39] 0.001549411225724626
[THTST][2021-10-06 15:15:07] 0.0015447041890523335



[THTST][2021-10-06 14:51:47]  cuda_fp16 = 0.0011s, max: 0.0050s, curr: 0.0030s

"""
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
  
  if False:
    model_loss = th.nn.CrossEntropyLoss()
    
    trainer = ModelTrainer(
      log=l, 
      model=model, 
      losses=model_loss, 
      validation_data=(x_dev, y_dev),
      batch_size=256
      )
    trainer.fit(x_train, y_train)
  else:  
    dev = th.device('cuda')
    model.to(dev)
    model.eval()
    l.P("Model: \n{}".format(model))
    th_x_dev = th.tensor(x_dev, device=dev)
    with th.no_grad():
      timings = []
      for _ in range(1000):
        l.start_timer('cuda_fp32')
        y_pred = model(th_x_dev)
        tt = l.stop_timer('cuda_fp32')
        timings.append(tt)
        
      if False:
        th_x_dev = th_x_dev.half()
        model.half()
        for _ in range(1000):
          l.start_timer('cuda_fp16')
          y_pred = model(th_x_dev)
          l.stop_timer('cuda_fp16')
          
    l.show_timers()
    np_yh = y_pred.cpu().numpy()
    l.save_pickle_to_data((x_dev, np_yh), 'test_mnist0')
    scr_model = th.jit.script(model)
    th.jit.save(scr_model, 'xperimental/th_basic/mnist0.sth')
    
    np_t = np.array(timings[1:])
    l.P(np_t.mean())
  
  
  
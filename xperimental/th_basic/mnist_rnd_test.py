"""
Script:
[THTST][2021-10-06 15:15:07]  torch_script = 0.0015s, max: 0.0060s, curr: 0.0010s
[THTST][2021-10-06 15:15:39]  torch_script = 0.0015s, max: 0.0070s, curr: 0.0021s
[THTST][2021-10-06 15:15:39] 0.001549411225724626
[THTST][2021-10-06 15:15:07] 0.0015447041890523335
"""
import numpy as np
import torch as th
import torchvision as tv

from naeural_core import Logger

  

if __name__ == '__main__':
  
  l = Logger('THTST', base_folder='.', app_folder='_cache', TF_KERAS=False)
  
  train = tv.datasets.MNIST(root=l.get_data_folder(), train=True, download=True)
  test = tv.datasets.MNIST(root=l.get_data_folder(), train=False, download=True)
  
  TOP = 10
  
  
  (x_train, y_train), (x_dev, y_dev) = (train.data.numpy(), train.targets.numpy()), (test.data.numpy(), test.targets.numpy())
  x_train = (x_train.reshape((-1, 1, 28, 28)) / 255.).astype('float32')
  x_dev = (x_dev.reshape((-1, 1, 28, 28)) / 255.).astype('float32')
  y_train = y_train.astype('int64')
  y_dev = y_dev.astype('int64')
  
  l.gpu_info(True)
  
 
  model = th.jit.load('xperimental/th_basic/mnist0.sth')
  dev = next(model.parameters()).device
  th_x_dev = th.tensor(x_dev, device=dev)
  l.P("Model: \n{}".format(model))
  timings = []
  with th.no_grad():
    for _ in range(1000):
      l.start_timer('torch_script')
      th_y_pred = model(th_x_dev)
      tt = l.stop_timer('torch_script')
      timings.append(tt)
  np_yh = th_y_pred.cpu().numpy()
  
  _xd, _yp = l.load_pickle_from_data('test_mnist0')
  assert np.allclose(_xd[:TOP], x_dev[:TOP])
  assert np.allclose(_yp[:TOP], np_yh[:TOP])
  
  l.show_timers()
  np_t = np.array(timings[1:])
  l.P(np_t.mean())



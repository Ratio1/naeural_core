import numpy as np
import os
import torch as th
import torchvision as tv

from naeural_core import Logger



if __name__ == '__main__':

  l = Logger('THTST', base_folder='.', app_folder='_cache', TF_KERAS=False)
  
  train = tv.datasets.MNIST(root=l.get_data_folder(), train=True, download=True)
  test = tv.datasets.MNIST(root=l.get_data_folder(), train=False, download=True)
  
  
  (x_train, y_train), (x_dev, y_dev) = (train.data.numpy(), train.targets.numpy()), (test.data.numpy(), test.targets.numpy())
  x_train = (x_train.reshape((-1, 1, 28, 28)) / 255.).astype('float32')
  x_dev = (x_dev.reshape((-1, 1, 28, 28)) / 255.).astype('float32')
  y_train = y_train.astype('int64')
  y_dev = y_dev.astype('int64')
  
  l.gpu_info(True)
  
  
  
  fn = os.path.join(l.get_models_folder(), 'simple_model_ts.pt')
  m = th.jit.load(fn)

  dev = next(m.parameters()).device
  
  with th.no_grad():
    for _ in range(100):
      _slice = np.random.randint(0, y_dev.shape[0])
      x_dev_1 = x_dev[:_slice]
      x_dev_2 = x_dev[_slice:]
      th_xd1 = th.tensor(x_dev_1, device=dev)
      th_xd2 = th.tensor(x_dev_2, device=dev)  
      
      l.start_timer('ts_run')
      th_yp1 = m(th_xd1)
      th_yp2 = m(th_xd2)
      l.stop_timer('ts_run')
      
    np_y1 = th_yp1.cpu().numpy().argmax(-1).ravel()
    np_y2 = th_yp2.cpu().numpy().argmax(-1).ravel()
    np_y = np.concatenate((np_y1, np_y2))
  
  res = (np_y == y_dev).sum() / y_dev.shape[0]
  l.P("Dev acc: {:.4f} / Model & data on {}".format(res, dev), color='g')
  l.show_timers()
  
  
  
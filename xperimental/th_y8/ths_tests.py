import os
import numpy as np
import torch as th
import json




class TestWrapper(th.nn.Module):
  def __init__(self, fn):
    super(TestWrapper, self).__init__()
    cfg = {'config.txt' : ''}
    self.script_model = th.jit.load(fn, _extra_files=cfg)
    self.config = json.loads(cfg['config.txt' ].decode('utf-8'))
    return

  def forward(self, x):
    x = self.script_model(x)
    x = x + 1
    return x


if __name__ == '__main__':
  GENERATE = False

  folder = os.path.split(__file__)[0]
  fn = os.path.join(folder, 'test.ths')
  
  if GENERATE:
    class Tester(th.nn.Module):
      def __init__(self, func):
        super(Tester, self).__init__()
        self.func = func
        return
      
      def forward(self, inputs):
        th_x = self.func(inputs)  
        return th_x
        
    @th.jit.script
    def func(inputs):
      th_x = inputs * 0
      for i in range(inputs.shape[0]):
        x = inputs[i]
        th_x[i] =  x  + (i + 1)
      return th_x    
    
    m = Tester(func)
    t1 = th.tensor(np.ones((2,5)))
    d = {'inputs' : list(t1.shape)}
    ts = th.jit.trace(m, t1, strict=False)
    extra_files = {'config.txt' : json.dumps(d)}
    ts.save(fn, _extra_files=extra_files)
  else:
    
    model = TestWrapper(fn)
    t2 = th.tensor(np.ones((3,5)))
    with th.inference_mode():
      res1 = model(t2)
  
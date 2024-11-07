import gc
import sys
from time import sleep

from naeural_core import Logger

if __name__ == '__main__':
  
  l = Logger('MEM', base_folder='.', app_folder='_local_cache')
  
  class A:
    def __init__(self, obj=None):
      self.a = 'a' * 100
      self.obj = obj
      
  class B:
    def __init__(self, obj=None):
      self.b = 'b' * 100
      self.obj = obj


  class C:
    def __init__(self, obj=None):
      self.b = {
        k : [1] *k for k in range(1000)
      }
      self.obj = obj
      
  a = A()
  b = B(obj=a)
    
  sa = l.get_obj_size(a)

  
  c = C()
  sc, tree = l.get_obj_size(c, return_tree=True)
  
  for _ in range(1000):
    l.start_timer('get_obj_size')
    sc, tree = l.get_obj_size(c, return_tree=True)
    l.stop_timer('get_obj_size')
  l.show_timers()
  
  
  # import torch as th
  # import numpy as np
  
  # gc.collect()
  # mem1 = l.get_avail_memory(False)  
  # l.P('{:0,.0f}'.format(mem1 / 1024**2))

  # tc = th.tensor(np.ones((2000,1000,1000)), dtype=th.float32)

  # gc.collect()
  # sleep(1)
  # mem2 = l.get_avail_memory(False)
  # l.P('{:0,.0f}: {}'.format(mem2 / 1024**2, sys.getsizeof(tc)))

  # tc = tc.to(th.device('cuda'))

  # gc.collect()
  # sleep(1)
  # mem3 = l.get_avail_memory(False)
  # l.P('{:0,.0f}: {}'.format(mem3 / 1024**2, sys.getsizeof(tc)))

  # tc = tc.type(th.uint8)

  # gc.collect()
  # sleep(1)
  # mem4 = l.get_avail_memory(False)
  # l.P('{:0,.0f}: {}'.format(mem4 / 1024**2, sys.getsizeof(tc)))
  
  
  # tcf = th.tensor(np.ones((2000,1000,1000)), dtype=th.float32)

  # gc.collect()
  # mem5 = l.get_avail_memory(False)
  # l.P('{:0,.0f}'.format(mem5 / 1024**2))


  # tcf = tcf.to(th.device('cuda'))

  # gc.collect()
  # mem6 = l.get_avail_memory(False)
  # l.P('{:0,.0f}'.format(mem6 / 1024**2))
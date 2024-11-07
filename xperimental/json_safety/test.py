import numpy as np
from copy import deepcopy

from naeural_core import Logger
from naeural_core.bc import DefaultBlockEngine, BCct

if __name__ == '__main__':
  
  log = Logger('JSN', base_folder='.', app_folder='_local_cache')
  
  bc_engine = DefaultBlockEngine(
      log=log,
      name="local",
      config= {
        "PEM_FILE"        : "e2.pem",
        "PASSWORD"        : None,      
        "PEM_LOCATION"    : "data"
      },
    )  
  
  dct = {
  'a' : float('nan'), 
  'b' : [
    {
      'ba' : float('nan'), 
      'bc': float('-inf'),
      'bi': float('inf'),
      'bn' : 100,
    },
    {
      'b1' : {'b1a' : float('nan')}
    }
    ], 
  'c': float('-inf'), 
  'd': np.array([1,2,3]), 
  'e': np.array([1], dtype='int64')[0]
  }  
  
  d2 = log.replace_nan(dct)
  log.P(log.safe_json_dumps(dct))
  log.P(log.safe_json_dumps(d2))
  
  dct2 = deepcopy(dct)
  
  s1 = bc_engine.sign(dct)
  assert dct[BCct.SIGN] == s1
  s2 = bc_engine.sign(dct2, replace_nan=True)
  assert dct2[BCct.SIGN] == s2
  s3 = bc_engine.sign(d2)
  assert d2[BCct.SIGN] == s3
  
  assert dct2[BCct.HASH] == d2[BCct.HASH]
  assert dct2[BCct.SENDER] == d2[BCct.SENDER]
  log.P(s1)
  log.P(s2)
  log.P(s3)
  
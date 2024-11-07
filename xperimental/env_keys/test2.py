import os
import json

from naeural_core import Logger

if __name__ == '__main__':
  l = Logger('KEYS', base_folder='.', app_folder='_local_cache')
  
  os.environ['EE_P1'] = 's1'
  os.environ['EE_P2'] = 's2'
  os.environ['EE_P3'] = 's3'  

  data = {
    '1' : [{
        'k1' : '$EE_P1',
        '2': 2,
      },
      {}
    ],
    'p2' : '$EE_P2',
    'p3' : '$EE_P3',
  }   
  
  l.save_data_json(data, 'no_secrets.txt')
  
  print(json.dumps(
    l.load_data_json('no_secrets.txt', replace_environment_secrets='$EE_'), 
      indent=4)
  )
  
from naeural_core import Logger
import json

if __name__ == '__main__':
  
  
  d2 = {
    1: [1,2,3],
    2: {
      22: 22,
      33 : [33,33],
      44 : [{55:55}]
      }
    }
  
  l = Logger('DCT', base_folder='.', app_folder='_local_cache')
  
  with open('core/xperimental/dict/gql_resp.json', 'rt') as f:
    d = json.load(f)
    
  l.dict_pretty_format(d, display=True)
  l.dict_pretty_format(d2, display=True)
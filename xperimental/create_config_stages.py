import os

from naeural_core import Logger
from collections import OrderedDict

NAMES = {
  'buc': [
    '1.1 BUC',
    '1.2 BUC+FP',
    '1.3 BUC+FP+PER',
    '1.4 BUC+FP+PER+QUE'
    ],
  'disp': [
    '2.1 DISP',
    '2.2 DISP+FP',
    '2.3 DISP+FP+PQ',
    '2.4 DISP+FP+PQ+CNT'
    ],
  'int': [
    '3.1 INT',
    '3.2 INT+QUE'
    ],
  'hol': [
    '4.1 HOL'
    ],
  'open': [
    '5.1 OPEN',
    '5.2 OPEN+FP',
    '5.3 OPEN+FP+PER'
    ]
  }

if __name__ == '__main__':
  cfg_file = 'main_config.txt'
  log = Logger(lib_name='DEVICE', config_file=cfg_file, max_lines=1000)
  
  STREAM_PATH = os.path.join('xperimental', 'streams', 'gts')
  
  all_stages = OrderedDict()
  for stream in NAMES.keys():
    path_folder = os.path.join(STREAM_PATH, stream)
    os.makedirs(path_folder, exist_ok=True)
    
    path_source = os.path.join(STREAM_PATH, stream + '.txt') 
    stream_json = log.load_json(path_source)
    
    lst_plugins = stream_json['PLUGINS']    
    
    stream_stages = {}
    for i in range(1, len(lst_plugins) + 1):
      l = lst_plugins[:i]
      crt_json = stream_json.copy()
      crt_json['PLUGINS'] = l
      save_name = NAMES[stream][i-1]
      out_path = os.path.join(path_folder, save_name + '.txt')
      log.save_json(crt_json, out_path)
      stream_stages[save_name] = crt_json
    #endfor
    out_path = os.path.join(path_folder, stream + '_stream_stages' + '.txt')
    log.save_json(stream_stages, out_path)
    all_stages.update(stream_stages)
  #endfor
  out_path = os.path.join(STREAM_PATH, 'all_stages' + '.txt')
  log.save_json(all_stages, out_path)
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
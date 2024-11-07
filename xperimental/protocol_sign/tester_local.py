import json
import os


from copy import deepcopy

from naeural_core import Logger
from naeural_core.bc import DefaultBlockEngine, BCct

def diff(str1, str2):
  offset = 40
  for i in range(1, len(str1)):
    if str1[:i] != str2[:i]:
      start = max(i-offset, 0)
      end = i + offset
      return str1[start:end], str2[start:end], i
  return None



if __name__ == '__main__':
  
  log = Logger("SGN2", base_folder='.', app_folder='_local_cache')
  
  LOCAL_NAME = 'e2'
  SUBFOLDER = 'payloads'
  
  bc_engine = DefaultBlockEngine(
    log=log,
    name=LOCAL_NAME,
    config= {
      "PEM_FILE"     : LOCAL_NAME + ".pem",
      "PASSWORD"     : None,      
      "PEM_LOCATION" : "data"
     },
  )
  
  folder = log.get_data_subfolder(SUBFOLDER)
  cases = list(set([x[7:].split('.')[0] for x in os.listdir(folder) if '_err' in x]))
  filtered = [x for x in cases if '1a9' in x]
  tests = cases #filtered[:1]
  for str_error in tests:
    fn_txt = os.path.join(folder,'_err-j-' + str_error + '.txt')
    with open(fn_txt, 'r') as fh:
      str_json = fh.read()
    fn_pkl = '_err-d-' + str_error + '.pkl'
    dct_data = log.load_pickle_from_data(fn_pkl, subfolder_path=SUBFOLDER)
    str_dct_data = log.safe_dumps_json(dct_data, replace_nan=False, ensure_ascii=False)
    msg_match = str_json == str_dct_data 
    if not msg_match:
      s1, s2, idx = diff(str_dct_data, str_json)
      log.P("Stringify match FAIL. Diff at {}: \n|{}|\n|{}|".format(idx, s1, s2), color='r')
    else:
      log.P("Stringify match: OK", color='g')     
    
    continue   
      
    assert dct_data == json.loads(str_json)
    msg_sender = dct_data[BCct.SENDER]
    msg_hash = dct_data[BCct.HASH]
    msg_sign = dct_data[BCct.SIGN]
    loc_sender = bc_engine.address
    prepared_data = log.replace_nan(dct_data, inplace=False)
    prepared_data == dct_data
    bdata, bin_hexdigest, hexdigest = bc_engine.compute_hash(
      dct_data, 
      return_all=True, 
      replace_nan=True,
    )
    color = 'g' if loc_sender == msg_sender else 'r'
    log.P("Msg sender: {}".format(msg_sender), color=color)
    log.P("Loc sender: {}".format(loc_sender), color=color)
    color = 'g' if hexdigest == msg_hash else 'r'
    log.P("Msg hash: {}".format(msg_hash), color=color)
    log.P("Loc hash: {}".format(hexdigest), color=color)

  

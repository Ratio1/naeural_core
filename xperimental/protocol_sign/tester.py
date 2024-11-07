import json


from copy import deepcopy

from naeural_core import Logger
from naeural_core.bc import DefaultBlockEngine

def diff(str1, str2):
  offset = 40
  for i in range(1, len(str1)):
    if str1[:i] != str2[:i]:
      start = max(i-offset, 0)
      end = i + offset
      return str1[start:end], str2[start:end], i
  return None



if __name__ == '__main__':
  
  log = Logger("SGN", base_folder='.', app_folder='_local_cache')

  TESTS = {
    "gts-staging" : dict(
      be_recv_str =  """{"EE_FORMATTER":"cavi2","EE_HASH":"a4a72c99bdd561cc94612de1b4ebb806647637c50bc1da4da9c4c75f3ba7dc67","EE_MESSAGE_ID":"8f21aa82-1447-4dc8-821c-b4dc87657d95","EE_PAYLOAD_PATH":["gts-staging","LPR-1",null,null],"EE_SENDER":"0xai_AvDJaXULCUbtjvTFZikVGQM3SJoG7XXw3RK5U6LZHD7S","EE_SIGN":"MEUCIQCpNYZgw4Oc_RPGs4hkine8e8pwRHOC-4SlCKsHY5VaBQIgaonkJQE3VCZXoHvQNastqosr5Avs8A7yobbJZPPzPA8=","category":"","data":{"identifiers":{},"img":{"height":null,"id":null,"width":null},"specificValue":{},"time":null,"value":{}},"demoMode":false,"messageID":"8f21aa82-1447-4dc8-821c-b4dc87657d95","metadata":{"displayed":true,"ee_message_seq":6199,"ee_timezone":"UTC+2","ee_tz":"Europe/Bucharest","error_code":null,"info":"EE v3.28.264, Lib v9.8.71, info text: None","initiator_id":"cavi2-staging","module":"VideoStreamDataCapture","notification":"Video DCT 'LPR-1' successfully connected. Overall 1910 reconnects.","notification_code":null,"notification_tag":null,"notification_type":"NORMAL","sbCurrentMessage":"8f21aa82-1447-4dc8-821c-b4dc87657d95","sbTotalMessages":6199,"session_id":null,"stream_name":"LPR-1","timestamp":"2023-11-10 10:52:27.465499","video_stream_info":{"buffersize":0.0,"current_interval":null,"fps":23,"frame_count":429,"frame_current":814873,"frame_h":1080,"frame_w":1920}},"sender":{"hostId":"gts-staging","id":"edge-node","instanceId":"edge-node-v3.28.264"},"time":{"deviceTime":"","hostTime":"2023-11-10 10:52:28.492998","internetTime":""},"type":"notification","version":"3.28.264"}""",
      be_stringify = """{"EE_FORMATTER":"cavi2","EE_MESSAGE_ID":"8f21aa82-1447-4dc8-821c-b4dc87657d95","EE_PAYLOAD_PATH":["gts-staging","LPR-1",null,null],"category":"","data":{"identifiers":{},"img":{"height":null,"id":null,"width":null},"specificValue":{},"time":null,"value":{}},"demoMode":false,"messageID":"8f21aa82-1447-4dc8-821c-b4dc87657d95","metadata":{"displayed":true,"ee_message_seq":6199,"ee_timezone":"UTC+2","ee_tz":"Europe/Bucharest","error_code":null,"info":"EE v3.28.264, Lib v9.8.71, info text: None","initiator_id":"cavi2-staging","module":"VideoStreamDataCapture","notification":"Video DCT 'LPR-1' successfully connected. Overall 1910 reconnects.","notification_code":null,"notification_tag":null,"notification_type":"NORMAL","sbCurrentMessage":"8f21aa82-1447-4dc8-821c-b4dc87657d95","sbTotalMessages":6199,"session_id":null,"stream_name":"LPR-1","timestamp":"2023-11-10 10:52:27.465499","video_stream_info":{"buffersize":0,"current_interval":null,"fps":23,"frame_count":429,"frame_current":814873,"frame_h":1080,"frame_w":1920}},"sender":{"hostId":"gts-staging","id":"edge-node","instanceId":"edge-node-v3.28.264"},"time":{"deviceTime":"","hostTime":"2023-11-10 10:52:28.492998","internetTime":""},"type":"notification","version":"3.28.264"}""",
      be_gen_hash = "c0b40a298c63bd479a05517f4713c61f5bc898eb407fb7ac754a7de54c93d91b",
    ),
    
    "gts-test2" : dict(
      be_recv_str =  './core/xperimental/0xai_sign/bad-0.bin',
      be_stringify = './core/xperimental/0xai_sign/str1.json',
      be_gen_hash = "86a92270c175f8175d2cc65797d9bf85acc652575a426a9b52173fc7be50b936",
    ),
  }
  
  test_name = 'gts-test2'
  test = TESTS[test_name]


  be_recv_str = test['be_recv_str']
  if be_recv_str.startswith('./'):
    if be_recv_str.endswith('.bin'):
      with open(be_recv_str, 'rb') as f:
        be_recv_bin = f.read()
        be_recv_str = be_recv_bin.decode('utf-8')
        
    else:
      with open(be_recv_str, 'r', encoding="utf-8") as f:
        be_recv_str = f.read()
      
  be_stringify = test['be_stringify']
  if be_stringify.startswith('./'):
    with open(be_stringify, 'r', encoding="utf-8") as f:
      be_stringify = f.read()
      
  be_gen_hash = test['be_gen_hash']
  
  bc_engine = DefaultBlockEngine(
    log=log,
    name=test_name,
    config= {
      "PEM_FILE"     : test_name + ".pem",
      "PASSWORD"     : None,      
      "PEM_LOCATION" : "data"
     },
  )
  
  dct_to_send = json.loads(be_recv_str)
  be_rcv_hash = dct_to_send['EE_HASH']
  be_rcv_addr = dct_to_send['EE_SENDER']
  be_rcv_sign = dct_to_send['EE_SIGN'] 
  
  verify_msg = bc_engine.verify(dct_to_send)
  log.P("Verify msg: {}".format(verify_msg), color='b')

    
  d = deepcopy(dct_to_send)
  dct_local = {k:v for k,v in d.items() if k not in ['EE_SIGN','EE_SENDER','EE_HASH']}
  
  log.P("Loc addr:  {}".format(bc_engine.address), color='b')
  log.P("Msg addr:  {}".format(be_rcv_addr), color='b')
  # assert bc_engine.address == be_rcv_addr # only if the SK is available locally
  
  str_local_ = bc_engine._generate_data_for_hash(dct_to_send)
  bdata, bin_hash, local_hash = bc_engine.compute_hash(dct_local, return_all=True)
  str_local = bdata.decode('utf-8')
  assert str_local == str_local_, "Something is wrong with the json encoding/decoding"
  
  msg_match = str_local == be_stringify 
  if not msg_match:
    s1, s2, idx = diff(str_local, be_stringify)
    log.P("Stringify match FAIL. Diff at {}: \n|{}|\n|{}|".format(idx, s1, s2), color='r')
  else:
    log.P("Stringify match: OK", color='g')        

    log.P("Loc hash: {}".format(local_hash), color='b')
    log.P("Msg hash: {}".format(be_rcv_hash), color='b')
    log.P("BE hash:  {}".format(be_gen_hash), color='b')
    local_vs_be = local_hash == be_gen_hash
    if not local_vs_be:
      log.P("Hash generated from BE and locally match FAIL", color='r')
    else:
      log.P("Hash generated from BE and locally match OK", color='g')
        
      hash_check = local_hash == be_rcv_hash
      if not hash_check:
        log.P("Hash computed locally vs hash received on BE  check: FAIL", color='r')
      else:
        log.P("Hash computed locally vs hash received on BE check: OK", color='g')
        
        str_sign = bc_engine.sign(dct_local)
        # sanity check as this should be equal due to additions in dict
        # in dct_local the signature is the above
        log.P("Local sign: {}".format(dct_local['EE_SIGN']), color='b')
        log.P("BE rcv sgn: {}".format(dct_to_send['EE_SIGN']), color='b')
        # do not test local sign with remote as the local will be different due to algorithm  

          
        sign_reconstruct = str_sign == be_rcv_sign
        if sign_reconstruct:
          log.P("Sign JS reconstruct: OK", color='g') 
        else:
          log.P("Sign JS reconstruct: FAIL", color='r')
      
      
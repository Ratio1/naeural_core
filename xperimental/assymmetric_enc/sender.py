import json

from naeural_core.bc import DefaultBlockEngine
from naeural_core import Logger


if __name__ == '__main__':
  l = Logger('AENC', base_folder='.', app_folder='_local_cache')
  
  snd_name = "asym-enc-dec-1"
  sender = DefaultBlockEngine(
    log=l,
    name=snd_name,
    config= {
      "PEM_FILE"     : snd_name + ".pem",
      "PASSWORD"     : None,      
      "PEM_LOCATION" : "data"
    },
  )
  
  data = {
    "value" : "Hello World",
  }
  str_data = json.dumps(data)
  receiver_address = "0xai_A3vtcVIv_yL7k945IuhNjLUXKj2DPvbapoH4D6ZairfT"
  
  l.P("Sender <{}> private data: {}".format(sender.address, data))
  enc_data = sender.encrypt(
    plaintext=str_data,
    receiver_address=receiver_address,
  )
  l.P("Sender <{}> encrypted data: {}".format(sender.address, enc_data))
  
  payload = {
    "EE_ENCRYPTED_DATA" : True,
    "ENCRYPTED_DATA" : enc_data,    
  }  
  
  signature = sender.sign(
    dct_data=payload
  )
  
  out_payload = payload
  
  str_output_payload = json.dumps(out_payload)
  print("Sender <{}> outgoing payload signed with {}:\n {}".format(
    sender.address, signature, json.dumps(out_payload, indent=2)))    
    
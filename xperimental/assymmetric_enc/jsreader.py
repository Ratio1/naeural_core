import json

from naeural_core.bc import DefaultBlockEngine
from naeural_core import Logger


if __name__ == '__main__':
  l = Logger('AENC', base_folder='.', app_folder='_local_cache')
  
  receiver = DefaultBlockEngine(
  log=l,
  name="jsreader",
  config= {
    "PEM_FILE"     : "jsreader.pem",
    "PASSWORD"     : None,      
    "PEM_LOCATION" : "data"
    },
  )
  
  str_output_payload = """
    {
      "EE_ENCRYPTED_DATA": true,
      "ENCRYPTED_DATA": "CjWJCMRV0GYWUOCjAeKEAOFbkp8pfFsWxE98MZHyoMpJx2lema7v+VwqcWNYy2b1ilwCcg==",
      "EE_SIGN": "MEUCIAufipoc4ltZUP9oo6sbhf4DXVmpOB09W5yaXTovDL4aAiEArOjuZ4TnscVtccHq2lwm7ch4x6Q7-C9o99yzkzrqMTQ=",
      "EE_SENDER": "0xai_AwwqvbL_Fw3y0MQzllx69JZSLYT3ybF9zanfrmcgAlEp",
      "EE_HASH": "b9ee0540599d52201dcc9b595b2bff7d35292882c4679ff5c89530a8b45539bf"
    }
  """
  

  str_incoming_payload = str_output_payload
  incoming_payload = json.loads(str_incoming_payload)

  is_verified = receiver.verify(
    dct_data=incoming_payload
  )  
  
  assert is_verified, "Signature verification failed"
  
  sender_address = incoming_payload.get("EE_SENDER")  
  incomind_enc_data = incoming_payload.get("ENCRYPTED_DATA")
  
  dec = receiver.decrypt(
    encrypted_data_b64=incomind_enc_data, 
    sender_address=sender_address,
    debug=True,
  )
  print(dec)
"""

"""
import json

from naeural_core.bc import DefaultBlockEngine
from naeural_core import Logger


if __name__ == '__main__':
  l = Logger('AENC', base_folder='.', app_folder='_local_cache')
  
  CREATE_SENDER = False
  
  if CREATE_SENDER:
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

  rcv_name = "asym-enc-dec-2"
  receiver = DefaultBlockEngine(
    log=l,
    name=rcv_name,
    config= {
      "PEM_FILE"     : rcv_name + ".pem",
      "PASSWORD"     : None,      
      "PEM_LOCATION" : "data"
     },
  )

  bandit = DefaultBlockEngine(
    log=l,
    name="bandit",
    config= {
      "PEM_FILE"     : "bandit.pem",
      "PASSWORD"     : None,      
      "PEM_LOCATION" : "data"
     },
  )

  l.P("*"*80)
  l.P("*"*80)
  l.P("Starting test:")

  if CREATE_SENDER:
    ## sender
    data = "Hello World"
    l.P("Sender <{}> private data: {}".format(sender.address, data))
    enc_data = sender.encrypt(
      plaintext=data,
      receiver_address=receiver.address,
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
  else:
    str_output_payload = """

    """

  
  ## bandit
  
  str_incoming_payload_bandit = str_output_payload
  incoming_payload_bandit = json.loads(str_incoming_payload_bandit)
  sender_address = incoming_payload_bandit.get("EE_SENDER")
  incoming_enc_data_bandit = incoming_payload_bandit.get("ENCRYPTED_DATA")

  dec_bandit = bandit.decrypt(
    encrypted_data_b64=incoming_enc_data_bandit, 
    sender_address=sender_address
  )
  l.P("Bandit <{}> decrypt attempt: {}".format(bandit.address, dec_bandit))
    
  
  ## receiver
  
  str_incoming_payload = str_output_payload
  incoming_payload = json.loads(str_incoming_payload)
  
  is_verified = receiver.verify(
    dct_data=incoming_payload
  )
    
  assert is_verified, "Payload is not valid"
  l.P("Receiver <{}> incoming payload verified: {}".format(
    receiver.address, is_verified)
  )
  
  sender_address = incoming_payload.get("EE_SENDER")  
  incomind_enc_data = incoming_payload.get("ENCRYPTED_DATA")
  
  dec = receiver.decrypt(
    encrypted_data_b64=incomind_enc_data, 
    sender_address=sender_address
  )
  l.P("Receiver <{}> decrypted message: {}".format(
    receiver.address, dec
  ))
  
  
  
  
  
  
import os
import random

import json
import base64

from copy import deepcopy
from hashlib import sha256
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives import hashes

from naeural_core import Logger

if __name__ == '__main__':

  # first the sender process
  # prepare SK, PK, ADDR
  
  # sk = ec.generate_private_key(curve=ec.SECP256K1())
  sk = ec.derive_private_key(private_value=1234, curve=ec.SECP256K1())
  pk = sk.public_key()
  data = pk.public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.CompressedPoint,
  )
  str_address = "0xai_" + base64.urlsafe_b64encode(data).decode()

  # now the actual hasing and signing
  dct_to_send = {'9'  : 9, '2':2, '3':3, '10':{'2':2,'100':100, '1':1}}
  deterministic_json = json.dumps(dct_to_send, sort_keys=True, separators=(',',':'))
  data = deterministic_json.encode()
  str_digest = sha256(data).hexdigest()    
  # bin_digest = str_digest.encode()
  bin_digest = sha256(data).digest()
  
  signature = sk.sign(
      data=bin_digest,
      signature_algorithm=ec.ECDSA(hashes.SHA256()),
  )
  str_signature = base64.urlsafe_b64encode(signature).decode()

  dct_to_send['SIGN'] = str_signature
  dct_to_send['ADDR'] = str_address
  dct_to_send['HASH'] = str_digest

  print("Fully signed object\n{}".format(json.dumps(dct_to_send, indent=2)))  

  if str_address != '0xai_AuN2SENcYNzRgbPUHVCFe6W1q-vieUKap2VY9mU_Fljy':
    Logger.print_color("ERROR: Address generation failed", color='r')
  else:
    Logger.print_color("Deterministic address generation OK", color='g')

  if deterministic_json != '{"10":{"1":1,"100":100,"2":2},"2":2,"3":3,"9":9}':
    Logger.print_color("ERROR: Deterministic JSON generation failed", color='r')
  else:
    Logger.print_color("Deterministic JSON generation OK", color='g')   
  
  if str_digest != '7d72cf5bd6cda16c86dfb2c2c4983464edda5bf78e1ca3a21139f5037454c6f3':
    Logger.print_color("ERROR: Hash generation failed", color='r')
  else:
    Logger.print_color("Hash generation OK", color='g')


"""
Javascript generated message:
r:     56684693624919999627438170883767084573186267748701669138919084275734263485571
s:     5787701224495155401849614702459149372951204779942523309061490057173504587799
Sign:  MEQCIH1SZk5vEQHxMKMKfh86BWtcv-9OxOJi9_YEHbNjMdSDAiAMy7kJzWki9Yg879ztF4KqDQYy4mOHn0RjYojaF1N4Fw==  
  
"""
import json
import base64

from hashlib import sha256
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from naeural_core import Logger


if __name__ == '__main__':
  # now the received signature check process
  PYTHON_MESSAGE = {
    "9": 9,
    "2": 2,
    "3": 3,
    "10": {
      "2": 2,
      "100": 100,
      "1": 1
    },
    # "SIGN": "MEQCIHWA7Jmtz-ENKo3JfMbfWGGmIAufwCFkKXz35gV8VudyAiBV8KlIKRglYlH8odTF-yfwJvHDv28BioFc1Vm1OONJVw==",
    "SIGN": "MEQCIEIz_Nfy9CJ0GYW1V7Iw0uFJAVzu1TnOWkCVYnrt8PNHAiB0JCk_pgzGGIMz-KIvOCC_BzbGB8jxkAb_OwPX7AQTyA==",
    "ADDR": "0xai_AuN2SENcYNzRgbPUHVCFe6W1q-vieUKap2VY9mU_Fljy",
    "HASH": "7d72cf5bd6cda16c86dfb2c2c4983464edda5bf78e1ca3a21139f5037454c6f3"
  }
  
  JS_GENERATED_MESSAGE = {
    "2": 2,
    "3": 3,
    "9": 9,
    "10": {
      "1": 1,
      "2": 2,
      "100": 100
    },
    "SIGN": "MEQCIH1SZk5vEQHxMKMKfh86BWtcv-9OxOJi9_YEHbNjMdSDAiAMy7kJzWki9Yg879ztF4KqDQYy4mOHn0RjYojaF1N4Fw==",
    "ADDR": "0xai_AuN2SENcYNzRgbPUHVCFe6W1q-vieUKap2VY9mU_Fljy",
    "HASH": "7d72cf5bd6cda16c86dfb2c2c4983464edda5bf78e1ca3a21139f5037454c6f3"
  }
    
  JS_r = 56684693624919999627438170883767084573186267748701669138919084275734263485571
  JS_s = 5787701224495155401849614702459149372951204779942523309061490057173504587799
    
  NON_DATA_FIELDS = ['SIGN', 'ADDR', 'HASH']
  dct_received = JS_GENERATED_MESSAGE
  # dct_received['9'] = 10
  str_sender_addr = dct_received['ADDR']
  str_sender_sign = dct_received['SIGN']  
  str_sender_hash = dct_received['HASH']
  dct_data = {k : dct_received[k] for k in dct_received if k not in NON_DATA_FIELDS}
  str_deterministic_json = json.dumps(dct_data, sort_keys=True, separators=(',',':'))
  if str_deterministic_json == '{"10":{"1":1,"100":100,"2":2},"2":2,"3":3,"9":9}':
    Logger.print_color("Deterministic JSON passed", color='g')
  else:
    Logger.print_color("ERROR: Deterministic JSON failed", color='r')
    
  data = str_deterministic_json.encode()
  obj_digest = sha256(data)
  str_digest = obj_digest.hexdigest()
  bin_digest = obj_digest.digest()
  simple_addr = str_sender_addr.replace("0xai_", '')
  bpublic_key = base64.urlsafe_b64decode(simple_addr)
  pk = ec.EllipticCurvePublicKey.from_encoded_point(
    curve=ec.SECP256K1(), 
    data=bpublic_key
  )

  # RECODING: To get the hexadecimal representation of the public key
  public_bytes = pk.public_bytes(
      encoding=ec._serialization.Encoding.X962,
      format=ec._serialization.PublicFormat.CompressedPoint
  )  
  str_recoded_addr = base64.urlsafe_b64encode(public_bytes).decode()
  
  signature = base64.urlsafe_b64decode(str_sender_sign)  
  r, s = decode_dss_signature(signature)
  failed = False
  
  if dct_received == JS_GENERATED_MESSAGE:
    if r == JS_r and s == JS_s:
      Logger.print_color("Decoded signature r,s check passed", color='g')
    else:
      Logger.print_color("ERROR: Decoded signature r,s check failed", color='r')
      exit(1)
  else:
    print('r:     {}'.format(r))
    print('s:     {}'.format(s)) 
    print('Sign:  {}'.format(str_sender_sign))
      
  if str_recoded_addr != simple_addr:
    Logger.print_color("ERROR: Recoded address check failed", color='r')
    exit(1)
  else:
    Logger.print_color("Recoded address check passed", color='g')
    
  if str_digest != str_sender_hash:
    Logger.print_color("ERROR: Hash check failed", color='r')
    exit(1)
  else:
    Logger.print_color("Hash check passed", color='g')
    

  err = None
  try:
    pk.verify(signature, bin_digest, ec.ECDSA(hashes.SHA256()))
    signature_check = True
  except Exception as e:
    signature_check = False

  if signature_check:
    Logger.print_color("Signature check passed", color='g')
  else:
    Logger.print_color("ERROR: Signature check failed", color='r')    

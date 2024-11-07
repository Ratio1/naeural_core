# 0xai_AhWU91s938BasILw7fyQ1bkGqWtw8dBCYDIg1Kz8wvfh MEUCIExlAnqAuMgJCPJ3GsguTE61MteZc0eR-akULK0vkIgRAiEA1PySc0xohCZGaX-C_kDilTQiUys0OFWniDT6WYpANJE=
# 0xai_AhWU91s938BasILw7fyQ1bkGqWtw8dBCYDIg1Kz8wvfh MEYCIQCUuHwnrzhrB2NlSJmY91n8vc1yBpnA0yrDmAgxE-Q8PgIhANgNKhcD3PJFLQaXSSfNdvC_PAbNz1YzWmHb-22O7aXE
data_message = '{"SERVER":"gts-test","COMMAND":"UPDATE_CONFIG","PAYLOAD":{"GIGI":"BUNA"},"EE_SIGN":"MEQCIGtGFxMOQcp9vv_3pQFwsM9fhPRs7AFsCoIU9F8UzXkGAiB4mOj1kKpk_vlXRwiNG1tigsB3pRAk6wwpbVQ-PaReqQ==","EE_SENDER":"0xai_AnrN5m2m8xWuZWNu4XAJlcczGW0WaLCslPxtJck67QDy","EE_HASH":"feca4c4882b2b0cfb872c73bda948b77048ced67b9eeae10c8bdd9028f9d20a1"}'
# Convert hex to bytes
import base64
import hashlib
import json
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend

from time import time


dct_received = json.loads(data_message)
compressed_pk = dct_received['EE_SENDER'].replace('0xai_','')
signature_hex = dct_received['EE_SIGN']  
received_hash = dct_received['EE_HASH']
dct_data = {k:v for k,v in dct_received.items() if k not in ['EE_SIGN','EE_SENDER','EE_HASH']}
inputs = dct_data

if isinstance(inputs,str):
  data = inputs.encode()
elif isinstance(inputs,dict):  
  deterministic_json = json.dumps(inputs, sort_keys=True, separators=(',',':'))
  data = deterministic_json.encode()


# direct
obj_hash = hashlib.sha256(data)
str_hash = obj_hash.hexdigest()
b_hash = obj_hash.digest()

signatureB64 = signature_hex
signature_bytes = base64.urlsafe_b64decode(signatureB64)

# Compressed point
t0 = time()
t01 = time()
bpublic_key = base64.urlsafe_b64decode(compressed_pk)
e11 = time() - t01
t02 = time()
crv = ec.SECP256K1()
e12 = time() - t02 
t03 = time()
pk = ec.EllipticCurvePublicKey.from_encoded_point(
  curve=crv, 
  data=bpublic_key
)
e13 = time() - t03
e1 = time() - t0
# RECODING: To get the hexadecimal representation of the public key
public_bytes = pk.public_bytes(
    encoding=ec._serialization.Encoding.X962,
    format=ec._serialization.PublicFormat.CompressedPoint
)  
str_recoded_addr = base64.urlsafe_b64encode(public_bytes).decode()
assert str_recoded_addr == compressed_pk, "Recoding failed"
print("Recoding passed")

assert received_hash == str_hash, "Hashes do not match"
print("Hashing match passed")

try:
  t1 = time()
  pk.verify(signature_bytes, b_hash, ec.ECDSA(hashes.SHA256()))
  e2 = time() - t1
  elapsed_total = e1 + e2
  print("X962/Compres pk signature check valid in {:.3f}s ({:.3f}s ({:.3f}s+{:.3f}s+{:.3f}s) + {:.3f}s)".format(
      elapsed_total, e1, e11, e12, e13,
      e2
    )
  )
except Exception as e:
  print(f"ERROR: Invalid signature: {e}")
    

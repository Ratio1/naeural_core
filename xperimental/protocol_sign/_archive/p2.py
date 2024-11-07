from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend
import base64
import hashlib
import json

# Generate keys
private_key = ec.generate_private_key(ec.SECP256K1(), default_backend())
public_key = private_key.public_key()

# Serialize public key in X9.62 compressed point format
public_bytes = public_key.public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.CompressedPoint
)

# Hash the message
dct_to_send = { '9': 9, '2': 2, '3': 3, '10': { '2': 2, '100': 100, '1': 1 } }
message = json.dumps(dct_to_send, sort_keys=True, separators=(',',':')).encode()
obj_hash = hashlib.sha256(message)
str_hash = obj_hash.hexdigest()
b_hash = obj_hash.digest()

# Sign the hash
signature_bytes = private_key.sign(b_hash, ec.ECDSA(hashes.SHA256()))

# Base64url encode the signature
signature_b64 = base64.urlsafe_b64encode(signature_bytes).decode()

pk_b64 = base64.urlsafe_b64encode(public_bytes).decode()

dct_msg = {
  **dct_to_send,
  'EE_SENDER': "0xai_" + pk_b64,
  'EE_SIGN': signature_b64,
  'EE_HASH': str_hash
}

str_msg = json.dumps(dct_msg) # no need for sorting
print("Message: '"+ str_msg + "'")




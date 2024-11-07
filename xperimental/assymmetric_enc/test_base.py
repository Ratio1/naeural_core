import os
import base64
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def generate_ecc_keys():
  """
  Generates an ECC public/private key pair.

  Returns:
    tuple: A tuple containing the private and public keys.
  """
  private_key = ec.generate_private_key(curve=ec.SECP256K1())
  public_key = private_key.public_key()
  return private_key, public_key



def __derive_shared_key(private_key, peer_public_key):
  """
  Derives a shared key using own private key and peer's public key.

  Parameters
  ----------
  private_key : cryptography.hazmat.primitives.asymmetric.ec.EllipticCurvePrivateKey
      The private key to use for derivation.
  peer_public_key : cryptography.hazmat.primitives.asymmetric.ec.EllipticCurvePublicKey
      The peer's public key.
  
  Returns
  -------
  bytes
      The derived shared key.
  """
  shared_key = private_key.exchange(ec.ECDH(), peer_public_key)
  derived_key = HKDF(
    algorithm=hashes.SHA256(),
    length=32,
    salt=None,
    info=b'handshake data',
    backend=default_backend()
  ).derive(shared_key)
  return derived_key

def encrypt(sender_sk, receiver_pk, plaintext):
  """
  Encrypts plaintext using the sender's private key and receiver's public key, 
  then base64 encodes the output.

  Parameters
  ----------
  sender_sk : cryptography.hazmat.primitives.asymmetric.ec.EllipticCurvePrivateKey
      The sender's private key for deriving the shared key.
      
  receiver_pk : cryptography.hazmat.primitives.asymmetric.ec.EllipticCurvePublicKey
      The receiver's public key for deriving the shared key.
      
  plaintext : str
      The plaintext to encrypt.

  Returns
  -------
  str
      The base64 encoded nonce and ciphertext.
  """
  shared_key = __derive_shared_key(sender_sk, receiver_pk)
  aesgcm = AESGCM(shared_key)
  nonce = os.urandom(12)  # Generate a unique nonce for each encryption
  ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
  encrypted_data = nonce + ciphertext  # Prepend the nonce to the ciphertext
  print(f"Encrypted Data: {encrypted_data}")
  print(f"Nounce: {nonce}")
  return base64.b64encode(encrypted_data).decode()  # Encode to base64

def decrypt(receiver_sk, sender_pk, encrypted_data_b64):
  """
  Decrypts base64 encoded encrypted data using the receiver's private key.

  Parameters
  ----------
  receiver_sk : cryptography.hazmat.primitives.asymmetric.ec.EllipticCurvePrivateKey
      The receiver's private key for deriving the shared key.
      
  sender_pk : cryptography.hazmat.primitives.asymmetric.ec.EllipticCurvePublicKey
      The sender's public key for deriving the shared key.
      
  encrypted_data_b64 : str
      The base64 encoded nonce and ciphertext.

  Returns
  -------
  str
      The decrypted plaintext.

  """
  encrypted_data = base64.b64decode(encrypted_data_b64)  # Decode from base64
  nonce = encrypted_data[:12]  # Extract the nonce
  ciphertext = encrypted_data[12:]  # The rest is the ciphertext
  print(f"Encrypted Data: {encrypted_data}")
  print(f"Nonce: {nonce}")
  shared_key = __derive_shared_key(receiver_sk, sender_pk)
  aesgcm = AESGCM(shared_key)
  plaintext = aesgcm.decrypt(nonce, ciphertext, None)
  return plaintext.decode()

# Example usage comments would indicate how to use these functions with base64 encoded data.

if __name__ == "__main__":
  import os

  # Generate key pairs for two parties (simulating sender and receiver)
  sender_private_key, sender_public_key = generate_ecc_keys()
  receiver_private_key, receiver_public_key = generate_ecc_keys()
  # bandit is an eavesdropper
  bandit_private_key, bandit_public_key = generate_ecc_keys()

  message = "Hello, receiver!"
  encrypted_message = encrypt(
    sender_sk=sender_private_key, 
    receiver_pk=receiver_public_key, 
    plaintext=message,
  )
  print("Encrypted Message:  {}".format(encrypted_message))

  decrypted_message = decrypt(
    receiver_sk=receiver_private_key, 
    sender_pk=sender_public_key,
    encrypted_data_b64=encrypted_message,
  )

  try:
    # Bandit tries to decrypt the message
    bandit_decrypted_message = decrypt(
      receiver_sk=bandit_private_key, 
      encrypted_data_b64=encrypted_message,
    )
  except:
    bandit_decrypted_message = "Decryption failed"

  print(f"Encrypted Message:  {encrypted_message}")
  print(f"Decrypted Message:  {decrypted_message}")
  print(f"Bandit Message:     {bandit_decrypted_message}")

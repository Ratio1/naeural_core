///
/// npm install elliptic
/// npm install json-stable-stringify
///
PYTHON_HASH = "7d72cf5bd6cda16c86dfb2c2c4983464edda5bf78e1ca3a21139f5037454c6f3"


const crypto = require('crypto');
const elliptic = require('elliptic');
const stringify = require('json-stable-stringify');

const ec = new elliptic.ec('secp256k1');

function urlSafeBase64ToBase64(urlSafeBase64) {
  return urlSafeBase64.replace(/-/g, '+').replace(/_/g, '/');
}

function base64ToUrlSafeBase64(base64) {
  return base64.replace(/\+/g, '-').replace(/\//g, '_');
}

if (require.main === module) {
  // Generating private and public keys
  const keyPair = ec.keyFromPrivate(1234);
  const publicKey = keyPair.getPublic();
  
  // Constructing the address string
  const pubBytes = Buffer.from(publicKey.encode('hex', true), 'hex');
  const str_address = "0xai_" + base64ToUrlSafeBase64(pubBytes.toString('base64'));

  // Preparing the data to be signed
  const dct_to_send = { '9': 9, '2': 2, '3': 3, '10': { '2': 2, '100': 100, '1': 1 } };
  const deterministic_json = stringify(dct_to_send);
  const str_digest = crypto.createHash('sha256').update(deterministic_json).digest('hex');
  
  // Signing the hash
  const bin_digest = Buffer.from(str_digest, 'hex');
  const signature = keyPair.sign(bin_digest);

  // Signing the byte representation of the hash
  const r = signature.r;
  const s = signature.s;
  const derSignature = signature.toDER();  // Assuming the library provides a toDER method
  const bderSignature = Buffer.from(derSignature)
  const base64sign = bderSignature.toString('base64');
  // URL-safe Base64 encoding the DER-encoded signature
  const str_signature = base64ToUrlSafeBase64(base64sign);
  console.log("r:    ", r); 
  console.log("s:    ", s);  
  console.log("Sign: ", str_signature);

  
  // Adding signature, address, and hash to the data object
  dct_to_send['SIGN'] = str_signature;
  dct_to_send['ADDR'] = str_address;
  dct_to_send['HASH'] = str_digest;



  console.log("Fully signed object\n", JSON.stringify(dct_to_send, null, 2));

  console.log("Hash: ", str_digest);
  if (str_digest !== PYTHON_HASH) {
    console.log("ERROR: Hash check failed");
  } else {
    console.log("Hash check passed");
  }  

  if (deterministic_json !== '{"10":{"1":1,"100":100,"2":2},"2":2,"3":3,"9":9}') {  
    console.log("ERROR: Deterministic JSON generation failed")
  } else {
    console.log("Deterministic JSON generation passed");
  }

}

///
/// npm install elliptic
/// npm install json-stable-stringify
///
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
  const PYTHON_MESSAGE = {
    "9": 9,
    "2": 2,
    "3": 3,
    "10": {
      "2": 2,
      "100": 100,
      "1": 1
    },
    "SIGN": "MEUCIQDWu5Utuydc8aJkfIGnNWqsJsA_EXNOoWqwNAgNhwVDxAIgNr0nfNDoj9kGagzbKuo9MjC5d9DcBzHaFL0u7ggpZgY=",
    "ADDR": "0xai_AioS5Bgfks874QwGcfB_cu8wWeN-dVakNhc-2Bwb9fQs",
    "HASH": "7d72cf5bd6cda16c86dfb2c2c4983464edda5bf78e1ca3a21139f5037454c6f3"
  };

  const JS_GENERATED_MESSAGE = {
    "2": 2,
    "3": 3,
    "9": 9,
    "10": {
      "1": 1,
      "2": 2,
      "100": 100
    },
    "SIGN": "MEQCIILM7ti63NQdC0ijb_Xf3CFCNLljHvwi8TfV9BlsiJ1wAiBkHKx0_wuDgUoxa6_-BiOdxYojhx5ZUHgK1BllugyGQg==",
    "ADDR": "0xai_AtNCUSKULwnx_FNn2GS_6gS4y88KDQQam2weBi3n2sva",
    "HASH": "7d72cf5bd6cda16c86dfb2c2c4983464edda5bf78e1ca3a21139f5037454c6f3"
  }
  
  const VALID_RECEIVED_JSON = '{"10":{"1":1,"100":100,"2":2},"2":2,"3":3,"9":9}';
  const NON_DATA_FIELDS = ['SIGN', 'ADDR', 'HASH'];
  const dct_received = JS_GENERATED_MESSAGE;
  const str_sender_addr = dct_received['ADDR'];
  const str_sender_sign = dct_received['SIGN'];
  const str_sender_hash = dct_received['HASH'];
  const dct_data = Object.fromEntries(Object.entries(dct_received).filter(([key]) => !NON_DATA_FIELDS.includes(key)));
  const str_deterministic_json = stringify(dct_data);
  console.log(str_deterministic_json)
  if (str_deterministic_json !== VALID_RECEIVED_JSON) {
    console.log("ERROR: Deterministic JSON check failed");
  } else {
    console.log("Deterministic JSON check passed");
  }
  const hash = crypto.createHash('sha256').update(str_deterministic_json).digest('hex');
  
  if (hash !== str_sender_hash) {
    console.log("ERROR: Hash check failed");
  } else {
    console.log("Hash check passed");
  } 
  
  const simple_addr = str_sender_addr.replace("0xai_", '');
  const bpublic_key = Buffer.from(urlSafeBase64ToBase64(simple_addr), 'base64');
  const hex_public_key = bpublic_key.toString('hex');
  const publicKey = ec.keyFromPublic(hex_public_key, 'hex');
  // Reconstruct the hex public key from the publicKey variable
  const reconstructed_hex_public_key = publicKey.getPublic().encode('hex', true);  // true for compressed format
  // Now base64 encode the reconstructed_hex_public_key to match simple_addr
  const reconstructed_simple_addr = base64ToUrlSafeBase64(Buffer.from(reconstructed_hex_public_key, 'hex').toString('base64'));
  console.log("Original:    " + simple_addr)
  console.log("Reconstruct: " + reconstructed_simple_addr);   
  
  const signature = Buffer.from(str_sender_sign, 'base64');
  
  // Decompose the signature into r and s values
  const r = signature.slice(0, 32).toString('hex');
  const s = signature.slice(32).toString('hex');
  const signature_check = publicKey.verify(hash, { r, s });
  
  console.log(signature_check)
 
  if(simple_addr !== reconstructed_simple_addr) { 
    console.log("ERROR: Address reconstruction failed");
  } else{
    console.log("Address reconstruction passed");
  }

  if (hash !== str_sender_hash) {
    console.log("ERROR: Hash check failed");
  } else {
    console.log("Hash check passed");
  } 

  console.log(signature_check)
  if (signature_check) {
    console.log("Signature check passed");
  } else {
    console.log("ERROR: Signature check failed");
 }

}

///
/// npm install elliptic
/// npm install json-stable-stringify
///
// Python generated message:
// Sign:  MEQCIHWA7Jmtz-ENKo3JfMbfWGGmIAufwCFkKXz35gV8VudyAiBV8KlIKRglYlH8odTF-yfwJvHDv28BioFc1Vm1OONJVw==
// r:     53148392663848026244174776058849552388174729548346002418120423506322658879346
// s:     38871803765700959950587377821463430492143600401123022971639663292232549747031

const crypto = require('crypto');
const elliptic = require('elliptic');
const stringify = require('json-stable-stringify');

const asn1 = require('asn1.js');

const ec = new elliptic.ec('secp256k1');

function urlSafeBase64ToBase64(urlSafeBase64) {
  return urlSafeBase64.replace(/-/g, '+').replace(/_/g, '/');
}

function base64ToUrlSafeBase64(base64) {
  return base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function logGreen(text) {
  console.log('%c' + text, 'color: green;');
}

function logRed(text) {
  console.log('%c' + text, 'color: red;');
}

if (require.main === module) {
  const PYTON_MESSAGE = {
    "9": 9,
    "2": 2,
    "3": 3,
    "10": {
      "2": 2,
      "100": 100,
      "1": 1
    },
    "SIGN": "MEQCIEIz_Nfy9CJ0GYW1V7Iw0uFJAVzu1TnOWkCVYnrt8PNHAiB0JCk_pgzGGIMz-KIvOCC_BzbGB8jxkAb_OwPX7AQTyA==",
    "ADDR": "0xai_AuN2SENcYNzRgbPUHVCFe6W1q-vieUKap2VY9mU_Fljy",
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
    "SIGN": "MEQCIH1SZk5vEQHxMKMKfh86BWtcv-9OxOJi9_YEHbNjMdSDAiAMy7kJzWki9Yg879ztF4KqDQYy4mOHn0RjYojaF1N4Fw==",
    "ADDR": "0xai_AuN2SENcYNzRgbPUHVCFe6W1q-vieUKap2VY9mU_Fljy",
    "HASH": "7d72cf5bd6cda16c86dfb2c2c4983464edda5bf78e1ca3a21139f5037454c6f3"
  }

  PYTHON_GENERATED_R = "29944502268853209619360675950805332678811343998557179264213874423984844501831"
  PYTHON_GENERATED_S = "52532181617554876589028943228476713736835593012037145907934249979039778608072"
  
  const VALID_RECEIVED_JSON = '{"10":{"1":1,"100":100,"2":2},"2":2,"3":3,"9":9}';
  const NON_DATA_FIELDS = ['SIGN', 'ADDR', 'HASH'];
  const dct_received =  PYTON_MESSAGE;
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
  const hash = crypto.createHash('sha256').update(str_deterministic_json).digest();
  const hex_hash = hash.toString('hex');
  
  
  const simple_addr = str_sender_addr.replace("0xai_", '');
  const bpublic_key = Buffer.from(urlSafeBase64ToBase64(simple_addr), 'base64');
  const hex_public_key = bpublic_key.toString('hex');
  const publicKey = ec.keyFromPublic(hex_public_key, 'hex');
  // Reconstruct the hex public key from the publicKey variable
  const reconstructed_hex_public_key = publicKey.getPublic().encode('hex', true);  // true for compressed format
  // Now base64 encode the reconstructed_hex_public_key to match simple_addr
  const reconstructed_simple_addr = base64ToUrlSafeBase64(
    Buffer.from(reconstructed_hex_public_key, 'hex').toString('base64')
  );
  
  // Define the ECDSASignature ASN.1 syntax
  const ECDSASignature = asn1.define('ECDSASignature', function() {
    this.seq().obj(
      this.key('r').int(),
      this.key('s').int()
    );
  });

  const signatureDER = Buffer.from(str_sender_sign, 'base64');
  const signature = ECDSASignature.decode(signatureDER, 'der');
  const r = signature.r; //.toString(16);
  const s = signature.s; //.toString(16);

  var prechecks_passed = true;
  if (dct_received === PYTON_MESSAGE) {
    if (r.toString(10) === PYTHON_GENERATED_R && s.toString(10) === PYTHON_GENERATED_S) {
      logGreen("r, s check passed");
    } else {
      logRed("ERROR: r,s check failed");
      prechecks_passed = false;
    }
  }
      
  
  if(simple_addr !== reconstructed_simple_addr) { 
    logRed("ERROR: Address reconstruction failed");
    prechecks_passed = false
  } else{
    logGreen("Address reconstruction passed");
  }

  if (hex_hash !== str_sender_hash) {
    logRed("ERROR: Hash check failed");
    prechecks_passed = false;
  } else {
    logGreen("Hash check passed");
  } 

  if (!prechecks_passed) {
    logRed("ERROR: Prechecks failed");
    return;
  } else {
    logGreen("Prechecks succeeded!");
    // const bhash = Buffer.from(hash, 'hex');
    const bhash = hash; // if using digest() instead of digest('hex');
    // const signature_check = publicKey.verify(bhash, { r: r.toString('hex'), s: s.toString('hex') });
    // const signature_check = publicKey.verify(bhash, { r, s });
    // const signatureHex = signatureDER.toString('hex');
    const signature_check = publicKey.verify(bhash, signatureDER);  

  
    if (signature_check) {
      logGreen("Signature check passed");
    } else {
      logRed("ERROR: Signature check failed");
   }
  }
}

const crypto = require('crypto');
const elliptic = require('elliptic');
const asn1 = require('asn1.js');
const ec = new elliptic.ec('secp256k1');
const stringify = require('json-stable-stringify');

function urlSafeBase64ToBase64(urlSafeBase64) {
  return urlSafeBase64.replace(/-/g, '+').replace(/_/g, '/');
}

function base64ToUrlSafeBase64(base64) {
  return base64.replace(/\+/g, '-').replace(/\//g, '_');
}

// Generate keys
const keyPair = crypto.generateKeyPairSync('ec', {
  namedCurve: 'secp256k1',
  publicKeyEncoding: {
    type: 'spki',
    format: 'der'
  },
  privateKeyEncoding: {
    type: 'pkcs8',
    format: 'der'
  }
});

const pk_der_hex = keyPair.publicKey.toString('hex'); // DER format public key as a hex string

// Extract the public key in 'spki' and 'der' format
const publicKeyDer = keyPair.publicKey;
// Define the ASN.1 syntax for SPKI
const SPKI = asn1.define('SPKI', function() {
  this.seq().obj(
    this.key('algorithm').seq().obj(
      this.key('id').objid(),
      this.key('namedCurve').objid()
    ),
    this.key('publicKey').bitstr()
  );
});
// Decode the DER-encoded public key
const publicKeyDecoded = SPKI.decode(publicKeyDer, 'der');
// Extract the raw public key bytes
const publicKeyBytes = publicKeyDecoded.publicKey.data;
// Import the public key into elliptic
const publicKeyObj = ec.keyFromPublic(publicKeyBytes, 'hex');
// Get the compressed public key
const compressedPublicKey = publicKeyObj.getPublic(true, 'hex');
// Convert the compressed public key to a buffer
const compressedPublicKeyBuffer = Buffer.from(compressedPublicKey, 'hex');
// Convert the buffer to base64
const compressedPublicKeyB64 = compressedPublicKeyBuffer.toString('base64');
// Convert Base64 to URL-safe Base64
const compressedPublicKeyUrlSafeB64 = base64ToUrlSafeBase64(compressedPublicKeyB64);

// Construct the message
const dct_to_send = { '9': 9, '2': 2, '3': 3, '10': { '2': 2, '100': 100, '1': 1 } };
const deterministic_json = stringify(dct_to_send);
// Hash the message
// Direct 
// const hash = crypto.createHash('sha256').update(message).digest();
// const hashHex = hash.toString('hex');
// on text
message = deterministic_json
const hashHex = crypto.createHash('sha256').update(deterministic_json).digest('hex');
const hash = Buffer.from(hashHex, 'hex');


// Sign the hash
const signature = crypto.sign(null, hash, {
  key: keyPair.privateKey,
  format: 'der',
  type: 'pkcs8'
});

// Base64url encode the signature
const signatureB64 = base64ToUrlSafeBase64(signature.toString('base64'));

console.log('public_key_hex = "' + pk_der_hex + '"');
console.log('signature_hex = "' + signatureB64 + '"');
console.log('received_hash = "' + hashHex + '"');
console.log('compressed_pk = "' + compressedPublicKeyUrlSafeB64 + '"');

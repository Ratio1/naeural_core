const crypto = require('crypto');
const elliptic = require('elliptic');

function urlSafeBase64ToBase64(urlSafeBase64) {
  return urlSafeBase64.replace(/-/g, '+').replace(/_/g, '/');
}

function base64ToUrlSafeBase64(base64) {
  return base64.replace(/\+/g, '-').replace(/\//g, '_');
}

// Assume publicKeyDerHex and signatureB64 are obtained from Python script
const publicKeyDerHex = '3056301006072a8648ce3d020106052b8104000a0342000486942c546bbceafa50af91bf7f3016c70074e38c036a6b11eace6af9ce0ef0447b12ba146b6de3baa3fb62baaee1104bad87c0034255a55cc52dea5d8080427e';  // Replace with actual public key in DER format as a hex string
const pk_b64 = 'AoaULFRrvOr6UK-Rv38wFscAdOOMA2prEerOavnODvBE';
const signatureB64 = 'MEQCIFk2G4uBZvTYS8ICtgpyNXEN3upA3Y8K5I79ZC2RlocCAiArma-dQ-a7_kAoDatMmpGld8SVTHcIo0VIxSAo8pENkQ==';  // Replace with actual Base64url encoded signature
const received_hash = '315f5bdb76d078c43b8ac0064e4a0164612b1fce77c869345bfc94c75894edd3'


// const signatureBuffer = Buffer.from(base64url.toBuffer(signatureB64));
const signatureBuffer =  Buffer.from(urlSafeBase64ToBase64(signatureB64), 'base64');


// start compressed
// Convert compressed public key to uncompressed public key
const ec = new elliptic.ec('secp256k1');
const publicKeyBuffer = Buffer.from(urlSafeBase64ToBase64(pk_b64), 'base64');
const publicKeyHex = publicKeyBuffer.toString('hex');
const publicKeyObj = ec.keyFromPublic(publicKeyHex, 'hex');
const uncompressedPublicKeyHex = publicKeyObj.getPublic(false, 'hex');

// Manually create DER formatted public key
const publicKeyDerManual = '3056301006072a8648ce3d020106052b8104000a034200' + uncompressedPublicKeyHex;
const publicKeyObj2 = crypto.createPublicKey({
  key: Buffer.from(publicKeyDerManual, 'hex'),
  format: 'der',
  type: 'spki'
});
// end compressed


// Hash the message
const message = 'Hello, world!';
const hash = crypto.createHash('sha256').update(message).digest();
const hashHex = hash.toString('hex');

if (hashHex != received_hash) {
  console.log('Hashes do not match!'); 
} else {
  console.log('Hashes match!');


  
  const signatureValid2 = crypto.verify(
    null,
    hash,
    {
      key: publicKeyObj2,
      padding: crypto.constants.RSA_PKCS1_PSS_PADDING
    },
    signatureBuffer
  );

  console.log('Validation:', signatureValid2);
}


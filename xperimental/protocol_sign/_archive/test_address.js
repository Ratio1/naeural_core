const TEST = "0xai_AioS5Bgfks874QwGcfB_cu8wWeN-dVakNhc-2Bwb9fQs"

const crypto = require('crypto');
const elliptic = require('elliptic');
const ec = new elliptic.ec('secp256k1');

function base64ToUrlSafeBase64(base64) {
    return base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

const str_sender_addr = "0xai_AioS5Bgfks874QwGcfB_cu8wWeN-dVakNhc-2Bwb9fQs";  // Replace with your actual address
const simple_addr = str_sender_addr.replace("0xai_", '');
const bpublic_key = Buffer.from(base64ToUrlSafeBase64(simple_addr), 'base64');
const hex_public_key = bpublic_key.toString('hex');
const publicKey = ec.keyFromPublic(hex_public_key, 'hex');

// Reconstruct the hex public key from the publicKey variable
const reconstructed_hex_public_key = publicKey.getPublic().encode('hex', true);  // true for compressed format

// Now base64 encode the reconstructed_hex_public_key to match simple_addr
const reconstructed_simple_addr = base64ToUrlSafeBase64(Buffer.from(reconstructed_hex_public_key, 'hex').toString('base64'));

console.log("Original:    " + simple_addr)
console.log("Reconstruct: " + reconstructed_simple_addr);  // This should match simple_addr
if (reconstructed_simple_addr == simple_addr) {
  console.log("Public key reconstruction check passed")
} else {
  console.log("ERROR: Public key reconstruction check failed")
}


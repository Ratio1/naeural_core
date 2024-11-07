const crypto = require('crypto');
const fs = require('fs');
const elliptic = require('elliptic');
const asn1 = require('asn1.js');
const ec = new elliptic.ec('secp256k1');
const stringify = require('json-stable-stringify');

const EE_SIGN      = 'EE_SIGN';
const EE_SENDER    = 'EE_SENDER';
const EE_HASH      = 'EE_HASH';
const ADDR_PREFIX   = "0xai_";

const NON_DATA_FIELDS = [EE_SIGN, EE_SENDER, EE_HASH];

function urlSafeBase64ToBase64(urlSafeBase64) {
  return urlSafeBase64.replace(/-/g, '+').replace(/_/g, '/');
}

function base64ToUrlSafeBase64(base64) {
  return base64.replace(/\+/g, '-').replace(/\//g, '_');
}

function logGreen(text) {
  console.log('%c' + text, 'color: green;');
}

function logRed(text) {
  console.log('%c' + text, 'color: red;');
}

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

class DecentrAIBC {
  constructor(filePath) {
    this.filePath = filePath;
    this.keyPair = this.loadOrCreateKeys();
    this.address = this.constructAddress();
    logGreen(DecentrAIBC.name + ' address: ' + this.address)
  }

  loadOrCreateKeys() {
    try {
      console.log(`Searching for saved keys at ${this.filePath}...`);
      const savedKeys = fs.readFileSync(this.filePath, { encoding: 'utf8' });
      console.log('Loading saved keys...');
      return this.parseKeys(JSON.parse(savedKeys));
    } catch (error) {
      console.log('No saved keys found or an error occurred. Generating new keys...');
      return this.generateAndSaveKeys();
    }
  }

  parseKeys(parsedKeys) {
    return {
      publicKey: Buffer.from(parsedKeys.publicKey, 'hex'),
      privateKey: this.createPrivateKey(Buffer.from(parsedKeys.privateKey, 'hex'))
    };
  }


  createPrivateKey(buffer) {
    return crypto.createPrivateKey({
      key: buffer,
      format: 'der',
      type: 'pkcs8'
    });
  }

  generateAndSaveKeys() {
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
    const savedKeys = {
      publicKey: keyPair.publicKey.toString('hex'),
      privateKey: keyPair.privateKey.toString('hex')
    };
    fs.writeFileSync(this.filePath, JSON.stringify(savedKeys, null, 2), { encoding: 'utf8' });
    console.log(`Keys generated and saved to ${this.filePath}.`);
    return keyPair;
  }


  constructAddress() {
    const publicKeyDer = this.keyPair.publicKey;
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
    return compressedPublicKeyUrlSafeB64;
  }  

  getPublicKeyDER() {
    return this.keyPair.publicKey.toString('hex'); ;
  }

  getPrivateKey() {
    return this.keyPair.privateKey;
  }

  getAddress() {
    return ADDR_PREFIX + this.address;
  }

  getHash(input) {
    let inputString;
    if (typeof input === 'object') {
      inputString = stringify(input);
    } else if (typeof input === 'string') {
      inputString = input;
    } else {
      throw new Error('Unsupported input type. Input must be a string or object.');
    }    
    // Hash the input string
    const strDigest = crypto.createHash('sha256').update(inputString).digest('hex');
    const binDigest = Buffer.from(strDigest, 'hex');
    return {strHash: strDigest, binHash: binDigest};
  }


  sign(input, format = 'json') {
    const { binHash } = this.getHash(input);
    const signatureB64 = this.signHash(binHash);
    return this.prepareMessage(input, signatureB64, format);
  }

  signHash(binHash) {
    const signature = crypto.sign(null, binHash, {
      key: this.getPrivateKey(),
      format: 'der',
      type: 'pkcs8'
    });
    return base64ToUrlSafeBase64(signature.toString('base64'));
  }

  prepareMessage(input, signatureB64, format) {
    const message = Object.assign({}, input, { [EE_SIGN]: signatureB64, [EE_SENDER]: this.getAddress(), [EE_HASH]: this.getHash(input).strHash });
    if (format === 'json') {
      return JSON.stringify(message);
    } else if (format === 'object') {
      return message;
    } else {
      throw new Error('Unsupported format. Format must be either "object" or "json".');
    }
  }

  
  verify(fullJSONMessage) {
    const objReceived = JSON.parse(fullJSONMessage);
    const signatureB64 = objReceived[EE_SIGN];
    const pkB64 = objReceived[EE_SENDER].replace(ADDR_PREFIX, '');
    const receivedHash = objReceived[EE_HASH];
    const objData = Object.fromEntries(Object.entries(objReceived).filter(([key]) => !NON_DATA_FIELDS.includes(key)));
    const strDeterministicJSON = stringify(objData);
    const hash = crypto.createHash('sha256').update(strDeterministicJSON).digest();
    const hashHex = hash.toString('hex');
    console.log('Verifying signature from '+ objReceived[EE_SENDER]);
    if (hashHex != receivedHash) {
      console.log('Hashes do not match!'); 
      return false;
    } else {
      console.log('Hashes match!');
    }

    const signatureBuffer =  Buffer.from(urlSafeBase64ToBase64(signatureB64), 'base64');
    const publicKeyBuffer = Buffer.from(urlSafeBase64ToBase64(pkB64), 'base64');
    const publicKeyHex = publicKeyBuffer.toString('hex');
    const publicKeyObjElliptic = ec.keyFromPublic(publicKeyHex, 'hex');
    const uncompressedPublicKeyHex = publicKeyObjElliptic.getPublic(false, 'hex');
    
    // Manually create DER formatted public key
    const publicKeyDerManual = '3056301006072a8648ce3d020106052b8104000a034200' + uncompressedPublicKeyHex;
    const publicKeyObj = crypto.createPublicKey({
      key: Buffer.from(publicKeyDerManual, 'hex'),
      format: 'der',
      type: 'spki'
    });  
    const signatureValid = crypto.verify(
      null,
      hash,
      {
        key: publicKeyObj,
        padding: crypto.constants.RSA_PKCS1_PSS_PADDING
      },
      signatureBuffer
    );  
    return signatureValid;
  }
}

module.exports = DecentrAIBC;

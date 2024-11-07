const path = require('path');
const DecentrAIBC = require('./0xaibc');  // Adjust the path if necessary

// sender side demo
const message = {"SERVER" : "gts-test", "COMMAND" : "UPDATE_CONFIG", "PAYLOAD" : {"GIGI" : "BUNA"}};
const eng = new DecentrAIBC(path.resolve(__dirname, 'keys.json'));
const messageToSend = eng.sign(message);

console.log("data_message = '" + messageToSend + "'");


// receiver side demo
const receivedMessage = '{"SERVER": "gigi", "COMMAND": "get", "PARAMS": "1", "EE_SENDER": "0xai_AsteqC-MZKBK6JCkSxfM-kU46AV0MP6MxiB4K1XAcjzo", "EE_SIGN": "MEQCIBML0hRjJtzKJnaZhLwki2awVTNKE_-TanMrapmkpsI2AiADjkUb8TuKCtysAIfBwKwwPzys-48X6zB9HyINJzGzPQ==", "EE_HASH": "e00e86d172c160edc66177b0c4cbc464ababc2f1827433789e68322c6eb766ed"}';
console.log('Incoming Signature validation: ', eng.verify(receivedMessage));

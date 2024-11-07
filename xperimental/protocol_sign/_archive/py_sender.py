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
# dct_to_send = { '9': 9, '2': 2, '3': 3, '10': { '2': 2, '100': 100, '1': 1 } }
str_to_send = """{"EE_FORMATTER":"cavi2","EE_MESSAGE_ID":"a512448e-8f9e-4e9b-a3b3-d8071bbaee3f","EE_PAYLOAD_PATH":["dev-1","Cam-Radu-1",null,null],"SB_IMPLEMENTATION":"cavi2","category":"","data":{"identifiers":{},"img":{"height":null,"id":null,"width":null},"specificValue":{},"time":null,"value":{}},"demoMode":false,"messageID":"a512448e-8f9e-4e9b-a3b3-d8071bbaee3f","metadata":{"displayed":true,"ee_message_seq":33501,"ee_timezone":"UTC+3","ee_tz":"Europe/Bucharest","error_code":null,"info":"EE v3.28.131, Lib v9.8.62, info text: None","initiator_id":"cavi2-local-radu","module":"VideoStreamDataCapture","notification":"Video DCT 'Cam-Radu-1' successfully connected. Overall 8231 reconnects.","notification_type":"NORMAL","sbCurrentMessage":"a512448e-8f9e-4e9b-a3b3-d8071bbaee3f","sbTotalMessages":33501,"sb_event_type":"NOTIFICATION","sb_id":"dev-1","session_id":null,"stream_name":"Cam-Radu-1","timestamp":"2023-10-19 14:49:03.680131","video_stream_info":{"buffersize":0,"current_interval":null,"fps":23,"frame_count":429,"frame_current":3513330,"frame_h":1080,"frame_w":1920}},"sender":{"hostId":"dev-1","id":"edge-node","instanceId":"edge-node-v3.28.131"},"time":{"deviceTime":"","hostTime":"2023-10-19 14:49:03.982422","internetTime":""},"type":"notification","version":"3.28.131"}"""
dct_to_send = {
  "EE_FORMATTER": "cavi2",
  "EE_MESSAGE_ID": "a512448e-8f9e-4e9b-a3b3-d8071bbaee3f",
  "EE_PAYLOAD_PATH": [
    "dev-1",
    "Cam-Radu-1",
    None,
    None
  ],
  "SB_IMPLEMENTATION": "cavi2",
  "category": "",
  "data": {
    "identifiers": {},
    "img": {
      "height": None,
      "id": None,
      "width": None
    },
    "specificValue": {},
    "time": None,
    "value": {}
  },
  "demoMode": False,
  "messageID": "a512448e-8f9e-4e9b-a3b3-d8071bbaee3f",
  "metadata": {
    "displayed": True,
    "ee_message_seq": 33501,
    "ee_timezone": "UTC+3",
    "ee_tz": "Europe/Bucharest",
    "error_code": None,
    "info": "EE v3.28.131, Lib v9.8.62, info text: None",
    "initiator_id": "cavi2-local-radu",
    "module": "VideoStreamDataCapture",
    "notification": "Video DCT 'Cam-Radu-1' successfully connected. Overall 8231 reconnects.",
    "notification_type": "NORMAL",
    "sbCurrentMessage": "a512448e-8f9e-4e9b-a3b3-d8071bbaee3f",
    "sbTotalMessages": 33501,
    "sb_event_type": "NOTIFICATION",
    "sb_id": "dev-1",
    "session_id": None,
    "stream_name": "Cam-Radu-1",
    "timestamp": "2023-10-19 14:49:03.680131",
    "video_stream_info": {
      "buffersize": 0,
      "current_interval": None,
      "fps": 23,
      "frame_count": 429,
      "frame_current": 3513330,
      "frame_h": 1080,
      "frame_w": 1920
    }
  },
  "sender": {
    "hostId": "dev-1",
    "id": "edge-node",
    "instanceId": "edge-node-v3.28.131"
  },
  "time": {
    "deviceTime": "",
    "hostTime": "2023-10-19 14:49:03.982422",
    "internetTime": ""
  },
  "type": "notification",
  "version": "3.28.131"
}
message = json.dumps(dct_to_send, sort_keys=True, separators=(',',':'))
bin_message = message.encode()
assert message == str_to_send
obj_hash = hashlib.sha256(bin_message)
str_hash = obj_hash.hexdigest()
print("Hash: "+ str_hash)
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




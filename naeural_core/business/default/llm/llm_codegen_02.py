"""
pipeline start example:
```json
{
  "EE_ID" : "aid_mob",
  "ACTION" : "UPDATE_CONFIG",
  "PAYLOAD" : {
      "NAME" : "llm_test",
      "TYPE" : "Void",
      "PLUGINS" : [
          {
              "SIGNATURE" : "LLM_CODEGEN_01",
              "INSTANCES" : [
                  {
                      "INSTANCE_ID" : "LLM_CODEGEN_01_def1"
                  }
              ]
          }
      ]
  },
  "INITIATOR_ID" : "Explorer_xxxx",
  "SESSION_ID" : "Explorer_xxxx20230918144709767060",
  "EE_SIGN" : "MEUCIBESpz-w3tdV01nuJ9mbxm5THwUeqnKQ-knZ4QZfnutfAiEAweX_VHpbkHUgrC7W7484f4rVnLSLmEyHdcWXC5iISGY=",
  "EE_SENDER" : "0xai_Au7FmjsXxtgLTFPV1ux9bI5a7UhAIK6azRfS0FGTMf2T",
  "EE_HASH" : "52189177d299b393f8ae04171f646a81cb424375230e3b0dc3bf2a4022550fa1"
}	
```
 
request example:
```json
	{
		"EE_ID" : "target-node",
		"ACTION" : "UPDATE_PIPELINE_INSTANCE",
		"PAYLOAD" : {
			"NAME" : "LLM_PIPELINE",
			"SIGNATURE" : "LLM_CODEGEN_01",
			"INSTANCE_ID" : "DEFAULT",
			"INSTANCE_CONFIG" : {
				"INSTANCE_COMMAND": {
					"history": [
						{
							"request" : "write the php script for connecting to a mqtt server",
							"response" : "<?php \n require("path/to/phpMQTT.php");..."
						},
						{
							"request" : "add a callback that filters all the messages that ..."",
							"response" : "function filterMessages($topic, $msg) {..."
						}
					],
					"request" : "add a hello world example",
					"request_id" : "1234"						
				}
			}
		},
		"INITIATOR_ID" : "Explorer_xxxx",
		"SESSION_ID" : "Explorer_xxxx20230918140745309935",
		"EE_SIGN" : "MEUCIHGvyPrdu5OmrqfjOsmZi5fpI7x0vQunRFbgpJwWTbqyAiEAvrZOhMnxXrbVchmgdmZ6q9xoQMVa6wS30R_qIRHnuOk=",
		"EE_SENDER" : "0xai_Au7FmjsXxtgLTFPV1ux9bI5a7UhAIK6azRfS0FGTMf2T",
		"EE_HASH" : "2aa7ada77792e421bcc8ecd31c6f270510f1dd8c6371d1f16cc5e0ba3474d70a"
	}
```

  
"""


from naeural_core.business.base import BasePluginExecutor as BasePlugin


__VER__ = '0.1.0.0'

_CONFIG = {

  # mandatory area
  **BasePlugin.CONFIG,

  # our overwritten props
  # 'AI_ENGINE'     : "llm_server",
  'OBJECT_TYPE'   : [],
  'PROCESS_DELAY' : 10,
  

  'VALIDATION_RULES' : {
    **BasePlugin.CONFIG['VALIDATION_RULES'],
  },  
}

class LlmCodegen02Plugin(BasePlugin):
  CONFIG = _CONFIG
  def __init__(self, **kwargs):
    self._nr_requests = 0
    super(LlmCodegen02Plugin, self).__init__(**kwargs)
    return
  
  
  
  def __process_request(self, query, request_id, history, user):
    static_result = f"Hello '{self.eeid}:{self.node_addr}:{self._nr_requests}'"
    if user is not None:
      # process with persistance
      result = self.chatapi_ask(
        question=query,
        persona="codegen_complete",
        user=user,
        set_witness=False,
        personas_folder='./core/utils/openai/personas'
      )
    else:
      result = static_result
    return result
  
  def on_command(self, data, **kwargs):
    self._nr_requests += 1
    request = data.get('request', None)
    request_id = data.get('request_id', None)
    history = data.get('history', [])
    user_id = data.get('user', None)
    temperature = data.get('temperature', None)

    resp = self.__process_request(
      query=request, 
      request_id=request_id,
      history=history,
      user=user_id,
    )
    
    self.P("Generated code:\n{}".format(resp))
    
    self.create_and_send_payload(
      codegen_response=resp,
      codegen_request_id=request_id,
      codegen_history=history,
      codegen_user=user_id,
      codegen_temperature=temperature,
		)
    return

  def _process(self):
    payload = None
    return payload
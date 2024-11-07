NLP_AGENT_MIXIN_CONFIG = {
  'OBJECT_TYPE': [],
  "ALLOW_EMPTY_INPUTS": False,  # if this is set to true the on-idle will be triggered continuously the process

  "VALIDATION_RULES": {
  },
}


class _NlpAgentMixin(object):
  def compute_response(self, data, inferences):
    processed_data = {k.lower(): v for k, v in data.items()}
    text_responses = [inf.get('text') for inf in inferences]
    model_name = inferences[0].get('MODEL_NAME', None) if len(inferences) > 0 else None
    payload = self._create_payload(
      data=data,
      inferences=inferences,
      request_id=processed_data.get('request_id', None),
      text_responses=text_responses,
      model_name=model_name,
    )
    return payload



from naeural_core.serving.ai_engines import AI_ENGINES

def _handle_ai_engine(ai_engine):
  model_instance_id = 'DEFAULT'
  if isinstance(ai_engine, (tuple, list)):
    assert len(ai_engine) == 2
    ai_engine, model_instance_id = ai_engine

  if '?' in ai_engine:
    ai_engine, model_instance_id = ai_engine.split('?')

  return {
    'AI_ENGINE' : ai_engine,
    'MODEL_INSTANCE_ID' : model_instance_id
  }

def get_serving_process_given_ai_engine(ai_engine):
  handle = _handle_ai_engine(ai_engine)
  ai_engine = handle['AI_ENGINE'].lower()
  instance_id = handle['MODEL_INSTANCE_ID']

  config_ai_engine = AI_ENGINES.get(ai_engine, {'SERVING_PROCESS': ai_engine})
  serving_process = config_ai_engine['SERVING_PROCESS']
  if config_ai_engine.get('REQUIRES_INSTANCE', False):
    # in this case we must code the serving process as CLASS, NAME particularly
    # for custom downloadable models
    # TODO: however the support seems to be lost downstream!
    serving_process = serving_process, instance_id
  return serving_process

def get_ai_engine_given_serving_process(serving_process, params={}):
  model_instance_id = None
  if isinstance(serving_process, (tuple, list)):
    assert len(serving_process) == 2
    serving_process, model_instance_id = serving_process

  for ai_engine, dct in AI_ENGINES.items():
    crt_params = dct.get('PARAMS', {})
    crt_serving_process = dct['SERVING_PROCESS']
    if serving_process == crt_serving_process and params == crt_params:
      if model_instance_id is None:
        return ai_engine
      else:
        return ai_engine, model_instance_id
  #endfor

  if model_instance_id is None:
    return serving_process
  else:
    return serving_process, model_instance_id

def get_params_given_ai_engine(ai_engine):
  handle = _handle_ai_engine(ai_engine)
  ai_engine = handle['AI_ENGINE']
  config_ai_engine = AI_ENGINES.get(ai_engine, {'SERVING_PROCESS': ai_engine})
  other_params = config_ai_engine.get('PARAMS', {})
  return other_params

def get_params_given_serving_process(serving_process):
  # this is tricky as multiple AI_ENGINES could share the same serving process
  # thus this function should work ONLY if there is just one serving_process
  model_instance_id = None
  if isinstance(serving_process, (tuple, list)):
    assert len(serving_process) == 2
    serving_process, model_instance_id = serving_process
  lst_found = []
  for ai_engine, dct in AI_ENGINES.items():
    crt_serving_process = dct['SERVING_PROCESS']
    if crt_serving_process == serving_process:
      lst_found.append(dct)
  if len(lst_found) == 1:
    return lst_found[0].get('PARAMS', {})
  else:
    # either nothing found or multiple found
    if len(lst_found) > 1:
      raise ValueError("Multiple AI Engines use '{}' - cannot retrieve the params!".format(serving_process))
    else:
      raise ValueError("NO AI Engines use '{}' - cannot retrieve the params!".format(serving_process))
  return
      
  

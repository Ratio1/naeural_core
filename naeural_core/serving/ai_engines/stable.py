AI_ENGINES = {}

AI_ENGINES['general_detector'] = {
  # 'SERVING_PROCESS': 'th_y5l6s'
  'SERVING_PROCESS': 'th_yf8l'  # Y8L best so far for large res
}

AI_ENGINES['lowres_general_detector'] = {
  # 'SERVING_PROCESS': 'th_y5s6s'
  # 'SERVING_PROCESS': 'th_yf5s'  # y5s best so far for low res better than y8s as it has better input resolution
  'SERVING_PROCESS': 'th_yf8s'
}

AI_ENGINES['nano_general_detector'] = {
  'SERVING_PROCESS': 'th_yf8n'
}

AI_ENGINES['general_detector_xp'] = {
  'SERVING_PROCESS': 'th_yf_xp',
}

AI_ENGINES['yf5_detector'] = {
  'SERVING_PROCESS': 'th_yf5l'
}

AI_ENGINES['lowres_yf5_detector'] = {
  'SERVING_PROCESS': 'th_yf5s'
}

# AI_ENGINES['yf8_detector'] = {
#   'SERVING_PROCESS': 'th_yf8l'
# }

AI_ENGINES['lowres_yf8_detector'] = {
  'SERVING_PROCESS': 'th_yf8s'
}

AI_ENGINES['advanced_general_detector'] = {
  # 'SERVING_PROCESS': 'th_effdet7'
  'SERVING_PROCESS': 'th_yf8x'
}

AI_ENGINES['pose_detector'] = {
  # 'SERVING_PROCESS' : 'th_y5l6s_move',
  # 'SERVING_PROCESS': 'th_y5l6s_mspn',
  'SERVING_PROCESS': 'th_yf_mspn'
}

AI_ENGINES['lowres_pose_detector'] = {
  # 'SERVING_PROCESS': 'th_y5s6s_mspn'
  'SERVING_PROCESS': 'th_yfs_mspn'
}

AI_ENGINES['nano_pose_detector'] = {
  'SERVING_PROCESS': 'th_yfn_mspn'
}

AI_ENGINES['face_detector'] = {
  'SERVING_PROCESS': 'th_rface'
}

AI_ENGINES['face_detector_identification'] = {
  'SERVING_PROCESS': 'th_retina_face_resnet_identification'  # 'face_identification'
}

AI_ENGINES['face_id'] = {
  'SERVING_PROCESS': 'th_rface_id'  # 'face_identification'
}

AI_ENGINES['lowres_face_detector'] = {
  'SERVING_PROCESS': 'th_rface_s'
}

AI_ENGINES['custom_second_stage_detector'] = {
  # TODO: support seems to be lost downstream
  'SERVING_PROCESS': 'th_yolo_second_stage',
  'REQUIRES_INSTANCE': True  # requires ('th_yolo_second_stage', 'safety_helmet') style
}

# TODO:
#  below config is just for demo purposes
#  so this file should be split between "core" and "plugins"
AI_ENGINES['a_dummy_ai_engine'] = {
  'SERVING_PROCESS': 'a_dummy_classifier',
  'PARAMS': {
    'TEST_INFERENCE_PARAM': 1,  # should overwrite default 0
    'TEST_ENGINE_PARAM': 100,
  }
}

AI_ENGINES['a_dummy_cv_ai_engine'] = {
  'SERVING_PROCESS': 'a_dummy_cv_classifier',
  'PARAMS': {
    'TEST_INFERENCE_PARAM': 1,  # should overwrite default 0
    'TEST_ENGINE_PARAM': 100,
  }
}

AI_ENGINES['code_generator'] = {
   'SERVING_PROCESS': 'code_llama_v2'
}


AI_ENGINES['llm'] = {
  'SERVING_PROCESS': 'llama_v31'
}

AI_ENGINES['llm_ro'] = {
  'SERVING_PROCESS': 'ro_llama_v31'
}


AI_ENGINES['doc_embed'] = {
  'SERVING_PROCESS': 'mxbai_embed'
}


AI_ENGINES['th_training'] = {
  'SERVING_PROCESS': 'th_training',
  'REQUIRES_INSTANCE': True
}

try:
  from extensions.serving.ai_engines.stable import AI_ENGINES as EXT_AI_ENGINES
  AI_ENGINES = {
    **AI_ENGINES,
    **EXT_AI_ENGINES
  }
except ImportError:
  import warnings
  msg = """
No extensions found for AI_ENGINES. Using only the default engines.
To add custom engines, create a file at `extensions/serving/ai_engines/stable.py` in your project and define 
a dictionary named `AI_ENGINES` following the structure in the default file. Alternatively, use 
the file name of your custom serving engine as the AI_ENGINE name, without needing to define `AI_ENGINES`.
To suppress this warning, set the environment variable `PYTHONWARNINGS` to 'ignore::UserWarning'.
  """
  warnings.warn(msg, UserWarning)



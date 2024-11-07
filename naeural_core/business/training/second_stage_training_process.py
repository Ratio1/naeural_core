from naeural_core.business.training.general_training_process import GeneralTrainingProcessPlugin
from naeural_core.business.training.general_training_process import _CONFIG as BASE_CONFIG

_CONFIG = {
  **BASE_CONFIG,
  'VALIDATION_RULES': {
    **BASE_CONFIG['VALIDATION_RULES'],
  },

  'AUTO_DEPLOY': {},
}


class SecondStageTrainingProcessPlugin(GeneralTrainingProcessPlugin):
  def prepare_inference_plugin_config(self) -> dict:
    return {
      'SECOND_STAGE_MODEL_ARCHITECTURE_PATH': self.training_output['METADATA']['MODEL_ARCHITECTURE_PATH'],
      'SECOND_STAGE_MODEL_HYPERPARAMS': self.training_output['STATUS']['BEST']['model_kwargs'],
      'SECOND_STAGE_CLASS_NAMES': list(self.training_output['METADATA']['CLASSES'].keys()),
      # The following 2 lines
      # 'SECOND_STAGE_MODEL_WEIGHTS_URL': self.cloud_path_to_url(self.weights_uri['CLOUD_PATH']),
      # 'SECOND_STAGE_MODEL_TRACE_URL': self.cloud_path_to_url(self.trace_uri['CLOUD_PATH']),
      'SECOND_STAGE_MODEL_WEIGHTS_URL': self.weights_uri['URL'],
      'SECOND_STAGE_MODEL_TRACE_URL': self.trace_uri['URL'],
      'SECOND_STAGE_INPUT_SIZE': self.training_output['STATUS']['BEST']['dct_grid_option']['input_size'],
      'SECOND_STAGE_TARGET_CLASS': self.training_output['METADATA']['FIRST_STAGE_TARGET_CLASS'],
      'SECOND_STAGE_PREPROCESS_DEFINITIONS': self.training_output['METADATA']['INFERENCE_PREPROCESS_DEFINITIONS'],
      'SECOND_STAGE_OBJECTIVE_NAME': self.cfg_objective_name,
    }

  def auto_deploy(self):
    box_auto_deploy = self.cfg_auto_deploy.get('BOX_ID', None)
    node_address_auto_deploy = self.cfg_auto_deploy.get('NODE_ADDRESS', None)
    streams = self.cfg_auto_deploy.get('STREAMS', [])
    target_classes = self.training_output['METADATA']['FIRST_STAGE_TARGET_CLASS']
    if not isinstance(target_classes, list):
      target_classes = [target_classes]

    for s in streams:
      if 'PLUGINS' not in s:
        s['PLUGINS'] = []

      s['PLUGINS'].append({
        'SIGNATURE': 'second_stage_detection',
        'INSTANCES': [
          {
            'INSTANCE_ID': self.log.now_str(),
            'AI_ENGINE': 'custom_second_stage_detector',
            'OBJECT_TYPE': target_classes,
            'SECOND_STAGE_DETECTOR_CLASSES': list(self.training_output['METADATA']['CLASSES'].keys()),
            'STARTUP_AI_ENGINE_PARAMS': {
              'CUSTOM_DOWNLOADABLE_MODEL_URL': self.inference_config_uri['URL'],  # maybe find way to put
              # cloud path or extend url lifetime (for now the model data is downloaded by the serving manager,
              # which might have a different bucket policy than the training manager)
              'MODEL_INSTANCE_ID': self.training_output['METADATA']['MODEL_NAME'],
            },
            "DESCRIPTION": self.cfg_description,
            "OBJECTIVE_NAME": self.cfg_objective_name,
          }
        ]
      })

      node_address = node_address_auto_deploy or self.netmon.network_node_addr(box_auto_deploy)
      self.cmdapi_start_stream_by_config_on_other_box(node_address=node_address, config_stream=s)
    #endfor

    return

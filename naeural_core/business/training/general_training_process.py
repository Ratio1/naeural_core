from naeural_core.business.base import BasePluginExecutor as BasePlugin

__VER__ = '0.1.0.0'

_CONFIG = {
  **BasePlugin.CONFIG,
  'VALIDATION_RULES' : {
    **BasePlugin.CONFIG['VALIDATION_RULES'],
  },

  'AI_ENGINE' : 'th_training',
  'STARTUP_AI_ENGINE_PARAMS' : {
    'PIPELINE_SIGNATURE' : None,
    'PIPELINE_CONFIG' : {},
  },

  "DESCRIPTION": "",
  "OBJECTIVE_NAME": "",
  'PLUGIN_LOOP_RESOLUTION' : 1/60,
  'ALLOW_EMPTY_INPUTS' : False,
  'AUTO_DEPLOY' : {},
}


class GeneralTrainingProcessPlugin(BasePlugin):
  CONFIG = _CONFIG
  def __init__(self, **kwargs):
    self.training_output = None
    self.performed_final = False

    self.weights_uri = {'URL': '', 'CLOUD_PATH' : ''}
    self.trace_uri = {'URL': '', 'CLOUD_PATH' : ''}
    self.training_output_uri = {'URL': '', 'CLOUD_PATH': ''}
    self.inference_config_uri = {'URL': '', 'CLOUD_PATH': ''}
    super(GeneralTrainingProcessPlugin, self).__init__(**kwargs)
    return

  def startup(self):
    super().startup()
    assert self.cfg_startup_ai_engine_params.get('PIPELINE_SIGNATURE', None) is not None
    assert bool(self.cfg_startup_ai_engine_params.get('PIPELINE_CONFIG', {}))
    if self.cfg_auto_deploy:
      assert self.inspect.getsource(self.prepare_inference_plugin_config) != self.inspect.getsource(GeneralTrainingProcessPlugin.prepare_inference_plugin_config),\
             "When auto deploy is configured, `_prepare_inference_plugin_config` should be defined"

      assert self.inspect.getsource(self.auto_deploy) != self.inspect.getsource(GeneralTrainingProcessPlugin.auto_deploy),\
             "When auto deploy is configured, `_auto_deploy` should be defined"
    #endif

    return

  @property
  def cfg_startup_ai_engine_params(self):
    return self._instance_config['STARTUP_AI_ENGINE_PARAMS']

  @property
  def cfg_auto_deploy(self):
    return self._instance_config.get('AUTO_DEPLOY', {})

  def cloud_path_to_url(self, cloud_path):
    return f'minio:{cloud_path}'

  def get_instance_config(self):
    """
    This method is overriden to allow for multiple training jobs on the same node
    Returns
    -------
    res - dict, the instance config with the MODEL_INSTANCE_ID added
    """
    current_instance_config = super().get_instance_config()
    curr_ai_engine = current_instance_config.get('AI_ENGINE', 'th_training')
    curr_startup_params = current_instance_config.get('STARTUP_AI_ENGINE_PARAMS', {})
    model_instance_id = curr_startup_params.get('MODEL_INSTANCE_ID', None)
    if model_instance_id is None:
      if '?' in curr_ai_engine:
        curr_ai_engine, model_instance_id = curr_ai_engine.split('?')
        current_instance_config['AI_ENGINE'] = curr_ai_engine
      else:
        model_instance_id = current_instance_config.get('INSTANCE_ID', 'default')
      # endif model_instance_id provided in AI_ENGINE
      current_instance_config['STARTUP_AI_ENGINE_PARAMS'] = {
        **curr_startup_params,
        'MODEL_INSTANCE_ID': model_instance_id
      }
    # endif model_instance_id not provided

    return current_instance_config

  def prepare_inference_plugin_config(self) -> dict:
    return {}

  def auto_deploy(self):
    return

  def on_training_finish(self, model_id):
    training_subdir = 'training'
    # Model
    best_weights = self.training_output['STATUS']['BEST']['best_file']
    traced_model_path = self.training_output['METADATA']['MODEL_EXPORT']
    files = {'weights': best_weights, 'trace': traced_model_path}
    if traced_model_path is None:
      self.P("Traced model path not found, uploading only the weights of best model", color='y')
      files = {'weights': best_weights}
    # endif trace not found
    uris = {}
    for file_id, file_path in files.items():
      uris[file_id] = {'CLOUD_PATH': f'TRAINING/{model_id}/{file_id}/{file_path.split(self.os_path.sep)[-1]}'}
      url, _ = self.upload_file(
        file_path=file_path,
        target_path=uris[file_id]['CLOUD_PATH'],
        force_upload=True,
      )
      uris[file_id]['URL'] = url
    # endfor files
    self.weights_uri = uris['weights']
    self.trace_uri = uris.get('trace',  {'URL': None, 'CLOUD_PATH': None})
    # Training output
    json_name = f'{model_id}_training_output.json'
    path_training_output = self.os_path.join(training_subdir, json_name)
    # First we save it, so we can upload it after
    json_path = self.diskapi_save_json_to_output(dct=self.training_output, filename=path_training_output)
    self.training_output_uri['CLOUD_PATH'] = 'TRAINING/{}/{}'.format(model_id, json_name)
    url, _ = self.upload_file(
      file_path=json_path,
      target_path=self.training_output_uri['CLOUD_PATH'],
      force_upload=True
    )
    self.training_output_uri['URL'] = url

    if bool(self.cfg_auto_deploy):
      # Inference config
      dct_inference_config = self.prepare_inference_plugin_config()
      json_name = f'{model_id}_inference_config.json'
      path_inference_config = self.os_path.join(training_subdir, json_name)
      json_path = self.diskapi_save_json_to_output(dct=dct_inference_config, filename=path_inference_config)
      self.inference_config_uri['CLOUD_PATH'] = 'TRAINING/{}/{}'.format(model_id, json_name)
      url, _ = self.upload_file(
        file_path=json_path,
        target_path=self.inference_config_uri['CLOUD_PATH'],
        force_upload=True
      )
      self.inference_config_uri['URL'] = url
    #endif

    return

  def _process(self):
    self.training_output = self.dataapi_specific_struct_data_inferences(idx=0, how='list', raise_if_error=True)
    assert len(self.training_output) == 1
    self.training_output = self.training_output[0]
    assert isinstance(self.training_output, dict)
    assert 'STATUS' in self.training_output
    has_finished = self.training_output.get('HAS_FINISHED', False)
    payload_kwargs = {
      'is_status': True,
      'train_status': self.training_output,
      'description': self.cfg_description,
      'objective_name': self.cfg_objective_name,
    }
    if self.training_output['STATUS'] != 'WAITING':
      payload_kwargs['job_status'] = 'Training' if not has_finished else 'Trained'
    save_payload_json = False
    model_id = None
    if has_finished:
      if not self.performed_final:
        self.P("Training has finished", color='g')
        model_id = '{}_{}'.format(self.log.session_id, self.training_output['METADATA']['MODEL_NAME'])
        save_payload_json = True
        self.on_training_finish(model_id=model_id)

        if bool(self.cfg_auto_deploy):
          self.auto_deploy()

        self.performed_final = True
      # endif performed final
      train_final_kwargs = {
        'MODEL_URI': self.weights_uri,
        'TRACE_URI': self.trace_uri,
        'TRAINING_OUTPUT_URI': self.training_output_uri,
        'INFERENCE_CONFIG_URI': self.inference_config_uri,
      }
      payload_kwargs['TRAIN_FINAL'] = train_final_kwargs
    # endif training finished

    payload = self._create_payload(**payload_kwargs)
    if save_payload_json:
      self.diskapi_save_json_to_data(
        dct=payload.to_dict(),
        filename=f'training/{model_id}_golden_payload.json'
      )
    # endif payload needs to be saved

    return payload

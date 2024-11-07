"""
{
	"NAME" : "auto_train_full_process",
	"TYPE" : "void",
	"PLUGINS" : [
		{
			"SIGNATURE" : "ai4e_end_to_end_training",
			"INSTANCES" : [
				{
					"INSTANCE_ID" : "alabala",  # job identifier
					"OBJECTIVE_NAME" : "weapons",  # job name
					"GENERAL_DETECTOR_OBJECT_TYPE" : ["person"],  # what will be cropped
					"GENERAL_DETECTOR_AI_ENGINE" : "general_detector",  # the engine that will be used for cropping
          "CLASSES": {  # the classes that will be trained and their descriptions
            "weapon": "Weapon class",
            "no_weapon": "No weapon class"
          },
          "DESCRIPTION": "Classify people based on if they have a weapon or not",  # job description
          'REWARDS': {  # rewards for the job
            "BUDGET": 1000,  # the budget for the job
            "SEATS": 5  # the number of seats for the job
          },
          'DATASET': {
            'MAX_SIZE': 1000,  # the maximum size of the dataset
          },
          'CREATION_DATE': "2022-08-10 14:55",  # the creation date of the job
					"DATA" : {  # the data sources
						"SOURCES" : [
							{
								"NAME" : "terasa",  # the name of the source
								"TYPE" : "VideoStream",  # the type of the source
								"URL"  : "__URL__",  # the url of the source
                # below are advanced parameters
								"LIVE_FEED" : true,  # if the source will be processed live or frame by frame
								"CAP_RESOLUTION" : 0.5,  # how many frames per second will be read
								"RECONNECTABLE" : true,  # if the source will be reconnected if it disconnects
								"STREAM_CONFIG_METADATA" : {
									"INTERVALS" : {  # this can be used if we only want to process the source at certain intervals
										"ziua" : ["10:00", "17:00"],
										"noaptea" : ["21:00", "23:59"]
									}
								}
							},

							{
								"NAME" : "openspace_est",
								"TYPE" : "VideoStream",
								"URL"  : "__URL__",
								"LIVE_FEED" : true,
								"RECONNECTABLE" : true,
								"CAP_RESOLUTION" : 0.5,
								"STREAM_CONFIG_METADATA" : {
									"INTERVALS" : {
										"ziua" : ["10:00", "17:00"],
										"noaptea" : ["21:00", "23:59"]
									}
								}
							}
						],

						"CROP_PLUGIN_PARAMS" : {  # parameters for the cropping plugin
							"REPORT_PERIOD" : 60,  # how often the plugin will report the progress
							"ALIVE_UNTIL" : "2022-08-10 14:55"  # until when the plugin will be alive(in case of not stopping it manually or in case we don't gather enough data)
						},

						"CLOUD_PATH" : "DATASETS/"  # the path where the dataset will be stored
					},

          "START_TRAINING": true,  # if the training should start automatically
					"TRAINING" : {  # the training parameters
						"BOX_ID" : "hidra-training",  # the id of the box where the training will be done
						"DEVICE_LOAD_DATA" : "cuda:3",  # the device where the data will be loaded
						"DEVICE_TRAINING"  : "cuda:3",  # the device where the training will be done
						"TRAINING_PIPELINE_SIGNATURE" : "weapons",  # the signature of the training pipeline
						"GRID_SEARCH" : {},  # the grid search parameters (if none are provided the defaults will be used)
						"BATCH_SIZE" : 64,  # the batch size
						"EPOCHS" : 4  # the number of epochs
					},

					"AUTO_DEPLOY" : {  # the auto deploy parameters
						"STREAMS" : [  # the streams that will be deployed
							{  # For details on the stream configuration see the stream configuration section
								"NAME" : "terasa",
								"TYPE" : "VideoStream",
								"URL"  : "__URL__",
								"LIVE_FEED" : true,
								"CAP_RESOLUTION" : 20,
								"RECONNECTABLE" : true
							}
						]
					}
				}
			]
		}
	]
}
"""

from naeural_core.business.base import BasePluginExecutor as BasePlugin

__VER__ = '0.1.0.0'

DEFAULT_DATA_CONFIG = {
  'SOURCES': [],
  "CROP_PLUGIN_PARAMS": {
    "REPORT_PERIOD": 5
  },
  "COLLECT_UNTIL": None,
  "CLOUD_PATH": "DATASETS/",
  "TRAIN_SIZE": 0.8,
}

DEFAULT_TRAIN_CONFIG = {
  'BOX_ID': None,
  'DEVICE_LOAD_DATA': None,  # None for no preloading data, string for device otherwise
  'DEVICE_TRAINING': 'cuda:0',
  'TRAINING_PIPELINE_SIGNATURE': 'custom',
  'MODEL_ARCHITECTURE': 'BASIC_CLASSIFIER',
  'GRID_SEARCH': {},
  'BATCH_SIZE': 8,
  'EPOCHS': 4,
}

_CONFIG = {
  **BasePlugin.CONFIG,
  'OBJECTIVE_NAME': None,
  'GENERAL_DETECTOR_OBJECT_TYPE': ['person'],
  "CLASSES": {},
  "DESCRIPTION": "",
  'DATA': {},
  'REWARDS': {},
  'DATASET': {},
  'CREATION_DATE': None,
  'TRAINING_REWARDS': {},

  'START_TRAINING': False,
  'TRAINING': {},
  'AUTO_DEPLOY': {},

  "ALLOW_EMPTY_INPUTS": True,

  'PLUGIN_LOOP_RESOLUTION': 1 / 5,  # once at 5s

  'VALIDATION_RULES': {
    **BasePlugin.CONFIG['VALIDATION_RULES'],
  },
}


class Ai4eEndToEndTrainingPlugin(BasePlugin):
  def on_init(self):
    super(Ai4eEndToEndTrainingPlugin, self).on_init()
    self.__started_gather = False
    self.__started_training = False
    self.maybe_load_previous_state()
    return

  def maybe_load_previous_state(self):
    obj = self.persistence_serialization_load()
    if obj is not None:
      self.__started_gather = obj.get('started_gather', self.__started_gather)
      self.__started_training = obj.get('started_training', self.__started_training)
    # endif obj is not None
    return

  def save_current_state(self):
    obj = {
      'started_gather': self.__started_gather,
      'started_training': self.__started_training,
    }
    self.persistence_serialization_save(obj)
    return

  def get_auto_deploy(self):
    auto_deploy = self.cfg_auto_deploy
    if 'BOX_ID' not in auto_deploy or "NODE_ADDRESS" not in auto_deploy:
      auto_deploy['BOX_ID'] = auto_deploy.get('BOX_ID', self.ee_id)
      auto_deploy['NODE_ADDRESS'] = auto_deploy.get('NODE_ADDRESS', self.node_addr)
    if 'STREAMS' not in auto_deploy:
      auto_deploy['STREAMS'] = []
    return auto_deploy

  def get_data(self):
    data_cfg = {
      **DEFAULT_DATA_CONFIG,
      **self.cfg_data
    }
    return data_cfg

  def get_training(self):
    training_cfg = {
      **DEFAULT_TRAIN_CONFIG,
      **self.cfg_training
    }
    return training_cfg

  def classes_dict(self):
    """
    The `CLASSES` parameter should be a dictionary with the following structure:
    {
      "class_1": "Class 1 description",
      "class_2": "Class 2 description",
      ...
    }
    Returns
    -------
    dict : The classes dictionary
    """
    return self.cfg_classes

  def classes_list(self):
    """
    Utility function to return the classes as a list
    Returns
    -------
    list : The classes list
    """
    return list(self.cfg_classes.keys())

  """DATA SECTION"""
  if True:
    @property
    def data_sources(self):
      return self.get_data().get('SOURCES', [])

    @property
    def data_crop_plugin_params(self):
      return self.get_data().get('CROP_PLUGIN_PARAMS', {})

    @property
    def data_cloud_path(self):
      return self.get_data().get('CLOUD_PATH', '')

    @property
    def data_train_size(self):
      return self.get_data().get('TRAIN_SIZE', 0.8)
  """END DATA SECTION"""

  """TRAINING SECTION"""
  if True:
    @property
    def training_box_id(self):
      return self.get_training().get('BOX_ID')

    @property
    def training_model_architecture(self):
      return self.get_training().get('MODEL_ARCHITECTURE')

    @property
    def training_device_load_data(self):
      return self.get_training().get('DEVICE_LOAD_DATA')

    @property
    def device_training(self):
      return self.get_training().get('DEVICE_TRAINING')

    @property
    def training_pipeline_signature(self):
      return self.get_training().get('TRAINING_PIPELINE_SIGNATURE')

    @property
    def training_grid_search(self):
      return self.get_training().get('GRID_SEARCH')

    @property
    def training_batch_size(self):
      return self.get_training().get('BATCH_SIZE')

    @property
    def training_epochs(self):
      return self.get_training().get('EPOCHS')
  """END TRAINING SECTION"""

  @property
  def dataset_object_name(self):
    return self.os_path.join(self.data_cloud_path, self.cfg_objective_name)

  """CONFIG PIPELINE SECTION"""
  if True:
    def process_data_gather_config(self, config):
      """
      Fill in the gaps in the data gather config
      """
      return {
        **config,
        'CAP_RESOLUTION': config.get('CAP_RESOLUTION', 1),
      }

    def _configured_metastream_collect_data(self):
      object_type = self.cfg_general_detector_object_type
      if not isinstance(object_type, list):
        object_type = [object_type]

      cfg_instance = {
        "INSTANCE_ID": self.get_instance_id(),
        "OBJECTIVE_NAME": self.cfg_objective_name,
        "DESCRIPTION": self.cfg_description,
        "CLOUD_PATH": self.data_cloud_path,
        "OBJECT_TYPE": object_type,
        'CLASSES': self.classes_dict(),
        'TRAIN_SIZE': self.data_train_size,
        'REWARDS': self.cfg_rewards,
        'DATASET': self.cfg_dataset,
        'CREATION_DATE': self.cfg_creation_date,
        'DATA_SOURCES': self.data_sources,
        **self.data_crop_plugin_params,
      }

      current_ai_engine = self.cfg_ai_engine
      if current_ai_engine is not None and len(current_ai_engine) > 0 and cfg_instance.get('AI_ENGINE', None) is None:
        cfg_instance['AI_ENGINE'] = current_ai_engine

      config = {
        "NAME": f"collect_data_{self.get_instance_id()}",
        "TYPE": "MetaStream",
        "COLLECTED_STREAMS": [x['NAME'] for x in self.data_sources],
        "PLUGINS": [
          {
            "SIGNATURE": "ai4e_crop_data",
            "INSTANCES": [cfg_instance]
          }
        ]
      }
      return config

    def _configured_training_pipeline(self):
      config = {
        "NAME": "training_{}".format(self.get_instance_id()),
        "TYPE": "minio_dataset",
        "STREAM_CONFIG_METADATA": {
          "DATASET_OBJECT_NAME": self.dataset_object_name,
        },
        "PLUGINS": [
          {
            "SIGNATURE": "second_stage_training_process",
            "INSTANCES": [
              {
                "INSTANCE_ID": self.get_instance_id(),
                'AI_ENGINE': f'th_training?{self.get_instance_id()}',
                'STARTUP_AI_ENGINE_PARAMS': {
                  'PIPELINE_SIGNATURE': self.training_pipeline_signature,
                  'PIPELINE_CONFIG': {
                    'CLASSES': self.classes_list(),
                    'MODEL_ARCHITECTURE': self.training_model_architecture,
                    'MODEL_NAME': self.get_instance_id(),
                    'PRELOAD_DATA': self.training_device_load_data is not None,
                    'DEVICE_LOAD_DATA': self.training_device_load_data,
                    'DEVICE_TRAINING': self.device_training,
                    'GRID_SEARCH': self.training_grid_search,
                    'BATCH_SIZE': self.training_batch_size,
                    'EPOCHS': self.training_epochs,
                    'FIRST_STAGE_TARGET_CLASS': self.cfg_general_detector_object_type,
                  },
                },
                'AUTO_DEPLOY': self.get_auto_deploy(),
                "DESCRIPTION": self.cfg_description,
                "OBJECTIVE_NAME": self.cfg_objective_name,
                "REWARDS": self.cfg_training_rewards
              }
            ]
          }
        ]
      }
      return config
  """END CONFIG PIPELINE SECTION"""

  def _process(self):
    if not self.__started_gather:
      self.P(f"Starting end to end training job {self.cfg_objective_name} with id {self.get_instance_id()}")
      for config in self.data_sources:
        processed_config = self.process_data_gather_config(config)
        self.P(f"Starting pipeline {config['NAME']} for data acquisition.")
        self.cmdapi_start_stream_by_config_on_current_box(config_stream=processed_config)
      # endfor data sources
      self.P(f"Starting metastream for data collection.")
      self.cmdapi_start_metastream_by_config_on_current_box(
        config_metastream=self._configured_metastream_collect_data()
      )
      self.__started_gather = True
      self.save_current_state()
    # endif not started gather
    if not self.__started_training and self.cfg_start_training:
      training_box_addr = self.net_mon.network_node_addr(self.training_box_id)
      self.P(f"Starting training pipeline for {self.cfg_objective_name} on {training_box_addr}")
      self._cmdapi_start_stream_by_config(
        config_stream=self._configured_training_pipeline(),
        node_address=training_box_addr
      )
      self.__started_training = True
      self.save_current_state()
    # endif not started training
    return

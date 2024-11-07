from naeural_core import DecentrAIObject
from naeural_core import Logger
import json
from naeural_core.serving.ai_engines.utils import (
  get_serving_process_given_ai_engine,
  get_ai_engine_given_serving_process,
  get_params_given_ai_engine
)

class MainLoopDataHandler(DecentrAIObject):
  """
  In order to understand the methods of this class please study below data inputs:

  ######################
  1.`self._dct_captures`
  ######################
  `self._dct_captures` is the data collected by CaptureThreadManager:
    {
      'simple_image_stream' : { ### th_y5l6s -> omni: lpr
        'STREAM_NAME' : 'simple_image_stream',
        'STREAM_METADATA' : {Dictionary with general stream metadata},
        'INPUTS' : [
          {
            'IMG' : np.ndarray,
            'STRUCT_DATA' : None,
            'INIT_DATA' : None,
            'TYPE' : 'IMG',
            'METADATA' : {Dictionary with current input metadata}
          },
        ]
      },

      'multi_modal_2_images_one_sensor_stream' : { ### th_y5l6s, anomaly_detection -> anomaly_detection, omni: pose, safety_gear, detection
        'STREAM_NAME' : 'multi_modal_2_images_one_sensor_stream',
        'STREAM_METADATA' : {Dictionary with general stream metadata},
        'INPUTS' : [
          {
            'IMG' : np.ndarray,
            'STRUCT_DATA' : None,
            'INIT_DATA' : None,
            'TYPE' : 'IMG',
            'METADATA' : {Dictionary with current input metadata}
          },

          {
            'IMG' : np.ndarray,
            'STRUCT_DATA' : None,
            'INIT_DATA' : None,
            'TYPE' : 'IMG',
            'METADATA' : {Dictionary with current input metadata}
          },

          {
            'IMG' : None,
            'STRUCT_DATA' : an_object,
            'INIT_DATA' : None,
            'TYPE' : 'STRUCT_DATA',
            'METADATA' : {Dictionary with current input metadata}
          },
        ]
       }
    }

    N.B. Each dictionary in 'INPUTS' is defined as below:
      {
        'IMG' : ..., # 'IMG' and 'STRUCT_DATA' are mutual exclusive (one of them should be None)
        'STRUCT_DATA' : ..., # 'IMG' and 'STRUCT_DATA' are mutual exclusive (one of them should be None)
        'INIT_DATA' : ..., # could be None if no initial data is provided
        'TYPE' : ..., # this value should be 'IMG' or 'STRUCT_DATA' in order to specify which field is completed
        'METADATA' : {Dictionary with current input metadata}
      }

  ###############################
  2.`self._dct_instances_details`
  ###############################
  `self._dct_instances_details` represents the instance details taken from PluginsManager
    (see PluginsManager -> property `dct_instances_details`)
    It is a mapping between each current plugin instance and their details. Each instance hash is created based on
    stream_name, signature and instance_id 
    (see  `instance_hash = self.log.hash_object((stream_name, signature, instance_id))` in PluginsManager)

  {
    'instance_hash_1' : (stream_id, signature, instance_config),
    'instance_hash_2' : (stream_id, signature, instance_config),
    ....
  }


  #############################################
  3. `self._dct_serving_processes_details`
  #############################################

  IMPORTANT:
    
    `self._dct_serving_processes_details` represents the serving processes details 
    taken from PluginsManager, i.e. the serving upstream config delivered from biz plugins
    via 'INFERENCE_AI_ENGINE_PARAMS' and used during inferences. This is different from the 
    serving startup upstream config delivered from 'STARTUP_AI_ENGINE_PARAMS'
  
    (see PluginsManager -> attribute `dct_serving_processes_details`)

  {
    'th_y5l6s' : {
      ('simple_image_stream', dict_upstream_yolo_model_params_1) : ['instance_hash_1', 'instance_hash_3'],
      ('multi_modal_2_images_one_sensor_stream', dict_upstream_yolo_model_params_1) : ['instance_hash_2'],
    },

    'anomaly_detection_model' : {
      ('multi_modal_2_images_one_sensor_stream', dict_upstream_anomaly_model_params_1) : ['instance_hash_2'],
    }
  }

  # {
  #   'y5_omni' : {
  #     ('simple_image_stream', dict_upstream_yolo_model_params_1) : ['instance_hash_1', 'instance_hash_3'],
  #     ('multi_modal_2_images_one_sensor_stream', dict_upstream_yolo_model_params_1) : ['instance_hash_2'],
  #   }
  # }


  """

  def __init__(self, log : Logger, owner, **kwargs):
    self._dct_models_stream_idx = None
    self.owner = owner

    self._dct_captures = None
    self._dct_instances_details = None
    self._dct_serving_processes_details = None

    self.dct_business_inputs = None
    super(MainLoopDataHandler, self).__init__(log=log, prefix_log='[DAGGM]', **kwargs)
    return

  def update(self,
             dct_captures,
             dct_instances_details,
             dct_serving_processes_details):
    """
    Updates the internal state of the manager.

    Parameters:
    ----------
    dct_captures:
      The data collected by CaptureThreadManager

    dct_instance_details:
      Instance details taken from PluginsManager

    dct_model_serving_processes_details:
      Model serving processes details taken from PluginsManager
    """

    self._dct_captures = dct_captures
    self._dct_instances_details = dct_instances_details
    self._dct_serving_processes_details = dct_serving_processes_details
    return

  def _get_stream_captured_data(self, stream_name):
    if stream_name not in self._dct_captures:
      return {}

    return {
      'STREAM_NAME'     : self._dct_captures[stream_name]['STREAM_NAME'],
      'STREAM_METADATA' : self._dct_captures[stream_name]['STREAM_METADATA'],
      'INPUTS'          : self._dct_captures[stream_name]['INPUTS'],
    }

  def append_captures(self):
    self.dct_business_inputs = {}

    for instance_hash, (stream_name, _, _) in self._dct_instances_details.items():
      self.dct_business_inputs[instance_hash] = self._get_stream_captured_data(stream_name)
    #endfor

    """
    At this point `self.dct_business_inputs` will be like this:
    
    {
      'instance_hash_1' : {
        'STREAM_NAME' : ...,
        'STREAM_METADATA' : ...,
        'INPUTS' : ...
      },
      
      'instance_hash_2' : {
        'STREAM_NAME' : ...,
        'STREAM_METADATA' : ...,
        'INPUTS' : ...
      },
      
      ...
    }
    """

    return

  def aggregate_for_inference(self):
    """
    This function is the main point where the input data is prepared for each individual
    serving process. It combines the following data:
      - input data from DCTs using `_get_stream_captured_data`
      - serving params pre-defined for inference time by AI_ENGINE using `get_params_given_ai_engine`
      - inference time params from biz plugin `INFERENCE_AI_ENGINE_PARAMS` param passed from
        `BizMgr.dct_serving_processes_details` via local `_dct_serving_processes_details` as JSON string
        that will be decoded into the actual params dict. 
        IMPORTANT: 
          while `STARTUP_AI_ENGINE_PARAMS` are passed directly into serving `config_data` of the serving 
          process, now the inference-time upstream config is given in input data and thus its extraction 
          must be handled by the serving process

    Returns
    -------
    dict with all the inputs.

    """

    self._dct_models_stream_idx = {}
    dct_servers_inputs = {}
    
    for ai_engine, dct in self._dct_serving_processes_details.items():
      # TODO:
      #   AI_ENGINE handling should be done in ServingManager
      serving_process = get_serving_process_given_ai_engine(ai_engine)
      # next we get params from AI ENGINE config
      ai_engine_params = get_params_given_ai_engine(ai_engine)
      dct_servers_inputs[serving_process] = []
      self._dct_models_stream_idx[serving_process] = {}
      i = 0
      for _, (stream, biz_plugin_model_params_json) in enumerate(list(dct.keys())):
        server_input = self._get_stream_captured_data(stream)
        ### make sure that data was collected on this particular stream
        if bool(server_input) or self.owner.serving_manager.server_runs_on_empty_input(serving_process): 
          # order is important - biz plugin should overwrite AI_ENGINE params
          server_input['SERVING_PARAMS'] = {**ai_engine_params, **json.loads(biz_plugin_model_params_json)}
          dct_servers_inputs[serving_process].append(server_input)
          self._dct_models_stream_idx[serving_process][i] = (stream, biz_plugin_model_params_json, ai_engine_params)
          i += 1
        #endif
      #endfor
    #endfor

    """
    At this point, `dct_servers_inputs` will be like this:
    
    {
      'th_y5l6s' : [
        {'STREAM_NAME' : 'simple_image_stream', 'STREAM_METADATA' : ..., 'INPUTS' : ..., 'SERVING_PARAMS' : dict_upstream_yolo_model_params_1},
        {'STREAM_NAME' : 'multi_modal_2_images_one_sensor_stream', 'STREAM_METADATA' : ..., 'INPUTS' : ..., 'SERVING_PARAMS' : dict_upstream_yolo_model_params_1}
      ],
      
      'anomaly_detection_model' : [
        {'STREAM_NAME' : 'multi_modal_2_images_one_sensor_stream', 'STREAM_METADATA' : ..., 'INPUTS' : ..., 'SERVING_PARAMS' : dict_upstream_anomaly_model_params_1}
      ]
    }    
    """

    return dct_servers_inputs ## this object will be input for ServingManager's `predict_parallel`

  def append_inferences(self, dct_models_outputs):
    """
    
    TODO: this does not correctly support multiple instances of the same serving process
    
    Parameters:
    ----------
    dct_model_outputs:
      The response from ServingManager's `predict_parallel` (see ModelServingProcess's `pack_results`)
    """

    """
    `dct_models_outputs` :
    {
      'th_y5l6s' : {
        'INFERENCES_META' : {'SYSTEM_TYME' : ..., 'VER' : ..., 'PICKED_INPUT' : ...},
        'INFERENCES' : [ ### 'INFERENCES' will have the same length as the input for 'th_y5l6s'  - below example model input = 3 imgs
          [
            [{'TLBR_POS' : ...}, {'TLBR_POS' : ...}]
          ],
          [ ### this list has two elements because on 'multi_modal_2_images_one_sensor_stream', 'th_y5l6s' dealed with 2 images!!!
            [{'TLBR_POS' : ...}, {'TLBR_POS' : ...}, {'TLBR_POS' : ...}],
            [{'TLBR_POS' : ...}]
          ]
        ]
      },
      
      'anomaly_detection_model' : {
        'INFERENCES_META' : {'SYSTEM_TYME' : ..., 'VER' : ..., 'PICKED_INPUT' : ...},
        'INFERENCES' : [ ### 'INFERENCES' will have the same length as the input for 'anomaly_detection_model' 
          [
            'True/False' ## the output of the 'anomaly_detection_model'
          ]
        ]
      }
      
    }    
    """

    for serving_process, dct_output in dct_models_outputs.items():
      if dct_output is None:
        continue
      for i, model_inference in enumerate(dct_output['INFERENCES']):
        stream, biz_plugin_model_params_json, ai_engine_params = self._dct_models_stream_idx[serving_process][i]
        ai_engine = get_ai_engine_given_serving_process(
          serving_process=serving_process, 
          params=ai_engine_params
        )
        key = (stream, biz_plugin_model_params_json)
        suited_instances_for_crt_inference = self._dct_serving_processes_details[ai_engine][key]
        for instance in suited_instances_for_crt_inference:
          if 'SERVING_PARAMS' not in self.dct_business_inputs[instance]:
            self.dct_business_inputs[instance]['SERVING_PARAMS'] = {}
          if 'INFERENCES' not in self.dct_business_inputs[instance]:
            self.dct_business_inputs[instance]['INFERENCES'] = {}
          if 'INFERENCES_META' not in self.dct_business_inputs[instance]:
            self.dct_business_inputs[instance]['INFERENCES_META'] = {}

          self.dct_business_inputs[instance]['SERVING_PARAMS'][ai_engine] = json.loads(biz_plugin_model_params_json)
          self.dct_business_inputs[instance]['INFERENCES'][ai_engine] = model_inference
          self.dct_business_inputs[instance]['INFERENCES_META'][ai_engine] = dct_output['INFERENCES_META']
        # endfor - each business plugin that should receive the input from the current inference
      # endfor - each inference for current model
    # endfor - each model with its inferences

    """
    At this point `self.dct_business_inputs` will be like this
    (will have completed the 'INFERENCES' only for the instances that do not have configured 'MODEL_SERVING_PROCESS' as None):

    {
      'instance_hash_1' : {
        'STREAM_NAME' : ...,
        'STREAM_METADATA' : ...,
        'INPUTS' : ...,
        'INFERENCES' : {
          'th_y5l6s' : [[{'TLBR_POS' : ...}, {'TLBR_POS' : ...}]],
        },
        'INFERENCES_META' : {
          'th_y5l6s' : {'SYSTEM_TYME' : ..., 'VER' : ..., 'PICKED_INPUT' : ...}
        }
      },

      'instance_hash_2' : {
        'STREAM_NAME' : ...,
        'STREAM_METADATA' : ...,
        'INPUTS' : ...,
        'INFERENCES : {
          'th_y5l6s' : [
            [{'TLBR_POS' : ...}, {'TLBR_POS' : ...}, {'TLBR_POS' : ...}],
            [{'TLBR_POS' : ...}]
          ],
          
          'anomaly_detection_model' : [
            'True/False'
          ]
        },
        'INFERENCES_META' : {
          'th_y5l6s' : {'SYSTEM_TYME' : ..., 'VER' : ..., 'PICKED_INPUT' : ...},
          'anomaly_detection_model' : {'SYSTEM_TYME' : ..., 'VER' : ..., 'PICKED_INPUT' : ...}
        }
      },
      
      'instance_hash_3' : {
        'STREAM_NAME' : ...,
        'STREAM_METADATA' : ...,
        'INPUTS' : ...,
        'INFERENCES : {
          'th_y5l6s' : [[{'TLBR_POS' : ...}, {'TLBR_POS' : ...}]],
        },
        'INFERENCES_META' : {
          'th_y5l6s' : {'SYSTEM_TYME' : ..., 'VER' : ..., 'PICKED_INPUT' : ...}
        }
      }
    }
    """
    return

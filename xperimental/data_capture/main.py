#global dependencies
import itertools

#local dependencies
from naeural_core import Logger

CONFIG = {
    "CAP_RESOLUTION": 50,
    "INITIATOR_ID": "1111",
    "LIVE_FEED": False,
    "NAME": None,
    "PLUGINS": [
        {
            "INSTANCES": [
                {
                    "AI_ENGINE": [
                        "general_detector"
                    ],
                    "COORDS": "NONE",
                    "INSTANCE_ID": "OBJ_DET0",
                    "OBJECT_TYPE": [
                        "person"
                    ]
                }
            ],
            "SIGNATURE": "OBJECT_DETECTOR_TRACKER_01"
        }
    ],
    "RECONNECTABLE": False,
    "STREAM_CONFIG_METADATA": {
        "NOTIFY_DOWNLOAD_DOWNSTREAM": True,
        "C_ARRAY": True
    },
    "STREAM_WINDOW": 1,
    "TYPE": "VideoFile",
    "URL": "https://www.dropbox.com/s/mcp9dk82jdcr6t9/Camera%20bariere%20acces%20Kaufland%201.mp4?dl=1"
}

if __name__ == '__main__':
  log = Logger(
    lib_name='SBTST',
    base_folder='.',
    app_folder='_local_cache',
    config_file='config_startup.txt',
    max_lines=1000, 
    TF_KERAS=False
  )
  
  AI_ENGINE = ['general_detector', 'advanced_general_detector']
  STREAM_WINDOW = [1, 32]
  C_ARRAY = [True, False]
  
  for comb in itertools.product(AI_ENGINE, STREAM_WINDOW, C_ARRAY):
    ai_eng, s_win, c_arr = comb
    
    cfg = CONFIG.copy()
    name = '_'.join([str(x) for x in comb])
    cfg['NAME'] = name
    cfg['PLUGINS'][0]['INSTANCES'][0]['AI_ENGINE'] = ai_eng
    cfg['STREAM_CONFIG_METADATA']['C_ARRAY'] = c_arr
    cfg['STREAM_WINDOW'] = s_win
    
    log.save_json(cfg, 'xperimental/data_capture/{}.txt'.format(name))
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
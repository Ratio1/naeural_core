"""
{
    "NAME": "UsbCameraMeta",
    "DESCRIPTION": "Test for croppers and other stuff",
    "TYPE": "MetaStream",
    "COLLECTED_STREAMS" : ["UsbCamera"],


    "PLUGINS": [
        {
            "INSTANCES": [
                {
                    "INSTANCE_ID": "WITNES_ON_META",
                    "PROCESS_DELAY": 1
                }
            ],
            "SIGNATURE": "VIEW_SCENE_01"
        }
    ]
}  

"""

from naeural_core.data.base import BaseDataCapture

_CONFIG = {
  **BaseDataCapture.CONFIG,
  'IS_META_STREAM'       : True,
  
  # Parameter that is used to tell the metastream if it should have data from all the collected streams ('IS_LOOSE' = False)
  # or if it can send the aggregated data whenever any collected stream has something ('IS_LOOSE' = True)
  'IS_LOOSE'          : True,
  
  'IS_THREAD'         : False,
  'COLLECTED_STREAMS' : [],
  
  
  'VALIDATION_RULES' : {
    **BaseDataCapture.CONFIG['VALIDATION_RULES'],
    
    'COLLECTED_STREAMS' : {
      'TYPE'    : 'list',
      'MIN_LEN' : 1,
    }
  },
}

class MetaStreamDataCapture(BaseDataCapture):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(MetaStreamDataCapture, self).__init__(**kwargs)
    return
  
  
  def post_process_inputs(self, lst_stream_inputs):
    return lst_stream_inputs
  



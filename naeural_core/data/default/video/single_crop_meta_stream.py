"""
{
    "NAME": "CroppedUsbCameraMeta",
    "DESCRIPTION": "Test for croppers and other stuff",
    "TYPE": "SingleCropMetaStream",
    
    "COLLECTED_STREAMS" : ["UsbCamera"],
    
    "CROP_TOP"         : 200,
    "CROP_LEFT"        : 300,
    "CROP_BOTTOM"      : 400,
    "CROP_RIGHT"       : 639,    


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

from naeural_core.data.default.meta_stream import MetaStreamDataCapture as BaseClass

_CONFIG = {
  **BaseClass.CONFIG,  
  
  'CROP_TOP'         : 0,
  'CROP_LEFT'        : 0,
  'CROP_BOTTOM'      : 100,
  'CROP_RIGHT'       : 100,
  
  'VALIDATION_RULES' : {
    **BaseClass.CONFIG['VALIDATION_RULES'],
    
  },
}

class SingleCropMetaStreamDataCapture(BaseClass):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(SingleCropMetaStreamDataCapture, self).__init__(**kwargs)
    return
  
  def validate_coords(self):
    delta = 50
    if (self.cfg_crop_top + delta) >= self.cfg_crop_bottom:
      self.add_error("CROP_TOP:{} BIGGER THAN CROP_BOTTOM:{}+{}".format(
        self.cfg_crop_top, self.cfg_crop_bottom, delta)
      )            
    if (self.cfg_crop_left + delta) >= self.cfg_crop_right:
      self.add_error("CROP_LEFT:{} BIGGER THAN CROP_RIGHT:{}+{}".format(
        self.cfg_crop_left, self.cfg_crop_right, delta)
      )
    return
  
  
  def post_process_inputs(self, lst_stream_inputs):
    if len(lst_stream_inputs) > 0:
      # work on just one image
      img_orig = lst_stream_inputs[0]['IMG']
      _t = self.cfg_crop_top
      _l = self.cfg_crop_left
      _r = self.cfg_crop_right
      _b = self.cfg_crop_bottom
      if _b > img_orig.shape[0] or _r > img_orig.shape[1]:
        raise ValueError("The proposed crop bottom/right coords of {} are outside of the given video stream resolution of {}".format(
          (_b, _r), img_orig.shape)
        )
      img_crop = img_orig[_t:_b, _l:_r]
      lst_stream_inputs[0]['IMG'] = img_crop
      lst_stream_inputs[0]['METADATA']['original_image'] = img_orig
      lst_stream_inputs[0]['METADATA']['offset_h'] = _t
      lst_stream_inputs[0]['METADATA']['offset_w'] = _l
      
    # end if min 1 image
    return lst_stream_inputs
      
    
  



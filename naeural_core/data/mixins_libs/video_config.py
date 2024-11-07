from naeural_core import constants as ct
import cv2

class _VideoConfigMixin(object):

  def __init__(self):
    self._resize_h, self._resize_w = None, None
    super(_VideoConfigMixin, self).__init__()
    return

  def _force_video_stream_config(self):
    if not self.has_cap_resolution_config:
      str_err = "Cannot start {} due to missing capture resolution configuration!".format(self.__class__.__name__)
      self.P(str_err, color='error')
      raise ValueError(str_err)
    
  
  def _maybe_configure_frame_crop_resize(self, height, width):
    ### universal video stream code
    # if we need live resize
    resize_h = self.cfg_stream_config_metadata.get(ct.RESIZE_H, None)
    resize_w = self.cfg_stream_config_metadata.get(ct.RESIZE_W, None)
    if resize_h is not None and resize_h > 0:
      ratio = height / width
      resize_w = resize_h / ratio if resize_w is None else resize_w
      self._resize_h = int(resize_h)
      self._resize_w = int(resize_w)
      self.P("Live resize enabled from {} to {}".format(
        (height, width), (self._resize_h, self._resize_w)
        ))
      self._metadata.resize_hw = self._resize_h, self._resize_w
    # end if we need live resize
    
    # if we need live crop / reframe 
    self._frame_crop = None
    frame_crop = self.cfg_stream_config_metadata.get(
      ct.FRAME_CROP, 
      self.cfg_stream_config_metadata.get(
        ct.CAPTURE_CROP,
        None,
      )
    )
    if frame_crop is not None:
      if self._resize_h is None:          
        # test if not valid crop to resume normal frame
        if (not isinstance(frame_crop, list) 
            or len(frame_crop) != 4 
            or frame_crop[0] > frame_crop[2]
            or frame_crop[1] > frame_crop[3]
            ):
          self.P(" {}: {} invalid. Resuming capture to normal source stream resolution.".format(
            ct.FRAME_CROP, frame_crop))
        else:
          self._frame_crop = frame_crop
          height = self._frame_crop[2] - self._frame_crop[0]
          width = self._frame_crop[3] - self._frame_crop[1]
          self.P("  Enabling cropping from {} to {} (TLBR:{})".format(
            (self._metadata.frame_h, self._metadata.frame_w), (height, width),
            frame_crop,
          ), color='y')
          self._metadata.frame_h = height
          self._metadata.frame_w = width  
      # end frame cropping
    ### end universal video stream code    
    return
  
  def _maybe_resize_crop(self, img):
    if self._resize_h is not None:
      # if RESIZE_H is enabled in STREAM_CONFIG_METADATA then resize live
      self.start_timer('cv2_resize')
      img = cv2.resize(img, dsize=(self._resize_w, self._resize_h))
      self.end_timer('cv2_resize')
    elif self._frame_crop is not None:
      self.start_timer('cv2_crop')
      _top, _left, _bottom, _right = self._frame_crop
      img = img[_top:_bottom, _left:_right]  
      self.end_timer('cv2_crop')
    return img

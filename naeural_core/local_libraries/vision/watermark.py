import numpy as np
import cv2 


def apply_watermark_from_file(np_bgr, fn_watermark):
  H, W = np_bgr.shape[:2]
  np_watermark = prepare_watermark(
    fn_watermark=fn_watermark,
    target_shape=(H,W)
  )
  return apply_watermark(np_bgr, np_watermark)

def apply_watermark(np_bgr, np_watermark):  
  if isinstance(np_watermark, np.ndarray):
    if np_watermark.shape[:2] != np_bgr.shape[:2]:
      H, W = np_bgr.shape[:2]
      wh, ww = np_watermark.shape[:2]
      nh = H // wh + 1
      nw = W // ww + 1
      w1 = np.tile(np_watermark, (nh, nw, 1))
      np_watermark = w1[:H, :W]
    np_bgr = cv2.addWeighted(np_bgr, 1, np_watermark, 0.3, 0)
  return np_bgr


def prepare_watermark(fn_watermark, target_shape_HW, text="<Execution Engine>"):
  small_watermark = None
  if fn_watermark is not None:
    small_watermark = cv2.imread(fn_watermark)
  H, W = target_shape_HW
  np_watermark = None
  if small_watermark is None:
    small_watermark = np.full((200, 340, 3), fill_value=0, dtype=np.uint8)
    s = text
    org = (15, 190)
    cv2.putText(
      img=small_watermark, 
      text=s, 
      org=org, 
      fontFace=cv2.FONT_HERSHEY_DUPLEX, 
      fontScale=1, 
      color=(255, 255, 255), 
      thickness=2,
    )
    M = cv2.getRotationMatrix2D(org, 30, 1)
    small_watermark = cv2.warpAffine(small_watermark, M, (small_watermark.shape[1], small_watermark.shape[0]))
  wh, ww = small_watermark.shape[:2]
  nh = H // wh + 1
  nw = W // ww + 1
  w1 = np.tile(small_watermark, (nh, nw, 1))
  np_watermark = w1[:H, :W]  
  return np_watermark

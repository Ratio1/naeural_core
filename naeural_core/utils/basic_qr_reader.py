import cv2

def read_qr(np_rgb_img, debug=False):
  USE_TRICK = False
  qr_det = cv2.QRCodeDetector()
  if USE_TRICK:
    # now we perform a trick - convert to BGR then convert from ... RGB to GRAY
    np_bgr_img = np_rgb_img[:,:,::-1] 
    np_gray_img = cv2.cvtColor(np_bgr_img, cv2.COLOR_RGB2GRAY)
  else:
    np_gray_img = np_rgb_img.mean(axis=-1, keepdims=True).astype('uint8')
  if debug:
    retval, points = qr_det.detect(np_gray_img)
    print("QR DEBUG: {}:'{}'".format(retval, points))
  data, bbox, qr_mat = qr_det.detectAndDecode(np_gray_img)
  top, left, bottom, right, np_slice = None, None, None, None, None
  if bbox is not None:  
    top = int(bbox[:,:,1].min())
    left = int(bbox[:,:,0].min())
    bottom = int(bbox[:,:,1].max())
    right = int(bbox[:,:,0].max())
    
    np_slice = np_gray_img[top:bottom, left:right].copy()
  
    return data, (top, left, bottom, right), bbox, qr_mat, np_slice
  return
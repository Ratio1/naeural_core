import cv2
import numpy as np
import tensorflow as tf

from tqdm import tqdm
from naeural_core import Logger

if __name__ == '__main__':
  cfg_file = 'main_config.txt'
  log = Logger(
    lib_name='XP_BMW', 
    config_file=cfg_file, 
    max_lines=1000, 
    TF_KERAS=False
    )

  TARGET_H = 1520
  TARGET_W = 2688

  img_bgr1 = cv2.imread(r'D:/vlcsnap-2021-09-01-10h01m50s069.png')
  img_bgr2 = cv2.imread(r'D:/vlcsnap-2021-09-01-10h01m50s069.png')
  
  img_rgb1 = img_bgr1[:,:,::-1]
  img_rgb2 = img_bgr2[:,:,::-1]
  
  np_imgs_bgr = np.array([img_bgr1, img_bgr2, img_bgr2])
  np_imgs_rgb = np.array([img_rgb1, img_rgb2, img_rgb2])
  # np_imgs_rgb = img_rgb1
  
  for _ in tqdm(range(20)):
    log.start_timer('log_center_image2')    
    l = []
    for img in np_imgs_bgr:
      img_center = log.center_image2(
        np_src=img, 
        target_h=TARGET_H, 
        target_w=TARGET_W
        )
      l.append(img_center)
    arr = np.array(l)
    tf_imgs = tf.constant(arr, dtype=np.uint8) + 0
    log.stop_timer('log_center_image2')    
    
    log.start_timer('tf.image.resize')
    tf_img_res = tf.image.resize(
      images=np_imgs_rgb, 
      size=(TARGET_H, TARGET_W), 
      preserve_aspect_ratio=True
      )
    # np_img_res = tf_img_res.numpy().astype(np.uint8)
    log.stop_timer('tf.image.resize')
    
    log.start_timer('tf.image.resize_with_pad')
    tf_img_res_pad = tf.image.resize_with_pad(
      image=np_imgs_rgb, 
      target_height=TARGET_H, 
      target_width=TARGET_W
      )
    # np_img_res_pad = tf_img_res_pad.numpy().astype(np.uint8)
    log.stop_timer('tf.image.resize_with_pad')

  if False:  
    cv2.imshow('Original', img_bgr)
    cv2.imshow('Log center_image2', img_center)
    cv2.imshow('tf.image.resize', tf_img_res.numpy()[:,:,::-1].astype(np.uint8))
    cv2.imshow('tf.image.resize_with_pad', tf_img_res_pad.numpy()[:,:,::-1].astype(np.uint8))
    cv2.imshow('img_rgb', img_rgb)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


  log.show_timers()
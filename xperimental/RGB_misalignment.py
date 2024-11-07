import os
import cv2
import re
import math
import shutil
import random
import numpy as np
import matplotlib.pyplot as plt
from naeural_core import Logger



if __name__ == '__main__':
  path = '/home/work/Pictures'
  l = Logger('YOLOBM', base_folder='.', app_folder='_local_cache')
  path_list = [
               # os.path.join(l.get_dropbox_drive(), '_vapor_data/_lpr_v2/_data/Rares_Efect/Good MULTI 10/Train'),
               # os.path.join(l.get_dropbox_drive(), '_vapor_data/_lpr_v2/_data/Rares_Efect/Good MULTI 10/Dev'),
               os.path.join(l.get_dropbox_drive(), '_vapor_data/_lpr_v2/_data/Rares_Efect/Good MULTI 10/FIX'),
               # os.path.join(l.get_dropbox_drive(), '_vapor_data/_lpr_v2/_data/Rares_Efect/Good MULTI 10/Test')
               ]
  for path in path_list:
    path_good = path + '/GOOD'
    path_edited = path + '/EDITED'
    shutil.rmtree(path_edited, ignore_errors=True)
    if not os.path.exists(path_edited):
      os.makedirs(path_edited)
    for img_name in os.listdir(path_good):
      img_path = path_good + '/' + img_name
      img_edited_path = path_edited + '/' + img_name
      img = cv2.imread(img_path)
      height, width = img.shape[:2]
      X_TRANSLATE = width / 100
      Y_TRANSLATE = height / 100
      # radius = random.uniform(width /100, width / 20)
      radius = width / 90
      sign = random.uniform(-1, 1)
      X_TRANSLATE = int(random.uniform(0, radius))
      if sign < 0:
        X_TRANSLATE = -X_TRANSLATE
        
      
      sign = random.uniform(-1, 1)
      Y_TRANSLATE = int(math.sqrt(math.pow(radius, 2) - math.pow(X_TRANSLATE, 2)))
      if sign < 0:
        Y_TRANSLATE = -Y_TRANSLATE
      img_R = img[:,:,2]
      img_G = img[:,:,1]
      img_B = img[:,:,0]
      Translate_G = np.float32([[1, 0, X_TRANSLATE], [0, 1, Y_TRANSLATE]])
      Translate_B = np.float32([[1, 0, 2*X_TRANSLATE], [0, 1, 2*Y_TRANSLATE]])
      img_translation_G = cv2.warpAffine(img_G, Translate_G, (width, height))
      img_translation_B = cv2.warpAffine(img_B, Translate_B, (width, height))
      final_img = cv2.merge((img_R, img_translation_G, img_translation_B))
    
      if X_TRANSLATE > 0:
        final_img = final_img[:,2 * X_TRANSLATE:,:]
      elif X_TRANSLATE < 0:
        final_img = final_img[:,:2 * X_TRANSLATE,:]
        
      if Y_TRANSLATE > 0:
        final_img = final_img[2 * Y_TRANSLATE:,:,:]
      elif Y_TRANSLATE < 0:
        final_img = final_img[:2 * Y_TRANSLATE,:,:]
        
      final_img = cv2.resize(final_img, (width, height))
        
      
      cv2.imwrite(img_edited_path, final_img)


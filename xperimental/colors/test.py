import numpy as np
import cv2
import seaborn as sns

from naeural_core import Logger
from decentra_vision.draw_utils import DrawUtils

if __name__ =='__main__':
  
  l = Logger('TST', base_folder='.', app_folder='_local_cache', TF_KERAS=False)
  
  painter = DrawUtils(log=l)
  
  cfg = l.load_data_json('config/config.txt')
  
  colors = cfg['CONFIG_PLUGINS']['HEATMAP_GRID_01']['HEAT_RANGE']
  
  img = np.zeros((1000,1000,3), dtype=np.uint8)
  
  for i, (start, stop, color) in enumerate(colors):
    img = painter.rectangle_tlbr(
      image=img, 
      top=i*100, 
      left=i*100, 
      bottom=(i+1)*100, 
      right=(i+1)*100,
      color=color,
      thickness=-1
      )
  cv2.imshow('Img', img)
  key = cv2.waitKey(0)
  cv2.close()

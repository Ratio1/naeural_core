import numpy as np
import cv2

from naeural_core import Logger

if __name__ == '__main__':
  
  l = Logger('CV2T', base_folder='.', app_folder='_cache')
  
  cap = cv2.VideoCapture(0)
  
  
  while(True):
    l.start_timer('cv2_read_ala_bala_portocala_long_name')
    ret, frame = cap.read() 
    l.stop_timer('cv2_read_ala_bala_portocala_long_name')
    if ret:
      cv2.imshow('frame', frame)
      
    if cv2.waitKey(1) & 0xFF == ord('q'):
      break

  rets = []
  rets2 = []
  fails = 0 
  while(True):
    for i in range(100):
      l.start_timer('cv2_grab')
      ret = cap.grab() 
      l.stop_timer('cv2_grab')
      rets.append(ret)
    
    if ret:
      l.start_timer('cv2_retrieve')
      ret2, frame = cap.retrieve()
      l.stop_timer('cv2_retrieve')
      rets2.append(ret2)
      if ret2:
        cv2.imshow('frame', frame)
    else:
      fails += 1
      
    if cv2.waitKey(1) & 0xFF == ord('q'):
      break

  cap.release()
  cv2.destroyAllWindows()
  
  l.show_timers()
  l.P("Fails: {}".format(fails))
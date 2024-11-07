import cv2 as cv
import numpy as np

if __name__ == '__main__':
  FPS = 12
  NR = 300
  path = 'C:/Users/ETA/Dropbox/DATA/_vapor_data/__sources/movies/lpr/SeeTec.avi'
  
  cap = cv.VideoCapture(path)
  fourcc = cv.VideoWriter_fourcc(*'MP4V')
  out = cv.VideoWriter('output.mp4', fourcc, FPS, (1280, 1024))
  crt = 0
  while cap.isOpened():    
    ret, frame = cap.read()
    crt += 1
    print(frame.shape)
    out.write(frame)
    cv.imshow('frame', frame)
    if cv.waitKey(1) == ord('q'):
      break
    
    if crt >= NR:
      break
  #endwhile
  cap.release()
  out.release()
  cv.destroyAllWindows()
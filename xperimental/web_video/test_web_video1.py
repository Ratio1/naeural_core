import cv2 

if __name__ == '__main__':
  
  cap = cv2.VideoCapture('https://live.zootargoviste.ro:7001')
  done = False
  frames = 0
  while not done:
    has_frame, frame = cap.read()
    if has_frame:
      cv2.imshow("zoo", frame)
      frames += 1

    if cv2.waitKey(1) & 0xFF == ord('q'):
      done = True
        
  cap.release()
  cv2.destroyAllWindows()
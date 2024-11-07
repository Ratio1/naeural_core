import cv2
vidcap = cv2.VideoCapture('/home/work/Downloads/people_walking.mp4')
success,image = vidcap.read()
count = 0
while success:
  if count % 10 == 0:
    cv2.imwrite("/home/work/Downloads/people_walking_frames/frame%s.jpg" % str(count).zfill(4), image)     # save frame as JPEG file      
    success,image = vidcap.read()
    print('Read a new frame: ', success)
  count += 1
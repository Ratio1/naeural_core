import cv2
import matplotlib.pyplot as plt

name = 'Camera1_10.144.35.235_POMPE + EXT_20191203091517_20191203091537_446794.jpg'
full_name = 'xperimental/' + name 
(W, H) = (640, 480)
# (W, H) = (720, 480)
# (W, H) = (1024, 768)
# (W, H) = (1280, 720)
# (W, H) = (1920, 1080)
# (W, H) = (3840, 2160)
img=cv2.imread(full_name)
print(img.shape)
# img = cv2.resize(img, (720, 480))
img = cv2.resize(img, (W, H))
print(img.shape)





cv2.imwrite('xperimental/Images/' + str(W) + 'x' + str(H) + '/' + str(name), img)
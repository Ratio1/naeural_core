import cv2
import time
from numba import jit
import matplotlib.pyplot as plt

@jit(nopython=False)
def resize_img(img):
  img = cv2.resize(img, (1920, 1080))
  return img

if __name__ == '__main__':
  img = cv2.imread('xperimental/covid2.jpg')
  plt.imshow(img)
  start = time.time()
  img = resize_img(img)
  print(time.time() - start)

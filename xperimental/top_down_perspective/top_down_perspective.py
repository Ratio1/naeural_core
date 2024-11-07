import cv2
import numpy as np
import matplotlib.pyplot as plt
import math

rotation_angle = -math.pi / 3
sh = -0.2
sv = -0.8

translation_mat = np.array([[1, 0, -1920/2], [0, 1, -1080/2], [0, 0, 1]])
rotation_mat = np.array([[math.cos(rotation_angle), math.sin(rotation_angle), 0], [-math.sin(rotation_angle), math.cos(rotation_angle), 0], [0, 0, 1]])
shear_h_mat = np.array([[1, sh, 0], [0, 1, 0], [0, 0, 1]])
shear_v_mat = np.array([[1, 0, 0], [sv, 1, 0], [0, 0, 1]])
translation_inv_mat = np.array([[1, 0, 1920/2], [0, 1, 1080/2], [0, 0, 1]])
tl = np.array([0,0,1])
tr = [1,-1,1]
bl = [-1, 1, 1]
br = [1,1, 1]

matrix = translation_inv_mat @ shear_v_mat @ shear_h_mat @ rotation_mat @ translation_mat

img = cv2.imread('/home/work/Pictures/vlcsnap-2021-07-02-15h29m06s352.png')
img_transformed = cv2.warpPerspective(img,matrix,(1920,1080))
plt.imshow(img_transformed[:, :, ::-1])  # Show results
plt.show()
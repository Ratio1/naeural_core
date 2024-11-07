import cv2
import numpy as np
import matplotlib.pyplot as plt

# targeted rectangle on original image which needs to be transformed
#vlcsnap-2021-07-02-13h26m43s659.png
tl = [533, 310]
tr = [674, 267]
bl = [792, 464]
br = [971, 393]

#vlcsnap-2021-07-02-15h29m06s352.png
# tl = [822, 220]
# tr = [928, 193]
# bl = [1144, 322]
# br = [1244, 282]

#'/home/work/Pictures/vlcsnap-2021-07-02-12h51m02s764.png'
# tl = [1017, 359]
# tr = [1112, 333]
# bl = [1204, 421]
# br = [1291, 391]


OX_center = int((tl[0] + tr[0] + bl[0] + br[0]) / 4)
OY_center = int((tl[1] + tr[1] + bl[1] + br[1]) / 4)

corner_points_array = np.float32([tl,tr,br,bl])

# original image dimensions
width = 1920
height = 1080
img = cv2.imread('/home/work/Pictures/vlcsnap-2021-07-02-15h29m06s352.png')
# img = cv2.imread('/home/work/Pictures/vheat.png')
# img = cv2.imread('/home/work/Pictures/vlcsnap-2021-07-02-15h29m06s352.png')
# print(np.shape(img))
img = cv2.resize(img,(1920,1080))

# Create an array with the parameters (the dimensions) required to build the matrix


width_AD = np.sqrt(((tl[0] - tr[0]) ** 2) + ((tl[1] - tr[1]) ** 2))
width_BC = np.sqrt(((bl[0] - br[0]) ** 2) + ((bl[1] - br[1]) ** 2))
maxWidth = max(int(width_AD), int(width_BC))


height_AB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
height_CD = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
maxHeight = max(int(height_AB), int(height_CD))



# imgTl = [OX_center + 0, OY_center + 0]
# imgTr = [OX_center + width, OY_center + 0]
# imgBr = [OX_center + width, OY_center + height]
# imgBl = [OX_center + 0, OY_center + height]

imgTl = [0, 0]
imgTr = [ width, 0]
imgBr = [width,  height]
imgBl = [ 0,  height]
img_params = np.float32([imgTl,imgTr,imgBr,imgBl])

# input_pts = np.float32([pt_A, pt_B, pt_C, pt_D])
output_pts = np.float32([[OX_center + 0, OY_center + 0],
                        [OX_center + maxWidth, OY_center + 0],
                        [OX_center + maxWidth, OY_center + maxHeight],
                        [OX_center + 0, OY_center + maxHeight]])

# Compute and return the transformation matrix
matrix = cv2.getPerspectiveTransform(corner_points_array,output_pts)

img_transformed = np.zeros((height, width, 3), np.uint8)
print(np.shape(img))
print(np.shape(img_transformed))
img_transformed[:] = (0, 0, 0)

# img_transformed = cv2.warpPerspective(img,matrix,(width, height), borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 0, 0))
# print(img_transformed)
count = 0
for i in range(width):
  for j in range(height):
    coord = [i, j, 1]
    new_coord = matrix@coord
    if int(new_coord[0] / new_coord[2]) >= 0 and int(new_coord[0] / new_coord[2]) < width and int(new_coord[1] / new_coord[2]) >= 0 and int(new_coord[1] / new_coord[2])< height:
      count += 1
      img_transformed[int(new_coord[1] / new_coord[2]), int(new_coord[0] / new_coord[2])] = img[j,i]





print(count)


plt.imshow(img_transformed[:, :, ::-1])  # Show results
# cv2.imwrite('/home/work/Pictures/BUC_top.png', img_transformed)
plt.show()
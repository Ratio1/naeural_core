import numpy as np
from shapely.geometry import Polygon
import matplotlib.pyplot as plt


def box_intersect(box, poly):
  if not isinstance(box[0], list):
    top    = box[0]
    left   = box[1]
    bottom = box[2]
    right  = box[3]
    box = [
      [top, left],
      [top, right],
      [bottom, right],
      [bottom, left],
      [top, left]
      ]
  elif len(box) == 4:
    box.append(box[0])
  box = np.array(box).astype(float)
  poly_shapely = Polygon(poly)
  box_shapely = Polygon(box)
  intersect = box_shapely.intersection(poly_shapely)
  
  return intersect.area / box_shapely.area * 100



if __name__ == '__main__':
  poly = np.array([[100,  100],
   [150,  80],
   [250, 150],
   [300, 110],
   [350, 200],
   [300, 200],
   [500, 220],
   [450, 270],
   [200, 250],
   [200, 270],
   [100, 250],
   [130, 200],
   [100, 100]]) * 3.5
  # box = [
    # [200, 400, 400, 600],
    # [600, 600, 800, 800],
    # [400, 1200, 600, 1400],
    # [200, 800, 600, 1000],
    # [400, 1100, 800, 1150]
    # ]
  # box = [200, 400, 400, 600]
  box = [[200, 400], [400, 400], [400, 600], [200, 600]]
  intersect = box_intersect(box, poly)
  print(intersect)


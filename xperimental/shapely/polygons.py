import cv2
import numpy as np

from decentra_vision.draw_utils import DrawUtils
from shapely.geometry import Polygon, LineString
from naeural_core import Logger

if __name__ == '__main__':
  cfg_file = 'main_config.txt'
  log = Logger(lib_name='VB', config_file=cfg_file, max_lines=1000)
  painter = DrawUtils(log=log)

  box_tlbr = [300, 600, 360, 640]
  top, left, bottom, right = box_tlbr
  box_points = [
    [left, top],
    [right, top],
    [right, bottom],
    [left, bottom],
    [left, top],
  ]

  line_points = [
      [187, 50],
      [195, 74],
      [204, 100],
      [220, 123],
      [237, 140],
      [252, 161],
      [274, 176],
      [276, 190],
      [303, 205],
      [320, 223],
      [342, 232],
      [378, 254],
      [408, 265],
      [439, 279],
      [476, 292],
      [515, 305],
      [538, 312],
      [568, 320],
      [591, 322],
      [619, 329],
      [658, 336],
      [691, 342],
      [751, 345],
      [776, 345],
      [802, 344],
      [830, 344],
      [857, 345],
      [884, 342],
      [917, 339],
      [944, 328],
      [969, 319],
      [990, 302],
      [1000, 273],
      [1003, 249],
      [1012, 222],
      [1035, 211],
      [1064, 200],
      [1089, 201]]

  # box = Polygon(box_points)
  # line = LineString(line_points)
  # target = Polygon(target_points)
  # pts_inter = list(box.intersection(target).exterior.coords)
  # pts_inter = np.array(pts_inter).astype(np.int32).tolist()
  # left, top, right, bottom = box.intersection(target).bounds
  img = np.ones((1080, 1920, 3), dtype=np.uint8) * 255

  img = painter.polygon(
    image=img,
    pts=box_points,
    color=(255, 0, 0)
  )
  # img = painter.polygon(
  #   image=img,
  #   pts=target_points,
  #   color=(0, 255, 0)
  #   )
  # img = painter.polygon(
  #   image=img,
  #   pts=pts_inter,
  #   color=(255, 255, 0)
  #   )
  img = painter.polygon(
    image=img,
    pts=line_points,
    color=(255, 255, 0)
  )

  cv2.imshow('test', img)
  cv2.waitKey(0)
  cv2.destroyAllWindows()

  lst_road = []
  dct_points = {(p[0], p[1]): False for p in line_points}

  line = LineString(line_points)
  box_poly = Polygon(box_points)
  intersect = box_poly.intersection(line)
  diff = box_poly.difference(line)

  if box_poly.intersects(line):
    # blur/noblur....

    # vanish line
    for line_point in line_points:
      line_point_x, line_point_y = line_point
      in_box = False
      if line_point_x >= left and \
              line_point_x <= right and \
              line_point_y >= top and \
              line_point_y <= bottom:
        in_box = True
        dct_points[(line_point_x, line_point_y)] = True
  else:
    print('box does not intersect line')

  print(line_points)

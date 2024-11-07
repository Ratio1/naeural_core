import cv2

from naeural_core import Logger
from decentra_vision.draw_utils import DrawUtils

if __name__ == '__main__':
  cfg_file = 'main_config.txt'
  log = Logger(
    lib_name='SB',
    config_file=cfg_file,
    max_lines=1000,
    TF_KERAS=False
  )

  painter = DrawUtils(log=log)
  img = cv2.imread('xperimental/covid1.jpg')
  img = painter.cross_from_tlbr(
    image=img,
    tlbr=[50, 50, 100, 100]
  )
  img = painter.cross(
    image=img,
    point=[150, 150],
    color=(255, 0, 0),
    thickness=2
  )
  painter.show('test', img)
  cv2.waitKey(0)
  cv2.destroyAllWindows()

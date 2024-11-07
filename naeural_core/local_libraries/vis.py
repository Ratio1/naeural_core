import os
import math
import numpy as np
import matplotlib.pyplot as plt
from tqdm import tqdm
from PIL import Image
from enum import Enum
from abc import abstractmethod
from naeural_client import BaseDecentrAIObject


__version__ = '1.0.1.1'
__VER__ = __version__


class VisEngineEnum(Enum):
  CV2 = 0
  PIL = 1

class RGBColorEnum(Enum):
  DEFAULT_BLACK     = (0, 0, 0)
  DEFAULT_WHITE     = (255, 255, 255)
  DEFAULT_RED       = (255, 0, 0)
  DEFAULT_GREEN     = (0, 255, 0)
  DEFAULT_BLUE      = (0, 0, 255)
  
  DARK_BLUE    = (11, 45, 109)
  LIGHT_BLUE   = (129, 255, 254)
  ORANGE       = (250, 149, 19)
  YELLOW       = (250, 222, 98)
  RED          = (248, 70, 100)
  GREEN        = (50, 200, 50)

IMG_EXT = ['.png', '.bmp', '.jpg', '.jpeg']

class BaseVisualization(BaseDecentrAIObject):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    return
  
  @abstractmethod
  def display(self, img, window_name):
    raise NotImplementedError()
  
  @abstractmethod
  def close_display(self):
    raise NotImplementedError()
  
  @abstractmethod  
  def draw_rect(self, img, top, left, bottom, right, color, thickness):
    raise NotImplementedError()
  
  @abstractmethod
  def draw_box(self, img, top, left, bottom, right, display_str, color, thickness):
    raise NotImplementedError()
  
  def draw_boxes(self, img, lst_boxes, lst_display_str, lst_color, thickness):
    raise NotImplementedError()
    
  @abstractmethod
  def draw_text(self, img, text, left, top, color, thickness, font_scale):
    raise NotImplementedError() 
  
  @abstractmethod
  def read_image(self, path):
    raise NotImplementedError()
    
  @abstractmethod
  def save_image(self, path, img):
    raise NotImplementedError()
    
  def resize_image(self, height, width, interpolation):
    raise NotImplementedError()
  
  def is_image(self, path):
    return any(path.endswith(x) for x in IMG_EXT)
  
  def read_images(self, fn):
    if isinstance(fn, str) and os.path.isdir(fn):
      lst_files = [x for x in os.listdir(fn) if self.is_image(x)]
      lst_paths = [os.path.join(fn, x) for x in lst_files]
    elif isinstance(fn, str):
      if os.path.exists(fn) and self.is_image(fn):
        lst_paths = [fn]
      else:
        raise ValueError('File named incorrect: {}'.format(fn))
    elif isinstance(fn, list):
      lst_paths = [x for x in fn if self.is_image(x)]
    else:
      raise ValueError('Value not understood: {}'.format(fn))
    lst_bgr = [self.read_image(x) for x in tqdm(lst_paths)]
    lst_rgb = [self.reverse_channels(x) for x in lst_bgr]
    return lst_paths, lst_bgr, lst_rgb

  def save_images(self, lst_imgs, reverse_channels, path=None):
    self.log.p('Saving images')
    if path is None:
      path = self.log.now_str()
    os.makedirs(path, exist_ok=True)
    for idx, img in tqdm(enumerate(lst_imgs)):
      if reverse_channels:
        img = self.reverse_channels(img)
      self.save_image(os.path.join(path, '{}.png'.format(idx)), img)
    self.log.p('Done saving images', show_time=True)
    return

  def show(self, img, title=None, xlabel=None, ylabel=None):
    plt.imshow(img)
    if title:
      plt.title(title)
    if xlabel:
      plt.xlabel(xlabel)
    if ylabel:
      plt.ylabel(ylabel)
    plt.show()
    return

  def side_by_side(self, lst_imgs, lst_titles=None, lst_xlabel=None, lst_ylabel=None, title=None):
    for img in lst_imgs:
      if img.max() <= 1: assert img.dtype in ['float32', 'float64']
      if img.max() > 1 : assert img.dtype == 'uint8'
    
    if lst_titles:
      assert len(lst_imgs) == len(lst_titles)
    if lst_xlabel:
      assert len(lst_imgs) == len(lst_xlabel)
    if lst_ylabel:
      assert len(lst_imgs) == len(lst_ylabel)
    
    nr = len(lst_imgs)
    for idx, img in enumerate(lst_imgs):
      plt.subplot(1, nr, idx+1)
      if lst_titles: plt.title(lst_titles[idx])
      if lst_xlabel: plt.xlabel(lst_xlabel[idx])
      if lst_ylabel: plt.ylabel(lst_ylabel[idx])
      plt.imshow(img)
    if title:
      plt.title(title)
    plt.show()
    return
  
  def display_images(self, imgs, n_cols, 
                     titles=None, nr_max=20, 
                     axis_off=False, title=None,
                     figsize=(13,8)):
    if titles:
      assert len(imgs) == len(titles)
    
    if nr_max is not None:
      imgs = imgs[:nr_max]    
    n_rows = int(math.ceil(len(imgs) / n_cols))      
    crt_img = 0
    fig, axs = plt.subplots(n_rows, n_cols, figsize=figsize)
    for row in range(n_rows):
      for col in range(n_cols):
        idx = row * n_cols + col
        if idx >= len(imgs):
          img = np.zeros(shape=(10, 10, 3), dtype=np.uint8)
        else:
          img = imgs[idx]
        if len(axs.shape) == 1:
          axs[col].imshow(img)
        else:
          axs[row, col].imshow(img)
        if titles:
          lbl = ''
          if idx < len(titles):
            lbl = titles[idx]
          if len(axs.shape) == 1:
            axs[col].set_title(lbl)
          else:
            axs[row, col].set_title(lbl)
        if axis_off:
          axs[row, col].axis('off')
        crt_img+= 1
        if crt_img >= (n_rows * n_cols):
          break
      #endfor
      if crt_img >= (n_rows * n_cols):
        break
    if title:
      fig.suptitle(title)
    plt.show()    
    return
  
  def display_in_batches(self, imgs, n_cols, batch_size=16, titles=None, title=None):
    if titles:
      assert len(imgs) == len(titles)
    
    nr_batches = int(math.ceil(len(imgs) / batch_size))
    for nr in tqdm(range(nr_batches)):
      start = batch_size * nr
      stop = start + batch_size if nr < nr_batches - 1 else len(imgs)
      x_batch = imgs[start:stop]
      if titles:
        y_batch = titles[start:stop]
      else:
        y_batch = None
      plot_title = '{} batch {}'.format(title, nr + 1) if title else 'Batch {}'.format(nr + 1)
      self.display_images(x_batch, n_cols=n_cols, titles=y_batch, title=plot_title)
    return
  
  def reverse_channels(self, img):
    img = img[:, :, ::-1]
    return img
      
  def transform_image(self, img, normalize, reverse_channels):
    if normalize:
      img/= 255
    if reverse_channels:
      img = self.reverse_channels(img)
    return img  
  
  def reverse_color(self, color):
    return color[::-1]
  
  def is_image(self, img_path):
    return any([img_path.endswith(x) for x in IMG_EXT])
  
  def plot_distribution(self, classes):
    assert isinstance(classes, (list, np.ndarray))
    np_unique, np_counts = np.unique(classes, return_counts=True)
    plt.bar(np.arange(len(np_unique)), np_counts)
    plt.xticks(np_unique)
    plt.title('Class distribution')
    plt.xlabel('Class')
    plt.ylabel('Nr occurance')
    plt.show()
    return
  
  
  
class VisualizationCV2(BaseVisualization):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    import cv2
    self.__cv2 = cv2
    self.font = kwargs.get('font', self.__cv2.FONT_HERSHEY_COMPLEX)
    return
  
  def close_display(self):
    self.__cv2.waitKey(1)
    for i in range(5):
      self.__cv2.waitKey(1)
      self.__cv2.destroyAllWindows()
    return
  
  def show(self, img, window_name='Debug'):
    if isinstance(img, str):
      img = self.read_image(img)
    self.__cv2.imshow(window_name, img)
    return
  
  def display(self, img, window_name='Debug'):
    self.show(img, window_name)
    self.__cv2.waitKey(0)
    self.close_display()
    return
  
  def read_image(self, path):
    assert os.path.exists(path)
    return self.__cv2.imread(path)
  
  def save_image(self, path, img):
    self.__cv2.imwrite(path, img)
    return
  
  def resize_image(self, img, height, width, interpolation=None):
    if interpolation is None:
      interpolation = self.__cv2.INTER_LINEAR
    dim = (width, height)
    img_res = self.__cv2.resize(img, dim, interpolation=interpolation)
    return img_res
  
  def draw_text(self, img, text, left, top, color=RGBColorEnum.DEFAULT_BLACK.value, thickness=1, font_scale=1):
    self.__cv2.putText(img=img, text=text, org=(left, top), color=color, thickness=thickness, 
                fontScale=font_scale, fontFace=self.font)
    return
  
  def draw_rect(self, img, top, left, bottom, right, color=RGBColorEnum.DEFAULT_GREEN.value, thickness=2):
    self.__cv2.rectangle(img=img, pt1=(left, top), pt2=(right, bottom), color=color, thickness=thickness)
    return
  
  def draw_box(self, img, top, left, bottom, right, display_str='', color=RGBColorEnum.DEFAULT_GREEN.value, thickness=2):
    #bounding box
    self.draw_rect(img=img, top=top, left=left, bottom=bottom, right=right, color=color, thickness=thickness)
    
    if display_str != '':
      font_scale = 0.6
      font_thickness = 1
      (tw, th) = self.__cv2.getTextSize(text=display_str, fontFace=self.font, fontScale=font_scale, thickness=font_thickness)[0]
      top_desc = top - th
      bottom_desc = top
      left_desc = left
      right_desc = left_desc + tw
      self.draw_rect(img=img, top=top_desc, left=left_desc, bottom=bottom_desc, right=right_desc, color=color, thickness=self.__cv2.FILLED)
      self.draw_text(img=img, text=display_str, left=left_desc, top=bottom_desc, color=RGBColorEnum.DEFAULT_BLACK.value,
                     thickness=font_thickness, font_scale=font_scale)
    #endif
    return
  
  def draw_boxes(self, img, lst_boxes, lst_display_str, lst_color=None, thickness=2):
    if lst_color is None:
      lst_color = [tuple([int(x) for x in np.random.randint(low=0, high=255, size=3)]) for _ in range(len(lst_boxes))]
    
    assert len(lst_boxes) == len(lst_display_str) == len(lst_color)        
    for box, display_str, color in zip(lst_boxes, lst_display_str, lst_color):
      top, left, bottom, right = box
      self.draw_box(img=img, top=top, left=left, bottom=bottom, right=right,
                    display_str=display_str, color=color, thickness=thickness)
    return img
  
  def draw_inferences(self, img, lst_preds, lst_color=None):
    lst_boxes = [x['TLBR_POS'] for x in lst_preds]
    lst_display_str = ['{}: {:.2f}%'.format(x['TYPE'], x['PROB_PRC']) for x in lst_preds]
    self.draw_boxes(
      img=img,
      lst_boxes=lst_boxes,
      lst_display_str=lst_display_str,
      lst_color=lst_color
      )
    return img
  
class VisualizationPIL(BaseVisualization):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    return
  
  def close_display(self):
    self.log.p('PIL opens images in separate processes. Please parse process and close the opend image')
    return
  
  def show(self, img, window_name='Debug'):
    if isinstance(img, str):
      img = self.read_image(img)
    if isinstance(img, np.ndarray):
      img = Image.fromarray(img)
    img.show()
    return
  
  def display(self, img, window_name='Debug'):
    self.show(img, window_name)
    return
  
  def read_image(self, path, return_numpy=True):
    assert os.path.exists(path)
    img = Image.open(path)
    if return_numpy:
      img = np.asarray(img)
    return img
  
  def save_image(self, path, img):
    if isinstance(img, np.ndarray):
      img = Image.fromarray(img)
    elif isinstance(img, Image):
      pass
    else:
      raise ValueError('Please provide either np image or PIL Image')
    img.save(path)
    return
  
  def resize_image(self, img, height, width, interpolation=Image.BILINEAR):
    size = (width, height)
    if isinstance(img, np.ndarray):
      img = Image.fromarray(img)
    img_res = img.resize(size=size, resample=interpolation)
    img_res = np.asarray(img_res)
    return img_res
  
  
class VisEngine(BaseDecentrAIObject):
  def __init__(self, vis_engine=VisEngineEnum.CV2, **kwargs):
    super().__init__(**kwargs)    
    self._init_eng(vis_engine, **kwargs)
    return
  
  def _init_eng(self, vis_engine, **kwargs):
    if vis_engine == VisEngineEnum.CV2:
      self.vis_eng = VisualizationCV2(**kwargs)
    else:
      self.vis_eng = VisualizationPIL(**kwargs)
    return
  
  def get_eng(self):
    return self.vis_eng

if __name__ == '__main__':
  from naeural_core import Logger
  log = Logger(lib_name='TST', config_file='config.txt')
  
  vis = VisEngine(log=log, vis_engine=VisEngineEnum.PIL).get_eng()
  img = vis.read_image('C:/Users/ETA/Dropbox/DATA/_vapor_data/_cervical_lessions/_data/train/0/20150722014/20150722163342.jpg')
  vis.show(img)
  vis.save_image('a.png', img)
  
  
  
  
  
  
  
import torch as th
import torchvision as tv



def th_resize_with_pad(img, h, w, device=None, float_convert_first=False):
  def _th_resize(th_img):
    if isinstance(th_img, np.ndarray):
      th_img = th.tensor(th_img, device=device)
    if th_img.shape[-1] == 3:
      if len(th_img.shape) == 3:
        th_img = th_img.permute((2,0,1))
      elif len(th_img.shape) == 4:
        th_img = th_img.permute((0, 3, 1, 2))
    if float_convert_first and th_img.dtype == th.uint8:
      th_img = th_img / 255
        
    fill_value = 144 if th_img.dtype == th.uint8 else 144/255
    new_shape = h, w
    shape = th_img.shape[-2:]  # current shape [height, width]
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    new_unpad = int(round(shape[0] * r)), int(round(shape[1] * r))
    dw, dh = new_shape[1] - new_unpad[1], new_shape[0] - new_unpad[0]  # wh padding
    dw /= 2  # divide padding into 2 sides
    dh /= 2
    if shape != new_unpad:
      th_resized = tv.transforms.Resize(new_unpad)(th_img)
    else:
      th_resized = th_img
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    th_resized_padded = tv.transforms.Pad((left, top, right, bottom), fill=fill_value)(th_resized)
    
    if th_img.dtype == th.uint8:
      th_img = th_img / 255
    return th_resized_padded
  
  if isinstance(img, list):
    images = []
    for im in img:
      images.append(_th_resize(im).unsqueeze(0))
    th_out = th.cat(images)
  else:
    th_out = _th_resize(img)
  return th_out
  

if __name__ == '__main__':
  import matplotlib.pyplot as plt
  import numpy as np
  from PIL import Image
  
  orig_fns = [
    'xperimental/_images/H1080_W1920/faces5.jpg',
    'xperimental/_images/H1080_W1920/faces9.jpg',
    'xperimental/_images/H1080_W1920/faces21.jpg',
    'xperimental/_images/H1520_W2688/bmw_man1.png',
    'xperimental/_images/H2048_W3072/bmw_man1.png',
    ]
  
  all_imgs = [np.asarray(Image.open(x)) for x in orig_fns]
  
  def plot(orig_img, imgs, with_orig=True, row_title=None, **imshow_kwargs):
    if not isinstance(imgs[0], list):
        # Make a 2d grid even if there's just 1 row
        imgs = [imgs]

    num_rows = len(imgs)
    num_cols = len(imgs[0]) + with_orig
    fig, axs = plt.subplots(nrows=num_rows, ncols=num_cols, squeeze=False, figsize=(13,8))
    for row_idx, row in enumerate(imgs):
        row = [orig_img] + row if with_orig else row
        for col_idx, img in enumerate(row):
            ax = axs[row_idx, col_idx]
            img = np.transpose(img.squeeze(), (1,2,0))
            ax.imshow(img, **imshow_kwargs)
            ax.set(xticklabels=[], yticklabels=[], xticks=[], yticks=[])
            ax.set(title='{}x{}'.format(*img.shape[:2]))

    if with_orig:
        axs[0, 0].set(title='Original image {}x{}'.format(*orig_img.shape[-2:]))
        axs[0, 0].title.set_size(8)
    if row_title is not None:
        for row_idx in range(num_rows):
            axs[row_idx, 0].set(ylabel=row_title[row_idx])

    plt.tight_layout()  

  dev = th.device('cuda')

  np_imgs = all_imgs[:3]  
  th_imgs = th.tensor(np_imgs, device=dev)
  
  inputs = np_imgs
  th_resized_padded = th_resize_with_pad(inputs,h=1200,w=1920,float_convert_first=False, device=dev)

  # TODO: 
  #    - test resize -> float/255 vs float/255 -> resize (timeit)
  # 

  for i in range(3):
    plot(
      orig_img=all_imgs[i].transpose((2,0,1)), 
      imgs=[
        th_resized_padded[i].cpu().numpy(),
        ])
  

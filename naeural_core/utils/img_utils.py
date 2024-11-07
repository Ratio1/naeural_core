import numpy as np

from naeural_core import constants as ct

from PIL import Image



def maybe_prepare_img_payload(sender, dct, keys=['IMG', 'IMG_ORIG'], force_list=False):
  dct['IMG_IN_PAYLOAD'] = False
  for key in keys:
    lst_img = dct.get(key, None)
    
    collected = True
  
    if not isinstance(lst_img, list):
      collected = False
      lst_img = [lst_img]
    
    sender.start_timer('all_img_to_msg')
    if isinstance(lst_img[0], np.ndarray):
      dct[key] = []
      dct[key + '_SIZE'] = []
      for img in lst_img:
        if img is None:
          continue
        if img.dtype != np.uint8:
          if img.dtype == 'float' and 0 <= img.min() <= 1 and 0 <= img.max() <= 1:
            img = img * 255.0
          #endif
          img = img.astype(np.uint8)
        #endif
  
        sender.start_timer('np_image_to_base64')
        try:
          is_original = 'ORIG' in key.upper()
          img_str, h, w = img_to_msg(sender.log, img, orig=is_original)
          dct['IMG_IN_PAYLOAD'] = True
        except Exception as e:
          err_msg = str(e)
          msg = "Error occured in 'IMG' preparation for image: {}. Exception: '{}'".format(
            img.shape, err_msg,
          )
          raise ValueError(msg)
        # end try-except on image to text        
        sender.end_timer('np_image_to_base64')
        
        dct[key].append(img_str)
        dct[key + '_HEIGHT'] = h
        dct[key + '_WIDTH'] = w
        dct[key + '_SIZE'].append([h,w])
      #endfor each image in list
      if (not collected) and (not force_list):
        dct[key] = dct[key][0]
        dct[key + '_SIZE'] = dct[key + '_SIZE'][0]
    #endif if np.ndarray
    sender.end_timer('all_img_to_msg')

  return


# When testing we observed that when resizing with PIL
# there was no need to increase the quality above 60 for images
# with height > 1080. Before, if we wanted to compress a 4K image
# we had to increase the quality to 80
# !!! When compressing a 4K image please be sure that
# the font scale and font thickness for the text on the image
# are both 2 for reading clarity
def img_to_msg(log, img, orig=False):
  # retrieving the original resolution
  h, w, _ = np.shape(img)

  if not orig:
    quality=ct.IMAGE_COMPRESSION.QUALITY
    max_height=ct.IMAGE_COMPRESSION.MAX_HEIGHT
  else:
    quality=95
    max_height=ct.IMAGE_COMPRESSION.ORIG_MAX_HEIGHT

  # resizing image and converting to string
  img_str = log.np_image_to_base64(
    np_image=img,
    quality=quality,
    max_height=max_height
  )

  return img_str, h, w

def msg_to_img(log, msg, h, w):
  # decoding image
  img = log.base64_to_np_image(msg)

  # retrieving the decoded resolution
  curr_h, curr_w, _ = np.shape(img)

  # checking if we need to resize it
  if h > curr_h:
    img = Image.fromarray(img)

    # resizing to original size
    img = img.resize((w, h))

    # converting to np array
    img = np.array(img)

  return img

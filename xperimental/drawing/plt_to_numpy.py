import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

import io

sns.set(style="ticks", context="talk")
plt.style.use('dark_background')

def plt_to_np(plt, close=True):
  plt.axis('off')
  fig = plt.gcf()
  fig.canvas.draw()
  data = np.frombuffer(fig.canvas.tostring_rgb(), dtype=np.uint8)
  w, h = plt.gcf().canvas.get_width_height()
  np_img = data.reshape((int(h), int(w), -1))
  if close:
    plt.close()
  return np_img  


def plt_to_np2(plt, close=True):
  with io.BytesIO() as buff:
      plt.savefig(buff, format='rgba', bbox_inches='tight', pad_inches=0.3)
      buff.seek(0)
      data = np.frombuffer(buff.getvalue(), dtype=np.uint8)
  print('actual size is', np.sqrt(data.shape[0] / 4))
  w, h = plt.gcf().canvas.get_width_height()
  im4c = data.reshape((int(h), int(w), -1))
  im = im4c[:,:,:-1]
  if close:
    plt.close()
  return im  

if __name__ == '__main__':
  
  
  
  x = [1,2,3,4,5]
  y = [4,5,2,4,6]
  
  _ = plt.figure(figsize=(13,8))
  plt.scatter(x,y)
  print(plt.gcf().canvas.get_width_height())
  np_img = plt_to_np(plt)
  plt.imshow(np_img)
  
  

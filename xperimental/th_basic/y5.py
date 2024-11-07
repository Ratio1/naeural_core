import torch as th
import cv2
from collections import OrderedDict

from naeural_core import Logger

if __name__ == '__main__':
  
  l = Logger('THY5', base_folder='.', app_folder='_cache', TF_KERAS=False)
  l.set_nice_prints()

  l.P("Loading data...")
  
  fns = [
    'xperimental/_images/FPs/FP1.png',
    'xperimental/_images/FPs/FP2.png',
    'xperimental/_images/FPs/FP3.png',
    
    ]
  imgs = [cv2.imread(x)[:,:,::-1] for x in fns]


  models_path = l.get_models_subfolder('u_y5')
  
  models = OrderedDict({
   'y5_l6' : {
     'model' : th.hub.load(models_path, 'yolov5l6', pretrained=True, source='local'),
     'H'     : 1280,
     },
   
   # 'y5_s6' : {
   #   'model' : th.hub.load(models_path, 'yolov5s6', pretrained=True, source='local'),
   #   'H'     : 1280,
   #   },
   
   # 'y5_s' : {
   #   'model' : th.hub.load(models_path, 'yolov5s', pretrained=True, source='local'),
   #   'H'     : 640,
   #   },
   
   'y5_l' : {
     'model' : th.hub.load(models_path, 'yolov5l', pretrained=True, source='local'),
     'H'     : 640,
     },

       
  })
  
  
  
  results = {}
  for name in models:
    model = models[name]['model']
    sz = models[name]['H']
    for _ in range(10):
      l.start_timer(name)
      res = model(imgs, size=sz)
      l.stop_timer(name)
    results[name] = res
  
  for name, res in results.items():
    l.P('Results for {}:\n{}'.format(name,res.pandas().xyxy[0]))
    res.print()
  l.show_timers(div=len(imgs))
    
  
  # jit
  # m = models['y5_l6']['model']
  # sm = th.jit.script(m)
  
    
  
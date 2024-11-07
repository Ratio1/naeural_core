import numpy as np

from naeural_core import Logger

if __name__ == '__main__':
  cfg_file = 'main_config.txt'
  log = Logger(
    lib_name='EE_XPER', 
    config_file=cfg_file, 
    max_lines=1000, 
    TF_KERAS=False
    )
  
  N = 14
  H = 1520
  W = 2688  
  ITERS = 100  
  for _ in range(ITERS):
    lst_imgs = [np.random.randint(0, 256, (H, W, 3), dtype=np.uint8) for _ in range(N)]
    log.start_timer('allocate')
    np_imgs = np.array(lst_imgs)
    log.stop_timer('allocate')
    
  np_cache = np.zeros_like(np_imgs, dtype=np.uint8)
  np_imgs = np.random.randint(0, 256, (N, H, W, 3), dtype=np.uint8)  
  for _ in range(ITERS):
    np_imgs-= 1
    log.start_timer('copy')
    np_cache[:,:] = 0
    for i in range(N):
      np_cache[i, :] = np_imgs[i]
    log.stop_timer('copy')
  
  log.show_timers()
  
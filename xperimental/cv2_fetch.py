import cv2
from time import perf_counter
import numpy as np
from time import sleep
import os

if __name__ == '__main__':

  path = '/Users/laurentiupiciu/Dropbox/DATA/_vapor_data/_config_repo/LAURENTIU_STREAMS/testing_framework/aggressive_close_+_fallen.mp4'
  # path = '/home/laurentiu/Dropbox/DATA/_vapor_data/_config_repo/LAURENTIU_STREAMS/testing_framework/aggressive_close_+_fallen.mp4'

  cap = cv2.VideoCapture(path)

  lst_processing_times = []
  lst_sleep_times = []
  lst_iter_times = []
  frame_count = 0
  CAP_RESOLUTION = 25

  from naeural_core import Logger
  log = Logger(lib_name='DCTR', base_folder='.', app_folder='_local_cache', TF_KERAS=False)


  if False:
    t0 = perf_counter()
    while True:
      start_iter_time = perf_counter()
      start_processing_time = perf_counter()
      has_frame, frame = cap.read()
      if has_frame:
        frame_count += 1
        frame = np.ascontiguousarray(frame[:, :, ::-1])
      else:
        break
      processing_time = perf_counter() - start_processing_time
      lst_processing_times.append(processing_time)
      sleep_time = max(1/CAP_RESOLUTION - processing_time, 0.00001)
      lst_sleep_times.append(sleep_time)
      sleep(sleep_time)
      iter_time = perf_counter() - start_iter_time
      lst_iter_times.append(iter_time)
    #endwhile

    t1 = perf_counter()

    print("Total capture time for {} frames: {:.2f}s".format(frame_count, t1-t0))

  if False:
    CAPTURE_BATCH = 32
    t0 = perf_counter()
    while True:
      start_iter_time = perf_counter()
      start_processing_time = perf_counter()
      lst_raw_frames = []
      for _ in range(CAPTURE_BATCH):
        log.start_timer('cv2_capread')
        has_frame, frame = cap.read()
        log.end_timer('cv2_capread')
        if has_frame:
          frame_count += 1
          lst_raw_frames.append(frame)
        else:
          break
        #endif
      #endfor

      if len(lst_raw_frames) == 0:
        break

      log.start_timer('np.array')
      np_raw_frames = np.array(lst_raw_frames)
      log.end_timer('np.array')

      log.start_timer('channels')
      np_switched_raw_frames = np_raw_frames[:,:,:, ::-1]
      log.end_timer('channels')

      log.start_timer('ascontiguousarray')
      np_switched_raw_frames = np.ascontiguousarray(np_switched_raw_frames)
      log.end_timer('ascontiguousarray')

      log.start_timer('virtual_add_inputs')
      lst_proc_frames = [x for x in np_switched_raw_frames]
      log.end_timer('virtual_add_inputs')

      processing_time = perf_counter() - start_processing_time
      lst_processing_times.append(processing_time)
      sleep_time = max(1 / CAP_RESOLUTION - processing_time, 0.00001)
      lst_sleep_times.append(sleep_time)
      sleep(sleep_time)
      iter_time = perf_counter() - start_iter_time
      lst_iter_times.append(iter_time)
    # endwhile

    t1 = perf_counter()

    print("Total capture time for {} frames (batch={}): {:.2f}s".format(frame_count, CAPTURE_BATCH, t1 - t0))

    log.show_timers()

  if True:
    lst_raw_imgs = []
    for i in range(32):
      log.start_timer('cv2_capread')
      has_frame, frame = cap.read()
      log.end_timer('cv2_capread')
      assert has_frame
      lst_raw_imgs.append(frame)
    #endfor

    np_imgs = np.array(lst_raw_imgs)

    np_one_image = np_imgs[0].copy()

    for _ in range(20):
      log.start_timer('channels_one')
      x_c = np_one_image[:,:,::-1]
      log.end_timer('channels_one')

      log.start_timer('ascontiguousarray_one')
      x = np.ascontiguousarray(x_c)
      log.end_timer('ascontiguousarray_one')

    for _ in range(20):
      log.start_timer('channels_{}'.format(np_imgs.shape[0]))
      x_c = np_imgs[:,:,:,::-1]
      log.end_timer('channels_{}'.format(np_imgs.shape[0]))

      log.start_timer('ascontiguousarray_{}'.format(np_imgs.shape[0]))
      x = np.ascontiguousarray(x_c)
      log.end_timer('ascontiguousarray_{}'.format(np_imgs.shape[0]))

    log.show_timers()


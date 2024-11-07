import numpy as np
import mmap
import posix_ipc as pipc
import cv2

from naeural_core.xperimental.shm_config import SIZE_DEF, WIDTH_DEF, HEIGHT_DEF, CHANNELS_DEF, SHM_STREAMER_VAPOR, SHM_VAPOR_STREAMER

USE_DUMMY = False

def get_dummy_frame():
  return np_img.copy()

def get_rtsp_frame():
  rtsp = '__URL__'
  
  cap = cv2.VideoCapture(rtsp)
  if not cap.isOpened():
    print("Cannot open camera")
    return
  while True:
    ret, frame = cap.read()    
    if not ret:
        print("Can't receive frame (stream end?). Exiting ...")
        break
    
    frame = cv2.resize(frame, dsize=(WIDTH_DEF, HEIGHT_DEF))  
    yield frame  
  return

if __name__ == '__main__':
  
  np_img = cv2.imread('xperimental/main_loop_issue.png')
  np_img = cv2.resize(np_img, dsize=(WIDTH_DEF, HEIGHT_DEF))  
  
  mem_out = pipc.SharedMemory(
    name=SHM_STREAMER_VAPOR,
    flags=pipc.O_RDWR | pipc.O_CREAT,
    size=SIZE_DEF,
    )
  
  mmap_obj_out = mmap.mmap(mem_out.fd, length=SIZE_DEF)
  mem_ptr_out = memoryview(mmap_obj_out)
  

  mem_in = pipc.SharedMemory(
    name=SHM_VAPOR_STREAMER,
    flags=pipc.O_RDWR | pipc.O_CREAT,
    size=SIZE_DEF,
    )

  mmap_obj_in = mmap.mmap(mem_in.fd, length=SIZE_DEF)
  mem_ptr_in = memoryview(mmap_obj_in)
  
  np_from_stream_to_alex = np.ndarray(
    shape=(HEIGHT_DEF, WIDTH_DEF, CHANNELS_DEF),
    dtype=np.uint8,
    buffer=mem_ptr_out,
    )

  np_from_alex_to_transcoder = np.ndarray(
    shape=(HEIGHT_DEF, WIDTH_DEF, CHANNELS_DEF),
    dtype=np.uint8,
    buffer=mem_ptr_in,
    )
  
  np_from_alex_to_transcoder.fill(0)
  
  gen = get_rtsp_frame()
  
  while True:
    if USE_DUMMY:
      frame = get_rtsp_frame()
    else:
      frame = next(gen)
    np_from_stream_to_alex[:] = frame[:]
    # time.sleep(1)
    frame_processed = np_from_alex_to_transcoder.copy()
    cv2.imwrite('xperimental/output.png', frame_processed)
    
    
  
  

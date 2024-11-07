import posix_ipc
from mmap import mmap
import cv2
import numpy as np
import argparse
from xperimental.shm_config import SIZE_DEF, WIDTH_DEF, HEIGHT_DEF, CHANNELS_DEF, SHM_STREAMER_VAPOR, SHM_VAPOR_STREAMER

class Shm:
    def __init__(self, size, h, w, c, yuv=True):
        self._size = size
        self._h = h
        self._w = w
        self._c = c
        self._yuv = yuv

    def init_memory(self):
        self._memory_in = posix_ipc.SharedMemory(SHM_STREAMER_VAPOR, posix_ipc.O_RDWR, size=self._size)
        self._memory_out = posix_ipc.SharedMemory(SHM_VAPOR_STREAMER, posix_ipc.O_RDWR, size=self._size)

        self.mmap_in = mmap(self._memory_in.fd, self._memory_in.size)
        self.mmap_out = mmap(self._memory_out.fd, self._memory_out.size)

        self.buf_in = memoryview(self.mmap_in)
        self.buf_out = memoryview(self.mmap_out)

        if self._yuv:
            self.np_in = np.ndarray(shape=(int(self._h*1.5), self._w), dtype=np.uint8, buffer=self.buf_in)
            self.np_out = np.ndarray(shape=(int(self._h*1.5), self._w), dtype=np.uint8, buffer=self.buf_out)
        else:
            self.np_in = np.ndarray(shape = (self._h, self._w, self._c), dtype=np.uint8, buffer=self.buf_in)
            self.np_out = np.ndarray(shape = (self._h, self._w, self._c), dtype=np.uint8, buffer=self.buf_out)
    
    def np_read(self):
        np_rgb = self.np_in.copy()
        if self._yuv:
          np_rgb = cv2.cvtColor(np_rgb, cv2.COLOR_YUV2RGB_I420)
        return np_rgb

    def np_write(self, np_rgb):
        if self._yuv:
          np_rgb = cv2.cvtColor(np_rgb, cv2.COLOR_RGB2YUV_I420)          
        self.np_out[:] = np_rgb[:]

    def write_to_memory(self, mapfile, s):
        mapfile.seek(0)
        mapfile.write(s)

    def read_from_memory(self, mapfile):
        global frame_bytes_size
        mapfile.seek(0)
        return mapfile.read(frame_bytes_size) 

    def process_rgb_frame(self, frame):
        if type(frame) == bytes:
            img = np.frombuffer(frame, dtype=np.uint8)
            img = img.reshape((self._h,self._w,self._c))
            img = img[:,:,::-1]
        else:
            img = frame
        
        img = cv2.circle(img,(self._w//2, self._h//2), self._h//2, (0,0,255), 2)
        (text_w, text_h),_ = cv2.getTextSize(text="Pseudo AI processing" , fontFace=cv2.FONT_HERSHEY_TRIPLEX , fontScale=0.8 , thickness=2)
        left = self._w//2 - text_w//2
        bottom = self._h//2 + text_h//2
        img = cv2.putText(img, "Pseudo AI processing", (left, bottom), fontFace=cv2.FONT_HERSHEY_TRIPLEX, fontScale=0.8, thickness=2, color=(0,0,255))
        return img


def get_args(parser):
    size_arg = SIZE_DEF
    width_arg = WIDTH_DEF
    heigth_arg = HEIGHT_DEF
    channels_arg = CHANNELS_DEF
    args = parser.parse_args()
    if args.size != None:
        size_arg = args.size_yuv
    if args.w != None:
        width_arg = args.w
    if args.h != None:
        heigth_arg = args.h
    if args.c != None:
        channels_arg = args.c
    return size_arg, width_arg, heigth_arg, channels_arg

def add_args(parser):
    parser.add_argument('--size', help = 'Frame size in bytes')
    parser.add_argument('--w', help = 'Frame width in pixels')
    parser.add_argument('--h', help = 'Frame height in pixels')
    parser.add_argument('--c', help = 'Frame number of channels')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    add_args(parser)
    size, width, height, channels = get_args(parser)

    shm = Shm(size, height, width, channels, yuv=False)
    shm.init_memory()

    while True:
        frame = shm.np_read()
        frame = shm.process_rgb_frame(frame)
        shm.np_write(frame)
        #frame = shm.read_from_memory(shm.mmap_in)
        #frame = shm.process_rgb_frame(frame)
        #shm.write_to_memory(shm.mmap_out, frame)


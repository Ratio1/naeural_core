import numpy as np
import subprocess as sp
import shlex
from time import sleep

import traceback



class FFmpegWriter(object):
  def __init__(self, 
               filename, 
               fps, 
               frameSize, 
               log, 
               method='h264', 
               overall_options=None, 
               use_shell=False,
               pipe_cmd=''):
    """
    

    Parameters
    ----------
    filename : str
      
    fps : int
      
    frameSize : tuple
        
    
    log : Logger
      
    
    method : str, optional
      'h264' or other encoding methods - use default. The default is 'h264'.
      
    overall_options : str, optional
      `-stats_period 10` will show from 10 to 10 seconds . The default is None.

    Returns
    -------
    None.

    """
    assert method in ['h264', 'h265']
    method = method[1:]
    self._codec = method
    self._fn = filename
    self._fps = fps
    self._fsize = frameSize
    self.log = log
    self._write_timer = 'h{}_write'.format(self._codec)
    width, height = frameSize
    
    if isinstance(overall_options, str) and len(overall_options) > 0:
      overall_options = overall_options + ' '
    else:
      overall_options = ''

    raw_cmd = f'ffmpeg -y {overall_options}-s {width}x{height} -pixel_format bgr24 -f rawvideo -r {fps} -i pipe: -video_track_timescale 30k -vcodec libx{method} -pix_fmt yuv420p -crf 24 "{filename}"'
    if use_shell and len(pipe_cmd.strip()) > 0:
      if pipe_cmd.strip()[0] != '|':
        pipe_cmd = '| ' + pipe_cmd
      raw_cmd = raw_cmd + ' ' + pipe_cmd
    
    self._cmd = raw_cmd    
    self.log.P("Running ffmpeg:\n{}".format(raw_cmd))
    starter = raw_cmd if use_shell else shlex.split(raw_cmd)
    
    # TODO (S): must get result output!
    self.process = sp.Popen(
      starter, 
      stdin=sp.PIPE,
      shell=use_shell,
    )
    sleep(2)
    # TODO (S): show & raise error if the process resulted in exec error (ie. wrong parameters)
    return
  
  @property
  def command(self):
    return self._cmd
  
  def get_command(self):
    return self.command
  
  def isOpened(self):
    return self.process.poll() is None
  
  def write(self, frame):
    assert isinstance(frame, np.ndarray), "write input must be ndarray"
    if len(frame.shape) == 4:
      b_size = frame.shape[0]
    else:
      b_size = 1
    # Write raw video frame to input stream of ffmpeg sub-process.
    self.log.start_timer(self._write_timer, section='FFmpegWriter')
    self.log.start_timer('bytes_b'+str(b_size), section='FFmpegWriter')
    buff = frame.tobytes()
    self.log.stop_timer('bytes_b'+str(b_size), section='FFmpegWriter')
    self.log.start_timer('write_b'+str(b_size), section='FFmpegWriter')
    try:
      self.process.stdin.write(buff)
    except Exception as e:
      raise ValueError("Error in FFmpegWriter: '{}'. Using:\n{}".format(
        str(e), self.command,
        )
      )
    self.log.stop_timer('write_b'+str(b_size), section='FFmpegWriter')
    self.log.stop_timer(self._write_timer, section='FFmpegWriter')
    return
  
  def release(self):
    # Close and flush stdin
    self.process.stdin.close()    
    # Wait for sub-process to finish
    self.process.wait()    
    # Terminate the sub-process
    self.process.terminate()
    return
  

      
      
  
  

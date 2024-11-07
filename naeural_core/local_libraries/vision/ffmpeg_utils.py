import math
import os
import subprocess
import tempfile
from datetime import datetime, timedelta
from time import time


class FFMPEGUtils(object):
  def __init__(self, *, caller=None):
    self._caller = caller
    return

  def P(self, text, **kwargs):
    if self._caller is not None:
      self._caller.P(text, **kwargs)
    else:
      print(text)

  def _call_subprocess(self, command):
    uid = time()
    outf = 'output_{}.txt'.format(uid)
    errf = 'error_{}.txt'.format(uid)

    outfd = open(outf, 'xb')
    errfd = open(errf, 'xb')

    proc = subprocess.Popen(
      command,
      shell=True,
      stdout=outfd,
      stderr=errfd
    )
    proc.wait()

    outfd.close()
    errfd.close()

    outfd = open(outf, 'rb')
    errfd = open(errf, 'rb')

    stdout = outfd.read().decode()
    stderr = errfd.read().decode()

    outfd.close()
    errfd.close()

    os.remove(outf)
    os.remove(errf)

    self._process_output(proc.returncode, stdout=stdout, stderr=stderr, command=command)

    return stdout, stderr

  def video_file_duration(self, path, *, as_float=False):
    command = "ffprobe -i \"{}\"".format(path)

    _, stderr = self._call_subprocess(command)

    duration = stderr.split('Duration: ')[1].split(',')[0]

    if not as_float:
      return duration

    dt_duration = datetime.strptime(duration, '%H:%M:%S.%f')
    dt_zero = datetime.strptime('1900-01-01', '%Y-%m-%d')
    total_seconds = (dt_duration - dt_zero).total_seconds()
    return total_seconds

  def video_resolution(self, path):
    command = "ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 \"{}\"".format(
      path)

    stdout, _ = self._call_subprocess(command)

    width, height = stdout.split('\r\n')[0].split('x')[:2]

    return (int(height), int(width))

  def _process_output(self, returncode, *, stdout, stderr, command=None):
    if returncode != 0:
      self.P("Return code != 0, maybe an error!", color='r')
      if self._caller is not None:
        self._caller._create_notification(
          notif='EXCEPTION',
          msg="Return code not 0, maybe an error! The command was: {}\n The output of the process was: {}\n The stderr of the process was {}\n Raising exception...".format(
            command, stdout, stderr)
        )
      raise Exception("Return code not 0, maybe an error! The command was: {}.\n The output of the process was: {}\n The stderr of the process was {}".format(
        command, stdout, stderr))

  def _fast_split(self, *, path, segment_time, path_to_output, prefix_output_name, ext):
    full_path_no_ext = os.path.join(path_to_output, prefix_output_name)
    command = "ffmpeg -i \"{}\" -c copy -map 0:v -segment_time {} -reset_timestamps 1 -f segment {}%03d{}".format(
      os.path.abspath(path), segment_time, full_path_no_ext, ext
    )

    self._call_subprocess(command)

    output_files = list(filter(lambda x: x.startswith(prefix_output_name), os.listdir(path_to_output)))
    return output_files

  def _slow_split(self, *, path, chunk_seconds, path_to_output, prefix_output_name, ext):
    full_path_no_ext = os.path.join(path_to_output, prefix_output_name)
    command = "ffmpeg -i {} -hide_banner  -err_detect ignore_err -segment_time {} -force_key_frames  \"expr: gte(t,n_forced*{})\" -f segment {}%03d{}".format(
      os.path.abspath(path), chunk_seconds, chunk_seconds, full_path_no_ext, ext
    )

    self._call_subprocess(command)

    output_files = list(filter(lambda x: x.startswith(prefix_output_name), os.listdir(path_to_output)))
    return output_files

  def _remove_generated_files(self, files, path_to_output):
    for file in files:
      if os.path.exists(os.path.join(path_to_output, file)):
        os.remove(os.path.join(path_to_output, file))

  def _remove_possible_generated_files(self, prefix_output_name, path_to_output):
    files = list(filter(lambda x: x.startswith(prefix_output_name), os.listdir(path_to_output)))
    for file in files:
      if os.path.exists(os.path.join(path_to_output, file)):
        os.remove(os.path.join(path_to_output, file))

  def _find_smallest_pair(self, files):
    smallest_sum = float('inf')
    for i in range(len(files) - 1):
      pair_sum = files[i][1] + files[i + 1][1]
      if pair_sum < smallest_sum:
        smallest_sum = pair_sum
        smallest_first = i
    return smallest_first

  def _concat_smallest_files(self, files, prefix_output_name, path_to_output, ext):
    # search for the consecutive pair with the smallest sum of durations
    idx = self._find_smallest_pair(files)

    # extract the two smallest files
    f1 = files.pop(idx)
    f2 = files.pop(idx)

    # compute the merged file name
    out_name = prefix_output_name + f1[0].split(prefix_output_name)[1].split(ext)[0] + \
        f2[0].split(prefix_output_name)[1].split(ext)[0]
    out_name += ext

    # compute the full paths of the two files
    f1_full_path = os.path.join(path_to_output, f1[0])
    f2_full_path = os.path.join(path_to_output, f2[0])

    # concat the two smallest files
    out_path = os.path.join(path_to_output, out_name)
    self.concatenate_multiple_video_files([f1_full_path, f2_full_path], out_path)

    # remove the smaller files
    self._remove_generated_files([f1[0], f2[0]], path_to_output)

    # add the resulted file
    files.insert(idx, (out_name, f1[1] + f2[1]))

  def split_video_file(self, path, nr_chunks, path_to_output, duration=None, prefix_output_name=None, default_ext=None):
    # if nr_chunks <= 1:
      # return {'segment_time' : [self.video_file_duration(path=path, as_float=True)], 'output_files' : [path], 'errors': "Splitting 1 movie in {} chunks".format(nr_chunks)}
    # TODO: check if output_file_name is greater than X
    # TODO: check 1k shards
    if prefix_output_name is None:
      prefix_output_name = 'output_part_'

    path_to_output = os.path.abspath(path_to_output)

    _, ext = os.path.splitext(path)
    if default_ext is not None:
      ext = default_ext

    assert len(ext) > 0

    if duration is None or type(duration) == str:
      duration = self.video_file_duration(path=path, as_float=True)
    #endif
    
    chunk_seconds = math.ceil(duration / nr_chunks) + 1
    segment_time = str(timedelta(seconds=chunk_seconds))

    if False:
      try:
        # the faster method
        output_files = self._fast_split(
          path=path,
          segment_time=segment_time,
          path_to_output=path_to_output,
          prefix_output_name=prefix_output_name,
          ext=ext
        )

        durations = [self.video_file_duration(path=os.path.join(
          path_to_output, shard_path), as_float=True) for shard_path in output_files]
        files = list(zip(output_files, durations))

        # split in exactly nr_chunks
        while len(files) > nr_chunks:
          self._concat_smallest_files(files, prefix_output_name, path_to_output, ext)

        output_files = [f[0] for f in files]
        self.P('Finished splitting the files blazingly fast.')

      except Exception as e:
        # the fast approach failed, resorting to the slow approach
        self.P('The fast splitting approach failed on movie {} with exception {}.'.format(path, e))

        self._remove_possible_generated_files(prefix_output_name, path_to_output)
    output_files = self._slow_split(
      path=path,
      chunk_seconds=chunk_seconds,
      path_to_output=path_to_output,
      prefix_output_name=prefix_output_name,
      ext=ext
    )

    output_files.sort()
    dct_ret = {'segment_time': segment_time, 'output_files': output_files, 'errors': _}
    return dct_ret

  def concatenate_multiple_video_files(self, input_paths, output_path):
    output_path = os.path.abspath(output_path)

    if len(input_paths) == 1:
      from shutil import copyfile
      copyfile(input_paths[0], output_path)
      return

    lst_ffmpeg_input_file_rows = ["file '{}'\n".format(os.path.abspath(f)) for f in input_paths]

    tmp = tempfile.NamedTemporaryFile(mode='w+t', delete=False)
    tmp.writelines(lst_ffmpeg_input_file_rows)
    tmp.seek(0)

    command = "ffmpeg -f concat -safe 0 -i {} -c copy {}".format(tmp.name, output_path)

    self._call_subprocess(command)

    tmp.close()
    os.remove(tmp.name)
    return

  def no_frames(self, path):
    command = "ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames -of default=nokey=1:noprint_wrappers=1 \"{}\"".format(
      path)

    stdout, _ = self._call_subprocess(command)

    return int(stdout)


if __name__ == "__main__":
  video_path = "/home/gts/exe_eng/_local_cache/_data/downloads/20221213170049405891"
  ff = FFMPEGUtils()
  _video_file_duration = ff.video_file_duration(path=video_path)

  dct_ret = ff.split_video_file(
      path=video_path,
      nr_chunks=4,
      path_to_output='/home/gts/exe_eng/_local_cache/_data/downloads/test',
      duration=_video_file_duration,
      default_ext='.mp4'
    )
  print(dct_ret)

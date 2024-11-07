from naeural_core import DecentrAIObject
from naeural_core import Logger
from naeural_core.business.mixins_base import _DiskAPIMixin
import pandas as pd
from time import sleep
import numpy as np
import cv2

class A(DecentrAIObject, _DiskAPIMixin):

  def __init__(self, log, **kwargs):
    super(A, self).__init__(log=log, **kwargs)
    return

if __name__ == '__main__':
  l = Logger(lib_name='diskapi', base_folder='.', app_folder='_local_cache')
  a = A(log=l)


  ### define data for checking DiskAPI
  # dataframe
  col3 = []
  for i in range(3):
    col3.append(l.now_str(nice_print=True, short=False))
    sleep(1)

  data_for_df = {'col1' : [1,2,3], 'col2' : ['a', 'b', 'c'], 'col3' : col3}
  df = pd.DataFrame(data_for_df)

  # json
  dct = {'1' : 'abc', 'a' : [{2 : 'c'}, {3 : 'e'}], 'x' : False}

  # pickle
  arr = np.random.randint(0, 100, size=(100,100))

  # video
  lst_frames = []
  cap = cv2.VideoCapture('https://live.zootargoviste.ro:7001')
  nr_frames = 0
  H,W = None, None
  while nr_frames < 100:
    has_frame, frame = cap.read()
    if has_frame:
      H,W = frame.shape[:2]
      lst_frames.append(frame)
      nr_frames += 1

  cap.release()
  cv2.destroyAllWindows()

  #### use the DiskAPI

  # dataframe
  path_df1 = a.diskapi_save_dataframe_to_output(
    df=df, filename='diskapi_tests/dataframes/df1.csv'
  )

  path_df2 = a.diskapi_save_dataframe_to_output(
    df=df, filename='diskapi_tests/dataframes/df2.csv',
    compress=True
  )

  df1 = a.diskapi_load_dataframe_from_output(
    filename='diskapi_tests/dataframes/df1.csv',
    timestamps=['col3']
  )

  df2 = a.diskapi_load_dataframe_from_output(
    filename='diskapi_tests/dataframes/df2.csv',
    timestamps=['col3'],
    decompress=True
  )


  # json
  path_j1 = a.diskapi_save_json_to_output(
    dct=dct, filename='diskapi_tests/jsons/j1.json',
    indent=True
  )

  path_j2 = a.diskapi_save_json_to_output(
    dct=dct, filename='diskapi_tests/jsons/j2.json',
    indent=False
  )

  j2 = a.diskapi_load_json_from_output(filename='diskapi_tests/jsons/j2.json', verbose=True)

  # pickle
  path_p1 = a.diskapi_save_pickle_to_output(
    obj=arr, filename='diskapi_tests/pickles/p1.pickle',
    compress=False
  )

  path_p2 = a.diskapi_save_pickle_to_output(
    obj=arr, filename='diskapi_tests/pickles/p2.pickle',
    compress=True
  )

  p2 = a.diskapi_load_pickle_from_output(
    filename='diskapi_tests/pickles/p2.pickle',
    decompress=True
  )

  # video
  handle, path_v1 = a.diskapi_create_video_file_to_output(
    filename='diskapi_tests/videos/v1.mp4',
    fps=25, str_codec="mp4v", frame_size=(H,W)
  )

  for f in lst_frames:
    a.diskapi_write_video_frame(handle, f)











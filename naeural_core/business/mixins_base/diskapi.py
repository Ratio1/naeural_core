"""
  TODO: 
    - basic file management:
        - add file history recording for all ops
        - add own file deletion `diskapi_remove`
  
"""
import shutil

import pandas as pd
import cv2
import os
import zipfile
from naeural_core import constants as ct
from typing import List, Union, Tuple
from naeural_core.local_libraries.vision.ffmpeg_writer import FFmpegWriter

def assert_folder(folder : str):
  assert folder in ['data', 'models', 'output']

class _DiskAPIMixin(object):

  def __init__(self):
    super(_DiskAPIMixin, self).__init__()
    return

  # Utils section
  if True:
    def is_path_safe(self, path):
      """
      Method for checking if a certain path is safe(it's inside the cache directory).
      Parameters
      ----------
      path - string, path to be checked

      Returns
      -------
      bool, True if the path is safe, False otherwise
      """
      abs_cache_directory = os.path.abspath(self.log.get_base_folder())
      return os.path.abspath(path).startswith(abs_cache_directory)
  # endif Utils

  # Dataframe serialization section
  if True:
    def _diskapi_save_dataframe(self, df : pd.DataFrame, filename : str, folder : str,
                                ignore_index : bool = True, compress : bool = False, mode : str = 'w',
                                header : Union[bool, List[str]] = True,
                                also_markdown : bool = False, verbose : bool = True,
                                as_parquet : bool = False):

      """
      Parameters:
      -----------
      df: pandas.DataFrame, mandatory
        The dataframe that should be saved

      filename: str, mandatory
        Relative path to `folder` (in local cache), where the dataframe should be saved

      folder: str, mandatory
        The folder in local cache
        Possible values: 'data', 'output', 'models'

      ignore_index: bool, optional
        Decides whether to ignore or not the index when writing
        The default value is True

      compress: bool, optional
        Save to zipped pickle
        The default value is False.

      mode: str, optional
        The writing mode in csv.
        Possible values: 'w' - write; 'a' - append
        The default value is 'w' - write.

      header: bool or List[str], optional
        Write out the column names. If a list of strings is given it is assumed to be aliases for the column names.
        This may be set to False for 'append' mode, for all but not the first save call.
        The default value is False.

      verbose: bool, optional
        Controls logging when saving the dataframe
        The default value is True
        
      as_parquet: bool, optional
        Save as parquet file. Default is False

      Returns:
      -------
      str
        full path to the saved file
      """

      mode = mode.lower()
      assert mode in ['w', 'a']
      assert_folder(folder)

      _, full_path = self.log.save_dataframe(
        df=df, fn=filename, folder=folder,
        ignore_index=ignore_index, compress=compress,
        mode=mode, header=header, also_markdown=also_markdown, verbose=verbose,
        as_parquet=as_parquet,
      )

      return full_path


    def diskapi_save_dataframe_to_data(self, df : pd.DataFrame, filename : str,
                                       ignore_index : bool = True, compress : bool = False, mode : str = 'w',
                                       header : Union[bool, List[str]] = True,
                                       also_markdown : bool = False, verbose : bool = True,
                                       as_parquet : bool = False
                                       ):
      """
      Shortcut to _diskapi_save_dataframe.
      """
      return self._diskapi_save_dataframe(
        df=df, filename=filename, folder='data',
        ignore_index=ignore_index, compress=compress, mode=mode, header=header,
        also_markdown=also_markdown, verbose=verbose, as_parquet=as_parquet,
      )

    def diskapi_save_dataframe_to_models(self, df : pd.DataFrame, filename : str,
                                         ignore_index : bool = True, compress : bool = False, mode : str = 'w',
                                         header : Union[bool, List[str]] = True,
                                         also_markdown : bool = False, verbose : bool = True,
                                         as_parquet : bool = False,
                                         ):
      """
      Shortcut to _diskapi_save_dataframe.
      """
      return self._diskapi_save_dataframe(
        df=df, filename=filename, folder='models',
        ignore_index=ignore_index, compress=compress, mode=mode, header=header,
        also_markdown=also_markdown, verbose=verbose, as_parquet=as_parquet,
      )

    def diskapi_save_dataframe_to_output(self, df : pd.DataFrame, filename : str,
                                         ignore_index : bool = True, compress : bool = False, mode : str = 'w',
                                         header : Union[bool, List[str]] = True,
                                         also_markdown : bool = False, verbose : bool = True,
                                         as_parquet : bool = False,
                                         ):
      """
      Shortcut to _diskapi_save_dataframe.
      """
      return self._diskapi_save_dataframe(
        df=df, filename=filename, folder='output',
        ignore_index=ignore_index, compress=compress, mode=mode, header=header,
        also_markdown=also_markdown, verbose=verbose, as_parquet=as_parquet,
      )

    def _diskapi_load_dataframe(self, filename : str, folder : str,
                                decompress : bool = False, timestamps : Union[str, List[str]] = None):
      """
      Parameters:
      ----------
      filename: str, mandatory
        Relative path to `folder` (in local cache), from where the dataframe should be loaded
        If filename ends in ".zip" then the loading will also uncompress in-memory

      folder: str, mandatory
        The folder in local cache
        Possible values: 'data', 'output', 'models'

      decompress: bool, optional
        Should be True if the file was saved with `compress=True`
        The default value is False.

      timestamps: str or List[str], optional
        Column names that should be parsed as dates when loading the dataframe
        The default is None

      Returns:
      --------
      pandas.DataFrame
      """
      assert_folder(folder)
      df = self.log.load_dataframe(
        fn=filename, 
        folder=folder, 
        decompress=decompress, 
        timestamps=timestamps
      )
      return df

    def diskapi_load_dataframe_from_data(self, filename : str, decompress : bool = False,
                                         timestamps : Union[str, List[str]] = None):
      """
      Shortcut to _diskapi_load_dataframe.
      """
      return self._diskapi_load_dataframe(
        filename=filename, folder='data', decompress=decompress, timestamps=timestamps
      )

    def diskapi_load_dataframe_from_models(self, filename : str, decompress : bool = False,
                                           timestamps : Union[str, List[str]] = None):
      """
      Shortcut to _diskapi_load_dataframe.
      """
      return self._diskapi_load_dataframe(
        filename=filename, folder='models', decompress=decompress, timestamps=timestamps)

    def diskapi_load_dataframe_from_output(self, filename : str, decompress : bool = False,
                                           timestamps : Union[str, List[str]] = None):
      """
      Shortcut to _diskapi_load_dataframe.
      """
      return self._diskapi_load_dataframe(
        filename=filename, folder='output', decompress=decompress, timestamps=timestamps
      )
  # endif

  # Pickle serialization section
  if True:
    def _diskapi_save_pickle(
        self, obj: object, filename: str, folder: str,
        subfolder: str = None, compress: bool = False,
        verbose: bool = True
    ):
      """
      Parameters:
      -----------
      obj: object, mandatory
        Any object that can be pickled to be saved

      filename: str, mandatory
        Relative path to `folder` (in local cache), where the object should be pickled

      folder: str, mandatory
        The folder in local cache
        Possible values: 'data', 'output', 'models'

      subfolder: str, optional
        A subfolder in `folder` where the object should be pickled

      compress: bool, optional
        if compression is required. It is activated also by setting '.pklz' as extension for `filename`
        The default value is False

      verbose: bool, optional
        Controls logging when saving the object
        The default value is True

      Returns:
      --------
      str
        full_path to the saved object
      """
      assert_folder(folder)

      full_path = self.log.save_pickle(
        data=obj, fn=filename, folder=folder,
        subfolder_path=subfolder,
        compressed=compress, verbose=verbose,
        locking=True
      )

      return full_path

    def diskapi_save_pickle_to_data(
        self, obj: object, filename: str, subfolder: str = None,
        compress : bool = False, verbose : bool = True
    ):
      """
      Shortcut to _diskapi_save_pickle.
      """
      return self._diskapi_save_pickle(
        obj=obj, filename=filename, subfolder=subfolder,
        folder='data', compress=compress, verbose=verbose
      )

    def diskapi_save_pickle_to_models(
        self, obj: object, filename: str, subfolder: str = None,
        compress: bool = False, verbose: bool = True
    ):
      """
      Shortcut to _diskapi_save_pickle.
      """
      return self._diskapi_save_pickle(
        obj=obj, filename=filename, subfolder=subfolder,
        folder='models', compress=compress, verbose=verbose
      )

    def diskapi_save_pickle_to_output(
        self, obj: object, filename: str, subfolder: str = None,
        compress: bool = False, verbose: bool = True
    ):
      """
      Shortcut to _diskapi_save_pickle.
      """
      return self._diskapi_save_pickle(
        obj=obj, filename=filename, subfolder=subfolder,
        folder='output', compress=compress, verbose=verbose
      )

    def _diskapi_load_pickle(
        self, filename: str, folder: str, subfolder: str = None,
        decompress: bool = False, verbose: bool = True
    ):
      """
      Parameters:
      -----------
      filename: str, mandatory
        Relative path to `folder` (in local cache), from where a pickled object should be loaded
        If filename ends in ".pklz" then the loading will also decompress

      folder: str, mandatory
        The folder in local cache
        Possible values: 'data', 'output', 'models'

      decompress: bool, optional
        Should be True if the file was saved with `compress=True`
        The default value is False.

      verbose: bool, optional
        Controls logging when loading the object
        The default value is True

      Returns:
      --------
      object
      """
      assert_folder(folder)

      obj = self.log.load_pickle(
        fn=filename, folder=folder, subfolder_path=subfolder,
        decompress=decompress, verbose=verbose,
        locking=True
      )

      return obj

    def diskapi_load_pickle_from_data(
        self, filename : str, subfolder: str = None,
        decompress: bool = False, verbose: bool = True
    ):
      """
      Shortcut to _diskapi_load_pickle.
      """
      return self._diskapi_load_pickle(
        filename=filename, folder='data', subfolder=subfolder,
        decompress=decompress, verbose=verbose
      )

    def diskapi_load_pickle_from_models(
        self, filename: str, subfolder: str = None,
        decompress: bool = False, verbose: bool = True
    ):
      """
      Shortcut to _diskapi_load_pickle.
      """
      return self._diskapi_load_pickle(
        filename=filename, folder='models', subfolder=subfolder,
        decompress=decompress, verbose=verbose
      )

    def diskapi_load_pickle_from_output(
        self, filename: str, subfolder: str = None,
        decompress: bool = False, verbose: bool = True
    ):
      """
      Shortcut to _diskapi_load_pickle.
      """
      return self._diskapi_load_pickle(
        filename=filename, folder='output', subfolder=subfolder,
        decompress=decompress, verbose=verbose
      )
  # endif PICKLE

  # JSON serialization section
  if True:
    def _diskapi_save_json(self, dct, filename : str, folder : str, indent : bool = True):
      """
      Parameters:
      -----------
      dct: dict or list, mandatory
        A dictionary or list like object that can be saved as json

      filename: str, mandatory
        Relative path to `folder` (in local cache), where the dictionary should be saved

      folder: str, mandatory
        The folder in local cache
        Possible values: 'data', 'output', 'models'

      indent: bool, optional
        Flag that controls the indentation in the output file
        The default value is False

      Returns:
      --------
      str
        full_path to the saved json
      """
      assert_folder(folder)

      full_path = self.log.thread_safe_save(
        datafile=filename,
        data_json=dct,
        folder=folder,
        indent=indent,
        locking=True
      )

      return full_path

    def diskapi_save_json_to_data(self, dct, filename : str, indent : bool = True):
      """
      Shortcut to _diskapi_save_json
      """
      return self._diskapi_save_json(dct=dct, filename=filename, folder='data', indent=indent)

    def diskapi_save_json_to_models(self, dct, filename : str, indent : bool = True):
      """
      Shortcut to _diskapi_save_json
      """
      return self._diskapi_save_json(dct=dct, filename=filename, folder='models', indent=indent)

    def diskapi_save_json_to_output(self, dct, filename : str, indent : bool = True):
      """
      Shortcut to _diskapi_save_json
      """
      return self._diskapi_save_json(dct=dct, filename=filename, folder='output', indent=indent)

    def _diskapi_load_json(self, filename : str, folder : str, verbose : bool = True):
      """
      Parameters:
      -----------
      filename: str, mandatory
        Relative path to `folder` (in local cache), from where a json should be loaded

      folder: str, mandatory
        The folder in local cache
        Possible values: 'data', 'output', 'models'

      verbose: bool, optional
        Controls logging when loading the json
        The default value is True

      Returns:
      --------
      dict
      """
      assert_folder(folder)

      dct = self.log.load_json(
        fname=filename, folder=folder, numeric_keys=True,
        locking=True, verbose=verbose
      )

      return dct

    def diskapi_load_json_from_data(self, filename : str, verbose : bool = True):
      """
      Shortcut to _diskapi_load_json.
      """
      return self._diskapi_load_json(filename=filename, folder='data', verbose=verbose)

    def diskapi_load_json_from_models(self, filename : str, verbose : bool = True):
      """
      Shortcut to _diskapi_load_json.
      """
      return self._diskapi_load_json(filename=filename, folder='models', verbose=verbose)

    def diskapi_load_json_from_output(self, filename : str, verbose : bool = True):
      """
      Shortcut to _diskapi_load_json.
      """
      return self._diskapi_load_json(filename=filename, folder='output', verbose=verbose)
  # endif

  # Video serialization section
  if True:
    def _diskapi_create_video_file(self, filename : str, folder : str, fps : int, str_codec : str,
                                   frame_size : Tuple[int,int], universal_codec : str = 'XVID'):
      """
      Parameters:
      -----------
      filename: str, mandatory
        Relative path to `folder` (in local cache), where the video file should be create

      folder: str, mandatory
        The folder in local cache
        Possible values: 'data', 'output', 'models'

      fps : int, mandatory
        fps of the video

      str_codec: str, mandatory
        which codec to be used
        ["H264", "h264", "mp4v", "XVID" , "MJPG", "DIVX", "FMP4"]

      frame_size: Tuple[int, int], mandatory
        H, W of each frame in the video

      universal_codec: str, optional
        codec to fallback on it if the chosen `str_codec` is not installed
        applies for all codecs except "H264"!
        The default value is "XVID"

      Returns:
      --------
      _:
        a handler to the video that can be used in `diskapi_write_video_frame`

      str:
        full_path to the video file
      """
      assert_folder(folder)
      video_path = os.path.join(self.log.get_target_folder(folder), filename)
      os.makedirs(os.path.split(video_path)[0], exist_ok=True)

      handler = None
      if str_codec.upper() == 'H264':
        self.P("Creating '{}' ffmpeg output movie '...{}' using video FPS: {}".format(
          str_codec, video_path[-20:], fps),
          color='y'
        )
        handler = FFmpegWriter(
          filename=video_path,
          fps=fps,
          frameSize=frame_size,
          log=self.log,
        )
      else:
        self.P("Creating cv2 output movie '...{}' using video FPS: {} and codec '{}'".format(
          video_path[-20:], fps, str_codec),
          color='y'
        )
        fourcc = cv2.VideoWriter_fourcc(*str_codec)

        handler = cv2.VideoWriter(
          filename=video_path,
          fourcc=fourcc,
          fps=fps,
          frameSize=frame_size,
        )

        if not handler.isOpened():
          msg = "Failed to create output movie '{}' using codec '{}'".format(video_path, str_codec)
          self._create_notification(
            notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
            msg=msg,
          )
          self.P(msg, color='r')
          self.P("  Creating output movie '...{}' using video codec '{}'".format(video_path[-20:], universal_codec), color='m')
          fourcc = cv2.VideoWriter_fourcc(*universal_codec)
          handler = cv2.VideoWriter(
            filename=video_path,
            fourcc=fourcc,
            fps=fps,
            frameSize=frame_size,
          )
          # if still doesn't work then...
          if not handler.isOpened():
            msg = "CRITICAL: Failed to create output movie '{}' using codec '{}'".format(video_path, universal_codec)
            raise ValueError(msg)
          # end critical error
        # end fail with given codec
      # end ffmpeg-vs-cv2

      if handler.isOpened():
        self.P("  Opened video handler for '{}' with codec '{}'".format(video_path, str_codec), color='g')

      return handler, video_path


    def diskapi_create_video_file_to_data(self, filename : str, fps : int, str_codec : str,
                                          frame_size : Tuple[int,int], universal_codec : str = 'XVID'):
      """
      Shortcut to `_diskapi_create_video_file`
      """
      return self._diskapi_create_video_file(
        filename=filename, folder='data',
        fps=fps, str_codec=str_codec,
        frame_size=frame_size, universal_codec=universal_codec
      )

    def diskapi_create_video_file_to_models(self, filename : str, fps : int, str_codec : str,
                                            frame_size : Tuple[int,int], universal_codec : str = 'XVID'):
      """
      Shortcut to `_diskapi_create_video_file`
      """
      return self._diskapi_create_video_file(
        filename=filename, folder='models',
        fps=fps, str_codec=str_codec,
        frame_size=frame_size, universal_codec=universal_codec
      )

    def diskapi_create_video_file_to_output(self, filename : str, fps : int, str_codec : str,
                                            frame_size : Tuple[int,int], universal_codec : str = 'XVID'):
      """
      Shortcut to `_diskapi_create_video_file`
      """
      return self._diskapi_create_video_file(
        filename=filename, folder='output',
        fps=fps, str_codec=str_codec,
        frame_size=frame_size, universal_codec=universal_codec
      )

    def diskapi_write_video_frame(self, handler, frame):
      """
      Parameters:
      -----------
      handler: _, mandatory
        the handler returned by `diskapi_create_video_file`

      frame: np.ndarray, mandatory
        the frame to be written in the video file.
        Must have the the same H,W specified in `diskapi_create_video_file`
      """
      handler.write(frame)
      return
  # endif

  # Dataset save section
  if True:
    def _get_file_path(self, folder: str, subdir: str, filename: str, extension: str):
      """
      Method for getting the full path to a file in local cache.
      Parameters
      ----------
      folder - string, the folder in local cache
      subdir - string, the subfolder in local cache
      filename - string, the name of the file
      extension - string, the extension of the file

      Returns
      -------
      string, the full path to the file
      """
      save_dir = self.log.get_target_folder(folder)
      if subdir is not None and len(subdir) > 0:
        save_dir = os.path.join(save_dir, subdir)
      if extension is not None and len(extension) > 0:
        if not filename.endswith(f'.{extension}'):
          filename = f'{filename}.{extension}'
      # endif extension is not None and len(extension) > 0
      return os.path.join(save_dir, filename)

    def _diskapi_save_image(self, image, filename: str, extension: str, folder: str, subdir: str):
      """
      Method for saving an image to local cache.
      Parameters
      ----------
      image - np.ndarray, the image to be saved
      filename - string, the name of the file
      extension - string, the extension of the file
      folder - string, the folder in local cache
      subdir - string, the subfolder in local cache

      Returns
      -------
      bool, True if the image was saved successfully, False otherwise
      """
      assert_folder(folder)
      save_path = self._get_file_path(
        folder=folder,
        subdir=subdir,
        filename=filename,
        extension=extension
      )
      os.makedirs(os.path.dirname(save_path), exist_ok=True)
      return cv2.imwrite(img=image[:, :, ::-1], filename=save_path)  # TODO: use PIL instead of opencv

    def diskapi_load_image(self, filename: str, folder: str, subdir: str):
      """
      Method for loading an image from local cache.
      Parameters
      ----------
      filename - string, the name of the file
      folder - string, the folder in local cache
      subdir - string, the subfolder in local cache

      Returns
      -------
      np.ndarray, the loaded image
      """
      assert_folder(folder)
      load_path = self._get_file_path(
        folder=folder,
        subdir=subdir,
        filename=filename,
        extension=''
      )
      if not os.path.exists(load_path):
        self.P(f'File {load_path} does not exist')
        return None
      return cv2.imread(load_path)[:, :, ::-1]  # TODO: use PIL instead of opencv

    def diskapi_save_image_output(self, image, filename: str, subdir: str, extension: str = 'jpg'):
      """
      Shortcut to _diskapi_save_image.
      Parameters
      ----------
      image - np.ndarray, the image to be saved
      filename - string, the name of the file
      subdir - string, the subfolder in local cache
      extension - string, the extension of the file

      Returns
      -------
      bool, True if the image was saved successfully, False otherwise
      """
      return self._diskapi_save_image(
        image=image,
        filename=filename,
        folder='output',
        subdir=subdir,
        extension=extension
      )

    def _diskapi_save_file(self, data: str or list, filename: str, folder: str, subdir: str, extension: str):
      """
      Method for saving a file to local cache.
      Parameters
      ----------
      data - string or list, the data to be saved
      filename - string, the name of the file
      folder - string, the folder in local cache
      subdir - string, the subfolder in local cache
      extension - string, the extension of the file

      Returns
      -------
      bool, True if the file was saved successfully, False otherwise
      """
      try:
        assert_folder(folder)
        save_path = self._get_file_path(
          folder=folder,
          subdir=subdir,
          filename=filename,
          extension=extension
        )
        if isinstance(data, list):
          data = '\n'.join([x for x in data])
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, 'w') as out:
          out.write(data)
      except Exception as e:
        self.P(f'Failed to save file {filename} to {folder} with error: {e}')
        return False
      return True

    def diskapi_save_file_output(self, data: str or list, filename: str, subdir: str = '', extension: str = ''):
      """
      Shortcut to _diskapi_save_file.
      Parameters
      ----------
      data - string or list, the data to be saved
      filename - string, the name of the file
      subdir - string, the subfolder in local cache
      extension - string, the extension of the file

      Returns
      -------
      bool, True if the file was saved successfully, False otherwise
      """
      return self._diskapi_save_file(
        data=data,
        filename=filename,
        folder='output',
        subdir=subdir,
        extension=extension
      )
  # endif

  # Zipfile section
  if True:
    def diskapi_zip_dir(self, dir_path, zip_path=None, include_dir=True):
      """
      Zip the contents of an entire folder (with that folder included).
      Parameters
      ----------
      dir_path - string, path of directory to zip
      zip_path - string, path of the output zip file. If None, zip_path will be dir_path + ".zip"
      include_dir - bool, whether to include the directory itself in the zip file

      Returns
      -------
      string, the path to the zipped directory
      """
      if zip_path is None:
        zip_path = dir_path + '.zip'
      self.P(f'Archiving {dir_path} to {zip_path}')
      ziph = zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED)
      for root, dirs, files in os.walk(dir_path):
        for file in files:
          file_root = os.path.join(dir_path, '..') if include_dir else dir_path
          ziph.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), file_root))
        # endfor files
      # endfor os.walk
      ziph.close()
      return zip_path

    def diskapi_unzip_dir(self, zip_path, dir_path=None):
      """
      Unzip a file into a given directory.
      Parameters
      ----------
      zip_path - string, path to .zip file
      dir_path - string, path to directory into which to unzip the input .zip file

      Returns
      -------
      string, the path to the unzipped directory
      """
      if dir_path is None:
        dir_path, ext = os.path.splitext(zip_path)
      self.P(f'Unzipping {zip_path} to {dir_path}')
      with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(dir_path)
      return dir_path
  # endif

  # Delete files/directories section
  if True:
    def diskapi_delete_file(self, file_path):
      """
      Delete a file from disk if safe.
      Parameters
      ----------
      file_path - string, path to the file to be deleted

      Returns
      -------

      """
      if self.is_path_safe(file_path):
        try:
          self.P(f'Trying to delete safe file {file_path}')
          os.remove(file_path)
          self.P(f'Deleted safe file from {file_path}')
        except Exception as e:
          self.P(e)
        # end try-except
      else:
        self.P(f'Provided unsafe file path ({file_path}) for deletion. Nothing happened.')
      # endif path is safe
      return

    def diskapi_delete_directory(self, dir_path):
      """
      Delete a directory from disk if safe.
      Parameters
      ----------
      dir_path - string, path to the directory to be deleted

      Returns
      -------

      """
      if self.is_path_safe(dir_path):
        try:
          self.P(f'Trying to delete safe directory {dir_path}')
          shutil.rmtree(dir_path)
          self.P(f'Deleted safe directory from {dir_path}')
        except Exception as e:
          self.P(e)
        # end try-except
      else:
        self.P(f'Provided unsafe directory path ({dir_path}) for deletion. Nothing happened.')
      # endif path is safe
      return
  # endif

  # Copy files section
  if True:
    def diskapi_copy_file(self, src_path, dst_path):
      """
      Copy a file from src to dst if safe.
      Parameters
      ----------
      src_path - string, path to the source file
      dst_path - string, path to the destination file

      Returns
      -------

      """
      if self.is_path_safe(src_path) and self.is_path_safe(dst_path):
        try:
          self.P(f'Trying to copy safe file from {src_path} to {dst_path}')
          dst_dir = os.path.dirname(dst_path)
          os.makedirs(dst_dir, exist_ok=True)
          shutil.copy2(src_path, dst_path)
          self.P(f'Copied safe file from {src_path} to {dst_path}')
        except Exception as e:
          self.P(e)
        # end try-except
      else:
        self.P(f'Provided unsafe file paths for copying. Nothing happened.')
      # endif paths are safe
      return
  # endif


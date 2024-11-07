"""
TODO: @Bleo use this and maybe "takeover"
"""

import naeural_core.constants as ct
import os

FN_PERSISTENT_DB = 'db_upload_mixin.pickle'

class _UploadMixin(object):
  def __init__(self):
    self._file_system_manager = None
    self.__um_initialized = False
    self.__uploaded_files = None

    super(_UploadMixin, self).__init__()
    return

  def _maybe_init_file_system_manager(self):
    if not self.__um_initialized:
      self._file_system_manager = self.global_shmem['file_system_manager']
      self.__um_initialized = True
      self.__uploaded_files = self.log.load_pickle_from_data(FN_PERSISTENT_DB, verbose=False) or set()
    #endif
    return

  def _save_db(self):
    self.log.save_pickle_to_data(self.__uploaded_files, FN_PERSISTENT_DB, verbose=False)
    return

  def upload_file(self, file_path, target_path, force_upload=False, save_db=False, verbose=0, **kwargs):
    self._maybe_init_file_system_manager()
    if self._file_system_manager is None:
      return None, None

    bool_new_file = file_path not in self.__uploaded_files
    # replace `\` with `/` on Windows, since this path is likely created with os.path.join
    target_path = target_path.replace(os.sep, '/')

    url = None
    if force_upload or bool_new_file:

      dct_upload_config = self.config_data.get(ct.IPFS.UPLOAD_CONFIG, {})
      kwargs = {**kwargs, **dct_upload_config}
      self.P("Uploading local file '{}' to remote '{}' {}...".format(
        file_path, target_path, "with overrides: {}".format(kwargs) if len(kwargs) >0 else '')
      )
      url = self._file_system_manager.upload(file_path, target_path, **kwargs)
    #endif

    if url is not None:
      if verbose >= 1:
        self.P("  Successfully uploaded.")
      self.__uploaded_files.add(file_path)
    #endif

    if save_db:
      self._save_db()

    return url, bool_new_file

  def upload_folder(self, folder_path, target_path, force_upload=False, **kwargs):
    # TODO: add include_subfolders and call this method from upload_output
    self._maybe_init_file_system_manager()
    if self._file_system_manager is None:
      return None

    urls = []
    _msg = "Uploading local folder '{}' to remote '{}' ...".format(folder_path, target_path)
    if force_upload:
      _msg += ' [FORCE]'
    self.P(_msg)

    files = os.listdir(folder_path)
    nr_old = 0
    for fn in files:
      url, bool_new_file = self.upload_file(
        file_path=os.path.join(folder_path, fn),
        target_path=os.path.join(target_path, fn),
        force_upload=force_upload,
        save_db=False,
        verbose=1,
        **kwargs
      )

      nr_old += not bool_new_file

      if url:
        urls.append(url)
    #endfor

    self.P("OK:{} old:{} total:{} files".format(len(urls), nr_old, len(files)))
    self._save_db()
    return urls

  def upload_logs(self, target_path, force_upload=False):
    if self._file_system_manager is None:
      return None

    urls = self.upload_folder(
      folder_path=self.log.get_target_folder('logs'),
      target_path=target_path,
      force_upload=force_upload
    )

    return urls

  def upload_output(self, target_path, force_upload=False):
    if self._file_system_manager is None:
      return None

    urls = []
    nr_chars_base_folder = len(self.log.get_base_folder())

    _msg = "Recursively uploading local cache output folder to remote '{}' ...".format(target_path)
    if force_upload:
      _msg += ' [FORCE]'
    self.P(_msg)

    nr_old = 0
    nr_total_files = 0
    for root, dirs, files in os.walk(self.log.get_target_folder('output')):
      nr_total_files += len(files)
      for file in files:
        url, bool_new_file = self.upload_file(
          file_path=os.path.join(root, file),
          target_path=os.path.join(
            target_path,
            '{}'.format(os.sep).join(root[nr_chars_base_folder+1:].split(os.sep)[1:]),
            file
          ),
          force_upload=force_upload,
          save_db=False,
          verbose=1
        )

        nr_old += not bool_new_file

        if url:
          urls.append(url)
      #endfor
    #endfor

    self.P("OK:{} old:{} total:{} files".format(len(urls), nr_old, nr_total_files))
    self._save_db()
    return urls


if __name__ == '__main__':

  from naeural_core import DecentrAIObject

  class Base(DecentrAIObject):
    def __init__(self, log, **kwargs):
      aaaa = 0
      super(Base, self).__init__(log=log, **kwargs)
      return

  class A(Base, _UploadMixin):
    def __init__(self, log, **kwargs):
      self._config_upload = {
        "TYPE" : "dropbox",
        "CONFIG_UPLOADER" : {
            "ACCESS_TOKEN": "4fohwiv544MAAAAAAAAAARvKOS34PxaMznuhdDZvTrT00zK1Sl9-W2TZgcUFbv9w"
        }
      }
      super(A, self).__init__(log=log, **kwargs)
      return


  from naeural_core import Logger

  log = Logger(lib_name='DBX', base_folder='.', app_folder='_local_cache', TF_KERAS=False)

  a = A(log=log)

  a.upload_output(target_path='_TEST_OUTPUT')


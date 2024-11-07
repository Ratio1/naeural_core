import shutil
import os

from functools import partial

from naeural_core import DecentrAIObject

class BaseDataBinarization(DecentrAIObject):

  def __init__(self, log,
               prefix=None,
               past_binarized_src_full_path=None,
               verbose=True,
               **kwargs):
    super(BaseDataBinarization, self).__init__(
      log=log,
      prefix_log='[DATAB]',
      **kwargs
    )
    self.Pnp = partial(self.log.P, noprefix=True)
    self.prefix = prefix
    self.past_binarized_src_full_path = past_binarized_src_full_path
    self.verbose = verbose

    self._setup_dir(prefix=self.prefix)
    self._move_past_binarized_src_to_prefix()
    return

  @property
  def target_name(self):
    return '_data'

  def get_dir(self, prefix=None):
    if not prefix:
      prefix = self.log.file_prefix

    fld = os.path.join(
      self.log.get_target_folder(self.target_name),
      prefix
    )

    return fld

  def _move_past_binarized_src_to_prefix(self):
    if self.past_binarized_src_full_path is None:
      return

    assert os.path.exists(self.past_binarized_src_full_path)

    # it is a full path folder, outside the logger's subtree, so we move it into prefix
    shutil.copytree(
      src=self.past_binarized_src_full_path,
      dst=self.get_dir(self.prefix),
      dirs_exist_ok=True
    )
    self.log.P("Copied data from source '{}' to logger's _output subfolder".format(self.past_binarized_src_full_path))

    return

  def _setup_dir(self, prefix=None):
    if self.verbose:
      P = self.P
    else:
      P = lambda x: x

    if not prefix:
      prefix = self.log.file_prefix

    fld = self.get_dir(prefix=prefix)

    if not os.path.exists(fld):
      os.makedirs(fld)
      P("Created directory '{}' for saving dataset caches.".format(fld))
    else:
      P("Directory '{}' for dataset caches already created.".format(fld))
    return fld
  # enddef

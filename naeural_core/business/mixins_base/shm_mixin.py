from naeural_core import constants as ct

from naeural_core.utils.shm_manager import SharedMemoryManager

class _ShmMixin(object):
  def __init__(self):
    self.plugins_shared_mem = None
    super(_ShmMixin, self).__init__()
    return

  def init_plugins_shared_memory(self, dct_global):
    self.P("Initialising shared memory...", color='b')
    self.plugins_shared_mem = SharedMemoryManager(
      dct_shared=dct_global,
      stream=self._stream_id, 
      plugin=self._signature, 
      instance=self.cfg_instance_id,
      category=ct.SHMEM.BUSINESS,
      linked_instances=self.cfg_linked_instances,
      log=self.log,
      )
    return


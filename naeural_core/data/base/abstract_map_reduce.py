import abc
from functools import partial
from naeural_core.data.base.base_decentrai_connector import BaseDecentraiConnectorDataCapture

_CONFIG = {
  **BaseDecentraiConnectorDataCapture.CONFIG,

  # this is not a live feed usually
  'LIVE_FEED': False,

  # keepalive DCT must be "zombie" until is archived by consumer
  'RECONNECTABLE': 'KEEPALIVE',

  'VALIDATION_RULES': {
    **BaseDecentraiConnectorDataCapture.CONFIG['VALIDATION_RULES'],
  },
}


class AbstractMapReduceDataCapture(BaseDecentraiConnectorDataCapture):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    self._workers = None
    self._finished_map = False
    self._heuristic_cap_resolution = 1
    self._try_again_counts = [0]
    super(AbstractMapReduceDataCapture, self).__init__(**kwargs)
    return

  def startup(self):
    super().startup()
    if False:
      # initial method disabled and left here for future reference
      for method_name, func in self.log.get_class_methods(self.network_monitor.__class__, include_parent=False):
        if 'network_' in method_name:
          # make aliases to the network_monitor's methods
          setattr(self, method_name, partial(func, self=self.network_monitor))
    # endif disable hackish approach

    self._phase = 'map'
    self._metadata.update(phase=self._phase, workers=None, workers_stream_name=None)
    return

  @property
  def cfg_workers(self):
    return self.cfg_stream_config_metadata.get('WORKERS', None)

  @property
  def cfg_nr_workers(self):
    return self.cfg_stream_config_metadata.get('NR_WORKERS', None)

  @property
  def nr_chunks(self):
    if self.cfg_workers is not None:
      return len(self.cfg_workers)

    return self.cfg_nr_workers

  @property
  def workers_stream_name(self):
    return {k: self.cfg_name + '_w_{}'.format(i) for i, k in enumerate(self._workers)}

  @property
  def formatter(self):
    io_formatter_manager = self.shmem.get('io_formatter_manager', None)
    if io_formatter_manager is not None:
      return io_formatter_manager.get_formatter()[0]
    return

  @property
  def network_monitor(self):
    return self.shmem['network_monitor']

  @property
  def netmon(self):
    return self.network_monitor

  @property
  def net_mon(self):
    return self.network_monitor

  def _find_workers_in_network(self):
    if self.cfg_workers is not None:
      self._workers = self.cfg_workers
    else:
      self._workers = self.netmon.network_top_n_avail_nodes(
        n=int(self.cfg_nr_workers),
        min_gpu_capability=10,
        permit_less=False
      )
    # endif
    self._metadata.workers_stream_name = self.workers_stream_name
    self._metadata.workers = self._workers
    return

  def _maybe_switch_to_reduce(self):
    if not self._finished_map:
      return

    if len(self._workers) == 0:
      cap_resolutions = [0.2, 0.1, 0.02, 0.01]
      l = len(self._try_again_counts)
      if self._try_again_counts[l - 1] >= 10 and l < len(cap_resolutions):
        self._try_again_counts.append(0)

      self._try_again_counts[l - 1] += 1
      self._heuristic_cap_resolution = cap_resolutions[l - 1]
      self.P("Did not find desired nr workers. Trying again in {}s ...".format(
        1 / self._heuristic_cap_resolution), color='y')
      return

    self.P("Successfully entering in reduce phase", color='g')
    self._phase = 'reduce'
    self._metadata.phase = self._phase
    self._heuristic_cap_resolution = None
    return

  def _init(self):
    self._init_map()
    self._init_reduce()

  def _release(self):
    self._release_map()
    self._release_reduce()

  def _maybe_reconnect(self):
    self._metadata.phase = self._phase
    if self._phase == 'map':
      self._maybe_reconnect_map()
    elif self._phase == 'reduce':
      self._maybe_reconnect_reduce()
    return

  def _run_data_aquisition_step(self):
    # TODO:
    #  if this stream is restarted maybe it was already in progress and it is already
    #  receiving data from workers so do NOT restart from map and try to catch the progress
    #  if this is unacceptable then it its the biz plugin decision
    if self._phase == 'map':
      self._find_workers_in_network()
      self._run_data_aquisition_step_map()
      self._maybe_switch_to_reduce()
    elif self._phase == 'reduce':
      self._run_data_aquisition_step_reduce()
    return

  @abc.abstractmethod
  def _init_map(self):
    raise NotImplementedError()

  @abc.abstractmethod
  def _release_map(self):
    raise NotImplementedError()

  @abc.abstractmethod
  def _maybe_reconnect_map(self):
    raise NotImplementedError()

  @abc.abstractmethod
  def _run_data_aquisition_step_map(self):
    """
    Must set `self._finished_map = True` when full map process is finished
    """
    raise NotImplementedError()

  def _init_reduce(self):
    super(AbstractMapReduceDataCapture, self)._init()

  def _release_reduce(self):
    super(AbstractMapReduceDataCapture, self)._release()

  def _maybe_reconnect_reduce(self):
    super(AbstractMapReduceDataCapture, self)._maybe_reconnect()
    return

  def _run_data_aquisition_step_reduce(self):
    if self.has_finished_acquisition:
      self._metadata.received_finish_command = True
      self._add_struct_data_input({'has_finished_acquisition': True})  # TODO: maybe delete this
    else:
      # in this setting we always get one payload from queue and send it downstream
      # so the map-reduce plugin has to take care of a message from just one worker
      # at a time and thus simplifying the process. This can happen at a high iteration
      # per second count.
      super(AbstractMapReduceDataCapture, self)._run_data_aquisition_step()
    # endif has_finished_acquisition or normal acquisition
    return

  def interpret_message(self, message):
    if message['TYPE'] != "payload":
      return
    message = message["DATA"]

    is_worker = message.get('EE_ID', None) in self._workers
    is_good_stream = message.get('STREAM', None) in list(self.workers_stream_name.values())
    is_good_initiator = message.get('INITIATOR_ID', None) == self._device_id
    is_good_session = message.get('SESSION_ID', None) == self.log.session_id  # Must clarify this !!!
    if is_worker and is_good_stream and is_good_initiator and is_good_session:
      return message

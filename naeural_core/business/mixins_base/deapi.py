class _DeAPIMixin(object):
  def __init__(self):
    self._commands = []
    super(_DeAPIMixin, self).__init__()
    return

  def deapi_get_wokers(self, n_workers):
    return self.netmon.network_top_n_avail_nodes(n_workers)

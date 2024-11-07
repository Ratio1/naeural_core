from naeural_core.data_structures import GeneralPayload

class _CountingTemplateMixin(object):

  def __init__(self, **kwargs):
    super(_CountingTemplateMixin, self).__init__(**kwargs)
    return

  def _create_payload(self, nr_objects=None, object_type=None, **kwargs):
    if object_type is None:
      object_type = self.cfg_object_type

    if isinstance(object_type, list):
      if len(object_type) == 0:
        object_type = object_type[0]

    payload = GeneralPayload(
      owner=self,
      _v_nr_objects=nr_objects,
      _v_object_type=object_type,
      plugin_category='counting',
      **kwargs
    )
    return payload
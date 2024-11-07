from naeural_core.manager import Manager
from naeural_core import constants as ct

class TestingManager(Manager):

  def __init__(self, log, owner, **kwargs):
    self._dct_testers = None
    self._tester = None
    self.owner = owner
    super(TestingManager, self).__init__(log=log, prefix_log='[TSTM]', **kwargs)
    return

  def startup(self):
    super().startup()
    self._dct_testers = self._dct_subalterns
    return

  @property
  def tester(self):
    return self._tester

  def _get_plugin_class(self, name):
    _module_name, _class_name, _class_def, _class_config = self._get_module_name_and_class(
      locations=ct.PLUGIN_SEARCH.LOC_TESTING_FRAMEWORK_TESTING_PLUGINS,
      name=name,
      suffix=ct.PLUGIN_SEARCH.SUFFIX_TESTING_FRAMEWORK_TESTING_PLUGINS,
      safety_check=True, # perform safety check     
      
    )
    return _class_def

  def create_tester(self, name, config):
    _cls = self._get_plugin_class(name)
    tester = _cls(log=self.log, config=config, owner=self.owner)
    self._dct_testers[name] = tester
    self._tester = tester
    return

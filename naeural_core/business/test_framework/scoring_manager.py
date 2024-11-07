from naeural_core.manager import Manager
from naeural_core import constants as ct


class ScoringManager(Manager):

  def __init__(self, log, owner, **kwargs):
    self._dct_scorers = None
    self.owner = owner
    super(ScoringManager, self).__init__(log=log, prefix_log='[SCOM]', **kwargs)
    return

  def startup(self):
    super().startup()
    self._dct_scorers = self._dct_subalterns
    return

  def _get_plugin_class(self, name):
    _module_name, _class_name, _class_def, _class_config = self._get_module_name_and_class(
      locations=ct.PLUGIN_SEARCH.LOC_TESTING_FRAMEWORK_SCORING_PLUGINS,
      name=name,
      suffix=ct.PLUGIN_SEARCH.SUFFIX_TESTING_FRAMEWORK_SCORING_PLUGINS,
      safety_check=True,  # perform safety check
    )
    return _class_def

  def maybe_create_scorer(self, name, y_true_src):
    if y_true_src is None:
      return

    identification = name + '_' + self.log.hash_object(y_true_src, size=4)
    if identification in self._dct_scorers:
      return self._dct_scorers[identification]

    _cls = self._get_plugin_class(name)

    scorer = _cls(
      log=self.log,
      y_true_src=y_true_src,
      owner=self.owner
    )

    self._dct_scorers[identification] = scorer
    return scorer

  def score(self, y_true_src, payload, config):
    name = payload['TESTER_NAME']
    scorer_obj = self.maybe_create_scorer(
      name=name,
      y_true_src=y_true_src,
    )

    if scorer_obj is None:
      return

    s, time_penalization = scorer_obj.score(payload, config)

    if type(s) == dict:
      total_score = s['ACCURACY'] * scorer_obj.max_score + time_penalization
    else:
      total_score = s + time_penalization

    dct_score = {
      'FUNCTIONALITY_SCORE': s,
      'TIME_PENALIZATION': time_penalization,
      'TOTAL_SCORE': total_score,
      'MAX_SCORE': scorer_obj.max_score
    }

    return dct_score

  def get_y_true(self, y_true_src, name):
    scorer_obj = self.maybe_create_scorer(
      name=name,
      y_true_src=y_true_src,
    )

    if scorer_obj is None:
      return

    return scorer_obj.y_true


if __name__ == '__main__':
  from naeural_core import Logger
  log = Logger(
    lib_name='EE',
    base_folder='.',
    app_folder='_local_cache',
    config_file='config_startup.txt',
    max_lines=3000,
    TF_KERAS=False
  )
  manager = ScoringManager(log=log, owner=None)
  locations = ['plugins.serving.pipelines.architectures']
  signature = 'custom'
  suffix = 'ModelFactory'
  tmp = manager._get_module_name_and_class(
    locations=locations,
    name=signature,
    suffix=suffix,
    safety_check=False,  # perform safety check
  )

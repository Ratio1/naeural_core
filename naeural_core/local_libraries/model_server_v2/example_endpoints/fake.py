

from naeural_core.local_libraries.model_server_v2 import FlaskWorker

_CONFIG = {
  'ADD' : 7
}

class FakeWorker(FlaskWorker):

  """
  Example implementation of a worker

  Obs: as the worker runs on thread, then no prints are allowed; use `_create_notification` and see all the notifications
       when calling /notifications of the server.
  """

  def __init__(self, **kwargs):
    super(FakeWorker, self).__init__(prefix_log='[FAKEW]', **kwargs)
    return


  ### defined properies for worker parameters;
  @property
  def cfg_add(self):
    ### this one is taken directly from default configuration `_CONFIG`
    return self.config_worker['ADD']

  @property
  def cfg_minus(self):
    ### this one can come in upstream configuraion
    return self.config_worker.get('MINUS', None)

  def _load_model(self):
    ### see docstring in parent
    ### abstract method implementation: no model to be loaded
    return

  def _pre_process(self, inputs):
    ### see docstring in parent
    ### abstract method implementation: parses the request inputs and keep the value for 'INPUT_VALUE'
    if 'INPUT_VALUE' not in inputs.keys():
      raise ValueError('INPUT_VALUE should be defined in inputs')

    s = inputs['INPUT_VALUE']
    return s

  def _predict(self, prep_inputs):
    ### see docstring in parent
    ### abstract method implementation: "in-memory model :)" that adds and subtracts.
    self._create_notification(
      notif='log',
      msg='Predicting on usr_input: {}'.format(prep_inputs)
    )

    if not self.cfg_minus:
      res = '{}+{}={} PREDICTED'.format(prep_inputs, self.cfg_add, int(prep_inputs) + self.cfg_add)
    else:
      res = '{}+{}-{}={} PREDICTED'.format(prep_inputs, self.cfg_add, self.cfg_minus, int(prep_inputs) + self.cfg_add - self.cfg_minus)
    return res

  def _post_process(self, pred):
    ### see docstring in parent
    ### abstract method implementation: packs the endpoint answer that will be jsonified
    return {'output_value': pred}

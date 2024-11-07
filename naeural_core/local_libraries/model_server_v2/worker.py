

import abc
import base64
import json
import traceback

from naeural_core import Logger
from naeural_client import BaseDecentrAIObject
from naeural_core.local_libraries import _ConfigHandlerMixin

class FlaskWorker(BaseDecentrAIObject, _ConfigHandlerMixin):

  """
  Base class for any worker / endpoint business logic
  """

  def __init__(self, log : Logger,
               default_config,
               verbosity_level,
               worker_id,
               upstream_config=None,
               **kwargs):

    """
    Parameters:
    -----------
    log : Logger, mandatory

    default_config: dict, mandatory
      The default configuration of the worker.
      See `libraries.model_server_v2.server -> create_worker` to see the entire flow; it calls `_get_module_name_and_class`
      and searches for a `_CONFIG` in the module with the implementation and passes the value of `_CONFIG` as `default_config`

    verbosity_level: int, mandatory
      A threshold that controls the verbosity - can use it in any implementation

    worker_id : int, mandatory
      The id of the worker.

    upstream_config: dict, optional
      The upstream configuration that comes from a configuration file of the process; this `upstream_config` is merged with `default_config`
      in order to compute the final config
      The default is None ({})
    """

    self._default_config = default_config
    self._upstream_config_params = upstream_config or {}
    self.config_worker = None
    self._worker_id = worker_id
    self.__last_query = None

    self._verbosity_level = verbosity_level

    self._counter = None
    self.__encountered_error = None
    prefix_log = kwargs.pop('prefix_log', '[FSKWKR]')
    super(FlaskWorker, self).__init__(log=log, maxlen_notifications=1000, prefix_log=prefix_log, **kwargs)
    return

  def startup(self):
    super().startup()
    self.config_worker = self._merge_prepare_config()
    self.setup_config_and_validate(self.config_worker)
    self._load_model()
    return

  @abc.abstractmethod
  def _load_model(self):
    """
    Implement this method in sub-class - custom logic for loading the model. If the worker has no model, then implement
    it as a simple `return`.
    """
    raise NotImplementedError

  @abc.abstractmethod
  def _pre_process(self, inputs):
    """
    Implement this method in sub-class - custom logic for pre-processing the inputs that come from the user

    Parameters:
    -----------
    inputs: dict, mandatory
      The request json

    Returns:
    -------
    prep_inputs:
      Any object that will be used in `_predict` implementation
    """
    raise NotImplementedError

  @abc.abstractmethod
  def _predict(self, prep_inputs):
    """
    Implement this method in sub-class - custom logic for predict

    Parameters:
    -----------
    prep_inputs:
      Any object returned by `_pre_process`

    preds:
      Any object that will be used in `_post_process` implementation
    """
    raise NotImplementedError

  @abc.abstractmethod
  def _post_process(self, pred):
    """
    Implement this method in sub-class - custom logic for post processing the predictions
    (packing the output that goes to the end-user)

    Parameters:
    ----------
    pred:
      Any object returned by `_predict`

    answer: dict
      The answer that goes to the end-user
    """
    raise NotImplementedError

  @staticmethod
  def __err_dict(err_type, err_file, err_func, err_line, err_msg):
    return {
      'ERR_TYPE' : err_type,
      'ERR_MSG' : err_msg,
      'ERR_FILE' : err_file,
      'ERR_FUNC' : err_func,
      'ERR_LINE' : err_line
    }


  def __pre_process(self, inputs):
    try:
      prep_inputs = self._pre_process(inputs)
    except:
      err_dict = self.__err_dict(*self.log.get_error_info(return_err_val=True))
      msg = 'Exception in _pre_process:\n{}'.format(err_dict)
      self.__encountered_error = err_dict #['ERR_MSG']
      
      self._create_notification(
        notif='exception',
        msg=msg,
      )
      return

    return prep_inputs

  def __predict(self, prep_inputs):
    if prep_inputs is None:
      return

    try:
      pred = self._predict(prep_inputs)
    except:
      exc_info = traceback.format_exc()
      err_dict = self.__err_dict(*self.log.get_error_info(return_err_val=True))
      self.__encountered_error = err_dict #['ERR_MSG']
      msg = 'Exception in _predict:\n{}'.format(err_dict)
      self._create_notification(
        notif='exception',
        msg=msg
      )
      self.P("{}:\n{}".format(msg, exc_info), color='r')
      pred = None
    return pred

  def __post_process(self, pred):
    if pred is None:
      return

    try:
      answer = self._post_process(pred)
    except:
      err_dict = self.__err_dict(*self.log.get_error_info(return_err_val=True))
      self.__encountered_error = err_dict #['ERR_MSG']
      msg = 'Exception in _post_process\n{}'.format(err_dict)
      self._create_notification(
        notif='exception',
        msg=msg,
      )
      return

    return answer
  
  
  def get_last_query(self):
    return self.__last_query
  

  def execute(self, inputs, counter):
    """
    The method exposed for execution.

    Parameters:
    ----------
    inputs: dict, mandatory
      The request json

    counter: int, mandatory
      The call id

    Returns:
    --------
    answer: dict
      The answer that goes to the end-user
    """
    self._counter = counter
    self.__encountered_error = None

    base64_keys = inputs.pop('BASE64_KEYS', [])
    base64_outputs = inputs.pop('BASE64_OUTPUTS', [])
    encoding = inputs.pop('ENCODING', 'ansi')
    for k in base64_keys:
      if k in inputs:
        inputs[k] = base64.b64decode(inputs[k]).decode(encoding)
      else:
        self.P("Key {} sent in 'BASE64_KEYS' does not exist in input", color='e')
    #endfor

    self.__last_query = inputs
    
    prep_inputs = self.__pre_process(inputs)
        
    pred = self.__predict(prep_inputs)

    answer = self.__post_process(pred)

    for k in base64_outputs:
      if k in answer:
        if isinstance(answer[k], str):
          answer[k] = base64.b64encode(answer[k].encode(encoding)).decode()
        else:
          answer[k] = base64.b64encode(json.dumps(answer[k]).encode(encoding)).decode()
      else:
        self.P("Key {} sent in 'BASE64_OUTPUTS' does not exist in answer", color='e')
    #endfor

    if self.__encountered_error:
      answer = {'{}_ERROR'.format(self.__class__.__name__) : self.__encountered_error}
      self.P("Worker {}:{} execute error: {}".format(
        self.__class__.__name__, self._worker_id, self.__encountered_error), color='r'
      )

    return answer

  def _create_notification(self, notif, msg, info=None, stream_name=None, **kwargs):
    msg = (self._counter or "INIT", msg)
    super()._create_notification(notif=notif, msg=msg, info=info, stream_name=stream_name, **kwargs)
    return

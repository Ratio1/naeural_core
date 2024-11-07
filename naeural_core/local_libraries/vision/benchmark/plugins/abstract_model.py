from naeural_core import DecentrAIObject
from decentra_vision.draw_utils import DrawUtils


class AbstractModel(DecentrAIObject):
  """
  As the name indicates, this is an abstract class that will impose a template
  for future model benchmarks. 
  Each benchmarked model should inherit this class.
  """

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self.init(**kwargs)

    self._painter = DrawUtils(log=self.log)
    return

  def init(self, **kwargs):
    """
    Method used to initialize variables/methods that your model needs.
    """
    raise NotImplementedError
    return

  def load(self, **kwargs):
    """
    Loads model/graph but does not executes GPU operations like sessions or .to(CUDA)
    """
    raise NotImplementedError
    return

  def prepare(self, **kwargs):
    """
    This method will create GPU specific ops like session creation, move model to cuda (in torch), etc.

    """
    raise NotImplementedError
    return

  def _predict(self, inputs, **kwargs):
    """
    Specific method used to predict

    """
    return

  def _process_input(self, inputs, **kwargs):
    """
    Specific method used to preprocess input

    """
    raise NotImplementedError
    return

  def draw(self, path_out, inputs, preds):
    raise NotImplementedError
    return

  def predict(self, inputs, **kwargs):
    self.log.start_timer('predict')

    inputs = self._process_input(inputs=inputs, **kwargs)
    preds = self._predict(inputs)

    self.log.stop_timer('predict')
    return preds

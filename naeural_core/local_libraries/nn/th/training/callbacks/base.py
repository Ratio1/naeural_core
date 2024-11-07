import torch as th
import numpy as np
import abc
import torchvision.transforms as T
from typing import List, Tuple, Any
from naeural_core import DecentrAIObject
from naeural_core import Logger
from naeural_core.local_libraries.nn.th.trainer import ModelTrainer
from naeural_core.local_libraries.nn.th.training_utils import to_device

class TrainingCallbacks(DecentrAIObject):

  def __init__(self, log : Logger, owner : ModelTrainer,
               preprocess_before_fw_callback,
               training_device : str = 'cuda:0',
               level_analysis=1,
               dct_augmentation_plan=None,
               **kwargs):
    self._owner = owner
    self._level_analysis = level_analysis
    self._preprocess_before_fw_callback = preprocess_before_fw_callback
    self._dct_augmentation_plan = dct_augmentation_plan or {}
    self._augmentation = None

    self.training_device = training_device
    super(TrainingCallbacks, self).__init__(log=log, **kwargs)
    return

  def startup(self):
    super().startup()
    self.__name__ = self.log.name_abbreviation(self.__class__.__name__)
    self._get_augmentation()
    return

  def P(self, s, **kwargs):
    super().P(s, prefix=True, **kwargs)
    return

  @to_device('training_device')
  def _augment_input_callback(self, x, epoch=None):
    if epoch in self._dct_augmentation_plan:
      self._get_augmentation(epoch=epoch)

    return self._augmentation(x)

  def _get_augmentation(self, epoch=None):
    kwargs = {}
    if epoch is not None:
      kwargs = self._dct_augmentation_plan[epoch]

    lst_augmentations_definitions = self._lst_augmentations(**kwargs)
    lst_augmentations = [x[0](**x[1]) for x in lst_augmentations_definitions]
    self._augmentation = T.Compose(lst_augmentations)
    return

  @property
  def _model(self):
    return self._owner.model

  def _get_idx(self, lst_indexes : List[np.ndarray]) -> np.ndarray:
    idx = np.hstack(lst_indexes)
    return idx

  @abc.abstractmethod
  def _get_y(self, lst_y):
    """
    Method that concatenates batches of targets into a List[Tensor] or Tensor (depending whether the model is
    multi-output or single-output) that is provided to evaluation / testing callback.

    The forward pass is performed in batches. For each batch, the target is put in a list.
    After the full forward pass, that list (`lst_y`) should be concatenated to be afterwards used in the
    evaluation / testing callback

    Parameters
    ----------
    lst_y: List[Tensor] for single-output or List[List[Tensor]] for multi-output
      List of target for each batch.

    Returns
    -------
    y: Tensor for single-output or List[Tensor] for multi-output
      The concatenated target
    """
    raise NotImplementedError

  @abc.abstractmethod
  def _get_y_hat(self, lst_y_hat):
    """
    Method that concatenates batches of inference into a List[Tensor] or Tensor (depending whether the model is
    multi-output or single-output) that is provided to evaluation / testing callback.

    The forward pass is performed in batches. For each batch, the inference is put in a list.
    After the full forward pass, that list (`lst_y_hat`) should be concatenated to be afterwards used in the
    evaluation / testing callback

    Parameters
    ----------
    lst_y_hat: List[Tensor] for single-output or List[List[Tensor]] for multi-output
      List of inference for each batch.

    Returns
    -------
    y_hat: Tensor for single-output or List[Tensor] for multi-output
      The concatenated inference
    """
    raise NotImplementedError

  @abc.abstractmethod
  def _evaluate_callback(self,
                         epoch : int,
                         dataset_info : dict,
                         y, y_hat,
                         idx : np.ndarray,
                         key: str = 'dev') -> dict:
    """
    The evaluation method that is called during training after each epoch.

    Parameters
    ----------
    epoch: int
      At which epoch the evaluation is performed

    dataset_info: dict
      Reference to DataLoaderFactory's `self.dataset_info`

    y : Tensor for single-output or List[Tensor] for multi-output
      The target that should be evaluated with the model's output.

    y_hat: Tensor for single-output or List[Tensor] for multi-output
      The model output that should be evaluated with the target

    idx: np.ndarray
      Indexes in `dataset_info` for `y` and `y_hat`

    key: str
      Which dataset is evaluated

    Returns
    -------
    dict{str:float}
      A dictionary {metric_name: value}
    """
    raise NotImplementedError

  @abc.abstractmethod
  def _test_callback(self,
                     dataset_info : dict,
                     y, y_hat,
                     idx : np.ndarray) -> dict:
    """
    The test method that is called at the end of the training

    Parameters
    ----------

    dataset_info: dict
      Reference to DataLoaderFactory's `self.dataset_info`

    y : Tensor for single-output or List[Tensor] for multi-output
      The target that should be evaluated with the model's output.

    y_hat: Tensor for single-output or List[Tensor] for multi-output
      The model output that should be evaluated with the target

    idx: np.ndarray
      Indexes in `dataset_info` for `y` and `y_hat`

    Returns
    -------
    dict{str:float}
      A dictionary {metric_name: value}
    """
    raise NotImplementedError

  @abc.abstractmethod
  def _lst_augmentations(self, **kwargs) -> List[Tuple[Any, dict]]:
    """
    List of augmentation techniques that will be performed for the training dataset.

    This augmentation step is performed between "load preprocess" and "forward preprocess" (please see
    libraries.nn.th.trainin.data.base, methods `_lst_on_load_preprocess` and `_lst_right_before_forward_preprocess`)

    All the augmentation techniques should be wrapped in classes that expose a __call__ method.
    (please see libraries.nn.th.image_dataset_stage_preprocessor)

    All these augmentation methods are automatically packed into a th transformation (T.Compose)

    Returns:
    -------
    List[Tuple[Any, dict]]
      for each augmentation technique, a tuple with: a) the class reference and b) the class __init__ kwargs
      should be provided
    """
    raise NotImplementedError

  def _forward_and_collect(self, data_generator: th.utils.data.DataLoader, dataset_info : dict):
    with th.no_grad():
      steps_per_epoch = len(data_generator)
      iterator = iter(data_generator)
      lst_y, lst_y_hat, lst_indexes = [], [], []
      for i in range(steps_per_epoch):
        x_batch, y_batch, idx_batch = next(iterator)
        x_batch, y_batch = self._transform_at_eval_time(x_batch, y_batch)
        model_result = self._model(x_batch)
        if isinstance(model_result, th.Tensor):
          lst_y_hat.append(model_result.cpu())
          lst_y.append(y_batch.cpu())
        else:
          lst_y_hat.append([_y.cpu() for _y in model_result])
          lst_y.append([_y.cpu() for _y in y_batch])
        #endif
        lst_indexes.append(idx_batch)

        if self.log.is_main_thread:
          print(
            '\r  Forward progress: {}/{} ({:.2f}%)'.format(i + 1, steps_per_epoch, 100 * (i + 1) / steps_per_epoch),
            flush=True, end=''
          )
      #endfor
      y = self._get_y(lst_y)
      y_hat = self._get_y_hat(lst_y_hat)
      idx = self._get_idx(lst_indexes)
    #endwith
    return y, y_hat, idx


  def evaluate_callback(self, epoch : int, data_generator : th.utils.data.DataLoader, dataset_info : dict, key : str = 'dev', **kwargs):
    self._model.eval()
    self.P("START EVALUATION for: {}".format(key), color='g')
    y, y_hat, idx = self._forward_and_collect(data_generator=data_generator, dataset_info=dataset_info)
    dct_score = self._evaluate_callback(
      epoch=epoch,
      dataset_info=dataset_info,
      y=y, y_hat=y_hat, idx=idx, key=key
    )
    self.P("END EVALUATION for: {}".format(key), color='g')
    self._model.train()
    return dct_score

  def test_callback(self, data_generator : th.utils.data.DataLoader, dataset_info : dict, **kwargs):
    self._model.eval()
    self.P("START TESTING", color='g')
    y, y_hat, idx = self._forward_and_collect(data_generator=data_generator, dataset_info=dataset_info)
    dct_score = self._test_callback(
      dataset_info=dataset_info,
      y=y, y_hat=y_hat, idx=idx
    )
    self.P("END TESTING", color='g')
    self._model.train()
    return dct_score

  def dev_callback(self, data_generator : th.utils.data.DataLoader, dataset_info : dict, **kwargs):
    self._model.eval()
    self.P("START DEV TESTING", color='g')
    y, y_hat, idx = self._forward_and_collect(data_generator=data_generator, dataset_info=dataset_info)
    dct_score = self._dev_callback(
      dataset_info=dataset_info,
      y=y, y_hat=y_hat, idx=idx
    )
    self.P("END DEV TESTING", color='g')
    self._model.train()
    return dct_score


  def _transform_at_training_time(self, x, y, epoch=None):
    x = self._augment_input_callback(x, epoch=epoch)
    x, y = self._preprocess_before_fw_callback(x, y)
    return x, y

  def _transform_at_eval_time(self, x, y):
    x, y = self._preprocess_before_fw_callback(x, y)
    return x, y

  def _get_batch_lst_x_lst_y(self, batch, y_index, batch_index):
    if batch_index:
      batch = batch[:-1]

    if len(batch) == 1:
      lst_th_x = [batch[0]]
      lst_th_y = None
    else:
      lst_th_x = batch[:y_index]
      lst_th_y = batch[y_index:]
    #endif

    if len(lst_th_x) == 1:
      lst_th_x = lst_th_x[0]

    if len(lst_th_y) == 1:
      lst_th_y = lst_th_y[0]

    return self._transform_at_training_time(lst_th_x, lst_th_y, epoch=self._owner.current_epoch)

  def train_forward(self, model, lst_th_x):
    return model(lst_th_x)

  def train_on_batch_callback(self, model, optimizer, losses, batch, y_index=1, batch_index=False):
    lst_th_x, lst_th_y = self._get_batch_lst_x_lst_y(batch=batch, y_index=y_index, batch_index=batch_index)

    # maybe move data on device if default is disabled
    if isinstance(lst_th_x, (list, tuple)):
      data_device = lst_th_x[0].device
    else:
      data_device = lst_th_x.device
    #endif

    model_device = next(model.parameters()).device
    assert data_device == model_device

    th_yh = self.train_forward(model, lst_th_x)
    losses_vals = []

    if not isinstance(th_yh, (list, tuple)):
      th_yh = [th_yh]

    if not isinstance(lst_th_y, (list, tuple)):
      lst_th_y = [lst_th_y]

    if len(losses) != len(lst_th_y):
      assert len(losses) == 1

    if lst_th_y is not None:
      ### TODO: CHECK If ok
      if len(losses) == len(lst_th_y):
        for i, loss in enumerate(losses):
          losses_vals.append(losses[loss](th_yh[i], lst_th_y[i]))
      else:
        key = list(losses.keys())[0]
        losses_vals.append(losses[key](th_yh, lst_th_y))

    else:
      for i, loss in enumerate(losses):
        losses_vals.append(losses[loss](th_yh[i]))
    # endif
    ### TODO: sanity check
    th_loss = th.stack(losses_vals, dim=0).sum(dim=0)  # th.sum(*losses_vals, keepdim=True)

    optimizer.zero_grad()
    th_loss.mean().backward()
    optimizer.step()
    err = th_loss.detach().cpu().numpy()
    return err



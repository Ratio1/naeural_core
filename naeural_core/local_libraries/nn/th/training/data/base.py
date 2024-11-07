import gc
import abc
import random
import multiprocessing
import inspect

import torch as th
import torchvision.transforms as T
import numpy as np
from typing import Union, List, Tuple, Any
from naeural_core import DecentrAIObject
from naeural_core import Logger
from naeural_core.local_libraries.nn.th.training_utils import get_shallow_dataset, to_device

class IterableDataset(th.utils.data.IterableDataset):

  def __init__(self, observations : List,
               labels: List,
               is_train_dataset : bool,
               are_observations_loaded : bool,
               load_x_and_y_callback,
               **kwargs):
    super(IterableDataset, self).__init__(**kwargs)
    self._observations = observations
    self._labels = labels
    self._is_train_dataset = is_train_dataset
    self._are_observations_loaded = are_observations_loaded
    self._indexes = list(range(len(self._observations)))

    self.load_x_and_y_callback = load_x_and_y_callback
    self.startup()
    return

  def startup(self):
    return

  def _shuffle(self):
    random.shuffle(self._indexes)
    return

  def _get_worker_indexes(self):
    ## TODO: Docstrings
    worker = th.utils.data.get_worker_info()
    worker_id = worker.id if worker is not None else -1

    if worker_id != -1:
      split_size = len(self._indexes) // worker.num_workers
      worker_indexes = range(worker_id * split_size, (worker_id + 1) * split_size)
    else:
      worker_indexes = range(len(self._indexes))
    #endif
    return worker_indexes

  def __len__(self):
    return len(self._indexes)

  def update_dataset(self, **kwargs):
    return

  def __iter__(self):
    self._shuffle()
    worker_indexes = self._get_worker_indexes()

    for i in worker_indexes:
      idx = self._indexes[i]

      if not self._are_observations_loaded:
        x,y = self.load_x_and_y_callback(self._observations, self._labels, idx)
      else:
        x = self._observations[idx]
        y = self._labels[idx]
      #endif

      ret = [x, y, idx]
      # if not self._is_train_dataset:
      #   ret.append(idx)

      yield tuple(ret)
    #endfor

  def release_dataset(self):
    del self._observations
    del self._labels
    gc.collect()
    th.cuda.empty_cache()
    return

class BaseDataLoaderFactory(DecentrAIObject, abc.ABC):

  def __init__(self, log : Logger,
               batch_size : int,
               path_to_dataset : str, data_subset_name : str,
               load_observations: bool = True,
               load_device : str = 'cpu',
               training_device : str = 'cuda:0',
               files_extensions : Union[List[str], str] = None,
               num_workers : int = None,
               **kwargs):
    """
    Data loader factory. Should be initialized one for each subset (train, dev, test).

    Parameters:
    -----------
    batch_size: int
      Batch size for training time

    path_to_dataset: str
      Local path to the subset of the data.

    dataset_subset_name : str
      The name of the subset of the data.
      Could be "train", "dev" or "test"

    load_observations: bool, optional
      Flag that determines if the observations will be fully loaded to be available in-memory at training time.
      The functionality of pre-loading the data before training speeds up the training process. If the dataset is
      too big to be accomodated on gpu, it is ok to pre-load it in the RAM memory. Pre-loading it directly on the
      gpu that is responsible with the training brings the best speedup.
      The default value is True.

    load_device: str, optional
      The device where the data will be loaded if `load_observations` is True.
      For gpu devices use "cuda:<index>".
      The default value is "cpu:0"

    training_device: str, optional
      The device where the training will happen.
      For gpu devices use "cuda:<index>".
      The default value is "cpu:0"

    files_extentions: Union[List[str], str], optional
      Which files from the dataset to be considered (based on their extensions).
      For example, a dataset may have images (.jpg, .png) and metadata files (.json / .xml) but for a particular model
      (let's say autoencoding) only the images are needed. In this case, set `file_extensions` to be `['.jpg', '.png']`
      The default value is None (i.e. will take into consideration all the files)
    """
    self._load_observations = load_observations
    self._batch_size = batch_size
    self._path_to_dataset = path_to_dataset
    self._data_subset_name = data_subset_name.lower()
    assert self._data_subset_name in ['train', 'dev', 'test']
    self._is_train_dataset = (self._data_subset_name == 'train')
    self.load_device = load_device
    self.training_device = training_device

    self._num_workers = 0 if self._load_observations else (
      num_workers if num_workers is not None else min(int(0.7 * multiprocessing.cpu_count()), 8)
    )
    self._pin_memory = self._num_workers != 0
    self._files_extensions = files_extensions

    self.dataset = None
    self.data_loader = None
    self.dataset_info = None

    self.lst_on_load_preprocess_definitions = []
    self.lst_before_forward_preprocess_definitions = []
    self._on_load_preprocess = None
    self._before_forward_preprocess = None
    self._augmentation = None
    self._collate_callback = getattr(self, "_collate_fn", None)

    self.data_loader_kwargs = {}
    super(BaseDataLoaderFactory, self).__init__(log=log, **kwargs)
    return

  def startup(self):
    super().startup()
    self.__name__ = self.log.name_abbreviation(self.__class__.__name__) + '_{}'.format(self._data_subset_name)

    self.dataset_info = get_shallow_dataset(
      log=self.log,
      path_to_dataset=self._path_to_dataset,
      extensions=self._files_extensions
    )

    self.dataset_info['is_train_dataset'] = self._is_train_dataset

    self.lst_on_load_preprocess_definitions = self._lst_on_load_preprocess()
    self.lst_before_forward_preprocess_definitions = self._lst_right_before_forward_preprocess()
    lst_on_load_preprocess = [x[0](**x[1]) for x in self.lst_on_load_preprocess_definitions]
    lst_before_forward_preprocess = [x[0](**x[1]) for x in self.lst_before_forward_preprocess_definitions]

    self._on_load_preprocess = T.Compose(lst_on_load_preprocess)
    self._before_forward_preprocess = T.Compose(lst_before_forward_preprocess)
    self.P("{} data factory initialized".format(self._data_subset_name.upper()), color='g')
    return

  @property
  def preprocess_definitions(self):
    return self.lst_on_load_preprocess_definitions + self.lst_before_forward_preprocess_definitions

  def P(self, s, **kwargs):
    super().P(s, prefix=True, **kwargs)
    return

  def _get_iterable_dataset_params(self) -> dict:
    observations_not_loaded, labels_not_loaded = self._get_not_loaded_observations_and_labels()
    assert len(observations_not_loaded) == len(labels_not_loaded)
    nr_observations = len(observations_not_loaded)

    observations, labels = [], []
    if self._load_observations:
      self.P("Loading the whole dataset ({} observations) to device={} ...".format(len(observations_not_loaded), self.load_device))
      for idx in range(nr_observations):
        x,y = self.load_x_and_y_callback(observations_not_loaded, labels_not_loaded, idx)
        observations.append(x)
        labels.append(y)

        if self.log.is_main_thread:
          print(
            '\r  Load progress: {}/{} ({:.2f}%)'.format(idx + 1, nr_observations, 100 * (idx + 1) / nr_observations),
            flush=True, end=''
          )
        #endif
      #endfor
      self.P("  Dataset loaded.")
      self.log.show_timers(selected_sections=['{}_preload'.format(self.__name__)])
    else:
      observations = observations_not_loaded
      labels = labels_not_loaded
    #endif

    dct = {
      'class_ref' : IterableDataset,
      'observations' : observations,
      'labels' : labels,
      'other_params':{
        **self.data_loader_kwargs
      }
    }
    return dct

  @abc.abstractmethod
  def _lst_on_load_preprocess(self) -> List[Tuple[Any, dict]]:
    """
    List of preprocessing techniques that will be performed at loading time.

    For example, if the images in the dataset do not have the same H,W, one may want to resize all the images to
    specific H,W.

    All the preprocessing techniques should be wrapped in classes that expose a __call__ method.
    (please see libraries.nn.th.image_dataset_stage_preprocessor).

    All these preprocess methods are automatically packed into a th transformation (T.Compose)

    Returns:
    -------
    List[Tuple[Any, dict]]
      for each preprocessing technique, a tuple with: a) the class reference and b) the class __init__ kwargs
      should be provided
    """
    raise NotImplementedError

  @abc.abstractmethod
  def _lst_right_before_forward_preprocess(self) -> List[Tuple[Any, dict]]:
    """
    List of preprocessing techniques that will be performed at right before model forward.

    For example, the images min-max normalization.

    The reason why this functionality is separated from the functionality exposed in `_lst_on_load_preprocess`
    is that between resize and min-max normalization, one may want to define some augmentations for training time.
    (please see libraries.nn.th.training.callbacks.base, method `_lst_augmentations`)

    All the preprocessing techniques should be wrapped in classes that expose a __call__ method.
    (please see libraries.nn.th.image_dataset_stage_preprocessor)

    All these preprocess methods are automatically packed into a th transformation (T.Compose)

    WARNINIG: None of these preprocess should be augmentations such as ColorJitter, because this is also applied during eval phase

    Returns:
    -------
    List[Tuple[Any, dict]]
      for each preprocessing technique, a tuple with: a) the class reference and b) the class __init__ kwargs
      should be provided
    """
    raise NotImplementedError

  @abc.abstractmethod
  def _get_not_loaded_observations_and_labels(self) -> Tuple[Union[List, np.ndarray], Union[List, np.ndarray]]:
    """
    Defines the sources of the observations and labels (before loading them in memory). Most of the time the
    observations are paths to some data and the labels are the actual labels as they are not very memory-consuming.
    The output of this method is passed to `_load_x_and_y` for actually loading in memory an observation and a label.

    Returns:
    -------
    (observations, labels): Tuple[Union[List, np.ndarray], Union[List, np.ndarray]]
      The sources of the observations and labels
    """
    raise NotImplementedError

  @abc.abstractmethod
  def _load_x_and_y(self, observations, labels, idx) -> Tuple[Union[List, np.ndarray], Union[List, np.ndarray]]:
    """
    Provides an actual in-memory loading logic for a certain observation and label.
    Do not bother with th.tensors and device transfers, because all these things are performed automatically.

    Parameters:
    ----------
    observations: Union[List, np.ndarray]
      The first element in the tuple returned by `_get_not_loaded_observations_and_labels`

    labels: Union[List, np.ndarray]
      The second element in the tuple returned by `_get_not_loaded_observations_and_labels`

    idx: int
      The observation and label index that should be loaded

    Returns:
    -------
    (x, y): Tuple[Union[List, np.ndarray], Union[List, np.ndarray]]
      np.ndarray tensors for a certain input and a corresponding output
      For multi-input and multi-output, there will be a list of np.ndarray tensors
    """
    raise NotImplementedError

  @abc.abstractmethod
  def _preprocess(self, x, y, transform):
    """
    Defines the actual preprocessing step for some input and its output
    Most of the time the output should not be preprocessed, so it just has to be returned without any modification.

    Parameters:
    ----------
    x: Union[List, th.tensor]
      The input

    y: Union[List, th.tensor]
      The output

    transform: T.Compose
      A transformation that should be applied. This is the compose generated from the list of preprocess
        methods, either for load or forward

    Returns:
    -------
    (x, y): Tuple[Union[List, np.ndarray], Union[List, np.ndarray]]
      Transformed x and y
    """
    raise NotImplementedError

  def update_data_factory(self, **kwargs):
    if inspect.getsource(self.dataset.update_dataset) == inspect.getsource(IterableDataset.update_dataset):
      raise NotImplementedError("Please implement method `update_dataset` in `IterableDataset` child")

    self.dataset.update_dataset(**kwargs)
    self.data_loader = th.utils.data.DataLoader(
      self.dataset,
      batch_size=self._batch_size,
      num_workers=self._num_workers,
      pin_memory=self._pin_memory
    )

  def load_x_and_y_callback(self, observations, labels, idx) -> Tuple[Union[List, th.tensor], Union[List, th.tensor]]:
    self.log.start_timer('one_image', section='{}_preload'.format(self.__name__))

    self.log.start_timer('load_x_and_y', section='{}_preload'.format(self.__name__))
    x, y = self._load_x_and_y(observations, labels, idx)
    self.log.end_timer('load_x_and_y', section='{}_preload'.format(self.__name__))

    self.log.start_timer('to_device', section='{}_preload'.format(self.__name__))
    x, y = self.__to_device(x, y)
    self.log.end_timer('to_device', section='{}_preload'.format(self.__name__))

    self.log.start_timer('preprocess_on_load', section='{}_preload'.format(self.__name__))
    x, y = self._preprocess_on_load(x, y)
    self.log.end_timer('preprocess_on_load', section='{}_preload'.format(self.__name__))

    self.log.end_timer('one_image', section='{}_preload'.format(self.__name__))
    return x,y

  @to_device('load_device')
  def __to_device(self, x, y):
    return x, y

  def _preprocess_on_load(self, x, y):
    x,y = self._preprocess(x,y,self._on_load_preprocess)
    return x,y

  @to_device('training_device')
  def preprocess_before_forward(self, x, y):
    x,y = self._preprocess(x, y, self._before_forward_preprocess)
    return x,y

  def create(self):
    self.P("Preparing the '{}' dataset ...".format(self._data_subset_name), color='g')
    # here can be added some logic for automatically determining whether the dataset will be fully loaded or workers will be employed, instead of using `load_observations`

    dct = self._get_iterable_dataset_params()
    assert 'class_ref' in dct
    assert 'observations' in dct
    assert 'labels' in dct

    self.dataset = dct['class_ref'](
      observations=dct['observations'],
      labels=dct['labels'],
      is_train_dataset=self._is_train_dataset,
      are_observations_loaded=self._load_observations,
      load_x_and_y_callback=self.load_x_and_y_callback,
      **dct.get('other_params', {}),
    )

    self.data_loader = th.utils.data.DataLoader(
      self.dataset,
      batch_size=self._batch_size,
      num_workers=self._num_workers,
      pin_memory=self._pin_memory,
      collate_fn=self._collate_callback
    )
    return

  def release_device(self):
    self.dataset.release_dataset()
    del self.data_loader
    gc.collect()
    th.cuda.empty_cache()
    return


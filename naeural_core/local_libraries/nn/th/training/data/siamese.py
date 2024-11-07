import gc
import abc
import random
import multiprocessing

import numpy as np
import torch as th

from typing import Union, List, Tuple, Any, Dict

from naeural_core.local_libraries.nn.th.training.data.base import BaseDataLoaderFactory, IterableDataset
from naeural_core.local_libraries.nn.th.training_utils import to_device, read_image


class SiameseVisionIterableDataset(IterableDataset):
  def __init__(self, observations : List,
               is_train_dataset : bool,
               are_observations_loaded : bool,
               anchor_label : str,
               load_x_and_y_callback,
               load_x_callback,

               **kwargs):

    super(SiameseVisionIterableDataset, self).__init__(
      observations=observations,
      is_train_dataset=is_train_dataset,
      are_observations_loaded=are_observations_loaded,
      load_x_and_y_callback=load_x_and_y_callback,
      **kwargs)

    self.anchor_label = anchor_label
    self.load_x_callback = load_x_callback
    self.labels_not_counted = []
    return

  def get_data_point(self, idx):
    """
    Returns the data point given by the index parameter.

    If there are multiple anchors for a given index, the anchors are chosen at random.

    For triplet loss, if there is no close image, we will use the anchor again.

    :param idx: datapoint index
    :return:
      if the data class is SiameseMode, ContrastiveLoss or is a dev/test TripletLoss dataset:
        returns a tuple of (anchor, image), the label of the training example and the id of the data point
      else if it is a train dataset of the TripletLoss data class
        returns a tuple of (anchor, close_image, distant image), the label of the training example and the id of the data point
    """
    # First we need to find the location where the given index resides. By computing the cumsum of each location lenght we can find
    #    where our datapoint is found.
    #  For example if the cumsum of location lengths is [45, 200, 350] it means that in the first location we have the
    #    indexes 0 to 44, in the second one we have indexes 45 to 199 and in the last one we have 200 to 350 so we need
    #    to find the first location with the cumsum higher than our given index

    #    argmin([45, 200, 350] < 210) = argmin([True, True, False]) = 2
    #    Explanation: argmin returns the smallest index of the smallest value <=> the index of the first False
    location_idx = np.argmin(self.cumsum_images <= idx)
    location = self._observations[location_idx]

    # The next step is to change the scope of the index, from dataset-scoped to location scoped
    #   (the x-th dataset point is equivalent to the y-th dataset point of the k-th location). We need to find y

    # In order to do so we need to first get the number of points in this location. To do so we subtract the cumsum of the
    #   previous location from the cumsum of the current location
    location_points = self.cumsum_images[location_idx] - (
      self.cumsum_images[location_idx - 1] if location_idx > 0 else 0)
    # Then we find the distance from the global index to the last index of the current location
    location_index_dist = (self.cumsum_images[location_idx] - idx)
    # Finally the location scoped index is given by the difference of the total points minus the distance given by the index
    #   and the final point
    location_scoped_idx = location_points - location_index_dist

    # Next we need to apply the same principle to find the label of the datapoint.
    # First we need to get the counted labels
    filtered_labels = [x for x in location.keys() if x not in self.labels_not_counted]

    # Next, same as before we find the first location with de cumsum higher than the location scoped index
    label_idx = np.argmin(self.cumsum_locations[location_idx] <= location_scoped_idx)

    # Finally, we can get the labeled scoped index (counting from the end) by simply subtracting the cumsum of the label
    #   from the index;
    # NOTE: This will yield a negative value and it's absolute value will represent the index counting from the end
    labeled_scoped_idx = location_scoped_idx - self.cumsum_locations[location_idx][label_idx]

    # Get the index image
    im = location[filtered_labels[label_idx]][labeled_scoped_idx][0]

    # Get the image label
    lbl = self._process_label(location[filtered_labels[label_idx]][labeled_scoped_idx][1])

    ret = self._get_return_tuple(
      im=im,
      lbl=lbl,
      idx=idx,
      location=location
    )

    if not self._are_observations_loaded:
      x, y, idx = ret
      ret = ([self.load_x_callback((im, None))[0] for im in x], y, idx)

    return ret

  def _process_label(self, label):
    return label

  def _get_return_tuple(self, im, lbl, idx, location):
    raise NotImplementedError()

  def __iter__(self):
    self._shuffle()
    worker_indexes = self._get_worker_indexes()

    for i in worker_indexes:
      idx = self._indexes[i]

      yield tuple(self.get_data_point(idx))
    #endfor


class SiameseDataLoaderFactory(BaseDataLoaderFactory):
  def __init__(self, image_height: int, image_width: int, **kwargs):
    self._image_height = image_height
    self._image_width = image_width
    super(SiameseDataLoaderFactory, self).__init__(**kwargs)

  @abc.abstractmethod
  def _get_dataset_class_ref(self):
    raise NotImplementedError

  @abc.abstractmethod
  def _get_not_loaded_observations(self) -> List[Dict[str, List[np.ndarray]]]:
    """
    Method used to load all the image paths in a standardized structure.

    The return structure should be a list of dictionaries. Each list element is a dictionary that represents a location.

    Each location dictionary should have as keys the labels and as values a list of paths to the images from the said label

    Eg.
    [
      {
        'Anchor': ['path/to/location_1/anchor/1.png', 'path/to/location_1/anchor/2.png', 'path/to/location_1/anchor/3.png', ...],
        'label_1': ['path/to/location_1/label_1/1.png', 'path/to/location_1/label_1/2.png', 'path/to/location_1/label_1/3.png', ...],
        ...
      },
      {
        'Anchor': ['path/to/location_2/anchor/1.png', 'path/to/location_2/anchor/2.png',  ...],
        'label_1': ['path/to/location_2/label_1/1.png', 'path/to/location_2/label_1/2.png', ...],
        ...
      },
      ...
    ]
    """
    raise NotImplementedError

  def _load_x(self, observation) -> Tuple[Union[th.tensor, np.ndarray]]:
    path, label = observation
    np_img = read_image(path)
    if label is not None:
      return np_img, label
    return np_img, None

  @to_device('load_device')
  def __to_device(self, *args, **kwargs):
    """
    Overwritten method in order to accept any number of parameters. Needed because we have a variable number of images
      per datapoint.
    """
    params = [arg for arg in args if arg is not None]
    if len(params) == 1:
      return params[0]
    return params

  def _preprocess_on_load(self, x, y):
    x = self._preprocess(x=x, y=y, transform=self._on_load_preprocess)
    return x

  def _preprocess(self, x, y, transform):
    if type(x) in [list, tuple]:
      x = [transform(inp) for inp in x]
    else:
      x = transform(x)

    return x, y

  @to_device('load_device')
  def __to_device(self, *args, **kwargs):
    """
    Overwritten method in order to accept any number of parameters. Needed because we have a variable number of images
      per datapoint.
    """
    params = [arg for arg in args if arg is not None]
    if len(params) == 1:
      return params[0]
    return params


  def load_x_callback(self, observation) -> Tuple[th.tensor]:
    """
    Overwritten method because we don't load the label from here

    """
    self.log.start_timer('one_image', section='{}_preload'.format(self.__name__))

    self.log.start_timer('load_x_and_y', section='{}_preload'.format(self.__name__))
    x = self._load_x(observation)
    self.log.end_timer('load_x_and_y', section='{}_preload'.format(self.__name__))

    self.log.start_timer('to_device', section='{}_preload'.format(self.__name__))
    x = self.__to_device(x)
    self.log.end_timer('to_device', section='{}_preload'.format(self.__name__))

    self.log.start_timer('preprocess_on_load', section='{}_preload'.format(self.__name__))
    x = self._preprocess_on_load(*x)
    self.log.end_timer('preprocess_on_load', section='{}_preload'.format(self.__name__))

    self.log.end_timer('one_image', section='{}_preload'.format(self.__name__))
    return x

  def _get_iterable_dataset_params(self) -> dict:
    """
    Overwritten method because we don't need load the labels separately. The labels are build on __iter__ time in the
      dataset object based on the relation between images / anchors

    The data loaded must be the same as specified in the '_get_not_loaded_observations' method

    Eg:
    [
      {
        'Anchor': [anchor_1, anchor_2, ...],
        'label_1': [label_1_image_1, label_1_image_2, ...],
        ...
      },
      {
        'Anchor': [anchor_1, anchor_2, ...],
        'label_1': [label_1_image_1, label_1_image_2, ...],
        ...
      },
      ...
    ]

    """
    observations_not_loaded = self._get_not_loaded_observations()
    nr_observations = sum([sum([len(x) for x in location.values()]) for location in observations_not_loaded])

    observations = []
    if self._load_observations:
      self.P("Loading the whole dataset ({} observations) to device={} ...".format(len(observations_not_loaded),
                                                                                   self.load_device))
      idx = 0
      for location in observations_not_loaded:
        dct_location = {}
        for cls, lst_paths in location.items():
          if cls not in dct_location:
            dct_location[cls] = []

          for path in lst_paths:
            dct_location[cls].append(self.load_x_callback(path))
            idx += 1

            if self.log.is_main_thread:
              print(
                '\r Observations load progress: {}/{} ({:.2f}%)'.format(idx, nr_observations, 100 * idx / nr_observations),
                flush=True, end=''
              )
            # endif
          #endfor path
        #endfor location
        observations.append(dct_location)
      # endfor

    else:
      observations = observations_not_loaded
    # endif

    dct = {
      'class_ref': self._get_dataset_class_ref(),
      'observations': observations,
      'labels':[],
      'other_params': {
        'load_x_callback': self.load_x_callback,
        **self.data_loader_kwargs
      }
    }
    return dct

  def _get_not_loaded_observations_and_labels(self):
    raise ValueError("Siamese data loaders should implement `_get_not_loaded_observations` method")

  def _load_x_and_y(self, observations, labels, idx):
    raise ValueError("Siamese data loaders should implement `_get_x` method")

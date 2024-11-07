import numpy as np
import torch as th

from naeural_core.local_libraries.nn.th.training.callbacks.classification import ClassificationTrainingCallbacks

class SiameseTrainingCallbacks(ClassificationTrainingCallbacks):
  def __init__(self, distance_multiplier=None, **kwargs):
    # Parameter used as a scalar multiplier for distance between classes; used in contrastive loss and triplet loss
    self.distance_multiplier = distance_multiplier
    super(SiameseTrainingCallbacks, self).__init__(**kwargs)


  def contrastive_train_on_batch(self, model, optimizer, losses, batch, y_index=1, batch_index=False):
    """
    Train on batch method used for training models with contrastive loss.

    Each batch must have the format: (anchor, test_image), distance

    It computes the embeddings for the anchors, then the test images and applies the loss
    """
    lst_th_x, lst_th_y = self._get_batch_lst_x_lst_y(batch=batch, y_index=y_index, batch_index=batch_index)

    (a, im) = lst_th_x
    a_enc, im_enc = model(a), model(im)

    losses_vals = [losses['loss'](a_enc, im_enc, lst_th_y)]
    th_loss = th.stack(losses_vals, dim=0).sum(dim=0)

    optimizer.zero_grad()
    th_loss.mean().backward()
    optimizer.step()
    err = th_loss.detach().cpu().numpy()
    return err

  def triplet_loss_train_on_batch(self, model, optimizer, losses, batch, y_index=1, batch_index=False):
    """
    Train on batch method used for training models with contrastive loss.

    Each batch must have the format: (anchor, close_image, distant_image), distance

    It computes the embeddings for the anchors, for the close images and for the distant images and finally applies the loss
    """
    lst_th_x, lst_th_y = self._get_batch_lst_x_lst_y(batch=batch, y_index=y_index, batch_index=batch_index)

    (a, p, n) = lst_th_x
    a_enc, p_enc, n_enc = model(a), model(p), model(n)

    losses_vals = [losses['loss'](a_enc, p_enc, n_enc, lst_th_y)]
    th_loss = th.stack(losses_vals, dim=0).sum(dim=0)

    optimizer.zero_grad()
    th_loss.mean().backward()
    optimizer.step()
    err = th_loss.detach().cpu().numpy()
    return err

  def _contrastive_loss_forward_and_collect(self, data_generator: th.utils.data.DataLoader, dataset_info : dict, **kwargs):
    with th.no_grad():
      breakpoints = self.get_breakpoints(model_class=kwargs.get('model_class', 'ContrastiveLoss'))
      steps_per_epoch = len(data_generator)
      iterator = iter(data_generator)
      lst_a_enc, lst_im_enc, lst_y, lst_indexes = [], [], [], []
      for i in range(steps_per_epoch):
        x_batch, y_batch, idx_batch = next(iterator)
        x_batch, y_batch = self._transform_at_eval_time(x_batch, y_batch)
        a, im = x_batch
        a_enc, im_enc = self._model(a), self._model(im)
        lst_a_enc.append(a_enc.cpu().numpy())
        lst_im_enc.append(im_enc.cpu().numpy())
        lst_y.append(y_batch.cpu().numpy())

        lst_indexes.append(idx_batch)

        if self.log.is_main_thread:
          print(
            '\r  Forward progress: {}/{} ({:.2f}%)'.format(i + 1, steps_per_epoch, 100 * (i + 1) / steps_per_epoch),
            flush=True, end=''
          )
      #endfor
      a_encs = np.vstack(lst_a_enc)
      im_encs = np.vstack(lst_im_enc)
      dist = im_encs - a_encs
      l1_dist = np.linalg.norm(dist, axis=1, ord=1)

      y_hat = np.argmin(np.abs(l1_dist.reshape((-1, 1)) - breakpoints), axis=-1)
      y = np.hstack(lst_y)
      idx = self._get_idx(lst_indexes)
    #endwith
    return y, y_hat, idx

  def _triplet_loss_forward_and_collect(self, data_generator: th.utils.data.DataLoader, dataset_info : dict, **kwargs):
    with th.no_grad():
      breakpoints = self.get_breakpoints(model_class=kwargs.get('model_class', 'TripletLoss'))
      steps_per_epoch = len(data_generator)
      iterator = iter(data_generator)
      lst_a_enc, lst_p_enc, lst_n_enc, lst_y, lst_indexes = [], [], [], [], []
      for i in range(steps_per_epoch):
        x_batch, y_batch, idx_batch = next(iterator)
        x_batch, y_batch = self._transform_at_eval_time(x_batch, y_batch)
        a, p, n = x_batch
        a_enc, p_enc, n_enc = self._model(a), self._model(p), self._model(n)

        lst_a_enc.append(a_enc.cpu().numpy())
        lst_p_enc.append(p_enc.cpu().numpy())
        lst_n_enc.append(n_enc.cpu().numpy())
        lst_y.append(y_batch.cpu().numpy())

        lst_indexes.append(idx_batch) ## THIS DOESN"T WORK

        if self.log.is_main_thread:
          print(
            '\r  Forward progress: {}/{} ({:.2f}%)'.format(i + 1, steps_per_epoch, 100 * (i + 1) / steps_per_epoch),
            flush=True, end=''
          )
      #endfor
      a_encs = np.vstack(lst_a_enc * 2)
      im_encs = np.vstack(lst_p_enc + lst_n_enc)
      dist = im_encs - a_encs
      l1_dist = np.linalg.norm(dist, axis=1, ord=1)

      y_hat = np.argmin(np.abs(l1_dist.reshape((-1, 1)) - breakpoints), axis=-1)
      y = np.hstack([np.array(0) for _ in range(im_encs.shape[0] // 2)] + lst_y)

      idx = self._get_idx(lst_indexes * 2)
    #endwith
    return y, y_hat, idx


  def get_breakpoints(self, model_class, number_of_classes=5):
    """
    Heuristic used for computing the median distances between classes. Used for inference of models trained by using
      triplet loss or contrastive loss.

    Works by running inference on the train set and then finding the median l1 distance between each class and the anchors

    At inference time y_hat is equals to the class that has its breakpoint (median) closest to the inferred distance to
      the anchor

    """
    th_dl = self._owner.th_dl
    assert model_class in ['ContrastiveLoss', 'TripletLoss'], ValueError("Invalid model class ''".format(model_class))

    self.P("Computing breakpoints ...")
    if model_class == 'ContrastiveLoss':
      lst_a, lst_im, lst_y = [], [], []
    elif model_class == 'TripletLoss':
      lst_a, lst_p, lst_n, lst_y = [], [], [], []
    #endif model_class

    iterator = iter(th_dl)
    steps_per_epoch = len(th_dl)
    for i in range(steps_per_epoch):
      x_batch, y_batch, idx_batch = next(iterator)
      x_batch, y_batch = self._transform_at_eval_time(x_batch, y_batch)

      if model_class == 'ContrastiveLoss':
        a, im = x_batch
        a_enc, im_enc = self._model(a), self._model(im)
        lst_a.append(a_enc.cpu().numpy())
        lst_im.append(im_enc.cpu().numpy())
        lst_y.append(y_batch.cpu().numpy())
      elif model_class == 'TripletLoss':
        a, p, n = x_batch
        a_enc, p_enc, n_enc = self._model(a), self._model(p), self._model(n)

        lst_a.append(a_enc.cpu().numpy())
        lst_p.append(p_enc.cpu().numpy())
        lst_n.append(n_enc.cpu().numpy())
        lst_y.append(y_batch.cpu().numpy())
      else:
        raise ValueError("Invalid model class '{}'".format(self._model.model_class))
      #end if model_class

      if self.log.is_main_thread:
        print(
          '\r  Forward progress: {}/{} ({:.2f}%)'.format(i + 1, steps_per_epoch, 100 * (i + 1) / steps_per_epoch),
          flush=True, end=''
        )
      #endif
    #endfor
    if model_class == 'ContrastiveLoss':
      a_encs = np.vstack(lst_a)
      im_encs = np.vstack(lst_im)
      ys = np.hstack(lst_y)
    elif model_class == 'TripletLoss':
      a_encs = np.vstack(lst_a * 2)
      im_encs = np.vstack(lst_p + lst_n)
      ys = np.hstack([np.array(0) for _ in range(im_encs.shape[0]//2)] + lst_y)

    dist_hat = im_encs - a_encs

    median_distances = np.array([
      np.median(np.linalg.norm(dist_hat, axis=1, ord=1)[ys == i * self.distance_multiplier])
      for i in range(number_of_classes)
    ])

    self.P("Breakpoints computed")
    self.P("TRAIN SET: Median l1 distances:\n {}".format(median_distances))

    return median_distances
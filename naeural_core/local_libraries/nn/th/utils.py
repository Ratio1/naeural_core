import gc
import numpy as np
import torch as th
import torchvision as tv
from naeural_core.local_libraries.nn.utils import get_dropout_rate

__VER__ = '0.2.0.0'


def clear_cache():
  gc.collect()
  th.cuda.empty_cache()
  return


def Pr(s=''):
  print('\r' + str(s), end='', flush=True)


def get_dropout(
    dropout=0.1,
    dropout_type='classic',
    value_type='constant',
    step=0,
    max_step=0
):
  """

  Parameters
  ----------
  dropout : float, The base dropout rate
  dropout_type : str, The type of dropout to be used
    if 'classic' the dropout used will be the standard dropout
    if 'spatial' the dropout used will be the spatial dropout
  value_type : str, The type of the dropout rate variation
    if 'constant' the dropout rate will be constant,
    if 'last' the dropout rate will be 0 until the last step,
    if 'progressive' the dropout rate will increase linearly from 0 to the specified dropout rate.
    otherwise the dropout rate will be 0
  step : int, The current step in the training process
  max_step : int, The maximum number of steps in the training process

  Returns
  -------

  """
  dropout_rate = get_dropout_rate(value_type, dropout, step, max_step)
  dropout_class = th.nn.Dropout2d if dropout_type == 'spatial' else th.nn.Dropout
  return dropout_class(dropout_rate)


def _th_normalize(th_x, sub_val, div_val, half=False):
  if half:
    th_x = th_x.half()
  else:
    th_x = th_x.float()
  if isinstance(sub_val, th.Tensor) or sub_val != 0:
    th_x -= sub_val
  th_x /= div_val
  return th_x


def auto_normalize(th_x, scale_min, scale_max):
  th_proc = (th_x - th_x.min()) / (th_x.max() - th_x.min())
  th_proc = th_proc * (scale_max - scale_min) + scale_min
  th_x = th.where(th_x.max() > 1, th_proc, th_x)
  return th_x


def test_norm(test=0):
  img = np.random.randint(0,255, size=(1000,1000,3), dtype='uint8')
  th_x = th.tensor(img, device=th.device('cuda'))
  if test == 0:
    th_out = _th_normalize(th_x, 0, 255, False)
  else:
    th_out = th_x / 255
  return th_out


def recompute_relative_y(old_size, new_size, old_y):
  """
  Method used to compute relative position after image was resized with `th_resize_with_pad`

  Parameters:
  old_size: tuple(int, int)
    The shape of the image before it was resized with `th_resize_with_pad`

  new_size: tuple(int, int)
    The shape of the image after it was resized with `th_resize_with_pad`

  old_y: tuple(float[0,1], float[0,1])
    Relative a point described relative position of the image before it was resized with `th_resize_with_pad`

  Returns:
  new_y: tuple(float[0,1], float[0,1])
    Relative a point described relative position of the image after it was resized with `th_resize_with_pad`

  -------------
  PROOF:

  old_ratio = old image width / height or height / width
  new_ratio = resized image width / height or height / width
  ratio_black_total = the ratio of the borders over the total image over a given dimension
  ratio_colored_total = 1 - ratio_black_total

  For visualisation we can imagine the following resized image (`*` = border):
       ____________________
      | ****************** |
      |                    |
      |                    |
      |                    |
      | ****************** |
       ---------------------
  We have
     ratio_black_total = 2 / 5 = 0.4
     ratio_color_total = 1 - ratio_black_total = 0.6
  -------

  new_ratio = new_width / new_height
  old_ratio = old_width / old_height

  as we have border along a single dimension:
  new_width = old_width + border_width
  new_height = old_height

  new_ratio - old_ratio = new_width / new_height - old_width / old_height
                        = (old_width +  border_width) / old_height - old_width / old_height
                        = border_width / old_height
  =>
  ratio_black_total = new_ratio - old_ratio

  (if the ratio is ratio_black_total it means wee need the take the inverses of `new_ratio` and `old_ratio`)


  new_coord = old_coord * ratio_colored_total + ratio_black_total / 2
             ( we need to find the point on    ( and add the top border )
              the colored part of the image )

  """
  if isinstance(old_y, np.ndarray):
    new_y = old_y.copy()
  elif isinstance(old_y, th.Tensor):
    new_y = th.clone(old_y)
  elif isinstance(old_y, list):
    new_y = old_y.copy()
  else:
    raise ValueError("Method `recompute_relative_y` not implemented for old_y type '{}'".format(type(old_y)))
  #endif
  old_ratio = old_size[0] / old_size[1]
  new_ratio = new_size[0] / new_size[1]

  ratio_black_total = new_ratio - old_ratio

  if ratio_black_total < 0:
    old_ratio = old_size[0] / old_size[1]
    new_ratio = new_size[0] / new_size[1]
    ratio_black_total = new_ratio - old_ratio
    ratio_colored_total = 1 - ratio_black_total

    new_y[0] = old_y[0] * ratio_colored_total + ratio_black_total / 2
    new_y[2] = old_y[2] * ratio_colored_total + ratio_black_total / 2
  else:
    ratio_colored_total = 1 - ratio_black_total

    new_y[1] = old_y[1] * ratio_colored_total + ratio_black_total / 2
    new_y[3] = old_y[3] * ratio_colored_total + ratio_black_total / 2
  return new_y


def inverse_recompute_relative_y(old_size, new_size, new_y):
  """
  Method used to compute the original relative position after image was resized with `th_resize_with_pad`

  Parameters:
  old_size: tuple(int, int)
    The shape of the image before it was resized with `th_resize_with_pad`

  new_size: tuple(int, int)
    The shape of the image after it was resized with `th_resize_with_pad`

  new_y: tuple(float[0,1], float[0,1])
    Relative a point described relative position of the image after it was resized with `th_resize_with_pad`


  Returns:
  old_y: tuple(float[0,1], float[0,1])
    Relative a point described relative position of the image before it was resized with `th_resize_with_pad`

  -------------
  PROOF:
  Following the `recompute_relative_y` proof we arrive at the conclusion that
      new_coord = old_coord * ratio_colored_total + ratio_black_total / 2

  So we can extract the old_coord from the above equation and result:
      old_coord = (new_coord - ratio_black_total / 2) / ratio_colored_total

  """
  if isinstance(new_y, np.ndarray):
    old_y = new_y.copy()
  elif isinstance(new_y, th.Tensor):
    old_y = th.clone(new_y)
  elif isinstance(new_y, list):
    old_y = new_y.copy()
  else:
    raise ValueError("Method `recompute_relative_y` not implemented for new_y type '{}'".format(type(new_y)))
  # endif

  old_ratio = old_size[0] / old_size[1]
  new_ratio = new_size[0] / new_size[1]

  ratio_black_total = new_ratio - old_ratio

  if ratio_black_total < 0:
    old_ratio = old_size[0] / old_size[1]
    new_ratio = new_size[0] / new_size[1]
    ratio_black_total = new_ratio - old_ratio
    ratio_colored_total = 1 - ratio_black_total

    old_y[0] = (new_y[0] - ratio_black_total / 2) / ratio_colored_total
    old_y[2] = (new_y[2] - ratio_black_total / 2) / ratio_colored_total
  else:
    ratio_colored_total = 1 - ratio_black_total

    old_y[1] = (new_y[1] - ratio_black_total / 2) / ratio_colored_total
    old_y[3] = (new_y[3] - ratio_black_total / 2) / ratio_colored_total
  return old_y

def th_resize_with_pad(img, h, w, 
                       device=None, 
                       pad_post_normalize=False,
                       normalize=True,
                       sub_val=0, div_val=255,
                       half=False,
                       return_original=False,
                       half_original=False,
                       normalize_original=False,
                       normalize_callback=None,
                       fill_value=None,
                       return_pad=False,
                       shrink_factor=None,
                       **kwargs
                       ):

  def _th_resize(th_img, normalize_callback=None, fill_value=None):
    # prep default callback
    if normalize_callback is None:
      normalize_callback = _th_normalize
    # if numpy load it to device before any other op
    if isinstance(th_img, np.ndarray):
      th_img = th.tensor(th_img, device=device)
    # check the shape
    if th_img.shape[-1] == 3:
      if len(th_img.shape) == 3:
        th_img = th_img.permute((2,0,1))
      elif len(th_img.shape) == 4:
        th_img = th_img.permute((0, 3, 1, 2))

    # copy  original
    th_img_orig = th_img * 1
    if fill_value is None:
      # fill with gray
      fill_value = 114 if th_img.dtype == th.uint8 else 114/255
    new_shape = h, w
    shape = th_img.shape[-2:]  # current shape [height, width] 
    # next code is quite tensor/graph safe
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    if shrink_factor is not None:
      r /= shrink_factor
    new_unpad = int(round(shape[0] * r)), int(round(shape[1] * r))
    dw, dh = new_shape[1] - new_unpad[1], new_shape[0] - new_unpad[0]  # wh padding
    dw /= 2  # divide padding into 2 sides
    dh /= 2
    if shape != new_unpad:
      th_resized = tv.transforms.Resize(new_unpad)(th_img)
    else:
      th_resized = th_img
    # finally we have the padding params
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))

    if not pad_post_normalize:
      # usually pad before normalize
      th_resized_padded = tv.transforms.Pad((left, top, right, bottom), fill=fill_value)(th_resized)
    else:
      th_resized_padded = th_resized
    #endif

    if th_resized_padded.dtype == th.uint8 and normalize:
      th_resized_padded = normalize_callback(
        th_x=th_resized_padded,
        sub_val=sub_val, 
        div_val=div_val,
        half=half
        )
    #endif

    if pad_post_normalize:
      # pad after normalize if needed ...
      fill_value = 114/255
      th_resized_padded = tv.transforms.Pad((left, top, right, bottom), fill=fill_value)(th_resized_padded)
    #endif
    
    if len(th_img.shape) == 4:
      # send downstream the paddings - copy redundant but easier for downstream
      pad = [(top, left, bottom, right)] * th_img.shape[0]
    else:
      pad = [(top, left, bottom, right)]
    
    result = (th_resized_padded,)
    if return_original:
      if normalize_original:
        th_img_orig = _th_normalize(
          th_x=th_img_orig,
          sub_val=sub_val,
          div_val=div_val,
          half=half
        )
      if half_original:
        th_img_orig = th_img_orig.half()
      # endif
      result += (th_img_orig,)
    if return_pad:
      result += (pad,)
      
    return result[0] if len(result) == 1 else result

  
  if isinstance(img, list):
    images = []
    originals = []
    lst_original_shapes = []
    lst_pads = []
    for im in img:
      if isinstance(im, th.Tensor):
        shape = im.shape[1:]
      else:
        shape = im.shape[:-1]
      lst_original_shapes.append(shape)
      out = _th_resize(im, fill_value=fill_value, normalize_callback=normalize_callback)      
      orig, pads = None, None      
      if isinstance(out, tuple) and len(out) == 3:
        th_img, orig, pads = out
      elif isinstance(out, tuple) and len(out) == 2:
        if return_pad:
          th_img, pads = out
        else:
          th_img, orig = out        
      else:
        th_img = out
      # if tuples or just a tensor
      images.append(th_img.unsqueeze(0))
      if orig is not None:
        originals.append(orig)
      if pads is not None:
        lst_pads += pads # TLBR      
    # end for each variabile size image
    th_out = th.cat(images)
    th_originals = originals if return_original else None
  else:
    if isinstance(img, th.Tensor):
      lst_original_shapes = [x.shape[-2:] for x in img] if len(img.shape) == 4 else [img.shape[-2:]]
    elif isinstance(img, np.ndarray): # could be a NHWC numpy array
      lst_original_shapes = [x.shape[:-1] for x in img] if len(img.shape) == 4 else [img.shape[:-1]]
    else:
      raise ValueError("Uknown img input for th_resize_with_pad: {}".format(type(img)))
    out = _th_resize(img, fill_value=fill_value, normalize_callback=normalize_callback)
    th_out = out
    if isinstance(out, tuple):
      if len(out) == 3:
        th_out, th_originals, lst_pads = out
      elif len(out) == 2:
        if return_pad:
          th_out, lst_pads = out
        else:
          th_out, th_originals = out
      # endif len(out) == 2
    # endif isinstance(out, tuple)
  
  final_result = (th_out, lst_original_shapes)
  if return_original:
    final_result += (th_originals,)
  if return_pad:
    final_result += (lst_pads,)
  return final_result
  


def get_torch_n_params(model):
  pp = 0
  for p in list(model.parameters()):
    nn = 1
    for s in list(p.size()):
      nn = nn * s
    pp += nn
  return pp


def get_activation(act):
  if act.lower() == 'relu6':
    return th.nn.ReLU6()
  if act.lower() == 'relu':
    return th.nn.ReLU()
  elif act.lower() == 'tanh':
    return th.nn.Tanh()
  elif act.lower() == 'selu':
    return th.nn.SELU()
  elif act.lower() == 'gelu':
    return th.nn.GELU()
  elif act.lower() == 'sigmoid':
    return th.nn.Sigmoid()
  elif act.lower() == 'softmax':
    return th.nn.Softmax()
  else:
    raise ValueError("Unknown activation function '{}'".format(act))


def get_optimizer_class(opt):
  if opt.lower() == 'adam':
    return th.optim.Adam
  elif opt.lower() == 'sgd':
    return th.optim.SGD
  elif opt.lower() == 'adagrad':
    return th.optim.Adagrad
  elif opt.lower() == 'adadelta':
    return th.optim.Adadelta
  elif opt.lower() == 'rmsprop':
    return th.optim.RMSprop
  else:
    raise NotImplementedError('optimizer {} not implemented in `libraries.nn.th.utils.get_optimizer_class`'.format(opt))


def get_loss(loss):
  from naeural_core.local_libraries.nn.th.losses import ConstrativeLoss, HuberLoss, QuantileLoss
  if loss.lower() == 'mse':
    return th.nn.MSELoss
  elif loss.lower() == 'kl':
    return th.nn.KLDivLoss
  elif loss.lower() == 'bce':
    return th.nn.BCELoss
  elif loss.lower() == 'ce' or loss.lower() == 'crossentropy':
    return th.nn.CrossEntropyLoss
  elif loss.lower() == 'l1':
    return th.nn.L1Loss
  elif loss.lower() == 'contrastive':
    return ConstrativeLoss
  elif loss.lower() == 'huber':
    return HuberLoss
  elif loss.lower() == 'quantile':
    return QuantileLoss
  else:
    raise NotImplementedError("Unknown loss {}".format(loss))


def enforce_reproducibility(seed=1234):
  # Sets seed manually for both CPU and CUDA
  th.manual_seed(seed)
  # For atomic operations there is currently 
  # no simple way to enforce determinism, as
  # the order of parallel operations is not known.
  #
  # CUDNN
  th.backends.cudnn.deterministic = True
  th.backends.cudnn.benchmark = False
  # System based
  np.random.seed(seed)


def auto_regression_step_level(X, model, nr_steps, output_tensor_idx, keep_output_idx=None):
  """
  :param model:
  :param X: List of torch tensors
  :param nr_steps:
  :param output_tensor_idx:
  :return:
  """

  for step in range(nr_steps):
    if step < nr_steps - 1:
      x_input = [x[:, :-(nr_steps - step - 1)] for x in X]
    else:
      x_input = X

    preds = model(x_input)
    if keep_output_idx is not None:
      preds = preds[keep_output_idx]

    if step < nr_steps - 1:
      X[output_tensor_idx][:, -(nr_steps - step - 1)] = preds[:, -1]

  return preds[:, -nr_steps:].cpu().detach().numpy()


def transfer_weights_tf_to_th(tf_model, th_model):
  import tensorflow as tf
  weight_dict = dict()
  for layer in tf_model.layers:
    if type(layer) is tf.keras.layers.Conv1D:
      weight_dict[layer.get_config()['name'] + '.conv.weight'] = np.transpose(layer.get_weights()[0], (2, 1, 0))
      weight_dict[layer.get_config()['name'] + '.conv.bias'] = layer.get_weights()[1]
    elif type(layer) is tf.keras.layers.Dense:
      weight_dict[layer.get_config()['name'] + '.weight'] = np.transpose(layer.get_weights()[0], (1,0))
      weight_dict[layer.get_config()['name'] + '.bias'] = layer.get_weights()[1]
    elif type(layer) is tf.keras.layers.BatchNormalization:
      weight_dict[layer.get_config()['name'] + '.weight'] = layer.get_weights()[0]
      weight_dict[layer.get_config()['name'] + '.bias'] = layer.get_weights()[1]
    elif type(layer) is tf.keras.layers.Embedding:
      ### TODO: CHECK
      weight_dict[layer.get_config()['name']] = layer.get_weights()[0]

  pyt_state_dict = th_model.state_dict()
  parsed_keys = []
  for th_key in pyt_state_dict.keys():
    for tf_key in weight_dict.keys():
      if tf_key in th_key:
        pyt_state_dict[th_key] = th.from_numpy(weight_dict[tf_key])
        parsed_keys.append(tf_key)
      #endif
    #endfor
  #endfor
  weights_transferred = 0
  for weights in weight_dict.values():
    weights_transferred += weights.size
  assert weights_transferred == tf_model.count_params()
  print(len(weight_dict))
  print(len(parsed_keys))
  print(set)
  assert len(weight_dict) == len(parsed_keys), 'Transfer weights failed, layers do not match'
 # assert len(parsed_keys) == len(pyt_state_dict), 'Transfer weights failed, layers do not match'
  th_model.load_state_dict(pyt_state_dict)
  return th_model

def l2distance(x, y):
  return th.sqrt(((x-y)*(x-y)).clip(min=th.finfo(th.float32).eps))


if __name__ == "__main__":
  class Dummy(th.nn.Module):
    def __init__(self):
      super().__init__()

    def forward(self, x):
      return x[0] + x[1]

  model = Dummy()
  device = th.device("cuda" if th.cuda.is_available() else "cpu")

  input_tensors = [np.array([np.arange(10)] * 2) for x in range(2)]
  input_tensors[1] += 1
  input_tensors[0] *= 10
  input_tensors[0][1] += 100
  input_tensors[1][1] += 100


  nr_steps = 3
  input_tensors[0][:, -(nr_steps - 1):] = 0

  Y = auto_regression_step_level(
    model=model,
    X=input_tensors,
    nr_steps=3,
    output_tensor_idx=0,
    device=device
  )


  


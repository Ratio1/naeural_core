import os
import json
import traceback
import numpy as np

from time import time as tm
from collections import OrderedDict
from datetime import datetime as dt

from .advanced_callback import AdvancedCallback


def get_adv_callback(log,
                     model_name=None,
                     lr_monitor='val_loss',
                     lr_mode='min',
                     lr_factor=0.1,
                     lr_patience=2,
                     lr_min_delta=1e-4,
                     lr_min_lr=1e-7,
                     save_monitor='val_loss',
                     save_mode='min',
                     save_last=2,
                     save_weights=False,
                     y_trans=None,
                     y_scale=None,
                     metric_calc=None,
                     calc_MAPE_TS=False,
                     calc_MAD_TS=None,
                     allow_neg_test=False,
                     log_validate_each=1,
                     DEBUG=False,
                     ):
  """
  model_name : if None the model name will be inferred from tf.keras.models.Model.name
  lr_* : parameters for learning rate monitor

  save_monitor : what should be measured
  save_mode : how the checkpoint monitor should be measured
  save_last : the monitor will only keep best 'save_last' files

  metric_calc: function with signature func(model, data1, data2) -> dict
               with "metric":value pairs

  y_scale, y_tras: transform y if needed

  log_validate_each :  will log chosen validation metrics every x epochs (default 1)

  calc_MAPE_TS: (default None) used in TS prediction calcs MAPE on last
                time-step on validation data
  calc_MAD_TS: (default None) value x used in TS prediction to calculate
                "period average deviation" for last x steps period


  *****

  `log` :  obiectul Logger din care se apeleaza - este automat dat de Logger - dont bother
  `model_name=None` : optional numele modelului urmarit - va lua numele Keras if None
  `lr_monitor='val_loss'` : ce monitorizam pentru scaderea ratei de invatare
  `lr_mode='min'` : cum monitorizam - min inseamna ca urmarim minimizarea lui lr_monitor
  `lr_factor=0.1` : cu ce scalam rata daca nu se respecta lr_mode pentru lr_patience pasi
  `lr_patience=3` : cati pasi avem 'rabdare' cu rata curenta
  `lr_min_delta=1e-4` : care este minimum de scadere/crestere a lr_monitor
  `lr_min_lr=1e-7`  : pana unde scadem de mult rata
  `save_monitor='val_loss'` : ce monitorizam ca sa salvam cele mai bune epoci
  `save_mode='min'` : cum monitorizam ...
  `save_last=2` : top epoci salvate
  `metric_calc=None` :  functie cu semnatura f(model,x_val,y_val)->dict(metric:val)
  `calc_MAPE_TS=False` : calcularea val_MAPE pe ultimul pas al series - doar pentru TS
  `calc_MAD_TS=None` : calculeaza val_MAD pe ultimii `calc_MAD_TS` pasi din TS
  `log_validate_each=1` : printeaza date validare la fiecare nr de epoci
  `y_trans=None` : variabila de translatare a output-ului de validare (daca a fost transf inainte)
  `y_scale=None` : variabile de scalare --//----
  `DEBUG=False` : True ca sa arate informatii extra la fiecare epoca
  `allow_neg_test=False` : Negative va face pozitive toate valorile pentru teste
  `save_weights=False` : true ca sa salveze doar weights




  Exemple:

  `model.fit(...., validation_data=(X_val,Y_val),
             callbacks=[log.get_adv_callback(calc_MAPE_TS=True, lr_monitor=None)`
                          va calcula folosind datele de validare val_MAPE dupa
                          fiecare epoca si nu va umbla deloc la rata de invatare
  `model.fit(...., validation_data=(X_val,Y_val),
             callbacks=[log.get_adv_callback(calc_MAD_TS=5)`
             va calcula folosind datele de validare `val_MAD` pe ultimii 5
             pasi ai fiecarei serie dupa fiecare epoca si utiliza `val_loss` (default)
             ca sa modifice rata de invatare daca pare sa nu convearga.
  """

  cb = AdvancedCallback(log=log,
                        model_name=model_name,
                        lr_monitor=lr_monitor,
                        lr_mode=lr_mode,
                        lr_factor=lr_factor,
                        lr_patience=lr_patience,
                        lr_min_delta=lr_min_delta,
                        lr_min_lr=lr_min_lr,
                        save_monitor=save_monitor,
                        save_mode=save_mode,
                        y_scale=y_scale,
                        y_trans=y_trans,
                        save_last=save_last,
                        metric_calc=metric_calc,
                        calc_MAPE_TS=calc_MAPE_TS,
                        calc_MAD_TS=calc_MAD_TS,
                        log_validate_each=log_validate_each,
                        DEBUG=DEBUG,
                        save_weights=save_weights,
                        )
  return cb

def retrofit_faruqui(
        log,
        np_X,
        dct_edges,
        max_iters=100,
        tol=5e-3,
        alpha=None,
        beta=None):
  """
  Implements retrofitting method of Faruqui et al.

  Inputs:
  ======
  np_X : np.ndarray
    This is the input embedding matrix

  dct_edges: dict
    This is the dict that maps a certain vector to all its relatives

  max_iters: int (default=100)

  alpha, beta: callbacks that return floats as per paper alpha/beta

  tol : float (default=1e-2)
    If the average distance change between two rounds is at or
    below this value, we stop. Default to 10^-2 as suggested
    in the paper.


  Outputs:
  ======
    np.ndarray: the retrofitted version of np_X

  """

  if alpha is None:
    alpha = lambda x: 1.0
  if beta is None:
    beta = lambda x: 1.0 / len(dct_edges[x])

  np_Y = np_X.copy()
  np_Y_prev = np_Y.copy()
  log.start_timer('retrofit_process')
  log.P("Performing retrofitting on {} embedding matrix...".format(np_X.shape))
  for iteration in range(1, max_iters + 1):
    for i, vec in enumerate(np_X):
      neighbors = dct_edges[i]
      n_neighbors = len(neighbors)
      if n_neighbors:
        a = alpha(i)
        b = beta(i)
        retro = np.array([b * np_Y[j] for j in neighbors])
        retro = retro.sum(axis=0) + (a * np_X[i])
        norm = np.array([b for j in neighbors])
        norm = norm.sum(axis=0) + a
        np_Y[i] = retro / norm
      # end if
    # end for matrix rows
    changes = log._measure_changes(np_Y, np_Y_prev)
    if changes <= tol:
      log.P("Retrofiting converged at iteration {}; change was {:.4f} ".format(
        iteration, changes))
      break
    else:
      np_Y_prev = np_Y.copy()
      print("\rIteration {:d}; change was {:.4f}".format(iteration, changes),
            flush=True,
            end='')
  # end for iterations
  log.end_timer('retrofit_process')
  return np_Y

def retrofit_vector_to_embeddigs(log, np_start_vect, np_similar_vectors):
  """
  this function will retrofit a single vector (can be even a random vector but
  preferably a centroid from the original latent space) to a pre-prepared matrix
  of similar embeddings using the basic Faruqui et al approach
  """
  log.P("Creating new item embedding starting from a {} vector and {} similar items".format(
    np_start_vect.shape, np_similar_vectors.shape))
  log.P("  Current distance: {:.2f}".format(
    log._measure_changes(np_start_vect, np_similar_vectors)))
  n_simils = np_similar_vectors.shape[0]
  np_full = np.concatenate((np_start_vect.reshape(-1, 1),
                            np_similar_vectors))
  dct_edges = {0: [x for x in range(1, n_simils)]}
  np_new_embeds = log.retrofit_faruqui(np_full, dct_edges)
  np_new_embed = np_new_embeds[0]
  log.P("  New distance: {:.2f}".format(
    log._measure_changes(np_new_embed, np_similar_vectors)))
  return np_new_embed



def save_tf_graph(log, tf_saver, tf_session, file_name, sub_folder='', debug=False):
  if file_name[-5] != '.ckpt':
    file_name += '.ckpt'
  mfolder = log.get_models_folder()
  folder = os.path.join(mfolder, sub_folder)
  if not os.path.isdir(folder):
    log.P("Creating folder [{}]".format(folder))
    os.makedirs(folder)
  path = os.path.join(folder, file_name)
  try:
    if debug:
      log.P("Saving tf checkpoint '{}'".format(file_name))
    tf_saver.save(tf_session, path)
  except:
    log.P("ERROR Saving session for {}".format(path[-40:]))
  return

def save_tf_graph_checkpoint(log, session, placeholders, operations, epoch, save_path):
  '''
    Saves a tensorflow graph, given placeholders and operations from it's session to save_path
    Params:
      session     : tf.Session: Current session of the graph to save
      placeholders: Array: tf.placeholder
      operations  : Array: tf.operation, tf.tensor (operations and metrics)
      epoch       : Int: epoch at which the checkpoint is made
      save_path   : String: log-explanatory
    Returns:
      string: save path returned by saver.save
  '''
  import tensorflow.compat.v1 as tf

  with session.graph.as_default():
    log.P("Saving tf graph in [..{}] ...".format(save_path[-70:]))
    saver = tf.train.Saver()

    placeholders_collection = tf.get_collection("Placeholders")
    operations_collection = tf.get_collection("Operations")

    for p in placeholders:
      if p not in placeholders_collection:
        tf.add_to_collection("Placeholders", p)
      # endif
    # endfor

    for o in operations:
      if o not in operations_collection:
        tf.add_to_collection("Operations", o)
      # endif
    # endfor

    ret = saver.save(session, save_path, global_step=epoch)
    log.P("  ", show_time=True)
    return ret
  # endwith

def load_tf_graph_checkpoint(log, session, epoch, save_path):
  '''
    Loads a tensorflow graph values into the current given session
    Params:
      session     : tf.Session: Current session of the graph in which to load
      placeholders: Array: labels given to placeholders which will be fetched from the saved model
      operations  : Array: labels given to operations and metrics which will be fetched from the saved model
      epoch       : Int: epoch at which the checkpoint was made
      save_path   : String: log-explanatory
    Returns:
      Tuple containing a dictionary with the placeholders and a dictionary with the operations
  '''
  import tensorflow.compat.v1 as tf
  saver = tf.train.import_meta_graph(save_path + '-' + str(epoch) + '.meta')
  saver.restore(session, save_path + '-' + str(epoch))

  placeholders_dict = tf.get_collection("Placeholders")
  operations_dict = tf.get_collection("Operations")

  return (placeholders_dict, operations_dict)

###
### Keras-to-TF section and Keras inference helpers
###


def model_h5_to_graph(log, file_name,
                      custom_objects=None,
                      model_name=None,
                      **kwargs):
  assert type(file_name) is str, "`file_name` must be name of .h5 file"
  import tensorflow.compat.v1 as tf
  tf.keras.backend.set_learning_phase(0)
  model = log.load_keras_model(
    file_name,
    custom_objects=custom_objects
  )
  return model_to_graph(
    log=log,
    model=model,
    model_name=model_name,
    **kwargs
    )

def model_file_to_graph(log, file_name, model_name=None):
  return log.model_h5_to_graph(file_name, model_name)

def model_to_graph(log, model, model_name=None, save_def=False):
  """
  quick and dirty converter from model to tf1 graph
  """
  log.P("Converting {} to tf1 graph".format(model.name))    
  model_name = model_name if model_name else model.name
  res = save_model_as_graph_to_models(
    log=log,
    model=model,
    model_name=model_name,
    save_def=save_def,
    )
  if res:
    return model.name + '.pb'
  else:
    return None


def save_model_as_graph_to_models(log, model, model_name,
                                  save_def=False):
  """
  """
  log._block_tf2()
  if model_name[-3:] != '.pb':
    model_name += '.pb'
  pb_file = os.path.join(log.get_models_folder(), model_name)
  try:
    save_model_as_graph(
      log=log,
      model=model,
      pb_file=pb_file,
      save_def=save_def,
      )    
    _res = True
  except:
    str_e = traceback.format_exc()
    log.P("ERROR: SaveModelAsGraphToModels failed: {}".format(str_e))
    _res = False
  return _res


def save_graph_to_models(log, session, output_tensor_list,
                         graph_name, input_names,
                         output_names=None,
                         ):
  log._block_tf2()
  if graph_name[-3:] != '.pb':
    graph_name += '.pb'
  pb_file = os.path.join(log.get_models_folder(), graph_name)
  fn = save_graph(log,
    session=session,
    output_tensor_list=output_tensor_list,
    pb_file=pb_file,
    check_input_names=input_names,
    )
  return fn


def save_model_as_graph(log, model, pb_file,
                        save_def=False):
  """
   saves keras model as frozen computational graph

  """
  import tensorflow.compat.v1 as tf
  # if tf.executing_eagerly():
  #   raise ValueError('`save_model_as_graph` from eager mode. Please start tf with eager execution disabled.')

  log._block_tf2() # seems is not actually required

  sess = tf.keras.backend.get_session()
  input_names = [x.name for x in model.inputs]
  out_tensors = model.outputs

  fn = save_graph(
    log=log,
    session=sess,
    output_tensor_list=out_tensors,
    pb_file=pb_file,
    check_input_names=input_names,
    save_def=save_def,
    )
  return fn


def save_graph_to_file(log, sess, graph, pb_file, input_tensor_names,
                       output_tensor_names):
  import tensorflow.compat.v1 as tf
  log._block_tf2()
  
  cfg_dict = OrderedDict()
  cfg_dict['DATE'] = dt.now().strftime("%Y%m%d_%H%M%S")
  # assume names are unique in graph
  assert len(output_tensor_names) != 0

  log.verbose_log("Saving graph with {} input(s) and {} output(s) ".format(
    len(input_tensor_names) if input_tensor_names else 0, len(output_tensor_names), ))

  # get and show inputs
  if input_tensor_names is not None:
    for i, name in enumerate(input_tensor_names):
      if name[-2:] != ':0':
        name += ':0'
      else:
        input_tensor_names[i] = input_tensor_names[i][:-2]
      cfg_dict['INPUT_{}'.format(i)] = name
      log.verbose_log("  Input: {}".format(name))

  # get and show outputs
  final_output_names = []
  for i, out_tensor in enumerate(output_tensor_names):
    out_name = out_tensor
    if out_tensor[-2:-1] != ':':
      out_tensor += ':0'
    cfg_dict['OUTPUT_{}'.format(i)] = out_tensor
    final_output_names.append(out_tensor[:-2])
    log.verbose_log("  Output: {}".format(final_output_names[-1] + ":0"))
  
  try:
    graph_def = graph.as_graph_def()
  except:
    graph_def = graph
  
  if int(tf.__version__.split('.')[1]) > 13 or log.is_tf2():
    constant_graph = tf.graph_util.convert_variables_to_constants(
      sess,
      graph_def,
      final_output_names)
  else:
    from tensorflow.python.framework import graph_util
    constant_graph = graph_util.convert_variables_to_constants(
      sess,
      graph_def,
      final_output_names
      )

 # prep path
  pb_file_path = log.get_models_folder()

  # save GraphDef to .pb
  from tensorflow.python.framework import graph_io
  log.verbose_log(" Saving {} in ...{}".format(pb_file, pb_file_path[-30:]))
  graph_io.write_graph(constant_graph, pb_file_path, pb_file,
                       as_text=False)
  log.save_models_json(cfg_dict, pb_file + '.txt')
  return

def save_graph(log, session, output_tensor_list,
               pb_file, check_input_names=None,
               save_def=False):
  """
  save TF computational graph
  session     : session where the tensors reside
  output_tensor_list : list of ouput tensors
  pb_file     : .pb filename
  check_input_names : list of input tensor names
  
  TODO: REFACTOR REQUIRED
  
  """
  import tensorflow.compat.v1 as tf
  
  log._block_tf2()
  
  klp = tf.keras.backend.eval(tf.keras.backend.learning_phase())
  if type(klp) != int or klp == 1:
    log.P("****************************************************************************", color='r')
    log.P("WARNING: you did not call `K.set_learning_phase(0)` before saving the graph.", color='r')
    log.P("         This graph is NOT optimzied for inference. Please set the learning ", color='r')
    log.P("         phase or use `model_h5_to_graph` method", color='r')
    log.P("****************************************************************************", color='r')

  tensor_list = output_tensor_list
  cfg_dict = OrderedDict()
  cfg_dict['DATE'] = dt.now().strftime("%Y%m%d_%H%M%S")
  cfg_dict['INPUTS'] = []
  cfg_dict['OUTPUTS'] = []
  # assume names are unique in graph
  assert len(tensor_list) != 0

  g = tensor_list[0].graph
  log.verbose_log("Saving graph with {} input(s) and {} output(s) ".format(
    len(check_input_names), len(tensor_list), ))

  # get and show inputs
  if check_input_names is not None:
    for i, name in enumerate(check_input_names):
      if name[-2:] != ':0':
        name += ':0'
      else:
        check_input_names[i] = check_input_names[i][:-2]
      cfg_dict['INPUTS'].append(name)
      log.verbose_log("  Input: {}".format(g.get_tensor_by_name(name)))

  # get and show outputs
  final_output_names = []
  for i, out_tensor in enumerate(tensor_list):
    out_name = out_tensor.name
    if out_name[-2:-1] != ':':
      out_name += ':0'
    cfg_dict['OUTPUTS'].append(out_name)
    final_output_names.append(out_name[:-2])
    log.verbose_log("  Output: {}".format(
      g.get_tensor_by_name(final_output_names[-1] + ":0")))

  # get graphdef
  graph_def = session.graph.as_graph_def()
  # now freeze the graph to constants
  if int(tf.__version__.split('.')[1]) > 13 or log.is_tf2():
    constant_graph = tf.graph_util.convert_variables_to_constants(
      session,
      graph_def,
      final_output_names)
  else:
    from tensorflow.python.framework import graph_util
    constant_graph = graph_util.convert_variables_to_constants(session,
                                                               graph_def,
                                                               final_output_names)
  # prep path
  pb_file_path, pb_file_name = os.path.split(pb_file)

  # save GraphDef to .pb
  from tensorflow.python.framework import graph_io
  log.verbose_log(" Saving {} in ...{}".format(pb_file_name, pb_file_path[-30:]))
  graph_io.write_graph(constant_graph, pb_file_path, pb_file_name,
                       as_text=False)
  log.save_models_json(cfg_dict, pb_file_name + '.txt')
  if save_def:
    from google.protobuf import json_format
    json_string = json_format.MessageToJson(graph_def)
    dct_def = json.loads(json_string)
    log.save_models_json(dct_def, pb_file_name + '.source.txt')

  return pb_file

def load_tf_graph_saved_model(log, pb_file, return_elements=None):
  import tensorflow.compat.v1 as tf
  from tensorflow.python.util import compat
  from tensorflow.python.platform import gfile
  from tensorflow.core.protobuf import saved_model_pb2
  log.verbose_log("Prep graph from [...{}]...".format(pb_file[-30:]))
  graph = None
  if os.path.isfile(pb_file):
    start_time = tm()      
    graph = tf.Graph()
    with graph.as_default():
      trt_graph_def = tf.GraphDef()
      with gfile.FastGFile(pb_file, 'rb') as f:
        data = compat.as_bytes(f.read())
        sm = saved_model_pb2.SavedModel()      
        sm.ParseFromString(data)
        trt_graph_def = sm.meta_graphs[0].graph_def
        tf.import_graph_def(
          trt_graph_def, 
          name='', #avoid 'import_' prefixes
          return_elements=return_elements
          )
    end_time = tm()
    log.verbose_log("Done preparing graph in {:.2f}s.".format(end_time - start_time))
  else:
    log.verbose_log(" FILE NOT FOUND [...{}]...".format(pb_file[-30:]))
  return graph

def load_tf_graph(log, pb_file, return_elements=None):
  import tensorflow.compat.v1 as tf
  log.verbose_log("Prep graph from [...{}]...".format(pb_file[-30:]))
  detection_graph = None
  if os.path.isfile(pb_file):
    start_time = tm()
    detection_graph = tf.Graph()
    with detection_graph.as_default():
      od_graph_def = tf.GraphDef()
      with tf.gfile.GFile(pb_file, 'rb') as fid:
        serialized_graph = fid.read()
        od_graph_def.ParseFromString(serialized_graph)
        tf.import_graph_def(od_graph_def, name='', return_elements=return_elements)
    end_time = tm()
    log.verbose_log("Done preparing graph in {:.2f}s.".format(end_time - start_time))
  else:
    log.verbose_log(" FILE NOT FOUND [...{}]...".format(pb_file[-30:]))
  return detection_graph

def load_graph_from_models(log, model_name, get_input_output=False):
  if model_name[-3:] != '.pb':
    model_name += '.pb'
  graph_file = os.path.join(log.get_models_folder(), model_name)
  tf_graph = load_tf_graph(log=log, pb_file=graph_file)
  if get_input_output:
    cfg = log.load_models_json(graph_file + '.txt')
    s_input = cfg['INPUT_0']
    s_output = cfg['OUTPUT_0']
    return tf_graph, s_input, s_output
  else:
    return tf_graph

def save_config_dict(log, model_name, tensor_names_dict):
  if model_name[-4:] != '.txt':
    model_name += '.txt'
  log.verbose_log("Saving cfg [{}] to models...".format(model_name))
  file_path = os.path.join(log.get_models_folder(), model_name)
  with open(file_path, 'w') as fp:
    json.dump(tensor_names_dict, fp, sort_keys=True, indent=4)
  return

def load_config_dict(log, model_name):
  if model_name[-4:] != '.txt':
    model_name += '.txt'
  log.verbose_log("Loading cfg [{}] from models...".format(model_name))
  file_path = os.path.join(log.get_models_folder(), model_name)
  with open(file_path, 'r') as fp:
    data = json.load(fp)
  return data

def save_tf2func_as_graph(log, func, 
                          tensorspec_kwargs, 
                          tensorspec_args=[],
                          folder='models', 
                          graph_name='frozen_graph.pb',
                          DEBUG=False):
  """
  The purpose of this function is to help you save a Tensorflow2 function into
  a frozen graph.
  Up until the moment of developing this function, no native Tensorflow method
  exists for this purpose.
  If you are in a spot to use a TF 'saved model' (custom, TFODAPI, etc) 
  and want to transform it to a frozen graph the following method should be 
  called with the parameters below.
  This method was inspired from https://leimao.github.io/blog/Save-Load-Inference-From-TF2-Frozen-Graph/
  
  Parameters
  ----------
  func : Tensorflow 2 function
    A function obtained by loading a 'saved model'.
  tensorspec_kwargs : dict
    'get_concrete_function' expects an kwargs in order to name the input graph tensors.
    tensorspec_kwargs should provide a key,value dictionary containing tensor descriptions.
    Ex: 
      tensorspec_kwargs={'image': tf.TensorSpec((None, None, None, 3), tf.float32)}
  tensorspec_args : args, optional
    'get_concrete_function' expects args in order to specify the output graph tensors.
    tensorspec_args should contain tensors descriptions.
    Ex: 
      tensorspec_args=[tf.TensorSpec((None, 100), tf.int32), tf.TensorSpec((None, 100, 4), tf.float32), tf.TensorSpec((None, 100), tf.float32)]
      The tensor descriptions above can describe some outputs tensors cooresponding
      to a object detection graph that outputs: classes, boxes, scores.
  folder : string, optional
    Path where the frozen graph will be saved. The default is 'models'.
  graph_name : string, optional
    Name of the frozen graph. The default is 'frozen_graph.pb'.
  DEBUG : bool, optional
    Used to print debug information. The default is False.

  Raises
  ------
  ValueError
    Raised when the folder destination path is not uderstood.

  Returns
  -------
  None.

  """
  import tensorflow as tf
  from tensorflow.python.framework.convert_to_constants import convert_variables_to_constants_v2
  
  if folder is None:
    folder = '.'
  elif folder in ['data', 'output', 'models']:
    folder = log.get_target_folder(target=folder)
  else:
    if not os.path.exists(folder):
      raise ValueError('Folder value not understood: {}'.format(folder))
  #endif
  
  
  # Get frozen ConcreteFunction
  func = func.get_concrete_function(*tensorspec_args, **tensorspec_kwargs)
  frozen_func = convert_variables_to_constants_v2(func)
  frozen_func.graph.as_graph_def()
  
  if DEBUG:
    tensors = log.get_tensors_in_tf_graph(frozen_func.graph)
    log.p("-" * 50)
    log.p("Frozen model tensors: ")
    for tensor in tensors:
      log.p(tensor)
  
    log.p("-" * 50)
    log.p("Frozen model inputs: ")
    log.p(frozen_func.inputs)
    log.p("Frozen model outputs: ")
    log.p(frozen_func.outputs)
  #endif
  
  # Save frozen graph from frozen ConcreteFunction to hard drive
  path = os.path.join(folder, graph_name)
  log.verbose_log('Frozen graph will be saved into: {}'.format(path))
  tf.io.write_graph(
    graph_or_graph_def=frozen_func.graph,
    logdir=folder,
    name=graph_name,
    as_text=False
    )
  log.p('The frozen graph was saved successfully in ...{}'.format(path[-50:]))
  return


def tfodapi2_ckpt_to_graph(log, 
                           path_pipeline_config, 
                           path_checkpoint, 
                           tensorspec_kwargs,
                           tensorspec_args=[],
                           folder='models',
                           graph_name='frozen_graph.pb',
                           DEBUG=False
                          ):
  """
  The purpose of this method is to help you easily load a TFODAPI2 checkpoint
  and saved it into a frozen graph.

  Parameters
  ----------
  path_pipeline_config : string
    Path to model pipeline config.
  path_checkpoint : string
    Path to model checkpoint.
  for the rest of the parameters please see save_tf2func_as_graph method
  Returns
  -------
  None

  """
  import tensorflow as tf
  from object_detection.utils import config_util
  from object_detection.builders import model_builder
  
  def get_model_detection_function(model):
    """Get a tf.function for detection."""
    @tf.function
    def detect_fn(image):
      """Detect objects in image."""
      image, shapes = model.preprocess(image)
      prediction_dict = model.predict(image, shapes)
      detections = model.postprocess(prediction_dict, shapes)
      return detections, prediction_dict, shapes
    #enddef
    return detect_fn
  #enddef
  
  assert os.path.exists(path_pipeline_config)
  assert os.path.split(os.path.split(path_checkpoint)[0])
  
  # Load pipeline config and build a detection model
  configs = config_util.get_configs_from_pipeline_file(path_pipeline_config)
  model_config = configs['model']
  detection_model = model_builder.build(
    model_config=model_config, 
    is_training=False
    )
  
  # Restore checkpoint
  ckpt = tf.compat.v2.train.Checkpoint(model=detection_model)
  ckpt.restore(path_checkpoint).expect_partial()

  detect_fn = get_model_detection_function(detection_model)
  
  if DEBUG:
    img = np.random.randint(
      low=0, 
      high=255, 
      size=(2, 300, 300, 3)
      ).astype(np.float32)
    detections, prediction_dict, shapes = detect_fn(img)
    log.p('Shapes: {}'.format(shapes.numpy()))
  #endif
  
  save_tf2func_as_graph(log,
    func=detect_fn,    
    tensorspec_kwargs=tensorspec_kwargs,
    tensorspec_args=tensorspec_args,
    folder=folder,
    graph_name=graph_name,
    DEBUG=DEBUG
    )
  return

###
### end keras/tf converter
###

### TF1 Graph helpers

def combine_graphs_tf1(log, lst_graphs, lst_names):
  """
  will return a graph that combines all given graphs
  individual tensors of graph `i` in `lst_graphs` can be accessed via
  `lst_names[i] + '/TENSOR_NAME'.
  """
  import tensorflow.compat.v1 as tf
  assert len(lst_graphs) == len(lst_names)
  gdefs = []
  for graph in lst_graphs:
    gdefs.append(graph.as_graph_def())
  full_graph = tf.Graph()
  log.P("Creating one graph out of {} graphs: {}".format(len(lst_graphs), lst_names))
  with full_graph.as_default():
    for gdef, gname in zip(gdefs, lst_names):
      log.P("  Adding {}".format(gname))
      tf.import_graph_def(graph_def=gdef, name=gname)
  return full_graph

### END TF1 Graph helpers

###############################
###                         ###
###  Ensemble models area   ###
###                         ###
###############################

def _gen_ensemble(log, models, model_name):
  import tensorflow as tf
  tf_inp = tf.keras.layers.Input(models[0].input.shape[1:], name='ensemble_input')
  lst_tensors = [x(tf_inp) for x in models]
  tf_out = tf.keras.layers.average(lst_tensors)
  model_ensemble = tf.keras.models.Model(inputs=tf_inp, outputs=tf_out, name=model_name)
  return model_ensemble

def ensemble_generate_tf1(log, lst_files_from_models, model_name):
  return ensemble_generate(log, lst_files_from_models=lst_files_from_models,
                                model_name=model_name,
                                generate_tf1_graph=True)

def ensemble_generate(log, lst_files_from_models, model_name, generate_tf1_graph=False):
  """
  takes as input a list of model files and generates a average-based ensemble model or tf1 graph
  assumes all inputs are identical
  """
  if generate_tf1_graph:
    log._block_tf2()

  log.P("Generating averaged ensemble tf1x graph from {} files.".format(len(lst_files_from_models)))
  lst_models = [log.load_keras_model(x) for x in lst_files_from_models]
  for i in range(len(lst_models)):
    lst_models[i]._name = lst_models[i]._name + str(i)
    
  model_ensemble = _gen_ensemble(log, models=lst_models, model_name=model_name)

  if generate_tf1_graph:
    _res = save_model_as_graph_to_models(log, model_ensemble, model_name)
                                              # output_layers=[model_ensemble.layers[-1]],
                                              # input_layers=[model_ensemble.layers[0]])
  else:
    _res = log.save_keras_model(model_ensemble, label=model_name)

  _res = model_name if _res else None
  return _res

def ensemble_search_tf1(log, test_func,
                        X_test, y_test,
                        nr_cand=3, name='ens',
                        lst_cand_files=None,
                        exlude_fn='ens',
                        test_func_extra_param=None,
                        batch_size=32,
                        max_iters=None,
                        ):
  return ensemble_search(log, test_func=test_func,
                              X_test=X_test, y_test=y_test,
                              nr_cand=nr_cand, name=name,
                              lst_cand_files=lst_cand_files,
                              exlude_fn=exlude_fn,
                              test_func_extra_param=test_func_extra_param,
                              batch_size=batch_size,
                              max_iters=max_iters,
                              generate_tf1_graph=True
                              )

def ensemble_search(log, test_func,
                    X_test, y_test,
                    nr_cand=3,
                    lst_cand_files=None,
                    name='ens',
                    exlude_fn='ens',
                    test_func_extra_param=None,
                    batch_size=32,
                    max_iters=None,
                    generate_tf1_graph=False,
                    output_csv_name=None
                    ):
  """
  generates all models or tf1 graph ensemble candidates out of `_models` folder bassed on `test_func`
  that has signature either f(preds, y_test) or f(preds, y_test, test_func_extra_param)
  and returns a performance indicator value such as `dev_acc`

  test_func : f(preds, y_test, test_func_extra_param=None) calculates the test results based on predictions
  X_test : input for models
  y_test : gold
  nr_cand: number of candidates for each ensemble
  max_iters : limit to a certain number of combinations
  generate_tf1_graph : will output a tf1 graph instead of a model
  output_csv_name: will save results into specified file
  """

  from itertools import combinations
  import pandas as pd
  from time import time
  import tensorflow as tf
  if generate_tf1_graph:
    import tensorflow.compat.v1 as tf
    log._block_tf2()

  if lst_cand_files:
    lst_all_cand_files = lst_cand_files
  else:
    lst_all_cand_files = [x for x in os.listdir(log.get_models_folder()) if x.endswith('.h5') and exlude_fn not in x]
  # endif
  lst_cands = list(combinations(lst_all_cand_files, r=nr_cand))
  n_files = len(lst_all_cand_files)
  nr_candidates = len(lst_cands)
  log.P("Testing {} base .pb graph models for a total of {} ensembles".format(n_files, nr_candidates))
  dct_results = {'MODEL': [], 'TEST': [], 'TIME': [], 'SZ_MB': [], 'COMPONENTS': []}

  t_iter = np.inf
  t_iter_models = np.inf

  log.P("Testing base candidates...")
  for i_sgle_cand, fn_model in enumerate(lst_all_cand_files):
    log.start_timer('MODELS_FULL_ITER')
    t_left = (n_files - i_sgle_cand) * t_iter_models
    log.P("Testing {:.1f}% done. Remaining time: {:.1f} hrs.".format(
      (i_sgle_cand + 1) / n_files * 100, t_left / 3600))
    tf.keras.backend.clear_session()
    model = log.load_keras_model(fn_model)
    for _ in range(2):
      t_start = time()
      np_preds = model.predict(X_test)
      t_end = time()
    t_run_time = t_end - t_start
    log.P("  Output preds {}".format(np_preds.shape))

    if test_func_extra_param is not None:
      test_result = test_func(preds=np_preds, y_test=y_test, test_func_extra_param=test_func_extra_param)
    else:
      test_result = test_func(preds=np_preds, y_test=y_test)

    dct_results['MODEL'].append(fn_model)
    dct_results['TEST'].append(test_result)
    dct_results['COMPONENTS'].append(fn_model)
    dct_results['TIME'].append(t_run_time)
    fn_full_h5 = log.get_models_file(fn_model)
    dct_results['SZ_MB'].append(round(os.path.getsize(fn_full_h5) / (1024 ** 2), 3))
    log.end_timer('MODELS_FULL_ITER')
    t_iter_models = log.get_timer_mean('MODELS_FULL_ITER')

  if max_iters is not None:
    idxs = np.random.choice(nr_candidates, size=max_iters, replace=False)
  else:
    idxs = np.arange(nr_candidates)

  n_tries = idxs.shape[0]
  for i_cand, idx in enumerate(idxs):
    lst_files = lst_cands[idx]
    log.start_timer('ENSEMBLE_FULL_ITER')
    t_left = (n_tries - i_cand) * t_iter
    log.P("Testing {:.1f}% done. Remaining time: {:.1f} hrs.".format(
      (i_cand + 1) / n_tries * 100, t_left / 3600))
    tf.keras.backend.clear_session()
    model_name = '{}_{:04d}'.format(name, i_cand + 1)
    fn_ens = ensemble_generate(log, 
                               lst_files,
                               model_name=model_name,
                               generate_tf1_graph=generate_tf1_graph)
    if fn_ens is None:
      log.raise_error("Could not load {}".format(fn_ens))

    tf.keras.backend.clear_session()
    if generate_tf1_graph:
      tf_graph = load_graph_from_models(log, fn_ens)
      tf_sess = tf.Session(graph=tf_graph)
      dct_config = log.load_models_json(fn_ens + '.pb.txt')
      tf_input = tf_graph.get_tensor_by_name(dct_config['INPUT_0'])
      tf_output = tf_graph.get_tensor_by_name(dct_config['OUTPUT_0'])
      # now test each graph twice and keep second set of results
    else:
      ens_model = log.load_keras_model(fn_ens)

    for _ in range(2):
      t_start = time()
      if generate_tf1_graph:
        nr_batches = X_test.shape[0] // batch_size
        lst_preds = []
        tf_runoptions = tf.RunOptions(report_tensor_allocations_upon_oom=True)
        for nr_batch in range(nr_batches + 1):
          start_idx = nr_batch * batch_size
          stop_idx = (nr_batch + 1) * batch_size
          x_batch = X_test[start_idx:stop_idx]
          probs = tf_sess.run(tf_output,
                                  feed_dict={tf_input: x_batch},
                                  options=tf_runoptions)
          lst_preds.append(probs)
      else:
        np_preds = ens_model.predict(X_test)
      # endfor 2
      t_end = time()
    t_run_time = t_end - t_start

    if generate_tf1_graph:
      np_preds = np.concatenate(lst_preds, axis=0)

    log.P("  Output preds {}".format(np_preds.shape))

    if test_func_extra_param is not None:
      test_result = test_func(preds=np_preds, y_test=y_test, test_func_extra_param=test_func_extra_param)
    else:
      test_result = test_func(preds=np_preds, y_test=y_test)

    dct_results['MODEL'].append(model_name)
    dct_results['TEST'].append(test_result)
    dct_results['COMPONENTS'].append([_fn[-15:] for _fn in lst_files])
    dct_results['TIME'].append(t_run_time)
    

    if generate_tf1_graph:
      fn_full_pb = log.get_model_file(fn_ens + '.pb')
      fn_full_txt = log.get_model_file(fn_ens + '.pb.txt')
      dct_results['SZ_MB'].append(round(os.path.getsize(fn_full_pb) / (1024 ** 2), 3))
      fn_new_pb = os.path.join(log.get_models_folder(), fn_ens + "_T_{:.3f}.pb".format(test_result))
      fn_new_txt = os.path.join(log.get_models_folder(), fn_ens + "_T_{:.3f}.pb.txt".format(test_result))
      os.rename(fn_full_pb, fn_new_pb)
      os.rename(fn_full_txt, fn_new_txt)
    else:
      fn_full_mdl = log.get_model_file(fn_ens + '.h5')
      dct_results['SZ_MB'].append(round(os.path.getsize(fn_full_mdl) / (1024 ** 2), 3))
      fn_new_mdl = os.path.join(log.get_models_folder(), fn_ens + "_T_{:.3f}.h5".format(test_result))
      os.rename(fn_full_mdl, fn_new_mdl)

    log.end_timer('ENSEMBLE_FULL_ITER')
    t_iter = log.get_timer_mean('ENSEMBLE_FULL_ITER')
    df = pd.DataFrame(dct_results).sort_values('TEST')
    
    log.set_nice_prints(df_precision=3, precision=3)
    log.P("Results so far based on {} trials:\n{}".format(i_cand + 1, df))
  # end ensemble search loop
  if output_csv_name:
    df.to_csv(os.path.join(log.get_output_folder(), output_csv_name))
  return df

###
### END Ensemble models area
###

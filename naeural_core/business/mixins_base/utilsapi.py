"""
  This utility API for business logic plugins contains various functionalities that do not have 
  a particulat niche, providing features that otherwise would have required importing external 
  packages that are not allowed in user plugin code. 
"""
import numpy as np
from shapely import geometry
from naeural_core.business import utils

import matplotlib.pyplot as plt
import seaborn as sns

from naeural_core.main.ver import __VER__ as __CORE_VER__

try:
  from ver import __VER__ as __APP_VER__
except:
  __APP_VER__ = None

from naeural_core.utils.plugins_base.plugin_base_utils import _UtilsBaseMixin

from decentra_vision import geometry_methods


class _GenericUtilsApiMixin(_UtilsBaseMixin):

  def __init__(self):
    self.__local_data_cache = {
      'object': self.DefaultDotDict(lambda: None),
      'str': self.DefaultDotDict(lambda: ''),
      'int': self.DefaultDotDict(lambda: 0),
      'float': self.DefaultDotDict(lambda: 0.0)
    }
    super(_GenericUtilsApiMixin, self).__init__()
    return

  @property
  def geometry_methods(self):
    """Proxy for geometry_methods from decentra_vision.geometry_methods
    """
    return geometry_methods

  @property
  def gmt(self):
    """Proxy for geometry_methods from decentra_vision.geometry_methods
    """
    return self.geometry_methods

  @property
  def system_versions(self):
    return __APP_VER__, __CORE_VER__, self.log.version

  @property
  def system_version(self):
    well_defined_versions = [v for v in self.system_versions if v is not None]
    versions = '/'.join(['{}' for _ in well_defined_versions])
    versions = versions.format(*well_defined_versions)
    return "v{}".format(versions)

  @property
  def utils(self):
    """
    Provides access to methods from naeural_core.bussiness.utils.py
    """
    return utils

  @property
  def local_data_cache(self):
    """
    Can be used as a statefull store of the instance - eg `plugin.state[key]` will return `None`
    if that key has never been initialized    


    Returns
    -------
    dict
      a default dict.


    Example
    -------
      ```
      obj = self.local_data_cache['Obj1']
      if obj is None:
        obj = ClassObj1()
        self.local_data_cache['Obj1'] = obj
      ```
    """
    return self.__local_data_cache['object']

  @property
  def state(self):
    """
    Alias for `plugin.local_data_cache`
    can be used as a statefull store of the instance - eg `plugin.state[key]` will return `None`
    if that key has never been initialized     

    Returns
    -------
    dict
      Full local data cache of the current instance.

    """
    return self.local_data_cache

  @property
  def obj_cache(self):
    """
    Can be used as a statefull store of the instance - eg `plugin.obj_cache[key]` will return `None`
    if that key has never been initialized    


    Returns
    -------
    dict
      a default dict for objects.


    Example
    -------
      ```
      obj = self.obj_cache['Obj1']
      if obj is None:
        obj = ClassObj1()
        self.obj_cache['Obj1'] = obj
      ```      

    """
    return self.__local_data_cache['object']

  @property
  def int_cache(self):
    """
    can be used as a statefull store of the instance - eg `plugin.int_cache[key]` will return 0
    if that key has never been initialized    


    Returns
    -------
    dict of ints
      Returns a default dict for int values initialized with zeros.


    Example
    -------
      ```
      self.int_cache['v1'] += 1
      if self.int_cache['v1']  == 100:
        self.P("100 run interations in this plugin")
      ```

    """
    return self.__local_data_cache['int']

  @property
  def str_cache(self):
    """
    Can be used as a statefull store of the instance - eg `plugin.str_cache[key]` will return empty string
    if that key has never been initialized    


    Returns
    -------
    defaultdict
      a defaultdict with empty strings.


    Example
    -------
     ```
     self.str_cache['s1'] += str(val)[0]
     if len(self.int_cache['s1']) > 10:
       self.P("10 numbers added in the string")
     ```

    """
    return self.__local_data_cache['str']

  @property
  def float_cache(self):
    """
    Can be used as a statefull store of the instance - eg `plugin.float_cache[key]` will return 0
    if that key has never been initialized    


    Returns
    -------
    dict of floats
      Returns a default dict for float values initialized with zeros.


    Example
    -------
      ```
      self.float_cache['f1'] += val
      if self.float_cache['f1']  >= 100:
        self.P("value 100 passed")
      ```
    """
    return self.__local_data_cache['float']

  @property
  def shapely_geometry(self):
    """
    Provides access to geometry library from shapely package


    Returns
    -------
    geometry : TYPE
      DESCRIPTION.

    """
    return geometry

  @property
  def sns(self):
    """
    Provides access to the seaborn library

    Returns
    -------
    sns : package
      the Seaborn package.

    Example
    -------
      ```
      self.sns.set()
      self.sns.distplot(distribution)
      ```

    """
    return sns
  
  @property
  def pyplot(self):
    """
    Returns the matplotlib.pyplot package

    Returns
    -------
    plt : package
      the matplotlib.pyplot package.
      
    Example
    -------
      ```
      plt = self.pyplot()
      plt.plot(x, y)
      ```

    """
    return plt
  
  
  def pyplot_to_np(self, plt):
    """
    Converts a pyplot image to numpy array

    Parameters
    ----------
    plt : pyplot
      the pyplot image.

    Returns
    -------
    np.ndarray
      the numpy array image.
      
    Example
    -------
      ```
      plt = self.pyplot()
      plt.plot(x, y)
      img = self.pyplot_to_np(plt)
      ```

    """
    return self.log.plt_to_np(plt)

  def should_progress(self, progress, step=5):
    """
    Helper function for progress intervals from 5 to 5%. Returns true if param progress hits the value
    else false. Once a `True` is returned it will never again be returned

    Parameters
    ----------
    progress : float
      percentage 0-100.

    Returns
    -------
    result : bool
      a milestone is reached or not.

    """
    if not hasattr(self, '_dct_progress_intervals'):
      self._dct_progress_intervals = {i: True for i in list(range(1, 101, step))}
      self.P("Created progress intervals at step {}".format(step))
    progress = int(progress)
    progress_status = self._dct_progress_intervals.get(progress, False)
    result = False
    if isinstance(progress_status, bool) and progress_status:
      self._dct_progress_intervals[progress] = self.time()
      result = True
    return result

  def get_alive_time(self, as_str=False):
    """
    Returns plugin alive time

    Parameters
    ----------
    as_str : bool, optional
      return as string. The default is False.

    Returns
    -------
    result : float or str


    Example
    -------
      ```
      result = 'Plugin was alive ' + self.get_alive_time(as_str=True)
      ```
    """
    res = self.time_alive
    if as_str:
      res = str(self.timedelta(seconds=int(self.time_alive)))
    return res

  def get_exception(self):
    """
    Returns last exception fullstack

    Returns
    -------
    string
      The full multi-line stack.

    Example:
      ```
      ```

    """
    import traceback
    return traceback.format_exc()

  def get_models_file(self, fn):
    """
    Retruns path to models file

    :param fn: string - file name

    """
    return self.log.get_models_file(fn)

  def create_basic_ts_model(self, series_min: int = 100, train_hist=None, train_periods=None):
    """
    Returns a basic time-series prediction model instance

    Parameters
    ----------
    series_min : int, optional
      Minimal accepted number of historical steps. The default is 100.
    train_hist : int, optional
      The training window size. The default is None.
    train_periods : int, optional
      how many windows to use. The default is None.

    Returns
    -------
    BasicSeriesModel() object


    Example
    -------
      ```
        # init model
        model = plugin.create_basic_ts_model(...)
      ```

    """
    from naeural_core.utils.basic_series_model import BasicSeriesModel

    return BasicSeriesModel(
      series_min=series_min,
      train_hist=train_hist,
      train_periods=train_periods
    )

  def basic_ts_create(self, series_min=100, train_hist=None, train_periods=None):
    """
    Returns a basic time-series prediction model instance

    Parameters
    ----------
    series_min : int, optional
      Minimal accepted number of historical steps. The default is 100.
    train_hist : int, optional
      The training window size. The default is None.
    train_periods : int, optional
      how many windows to use. The default is None.

    Returns
    -------
    BasicSeriesModel() object


    Example
    -------
      ```
        # init model
        model = plugin.basic_ts_create(...)
      ```

    """
    return self.create_basic_ts_model(
      series_min=series_min,
      train_hist=train_hist,
      train_periods=train_periods
    )

  def basic_ts_fit_predict(self, data, steps):
    """
    Takes a list of values and directly returns predictions using a basic AR model


    Parameters
    ----------
    data : list
      list of float values.
    steps : int
      number of prediction steps.

    Returns
    -------
    yh : list
      the `steps` predicted values.


    Example
    -------
      ```
      yh = self.basic_ts_fit_predict(data=[0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89], steps=3)
      result = {'preds' : yh}
      ```

    """

    model = self.create_basic_ts_model(series_min=len(data))
    model.fit(data)
    yh = model.predict(steps)
    return yh

  def create_sre(self, **kwargs):
    """
    Returns a Statefull Rule Engine instance


    Returns
    -------
    SRE()


    Example
    -------
      ```
      eng = self.create_sre()  
      # add a data stream
      eng.add_entity(
        entity_id='dev_test_1', 
        entity_props=['f1','f2','f3'],
        entity_rules=['state.f1.val == 0 and state.f2.val == 0 and prev.f2.val==1'],
      )

      ```

    """
    # TODO: encapsulate this in try-except + change location to extensions
    try:
      from extensions.utils.sre import SRE
      return SRE(log=self.log, **kwargs)
    except:
      return None

  def create_statefull_rule_engine(self, **kwargs):
    """
    Returns a Statefull Rule Engine instance


    Returns
    -------
    SRE()


    Example
    -------
      ```
      eng = self.create_statefull_rule_engine()  
      # add a data stream
      eng.add_entity(
        entity_id='dev_test_1', 
        entity_props=['f1','f2','f3'],
        entity_rules=['state.f1.val == 0 and state.f2.val == 0 and prev.f2.val==1'],
      )

      ```

    """
    return self.create_sre(**kwargs)

  def plot_ts(self, vals, vals_pred=None, title=''):
    """
    Generates a `default_image` that will be embedded in the plugin response containing
    a time-series plot


    Parameters
    ----------
    vals : list[float]
      the backlog data.
    vals_pred : list[float], optional
      prediction data. The default is None.
    title : str, optional
      a title for our plot. The default is ''.

    Returns
    -------
    msg : str
      a error or success `'Plot ok'` message.


    Example
    -------
      ```
      vals = [0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89]
      steps = 3
      yh = self.basic_ts_fit_predict(data=vals, steps=steps)
      has_plot = self.plot_ts(vals, vals_pred=yh, title='prediction for {} steps'.format(steps))
      result = {'preds' : yh, 'plot_ok' : has_plot}
      ```


    """
    self.maybe_start_thread_safe_drawing()
    try:
      import matplotlib.pyplot as plt
      import seaborn as sns
      sns.set()
      sns.set(font_scale=2)
      plt.figure(figsize=(30, 14))
      l1 = len(vals)
      min_tick = 1
      max_tick = l1
      plt.plot(np.arange(min_tick, max_tick + 1), vals, 'o--', c='b', label='data')
      if vals_pred is not None and len(vals_pred) > 0:
        l2 = len(vals_pred)
        max_tick = l1 + l2
        plt.plot(np.arange(l1, max_tick + 1), [vals[-1]] + vals_pred, 'o--', c='r', label='prediction')
      plt.xticks(np.arange(min_tick, max_tick + 1))
      plt.xlabel('Steps')
      plt.title(title)
      plt.legend()
      img = self.log.plt_to_np(plt, axis=True)
      self.set_output_image(img)
      self.log.unlock_resource('api_safe_plot')
      msg = 'Plot ok.'
    except Exception as e:
      msg = str(e)
    self.maybe_stop_thread_safe_drawing()
    return msg

  def vision_plot_detections(self):
    """
    Plots detection on default output image if any


    Returns
    -------
    None.


    Example
    -------
      ```
      img = self.dataapi_image()
      if img is not None: # no need to try and plot if there is not image        
        self.vision_plot_detections()
      ```

    """
    instance_inferences = self.dataapi_image_instance_inferences()
    if instance_inferences is not None and len(instance_inferences) > 0:
      img = self.dataapi_image()
      img = self._painter.draw_inference_boxes(
        image=img.copy(),
        lst_inf=instance_inferences
      )
      self.set_output_image(img)
    return

  def set_default_image(self, img=None):
    """
    Sets given image as witness for current payload

    Parameters
    ----------
    img : np.ndarray
      the RGB image.


    Example
    -------
      ```
      img = self.dataapi_image()
      self.set_default_image(img)
      ```

    """
    if img is None:
      img = self.dataapi_image()
    self.set_output_image(img)
    return

  def set_witness_image(self, img=None):
    """
    Sets given image as witness for current payload

    Parameters
    ----------
    img : np.ndarray
      the RGB image.


    Example
    -------
      ```
      img = self.dataapi_image()
      self.set_witness_image(img)
      ```

    """
    self.set_default_image(img)
    return

  def set_text_witness(self, text: str):
    """
    Creates a simple empty witness with given centered text.

    Parameters
    ----------
    text : str
      The text that will be in the output. If the text is bigger than the screen 
      it will be displayed on multiple lines

    Returns
    -------
    None.


    Example
    -------
      ```
      self.set_text_witness('Hello world in witness image :)')
      ```


    """
    H, W = 720, 1280
    left = 100
    text = self.normalize_text(text)
    font = self.cv2.FONT_HERSHEY_SIMPLEX
    size = 0.7
    thickness = 2
    font_scale = size
    tw, _ = self._painter.text_size(text, font, font_scale, thickness)[0]
    if tw > (W - left):
      segs = tw // (W - left * 2) + 1
      words = text.split()
      n_words = len(words)
      nwps = n_words // segs
      remain = n_words % segs
      parts = []
      sidx = 0
      for i in range(segs):
        eidx = sidx + nwps
        parts.append(' '.join(words[sidx:eidx]))
        sidx = eidx
      if remain > 0:
        parts.append(' '.join(words[sidx:sidx + remain]))
      text = parts
    img = np.zeros((H, W, 3), dtype='uint8')
    img = self._painter.alpha_text_rectangle(
      image=img,
      text=text,
      left=left,
      top=300,
      color=(0, 250, 0),
      font=font,
      size=size,
      thickness=thickness,
    )
    self.set_default_image(img=img)
    return

  def chatapi_ask(self, question: str, persona: str, user: str, set_witness: bool = True, personas_folder: str = None):
    """
    Simple single-function API for accessing chat backend. Provides statefullness based on
    provided `user` for the caller plugin instance.

    Parameters
    ----------
    question : str
      The question.
    persona : str
      A valid persona.
    user : str
      A user name for tracking your session.
    set_witness : bool, optional
      If `True` then a witness will be generated. The default is True.

    Returns
    -------
    result : str
      The response.


    Example
    -------
      ```
      result = plugin.chatapi_ask(
        question="Who are you?",
        persona='codegen',
        user="John Doe",
      )      
      ```

    """
    from naeural_core.utils.openai.app import OpenAIApp, PERSONAS_FOLDER
    result = None
    assert isinstance(question, str) and len(question) > 1, "Please ask a valid question"
    assert isinstance(persona, str) and len(persona) > 1, "Please give a valid persona"
    assert isinstance(user, str) and len(user) > 1, "Please give a valid user name"
    personas_folder = personas_folder or PERSONAS_FOLDER
    eng_id = 'chat_eng_' + user
    eng = self.obj_cache[eng_id]
    if eng is None:
      eng = OpenAIApp(persona=persona, user=user, persona_location=personas_folder)
      self.obj_cache[eng_id] = eng
    self.P("Executing `{}.ask()` for user '{}' with persona '{}'...".format(eng_id, user, persona))
    t0 = self.time()
    result = eng.ask(question)
    elapsed = self.time() - t0
    self.P("  Executed `{}.ask()` in {:.2f}s seconds".format(eng_id, elapsed))
    if set_witness and self._painter is not None:
      self.set_text_witness(text=result)
    return result

  def get_serving_processes(self):
    """
    Returns a list of used AI Engines within the current plugin instance based on given configuration

    Parameters
    ----------
    None.

    Returns
    -------
    result : list
      The list.


    Example
    -------
      ```
      lst_servers = plugin.get_serving_processes()
      ```
    """
    engines = self.cfg_ai_engine
    if not isinstance(engines, list):
      engines = [engines]
    return [self.get_serving_process_given_ai_engine(x) for x in engines]

  # payload section

  def payload_set_value(self, key: str, val):
    """
    This method allows the addition of data directly in the next outgoing payload
    from the current biz plugin instance

    Parameters
    ----------
    key : str
      the name of the key
    val : any
      A value that will be json-ified.

    Returns
    -------
    None.


    Example:
    -------      
      ```
      bool_is_alert = ...
      plugin.payload_set_value("is_special_alert", bool_is_alert)
      ```

    """
    self.payload_set_data(key, val)
    return

  def download(self, url, fn, target='output', **kwargs):
    """
    Dowload wrapper that will download a given file from a url to `_local_cache/_output.


    TODO: fix to use specific endpoints configs not only from base file_system_manager

    Parameters
    ----------
    url : str
      the url where to find the file.

    fn : str
      local file name to be saved in `target` folder.

    **kwargs : dict
      params for special upload procedures such as minio.


    Returns
    -------
    res : str
      path of the downloaded file, None if not found.


    Example
    -------

      ```
      res = plugin.download('http://drive.google.com/file-url', 'test.bin')
      if res is not None:
        plugin.P("Downloaded!")
      ```

    """
    # this bypasses file-manager and only uses the config data due to incomplete feats in file-mgr
    file_system = self.global_shmem['file_system_manager']._file_system
    fs_args = {k.lower(): v for k, v in file_system.config_data.items()}
    all_args = {
      **fs_args,
      **kwargs,
    }
    self.P("Running download '{}' with arguments '{}' based on {}...".format(url, all_args, file_system.__class__.__name__))
    res = self.maybe_download(
      url=url,
      fn=fn,
      target=target,
      **all_args,
    )
    return res

  # end payload section

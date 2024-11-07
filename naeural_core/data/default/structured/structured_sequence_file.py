import pandas as pd
from naeural_core import constants as ct

from naeural_core.data.base import DataCaptureThread

_CONFIG = {
  **DataCaptureThread.CONFIG,
  'VALIDATION_RULES' : {
    **DataCaptureThread.CONFIG['VALIDATION_RULES'],
  },
}

class StructuredSequenceFileDataCapture(DataCaptureThread):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):

    self._initial_data = None

    super(StructuredSequenceFileDataCapture, self).__init__(**kwargs)
    return

  def startup(self):
    # NON-threaded code in startup
    super().startup()    

    self.P("Preparing INIT training data...")
    url_initial_data = self.config.get(ct.INIT_DATA, None)
    if url_initial_data is None:
      self.P("{} URL is missing!".format(ct.INIT_DATA), color='r')
      raise ValueError("{} failed at startup".format(self.__class__.__name__))

    fn = self._download(
      url=url_initial_data,
      progress=True
    )

    df = pd.read_csv(fn)
    self._initial_data = df.to_dict()
    self.P("Done preparing INIT data.")

    self._metadata.update(
      train_url=url_initial_data,
      train_data_shape=df.shape,
      test_data_url=self.cfg_url,
      dataframe_current=0,
      dataframe_count=0,
      current_pass=0,
    )
    return  
  
  def _init(self):
    ### TODO why don't we move the training data download here?!

    self.P('Downloading test-time data...')
    try:
      # following code will be replaced with connnection to IoT device 
      # for non-file data plugins
      path = self._download(
        url=self.cfg_url,
        progress=False,
      )
      self.P('  File downloaded in: {}'.format(path))
      self._df = pd.read_csv(path)
      self.frame_current = 0
      self._metadata.dataframe_count = self._df.shape[0]
      self._current_pass = 0
      self.has_connection = True
      self._generate_data_payload(data=None, initial_data=self._initial_data)
    except:
      msg = "Exception download"
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        autocomplete_info=True
      )
    return
  
  def _maybe_reconnect(self):
    if self.has_connection:
      return    
    self.frame_current = 0
    self.has_connection = True
    self._current_pass += 1
    return
  
  def _generate_data_payload(self, data=None, initial_data=None):
    self._add_inputs(
      [
        self._new_input(img=None, struct_data=data, metadata=self._metadata.__dict__.copy(), init_data=initial_data)
      ]
    )

    return
  
  def _run_data_aquisition_step(self):
    if self.frame_current >= self._metadata.dataframe_count:
      self.P("Abnormal read reached!")
      self.has_finished_acquisition = True
      return
    dct_rec = self._df.iloc[self.frame_current].to_dict()
    self._metadata.dataframe_current = self.frame_current
    self._metadata.current_pass = self._current_pass
    self._generate_data_payload(data=dct_rec, initial_data=None)
    
    self.frame_current += 1
    if self.frame_current > self._metadata.dataframe_count:
      self.has_finished_acquisition = True
    return 
    
  def _release(self):
    return
  
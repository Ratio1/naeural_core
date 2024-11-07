
from naeural_core import DecentrAIObject
import abc
import time as tm
import pandas as pd



class BaseConnector(DecentrAIObject):
  def __init__(self, log, config, verbose=True, **kwargs):
    self.readers = {}
    self.config = config
    self.verbose = verbose
    super(BaseConnector, self).__init__(log=log, prefix_log='[CONN]', **kwargs)
    return

  def connect(self, nr_retries=None):
    if nr_retries is None:
      nr_retries = 5

    self.P("Connecting ...")
    count = 0
    while count < nr_retries:
      count += 1
      try:
        self._connect(**self.config['CONNECT_PARAMS'])
        self.P("connection created.", color='g')
        break
      except Exception as err:
        self.P("ERROR! connection failed\n{}".format(err))
        tm.sleep(0.5)
      #end try-except
    #endwhile
    return

  def maybe_create_reader(self, reader_name, **kwargs):
    if len(kwargs) == 0:
      query_params = self.config['QUERY_PARAMS'].get(reader_name, None)
    else:
      query_params = kwargs
      
    if query_params is None:
      self.P("Could not create reader {}. No query params provided or found in config.".format(reader_name), color='r')

    reader = self.readers.get(reader_name, None)
    if reader is None:
      try:
        reader = self._create_reader(**query_params)
        self.readers[reader_name] = reader
        if self.verbose:
          self.P("Successfully created reader {}".format(reader_name), color='g')
      except Exception as e:
        self.P("Error creating reader {}\n{}".format(reader_name, e), color='r')
    #endif
    return

  
  def get_data(self, **kwargs):
    kwargs['chunksize'] = None
    temp_name = self.log.now_str()
    self.maybe_create_reader(temp_name, **kwargs)
    data = self.readers[temp_name].copy()
    self.delete_reader(temp_name)
    return data

  def create_readers_from_config(self):
    reader_names = list(self.config['QUERY_PARAMS'].keys())
    for reader_name in reader_names:
      self.create_reader(reader_name)
    return

  def get_data_generator_from_reader(self, reader_name):
    self.maybe_create_reader(reader_name)
    reader = self.readers.get(reader_name, None)
    if reader is None:
      return
    if isinstance(reader, pd.DataFrame):
      raise ValueError("Attempted to use whole dataframe {} as generator failed. Please use `chunksize` to create generators".format(
        reader.shape))
    self.P("Iterating chunks for reader {} ...".format(reader_name), color='b')
    for i, df_chunk in enumerate(reader):
      d1, d2 = df_chunk.shape
      print_msg = "Chunk events: {}".format(d1)
      if i == 0:
        print_msg += " / cols: {}".format(d2)

      self.P(print_msg, color='b')

      yield df_chunk

  def get_all_data_generators(self):
    reader_names = list(self.config['QUERY_PARAMS'].keys())
    for reader_name in reader_names:
      for df_chunk in self.get_data_generator_from_reader(reader_name):
        yield reader_name, df_chunk

  def get_data_from_reader(self, reader_name):
    lst_df_chunks = []
    for df_chunk in self.get_data_generator_from_reader(reader_name):
      lst_df_chunks.append(df_chunk)
    if len(lst_df_chunks) > 1:
      return pd.concat(lst_df_chunks)
    else:
      if len(lst_df_chunks) == 1:
        return lst_df_chunks[0]
      else:
        return None

  def get_all_readers_data(self):
    reader_names = list(self.config['QUERY_PARAMS'].keys())
    dct_all_data = {}
    for reader_name in reader_names:
      dct_all_data[reader_name] = self.get_data_from_reader(reader_name)
    return dct_all_data

  def _connect(self, **kwargs):
    return
  
  def delete_reader(self, reader_name):
    if reader_name in self.readers:
      del self.readers[reader_name]
    return

  @abc.abstractmethod
  def _create_reader(self, **kwargs):
    raise NotImplementedError
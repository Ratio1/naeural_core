import os
import pandas as pd
import numpy as np
import operator
from functools import partial
from scipy import sparse

from naeural_core import DecentrAIObject

from naeural_core.utils.predictive_analytics.timeseries.numpyize_constants import NumpyizeConstants
from naeural_core.utils.predictive_analytics.timeseries import connectors
from naeural_core.utils.predictive_analytics.timeseries.connector_constants import ConnectorConstants
from naeural_core.utils.predictive_analytics.timeseries.binarization import BaseDataBinarization

def tuple_scalar(x):
  if type(x) is not tuple:
    return tuple([x,])
  return x
#enddef

def save_dict_to_hdf5(dct, filename, root='/', write_mode='w'):
  import h5py
  
  if not root.startswith('/'):
    root = '/' + root

  if not root.endswith('/'):
    root = root + '/'

  with h5py.File(filename, write_mode) as h5file:
    recursively_save_dict_contents_to_group(h5file, root, dct)


def recursively_save_dict_contents_to_group(h5file, path, dct):
  base_instances = (
    np.ndarray,
    np.int16, np.int32, np.int64, np.uint8, np.uint16, np.uint32,
    np.float16, np.float32, np.float64,
    str, bytes, int, float,
  )
  for key, item in dct.items():
    if isinstance(item, base_instances):
      h5file[path + key] = item
    elif isinstance(item, dict):
      recursively_save_dict_contents_to_group(h5file, path + key + '/', item)
    else:
      raise ValueError('Cannot save %s type' % type(item))


def load_dict_from_hdf5(filename, root='/'):
  import h5py
  
  if not root.startswith('/'):
    root = '/' + root

  if not root.endswith('/'):
    root = root + '/'

  with h5py.File(filename, 'r') as h5file:
    return recursively_load_dict_contents_from_group(h5file, root)


def recursively_load_dict_contents_from_group(h5file, path):
  import h5py

  ans = {}
  for key, item in h5file[path].items():
    if isinstance(item, h5py.Dataset):
      ans[key] = item[()]
    elif isinstance(item, h5py.Group):
      ans[key] = recursively_load_dict_contents_from_group(h5file, path + key + '/')
  return ans

def is_void_list(lst):
  if lst is None:
    return True

  if isinstance(lst, list):
    return len(lst) == 0
  else:
    raise ValueError("Object of type `list` expected in `is_void_list`")


class TimeseriesDataMaster(BaseDataBinarization):
  def __init__(self,
               log,
               prefix=None,
               aggregation_fields=None,
               time_field=None,
               target_field=None,
               target_steps=None,
               verbose=True,
               past_binarized_src_full_path=None,
               timers_section=None,
               **kwargs):
    self._fn_metadata = 'metadata.json'
    self._fn_mappings = 'mappings.pickle'

    super(TimeseriesDataMaster, self).__init__(
      log=log,
      prefix=prefix,
      verbose=verbose,
      past_binarized_src_full_path=past_binarized_src_full_path,
      **kwargs
    )
    self._timers_section = timers_section
    ### attributes that will be set in `set_data_column_names`
    self.aggregation_fields = None
    self.time_field = None
    self.target_field = None
    self.target_steps = None
    ###
    self.series2agg = None
    self.agg2series = None
    self.series2aggfields = None
    self.dct_metadata = None
    self.datasets = {}
    self._nr_create_calls = 0
    self._mappings_loaded = False

    self.set_data_column_names(
      aggregation_fields=aggregation_fields,
      time_field=time_field,
      target_field=target_field,
      target_steps=target_steps
    )

    self.dct_metadata = {
      NumpyizeConstants.KEY_DATASET : {},
      NumpyizeConstants.KEY_DATASET_ORIGIN : {},
      NumpyizeConstants.KEY_AGGREGATION_FIELDS : self.aggregation_fields,
      NumpyizeConstants.KEY_TIME_FIELD : self.time_field,
      NumpyizeConstants.KEY_TARGET_FIELD: self.target_field,
      NumpyizeConstants.KEY_OTHER_COVARIATE_FIELDS : [],
      NumpyizeConstants.KEY_FREQ: None,
      NumpyizeConstants.KEY_NR_SERIES: None,
      NumpyizeConstants.KEY_NR_STEPS: None,
      NumpyizeConstants.KEY_TARGET_STEPS: self.target_steps
    }

    if self.prefix is not None:
      self.load_metadata(self.prefix)

    assert self.aggregation_fields is not None
    assert type(self.aggregation_fields) is list
    assert self.time_field is not None
    assert self.target_field is not None

    if self.prefix is not None:
      self.load_mappings(self.prefix)

    # for _ in self.dct_metadata[NumpyizeConstants.KEY_DATASET]:
    #   self.create_dataset()

    return

  def get_dataset_type(self, meta_info_id):
    return self.dct_metadata[NumpyizeConstants.KEY_DATASET][meta_info_id][NumpyizeConstants.KEY_DATASET_TYPE]

  def get_other_covariate_fields(self):
    return self.dct_metadata[NumpyizeConstants.KEY_OTHER_COVARIATE_FIELDS]
  
  def set_data_column_names(self,
                            aggregation_fields=None,
                            time_field=None,
                            target_field=None,
                            target_steps=None):
    """
    Method used for setting the data columns names. They are read directly
    from config data; otherwise, if specified here, they are overwritten
    """
    
    self._parse_config_data(
      aggregation_fields=aggregation_fields,
      time_field=time_field,
      target_field=target_field,
      target_steps=target_steps
    )
    return
  #enddef
  
  @staticmethod
  def time_mapping(start, end, freq='D'):
    date_range = pd.date_range(
      start=start,
      end=end,
      freq=freq
    )
    
    dct_map = {k:i for i, k in enumerate(date_range)}
    return date_range, dct_map
  #enddef
  
  def compute_mappings(self, df_chunk):    
    self.log.start_timer('_compute_mappings', section=self._timers_section)
    
    if not self.series2agg:
      self.series2agg = []
    
    if not self.agg2series:
      self.agg2series = {}
      
    if not self.series2aggfields:
      self.series2aggfields = {}
      for f in self.aggregation_fields:
        self.series2aggfields[f] = []

    self.log.start_timer('groupby_aggregation_fields_1', section=self._timers_section)
    keys = df_chunk.groupby(self.aggregation_fields).indices.keys()
    self.log.end_timer_no_skip('groupby_aggregation_fields_1', section=self._timers_section)
    set_series = set(self.series2agg)

    self.series2agg.extend([x for x in keys if x not in set_series])
    self.agg2series = {v:i for i,v in enumerate(self.series2agg)}

    for i, f in enumerate(self.aggregation_fields):
      self.series2aggfields[f] = [tuple_scalar(x)[i] for x in self.series2agg]
    #endfor

    self.log.end_timer_no_skip('_compute_mappings', section=self._timers_section)
    self.P("Master computed mappings... describe:")
    self._describe()
    return
  #enddef
  
  def save_mappings(self, prefix=None):
    if not prefix:
      prefix = self.log.file_prefix

    all_mappings = {
      'series2agg' : self.series2agg,
      'agg2series' : self.agg2series,
      'series2aggfields' : self.series2aggfields,
    }
    
    fn = os.path.join(prefix, self._fn_mappings)
    self.log.save_pickle_to_output(all_mappings, fn)
    return
  #enddef
  
  def load_mappings(self, prefix):
    datafiles = os.listdir(self.get_dir(prefix))
    datafiles = [x for x in datafiles if x == self._fn_mappings]
    
    if len(datafiles) > 1:
      raise ValueError("Datafiles content: {}".format(datafiles))
    elif len(datafiles) == 0:
      return
    
    fn = os.path.join(prefix, datafiles[0])
    all_mappings = self.log.load_pickle_from_output(fn, verbose=self.verbose)
    
    self.series2agg       = all_mappings['series2agg']
    self.agg2series       = all_mappings['agg2series']
    self.series2aggfields = all_mappings['series2aggfields']
    
    self._mappings_loaded = True
    self.P("Master loaded mappings... describe:")
    self._describe()
    return
  #enddef

  def save_metadata(self, prefix=None):
    if not prefix:
      prefix = self.log.file_prefix

    fn = os.path.join(prefix, self._fn_metadata)
    self.log.save_output_json(self.dct_metadata, fn)
    return

  def load_metadata(self, prefix):
    datafiles = os.listdir(self.get_dir(prefix))
    datafiles = [x for x in datafiles if x == self._fn_metadata]

    if len(datafiles) > 1:
      raise ValueError("Datafiles content: {}".format(datafiles))
    elif len(datafiles) == 0:
      return

    fn = os.path.join(prefix, datafiles[0])
    self.dct_metadata = self.log.load_output_json(fn, verbose=self.verbose)

    self.aggregation_fields = self.dct_metadata[NumpyizeConstants.KEY_AGGREGATION_FIELDS]
    self.time_field = self.dct_metadata[NumpyizeConstants.KEY_TIME_FIELD]
    self.target_field = self.dct_metadata[NumpyizeConstants.KEY_TARGET_FIELD]
    self.target_steps = self.dct_metadata[NumpyizeConstants.KEY_TARGET_STEPS]
    return

  
  def get_nr_entities_per_each_agg(self):
    dct = {}
    for f in self.aggregation_fields:
      nr = len(set(self.series2aggfields[f]))
      dct[f] = nr
    #endfor
    return dct
  

  def _describe(self):
    if not self.verbose:
      return

    self.Pnp("Master sumar statistics:")
    self.Pnp("* Total number of series: {:,}".format(len(self.series2agg)))
    dct_nr_entities = self.get_nr_entities_per_each_agg()
    for f,nr in dct_nr_entities.items():
      self.Pnp("* Total number of '{}': {:,}".format(f, nr))
    self.Pnp("\n")
    return
  #enddef
  
  def create_dataset(self):
    ds = TimeseriesDataMaster.Dataset(
      log=self.log,
      outer=self,
      meta_info_id=self._nr_create_calls,
      DEBUG=self.DEBUG
    )
    
    self.datasets[self._nr_create_calls] = ds
    self._nr_create_calls += 1
    return ds
  #enddef
  
  class Dataset(DecentrAIObject):  
    def __init__(self, log, outer, meta_info_id=0, **kwargs):
      super().__init__(log=log, prefix_log='[DSDATAB]', **kwargs)
      self.outer = outer
      self.verbose = self.outer.verbose
      self.meta_info_id = meta_info_id
      self.is_master = self.meta_info_id == 0
      self.freq = None

      self.dataset_type = None
      self.filename = None
      
      self.global_min_dt = None
      self.global_max_dt = None
      self.series_min_dt = {}
      self.series_ids_min_dt = {}
      self.series_max_dt = {}
      self.series_ids_max_dt = {}
      self.date_range = []
      self.dates2id = {}
      self.strdates2id = {}
      
      self.connector = None
      self.filters = None
      self.ffill_bfill_columns = None
      
      self.column_names = None
      
      self._fn_preprocess = 'preprocess_{}.pickle'
      self._fn_series = 'series_{}.hdf5'
      
      self._load_preprocessed_data(prefix=self.outer.prefix)

      return
    #enddef
    
    def _not_preprocessed(self):
      not_preprocessed = self.global_min_dt is None
      not_preprocessed = self.global_max_dt is None or not_preprocessed
      not_preprocessed = len(self.series_min_dt) == 0 or not_preprocessed
      not_preprocessed = len(self.series_max_dt) == 0 or not_preprocessed
      not_preprocessed = len(self.date_range) == 0 or not_preprocessed
      not_preprocessed = len(self.dates2id) == 0 or not_preprocessed
      return not_preprocessed
    #enddef
    
    def star_series(self, np_datasets, count=0, lint=None, rint=None,
                    np_mappings=None, min_dt=None):
      if lint is not None:
        assert type(lint) is str
        lint = self.strdates2id[lint]
      
      if rint is not None:
        assert type(rint) is str
        rint = self.strdates2id[rint]
        rint += 1
        
      arr = None
      if type(np_datasets) is dict:
        for k,v in np_datasets.items():
          break
        arr = v
      elif type(np_datasets) in np.ndarray:
        arr = np_datasets
      else:
        raise ValueError('Unknown np_datasets')
    
      if np_mappings is None:
        np_mappings = np.arange(arr.shape[0])
    
      good_starts = [True for _ in range(np_mappings.shape[0])]
      
      if min_dt is not None:
        starts = [self.series_ids_min_dt[i] for i in np_mappings]
        good_starts = list(map(lambda x: x <= pd.to_datetime(min_dt), starts))
      #endif
      
      good_starts = np.array(good_starts)
      mask_good_series = (arr[:, lint:rint] > 0).sum(axis=1) >= count
      mask_good_series = mask_good_series & good_starts
      ### TODO debug for _lens_data/_v2/_adph_dev_other_covariates - weekly-tran-v3
      
      mask_star_series = ~mask_good_series
      idx = np.where(mask_star_series)[0]
      
      return np_mappings[idx], mask_star_series
    
    
    def train_test_split(self, np_datasets, lint_train=None,
                         rint_train=None, lint_test=None,
                         rint_test=None):
      
      
      def _split(arr):
        if len(arr.shape) == 1:
          arr = np.expand_dims(arr, -1)
        _arr_train = arr[:,lint_train:rint_train]
        _arr_test = None
        if lint_test or rint_test:
          _arr_test = arr[:, lint_test:rint_test]
        return _arr_train, _arr_test
      
      np_train_datasets, np_test_datasets = None, None

      if lint_train is not None:
        assert type(lint_train) in [str, int, np.int32, np.int16, np.int64]
        if type(lint_train) is str:
          lint_train = self.strdates2id[lint_train]
      
      if rint_train is not None:
        assert type(rint_train) in [str, int, np.int32, np.int16, np.int64]
        if type(rint_train) is str:
          rint_train = self.strdates2id[rint_train]
          rint_train += 1
        
      if lint_test is not None:
        assert type(lint_test) in [str, int, np.int32, np.int16, np.int64]
        if type(lint_test) is str:
          lint_test = self.strdates2id[lint_test]
        
      if rint_test is not None:
        assert type(rint_test) in [str, int, np.int32, np.int16, np.int64]
        if type(rint_test):
          rint_test = self.strdates2id[rint_test]
          rint_test += 1
        
        
      if type(np_datasets) is dict:
        np_train_datasets = {}
        
        if lint_test or rint_test:
          np_test_datasets = {}
        
        for k,v in np_datasets.items():
          arr_train, arr_test = _split(v)
          np_train_datasets[k] = arr_train
          if type(np_test_datasets) is dict:
            np_test_datasets[k] = arr_test
          #endif
        #endfor
      elif type(np_datasets) is np.ndarray:
        np_train_datasets, np_test_datasets = _split(np_datasets)
      else:
        raise ValueError('Unknown np_datasets')
      
      return np_train_datasets, np_test_datasets

    
    def __call__(self,
                 lint_train=None, rint_train=None,
                 lint_test=None, rint_test=None,
                 lint_star_not_predictible=None,
                 rint_star_not_predictible=None,
                 count_star_not_predictible=0,
                 min_dt_predictible=None,
                 sanity=False):
      
      if self._not_preprocessed():
        self._load_preprocessed_data(prefix=self.outer.prefix)

      datasets = self._load_sparse_series(
        prefix=self.outer.prefix,
        sanity=sanity
      )
      
      lst_np_datasets,\
        lst_np_mappings, _ = self._sparse_series_to_array(*datasets)
        
      lst_final_np_datasets = []
      for np_datasets in lst_np_datasets:
        final_np_datasets = {}
        for col,v in np_datasets.items():
          if col in self.ffill_bfill_columns:
            # self.P("Applying ffill / bfill on '{}' array ...".format(col))
            final_np_datasets[col] = self.log.np_bfill(self.log.np_ffill(v))
            # self.P("", t=True)
          else:
            final_np_datasets[col] = v
          #endif
        #endfor
        lst_final_np_datasets.append(final_np_datasets)
      #endfor
      
      # stars = []
      lst_train_np_datasets = []
      lst_test_np_datasets = []
      for i in range(len(lst_final_np_datasets)):
        # stars.append(self.star_series(
        #   np_datasets=lst_final_np_datasets[i],
        #   count=count_star_not_predictible,
        #   lint=lint_star_not_predictible,
        #   rint=rint_star_not_predictible,
        #   np_mappings=lst_np_mappings[i],
        #   min_dt=min_dt_predictible
        # ))
        
        np_train_datasets, np_test_datasets = self.train_test_split(
          np_datasets=lst_final_np_datasets[i],
          lint_train=lint_train,
          rint_train=rint_train,
          lint_test=lint_test,
          rint_test=rint_test
        )
        
        lst_train_np_datasets.append(np_train_datasets)
        lst_test_np_datasets.append(np_test_datasets)
      #endfor
      
      ret_train_datasets = lst_train_np_datasets
      ret_test_datasets  = lst_test_np_datasets
      ret_mappings = lst_np_mappings
      # ret_stars = stars
      # ret_tmp = lst_final_np_datasets
      
      if len(lst_train_np_datasets) == 1:
        ret_train_datasets = lst_train_np_datasets[0]
        ret_test_datasets = lst_test_np_datasets[0]
        ret_mappings = lst_np_mappings[0]
        # ret_stars = stars[0]
        # ret_tmp = lst_final_np_datasets[0]
      #endif
  
      return (
              ret_train_datasets,
              ret_test_datasets,
              ret_mappings,
              # ret_stars,
              # ret_tmp,
             )
    #enddef
    
    def setup(self,
              dataset_type=None,
              column_names=None,
              filters=None,
              ffill_bfill_columns=None,
              freq=None,
              series_chunksize=None,
              connector_type=ConnectorConstants.DF_CONNECTOR,
              column_sanity_check=None,
              force=False,
              **conn_kwargs):
      ### freq='D' / 'W-MON'
      if dataset_type is None:
        dataset_type = NumpyizeConstants.DATASET_TRAIN

      if freq is None:
        freq = self.outer.dct_metadata[NumpyizeConstants.KEY_FREQ]

      assert freq is not None
      assert isinstance(freq, str)
      assert dataset_type in NumpyizeConstants.VALID_DATASET_NAMES

      if ffill_bfill_columns is None:
        ffill_bfill_columns = []
      if filters is None:
        filters = []
      if column_names is None:
        column_names = []
      
      self.freq = freq.upper()
      self.filters = filters
      self.column_names = column_names
      self.ffill_bfill_columns = ffill_bfill_columns
      self.dataset_type = dataset_type

      assert type(self.column_names) is list
      assert type(self.ffill_bfill_columns) is list
      self.ffill_bfill_columns = list(set(self.ffill_bfill_columns) & set(self.column_names))

      ### if we add multiple EXTRA_PROCESSING params such as ffill_bfill_columns, then do the intersect with self.column_names
      
      self.keep_columns = self.outer.aggregation_fields +\
                          [self.outer.time_field] +\
                          self.column_names
      
      if not self.outer._mappings_loaded or force:
        self.outer.dct_metadata[NumpyizeConstants.KEY_DATASET][self.meta_info_id] = {}

        self.connector = self._create_connector(
          connector_type=connector_type,
          **conn_kwargs
        )

        self.outer.dct_metadata[NumpyizeConstants.KEY_DATASET][self.meta_info_id][NumpyizeConstants.KEY_DATASET_TYPE] = self.dataset_type
        self.preprocess()
        self.process(
          series_chunksize=series_chunksize,
          column_sanity_check=column_sanity_check
        )

        self.outer.save_metadata(prefix=self.outer.prefix)



      #endif

      return
    #enddef

    def __repr__(self):
      str_descr  = '* dataset type: {}'.format(self.dataset_type)
      str_descr += '\n* targets: {}'.format(self.column_names)
      str_descr += "\n* frequency='{}'".format(self.freq)
      str_descr += '\n* history between {} and {}'.format(
        self.global_min_dt, self.global_max_dt
      )
      
      return str_descr
    
    
    def _create_connector(self,
                          connector_type=ConnectorConstants.CSV_CONNECTOR,
                          **kwargs):
      """
      Setups the data connector to the dataset
      
      Parameters:
      ----------
      connector_type : str, optional
        Specifies how the data should be consumed:
          - const.CSV_CONNECTOR ('CSV'): consumes data from a csv file
          - const.ODBC_CONNECTOR ('ODBC'): consumes data from an ODBC connector
        The default is 'CSV'.
  
      **kwargs
        Contain configurations for the data connector.
        The **kwargs can have the following parameters:
          1. `dataset` : str, optional
            The dataset name as described in the data config file.
            Used for [CSV / ODBC].
            The default is None
          
          2. `fn_data` : str, optional
            The path to the dataset (if specified, overwrites the one from the
                                     config file).
            Used for [CSV].
            The default is None
          
          3. `chunksize` : int optional
            Number of data row processed per chunk
            Used for [CSV / ODBC].
            The default is None
            
          4. `parse_dates` : bool, optional
            Sets if the dataset time field should be casted to DateTime
            Used for [CSV / ODBC]
            The default is True
        
          5. `database` : str, optional
            specifies what databese schema should be used (if specified,
            overwrites the one from the config file).
            Used for [ODBC]
            The default is None.
        
          6. `uid` : str, optional 
            the username used for the database connection (if specified,
            overwrites the one from the config file).
            Used for [ODBC]
            The default is None.
          
          7. `password` : str, optional 
            the password used for the database connection (if specified,
            overwrites the one from the config file).
            Used for [ODBC]
            The default is None.
            
          8.  `odbc_driver` : str, optional 
            the driver to use for connecting to the database (if specified,
            overwrites the one from the config file).
            Used for [ODBC].
            The default is None.
            
          9. `port` : str, optional
            port used in the database connection (if specified,
            overwrites the one from the config file).
            Used for [ODBC].
            The default is ''.
            
          10. `table_data` : str, optional 
            from which table to extract the data (if specified,
            overwrites the one from the config file).
            Used for [ODBC]
            The default is None.
            
          11. `server` : str, optional
            the server which hosts the database (if specified,
            overwrites the one from the config file).
            Used for [OBDC]
            The default is None.
            
          12. `DEBUG` : bool, optional
            DEBUG flag in connectors that enables additional logs
            Used for [CSV / ODBC]
            The default is True.

          13. `absolute_path_fn_data` : bool, optional
            Flag that specifies if `fn_data` is an absolute path or not. If not, it looks
            for `fn_data` in logger's _data main subfolder
            Used for [CSV]
            The default is False
        
      Returns
      -------
      a connector object
      """

      DEBUG = kwargs.get('DEBUG', True)
      assert connector_type.upper() in [
        ConnectorConstants.CSV_CONNECTOR,
        ConnectorConstants.ODBC_CONNECTOR,
        ConnectorConstants.DF_CONNECTOR
      ]
      
      if connector_type.upper() == ConnectorConstants.CSV_CONNECTOR:
        dataset = kwargs.get('dataset', None)
        chunksize = kwargs.get('chunksize', None)
        bool_parse_dates = kwargs.get('parse_dates', True)

        fn_data = kwargs.get('fn_data', None)
        absolute_path_fn_data = kwargs.get('absolute_path_fn_data', False)

        if fn_data is not None:
          self.filename = fn_data.split('/')[-1]

        if self.meta_info_id not in self.outer.dct_metadata[NumpyizeConstants.KEY_DATASET]:
          self.outer.dct_metadata[NumpyizeConstants.KEY_DATASET][self.meta_info_id] = {}

        if self.meta_info_id not in self.outer.dct_metadata[NumpyizeConstants.KEY_DATASET_ORIGIN]:
          self.outer.dct_metadata[NumpyizeConstants.KEY_DATASET_ORIGIN][self.meta_info_id] = {}

        self.outer.dct_metadata[NumpyizeConstants.KEY_DATASET_ORIGIN][self.meta_info_id][
          NumpyizeConstants.KEY_DATASET_FN_DATA] = fn_data
        self.outer.dct_metadata[NumpyizeConstants.KEY_DATASET_ORIGIN][self.meta_info_id][
          NumpyizeConstants.KEY_DATASET_ABSOLUTE_PATH_FN_DATA] = absolute_path_fn_data
        self.outer.dct_metadata[NumpyizeConstants.KEY_DATASET_ORIGIN][self.meta_info_id][
          NumpyizeConstants.KEY_DATASET_FILENAME] = self.filename

        parse_dates = None
        if bool_parse_dates and self.outer.time_field:
          parse_dates = [self.outer.time_field]

        c = connectors.CSVConnector(log=self.log, DEBUG=DEBUG)
        c.set_data_connector_params(
          dataset=dataset,
          fn_data=fn_data,
          chunksize=chunksize,
          parse_dates=parse_dates,
          absolute_path_fn_data=absolute_path_fn_data
        )
  
      elif connector_type.upper() == ConnectorConstants.ODBC_CONNECTOR:
        database = kwargs.get('database', None)
        uid = kwargs.get('uid', None)
        password = kwargs.get('password', None)
        odbc_driver = kwargs.get('odbc_driver', None)
        port = kwargs.get('port', '')
        table_data = kwargs.get('table_data', None)
        server = kwargs.get('server', None)
        chunksize = kwargs.get('chunksize', None)
        c = connectors.ODBCConnector(log=self.log, DEBUG=DEBUG)
        c.set_data_connector_params(
          server=server,
          database=database, uid=uid, password=password,
          odbc_driver=odbc_driver, port=port,
          table_data=table_data, chunksize=chunksize
        )

      elif connector_type.upper() == ConnectorConstants.DF_CONNECTOR:
        df = kwargs['df']
        c = connectors.DFConnector(log=self.log, DEBUG=DEBUG)
        c.set_data_connector_params(df=df)
      #endif

      return c

    def _compute_min_max_dt(self, df_chunk):
      self.log.start_timer('_compute_min_max_dt', section=self.outer._timers_section)
      crt_min_time = df_chunk[self.outer.time_field].min()
      crt_max_time = df_chunk[self.outer.time_field].max()
      
      self.global_min_dt = min(self.global_min_dt, crt_min_time) \
                           if self.global_min_dt is not None \
                           else crt_min_time
                           
      self.global_max_dt = max(self.global_max_dt, crt_max_time) \
                           if self.global_max_dt is not None \
                           else crt_max_time
      self.log.end_timer_no_skip('_compute_min_max_dt', section=self.outer._timers_section)
      return
    #enddef

    def _auto_compute_weekly_freq(self):
      day_of_week = self.global_min_dt.day_of_week

      dct_day_of_week = {
        0 : 'W-MON', 1 : 'W-TUE', 2 : 'W-WED', 3 : 'W-THU',
        4 : 'W-FRI', 5 : 'W-SAT', 6 : 'W-SUN'
      }

      return dct_day_of_week[day_of_week]


    
    def _describe(self):
      """
      Prints basic information about the current loaded preprocess data:
  
      Constrains:
      -----------
        Must be called after preprocess
      """
      if not self.verbose:
        return

      P = self.outer.Pnp
      
      P("Dataset {} sumar statistics:".format(self.meta_info_id))
      P(repr(self))

      random_series_indexes = np.random.choice(
        range(len(self.outer.series2agg)),
        replace=False,
        size=5
      )

      P("Printing random series ...")
      P("  - min_dt   = the first day of transaction")
      P("  - max_dt   = the last day of transaction")
      for idx in np.sort(random_series_indexes):
        fields = self.outer.series2agg[idx]
        if fields not in self.series_min_dt:
          continue

        min_dt = str(self.series_min_dt[fields])[:10]
        max_dt = str(self.series_max_dt[fields])[:10]
        str_fields = '[{}]'.format(
          '_'.join(map(lambda x:str(x), tuple_scalar(fields)))
        )
  
        P("Series #{:>9} (aggregation {:>15}) - min_dt=[{}] / max_dt=[{}]".format(
          idx, str_fields, min_dt, max_dt
        ))

      P("\n")
      return
    #enddef
    
    def _data_check(self, df_chunk):
      """
      Checks the data for `inf` or `nan` values and raises a `ValueError`
      if any found
  
      Parameters:
      ----------
        `df_chunk`: the data chunk to be checked
  
      Returns:
      -------
        None
      """
      
      def _has_nulls(col):
        return df_chunk[col].isnull().values.any()
      
      def _has_infs(col):
        return np.inf in df_chunk[col].unique()
      
      for column in df_chunk.columns:
        if np.issubdtype(df_chunk[column].dtype, np.number):
          if _has_infs(column):
            raise ValueError
        
        if column not in self.ffill_bfill_columns:
          if _has_nulls(column):
            self.ffill_bfill_columns.append(column)
            self.P("WARNING! Column '{}' added automatically to ffill_bfill_columns because it contains nans!".format(column), color='r')
        
      return
    #enddef
    
    def _filter_series(self, df_chunk, *filters):
      """
      Applies basic filters over a chunk of data to discard unwanted data
  
      Parameters:
      -----------
        1. `df_chunk`: the data chunk to be filtered
        ......
        (column, string operator, value, [optional convert to datetime])
  
      Returns:
      --------
        Filtered chunk
  
      """
      
      dct_operators = {
        "<"    : operator.lt,
        "<="   : operator.le,
        "=="   : operator.eq,
        "!="   : operator.ne,
        ">="   : operator.ge,
        ">"    : operator.gt,
        "isin" : lambda x,v: x.isin(v)
      }

      if len(filters) == 0:
        return df_chunk

      self.log.start_timer('_filter_series', section=self.outer._timers_section)
      self.P("Filtering series chunk with [{}] ...".format(filters))

      for arg in filters:
        assert len(arg) >= 3
        convert_to_datetime = False
        column = arg[0]
        string_op = arg[1]
        assert string_op in dct_operators
        value = arg[2]
        if len(arg) > 3:
          convert_to_datetime = arg[3]
        
        if convert_to_datetime:
          value = pd.to_datetime(value)
        
        op = dct_operators[string_op]
        
        df_chunk = df_chunk[op(df_chunk[column], value)]
      #endfor
      self.P("", t=True)
      self.log.end_timer_no_skip('_filter_series', section=self.outer._timers_section)
      return df_chunk
    #enddef
    
    def _save_preprocessed_data(self, prefix=None):
      """
      Saves the preprocessed data to disk.

      Parameters:
      -----------
        1. `prefix`: Prefix of the filename.
        If it's not specified `Logger` will pick one for it

      Returns:
      --------
        None

      Constrains:
      -----------
        Must be called after preprocess
      """
      if not prefix:
        prefix = self.log.file_prefix
  
      preprocessed_data = {
        'filters' : self.filters,
        'freq'    : self.freq,
        'column_names' : self.column_names,
        'ffill_bfill_columns' : self.ffill_bfill_columns,
        'dataset_type' : self.dataset_type,
        'filename' : self.filename,

        'global_min_dt' : self.global_min_dt,
        'global_max_dt' : self.global_max_dt,
        'series_min_dt' : self.series_min_dt,
        'series_max_dt' : self.series_max_dt,
      }
      
      fn = os.path.join(
        prefix,
        self._fn_preprocess.format(self.meta_info_id)
      )
      self.log.save_pickle_to_output(preprocessed_data, fn)

      return
    #enddef
    
    def _load_preprocessed_data(self, prefix):
      """
        Loads the preprocessed data from disk.
  
        Parameters:
          1. `prefix`: Prefix of the filename.
  
        Returns:
          None
      """
      datafiles = os.listdir(self.outer.get_dir(prefix))
      datafiles = [x for x in datafiles if
                   x == self._fn_preprocess.format(self.meta_info_id)]
  
      if len(datafiles) > 1:
        raise ValueError("Datafiles content: {}".format(datafiles))
      elif len(datafiles) == 0:
        return False
      
      fn = os.path.join(prefix, datafiles[0])
      preprocessed_data = self.log.load_pickle_from_output(fn, verbose=self.verbose)
      
      self.filters       = preprocessed_data['filters']
      self.freq          = preprocessed_data['freq']
      self.column_names  = preprocessed_data['column_names']
      self.global_min_dt = preprocessed_data['global_min_dt']
      self.global_max_dt = preprocessed_data['global_max_dt']
      self.series_min_dt = preprocessed_data['series_min_dt']
      self.series_max_dt = preprocessed_data['series_max_dt']
      self.ffill_bfill_columns = preprocessed_data['ffill_bfill_columns']
      self.dataset_type = preprocessed_data.get('dataset_type', None)
      self.filename = preprocessed_data.get('filename', None)
      
      self._describe()
      self._create_date_range()
      self._compute_series_ids_min_max_dt()
      return True
    #enddef
    
    def _create_date_range(self):
      self.date_range, self.dates2id = self.outer.time_mapping(
        start=self.global_min_dt,
        end=self.global_max_dt,
        freq=self.freq
      )
      self.strdates2id = {
        k.strftime('%Y-%m-%d'):v for k,v in self.dates2id.items()
      }
      return
    
    def preprocess(self):
      """
      The preprocess method computes all the preprocessing for the data
      and needs configuration for the data connector.
      In the end saves the preprocess data to the disk.
  
      """

      self.P("Start - Preprocess step for dataset {}".format(self.meta_info_id), color='b')

      gen = self.connector.chunk_generator()
      for itr, df_chunk in enumerate(gen):
        self.P("Start - Chunk {} [{}] ...".format(itr, df_chunk.shape))

        if itr == 0:
          if is_void_list(self.column_names):
            all_columns = df_chunk.columns.to_list()
            self.column_names = list(set(all_columns) - set(self.outer.aggregation_fields + [self.outer.time_field]))
            self.keep_columns += self.column_names
          #endif

          other_covariate_columns = list(set(self.column_names) - set([self.outer.target_field]))
          self.outer.dct_metadata[NumpyizeConstants.KEY_OTHER_COVARIATE_FIELDS] += other_covariate_columns
          self.outer.dct_metadata[NumpyizeConstants.KEY_OTHER_COVARIATE_FIELDS] = list(set(self.outer.dct_metadata[NumpyizeConstants.KEY_OTHER_COVARIATE_FIELDS]))
        #endif
        
        df_chunk = df_chunk[self.keep_columns]
        self._data_check(df_chunk)
        df_chunk = self._filter_series(df_chunk, *self.filters)
        
        self._compute_min_max_dt(df_chunk)

        if self.freq == 'W':
          self.freq = self._auto_compute_weekly_freq()

        if self.is_master:
          self.outer.compute_mappings(df_chunk)

        self._compute_series_min_max_dt(df_chunk)
        
        self.P("End - Chunk {} [{}].".format(itr, df_chunk.shape))
      #endfor
      
      self._compute_series_ids_min_max_dt()
      self._create_date_range()

      if self.is_master:
        self.outer.dct_metadata[NumpyizeConstants.KEY_FREQ] = self.freq
        self.outer.dct_metadata[NumpyizeConstants.KEY_NR_SERIES] = len(self.outer.series2agg)
        self.outer.dct_metadata[NumpyizeConstants.KEY_NR_STEPS] = self.date_range.shape[0]
        self.outer.save_mappings(prefix=self.outer.prefix)

      self._save_preprocessed_data(prefix=self.outer.prefix)

      self.P("Dataset {} preprocessed ... describe:".format(self.meta_info_id))
      self._describe()

      self.P("#" * 30)
      self.log.show_timers()
      self.P("#" * 30)

      self.P("End - Preprocess step for dataset {}".format(self.meta_info_id), color='b')
      return
    #enddef
    
    def _compute_series_ids_min_max_dt(self):
      self.series_ids_min_dt = {
        self.outer.agg2series.get(k, None) : v for k,v in self.series_min_dt.items()
      }
      self.series_ids_max_dt = {
        self.outer.agg2series.get(k, None) : v for k,v in self.series_max_dt.items()
      }
      self.series_ids_min_dt.pop(None, None)
      self.series_ids_max_dt.pop(None, None)
      return
    
    def _compute_series_min_max_dt(self, df_chunk):
      self.P("Computing min/max datetime per each serie ...")
      self.log.start_timer('_compute_series_min_max_dt', section=self.outer._timers_section)
      self.log.start_timer('groupby_aggregation_fields_2', section=self.outer._timers_section)
      grp = df_chunk.groupby(self.outer.aggregation_fields)
      self.log.end_timer_no_skip('groupby_aggregation_fields_2', section=self.outer._timers_section)
  
      # compute min dt
      dct_min_dt = grp[self.outer.time_field].min().to_dict()
      self.series_min_dt = {
        x : min(self.series_min_dt.get(x, pd.Timestamp.max),
                dct_min_dt.get(x, pd.Timestamp.max))
        for x in set(self.series_min_dt.keys()) | set(dct_min_dt.keys())
      }
  
      # compute max dt
      dct_max_dt = grp[self.outer.time_field].max().to_dict()
      self.series_max_dt = {
        x : max(self.series_max_dt.get(x, pd.Timestamp.min),
                dct_max_dt.get(x, pd.Timestamp.min))
        for x in set(self.series_max_dt.keys()) | set(dct_max_dt.keys())
      }
      
      self.log.end_timer_no_skip('_compute_series_min_max_dt', section=self.outer._timers_section)
      return
    #enddef
    
    def process(self, series_chunksize=None, column_sanity_check=None):
    
      nr_series = len(self.outer.series2agg)
      if not series_chunksize:
        series_chunksize = nr_series

      self.P("Start - Process step for dataset {}".format(self.meta_info_id), color='b')
  
      for series_chunk_idx in range(0, nr_series, series_chunksize):
        gen = self.connector.chunk_generator()
        data = None
  
        self.log.start_timer('process_dataset_pass', section=self.outer._timers_section)
        for itr, df_chunk in enumerate(gen):
          self.P("Start - Chunk #{} [{}] ...".format(itr, df_chunk.shape))
          df_chunk = df_chunk[self.keep_columns]
          df_chunk = self._filter_series(df_chunk, *self.filters)
          df_chunk = self._map_aggregation2series(df_chunk)
          
          df_chunk = self._select_subseries(
            df_chunk,
            series_chunk_idx=series_chunk_idx,
            series_chunksize=series_chunksize
          )
          
          # in case none of the subseries are in the current chunk, continue
          if df_chunk.empty:
            continue
          
          if data is None:
            data = df_chunk
          else:
            data = data.append(df_chunk)
          #endif
          self.P("End - chunk #{} [{}].".format(itr, df_chunk.shape))
        #endfor
        self.log.end_timer_no_skip('process_dataset_pass', section=self.outer._timers_section)
        
        if self.is_master: #TODO
          data = self._normalize_series(data)
        
        dataset = self._parse_series_sparse(
          data,
          *self.column_names,
          column_sanity_check=column_sanity_check
        )
  
        index = 'subset_' + str(series_chunk_idx//series_chunksize)
        write_mode = 'w'
        if series_chunk_idx > 0:
          write_mode = 'a'

        self._save_sparse_series(
          index=index,
          dataset=dataset,
          write_mode=write_mode,
          prefix=self.outer.prefix
        )
        self.P("#" * 30)
        self.log.show_timers()
        self.P("#" * 30)

        self.P("End - Process step for dataset {}".format(self.meta_info_id), color='b')
      return
    #enddef
    
    def _map_aggregation2series(self, df_chunk):
      """
      Maps aggregation to series_id for each data point
  
      Parameters:
      -----------
        1. `df_chunk`: DataFrame to be mapped.
  
      Returns:
      --------
        DataFrame
  
      Constraints:
      ------------
        The `chunk` parameter must have `aggregation_fields` columns
  
      """
      self.log.start_timer('_map_aggregation2series', section=self.outer._timers_section)
      self.P("  Mapping aggregation to series_ids ...")
      df_chunk = df_chunk.set_index(self.outer.aggregation_fields)
      series_index = df_chunk.index.to_series().map(self.outer.agg2series)
      df_chunk[NumpyizeConstants.COLUMN_SERIES_INDEX] = series_index
      if df_chunk[NumpyizeConstants.COLUMN_SERIES_INDEX].hasnans:
        df_chunk.dropna(subset=[NumpyizeConstants.COLUMN_SERIES_INDEX], inplace=True)
        df_chunk[NumpyizeConstants.COLUMN_SERIES_INDEX] = df_chunk[NumpyizeConstants.COLUMN_SERIES_INDEX].astype(np.int32)

      self.P("  ", t=True)
      self.log.end_timer_no_skip('_map_aggregation2series', section=self.outer._timers_section)
      return df_chunk.reset_index()
    #enddef
    
    def _select_subseries(self, df_chunk, series_chunk_idx, series_chunksize):
      """
      From the given data, selects only the data points with series_id
        between `subseries_index` and `subseries_index` +
        `subseries_chunk_size`
  
      Parameters:
      -----------
        1. `chunk`: DataFrame to be mapped.
        2. `subseries_index`: The first series_id of the current
        subseries chunk
        3. `subseries_chunk_size`: The size of each subseries chunk
  
      Returns:
      --------
        DataFrame
  
      Constraints:
      ------------
        `df_chunk` parameter must have column `const.COLUMN_SERIES_INDEX`
      """
      
      self.log.start_timer('select_subseries', section=self.outer._timers_section)
      self.P("  Selecting subseries from index {} to {} ..."
         .format(series_chunk_idx, series_chunk_idx+series_chunksize))
      range_finish = min(
        series_chunk_idx + series_chunksize,
        len(self.outer.series2agg)+1
      )
      select = list(range(series_chunk_idx, range_finish))
      df_chunk = df_chunk[df_chunk[NumpyizeConstants.COLUMN_SERIES_INDEX].isin(select)]
      self.P("  ", t=True)
      self.log.end_timer_no_skip('select_subseries', section=self.outer._timers_section)
      return df_chunk.reset_index()
    #enddef
    
    def _normalize_series(self, df_chunk, *rules):
      """
      Aggregates multiple transactions per period:
        - Sums quantity
        - TODO in the future - other columns will have particular aggregation
        
      Parameters:
      -----------
        1. `df_chunk`: DataFrame to be normalized
  
      Returns:
      --------
        DataFrame
  
      Constraints:
      ------------
        `df_chunk` parameter must have columns `const.COLUMN_SERIES_INDEX`,
        `time_field`, `quantity_field`
      """
      # because we can have multiple rows for each series index and timestamp,
      # we need to aggregate the data
      self.log.start_timer('_normalize_series', section=self.outer._timers_section)
      self.P("Normalizing series data (multiple sales per step) ...")
      grp = df_chunk.groupby([NumpyizeConstants.COLUMN_SERIES_INDEX,
                              self.outer.time_field])
      df_grp = grp[self.column_names].sum() #TODO
      self.P("", t=True)
      self.log.end_timer('_normalize_series', section=self.outer._timers_section)
      return df_grp.reset_index()
    #enddef
    
    def _parse_series_sparse(self, df, *value_columns, column_sanity_check=None):
      """
       Splits a DataFrame into multiple data sets in order to be
       saved as sparce matrices
  
       Parameters:
         1. `partial_data`: DataFrame to be parsed
  
       Returns:
         `datasets` - a dictionary of data sets with the following elements:
            - quantity_field: a series of all transactions
            - price_field: a series of all prices
            - 'col_ind': the column index for each transaction
            - 'row_ind': the row index for each transaction
            - 'mapping': a map from row_ind to series_id
  
  
       Constraints:
         - `partial_data` must have columns `const.COLUMN_SERIES_INDEX`,
         `time_field`, `quantity_field`
         - needs `global_statistics` dict to be loaded in
         order to extract min_dt and max_dt
  
       """
      self.log.start_timer('_parse_series_sparse', section=self.outer._timers_section)
      self.P('Processing series as sparse matrices ...')
      
      df[NumpyizeConstants.COLUMN_TIME_ID] = df[self.outer.time_field].map(self.dates2id)
      self.log.start_timer('sort_values', section=self.outer._timers_section)
      df.sort_values(by=[NumpyizeConstants.COLUMN_SERIES_INDEX, self.outer.time_field],
                     inplace=True)
      self.log.end_timer_no_skip('sort_values', section=self.outer._timers_section)

      col_ind = df[NumpyizeConstants.COLUMN_TIME_ID]\
                  .values\
                  .astype(np.int32)
      mapping = df[NumpyizeConstants.COLUMN_SERIES_INDEX]\
                  .unique()\
                  .astype(np.int32)
      row_ind = df[NumpyizeConstants.COLUMN_SERIES_INDEX]\
                  .map({j:i for i,j in enumerate(mapping)})\
                  .values\
                  .astype(np.int32)

      dataset = {}
      for v in value_columns:
        assert v in df.columns
        values = df[v].values
        mask_non_zero = values != 0
        dataset[v] = {}

        dataset[v][NumpyizeConstants.SPARSE_VALUES]  = values[mask_non_zero]
        dataset[v][NumpyizeConstants.SPARSE_COL_IND] = col_ind[mask_non_zero]
        dataset[v][NumpyizeConstants.SPARSE_ROW_IND] = row_ind[mask_non_zero]
      #endfor

      dataset[NumpyizeConstants.SPARSE_MAPPING] = mapping
      dataset[NumpyizeConstants.SPARSE_FREQ] = self.freq
      
      self.P("", t=True)
      self.log.end_timer_no_skip('_parse_series_sparse', section=self.outer._timers_section)

      if column_sanity_check is None:
        if self.outer.target_field in dataset.keys():
          column_sanity_check = self.outer.target_field

      self._sanity_check_sparse_dataset(
        dataset,
        target=column_sanity_check,
        nr_series=3
      )

      return dataset
    #enddef
    
    def _sanity_check_sparse_dataset(self, dataset,
                                     target=None, nr_series=3):
      self.P("Sanity check sparse dataset:")
      self.P("-" * 30)
      
      if not target:
        target = self.column_names[0]
  
      P = partial(self.log.P, noprefix=True)
      sparse_row_ind = dataset[target][NumpyizeConstants.SPARSE_ROW_IND]
      sparse_col_ind = dataset[target][NumpyizeConstants.SPARSE_COL_IND]
      freq = dataset[NumpyizeConstants.SPARSE_FREQ]
      sparse_mapping = dataset[NumpyizeConstants.SPARSE_MAPPING]

      nr_lines = np.max(sparse_row_ind) + 1

      series_sparse_idx = np.random.choice(
        np.arange(nr_lines),
        replace=False,
        size=nr_series
      )
      
      series_real_id = sparse_mapping[series_sparse_idx]
      
      for i in range(nr_series):
        s_id = series_real_id[i]
        s_idx = series_sparse_idx[i]
        non_zero = np.where(sparse_row_ind == s_idx)[0]
        targets = dataset[target][NumpyizeConstants.SPARSE_VALUES][non_zero]
        time_ids = sparse_col_ind[non_zero]
        time_range, dct_map = self.outer.time_mapping(
          start=self.global_min_dt,
          end=self.global_max_dt,
          freq=freq
        )
        dct_inv_map = {v:k for k,v in dct_map.items()}
        timestamps = [dct_inv_map[t] for t in time_ids]
        
        nr_targets = targets.shape[0]
        lst_print = []
        for j in range(nr_targets):
          lst_print.append(
            timestamps[j].strftime("%Y-%m-%d") + " / {:>5.2f}".format(targets[j])
          )
        
        agg = self.outer.series2agg[s_id]
        P("Series id #{} ({}) [{} - {}]:".format(
          s_id,
          agg, 
          self.series_min_dt[agg].strftime('%Y-%m-%d'),
          self.series_max_dt[agg].strftime('%Y-%m-%d'))
        )
        
        self.log.print_on_columns(
          *lst_print,
          nr_print_columns=3,
          nr_print_chars=20
        )
        P("")
      #endfor
      return
    #enddef
    
    def _save_sparse_series(self,
                            index,
                            dataset,
                            write_mode='w',
                            prefix=None):
      """
      Saves a subset of the data as a hdf5 file.
  
      Parameters:
      -----------
        1. `index`: the name of the subset
        2. `dataset`: the data sets to be saved
        3. `prefix`: the file prefix, if None specified,
        the method will use log.file_prefix
  
      Returns:
      --------
        None
      """
      if not prefix:
        prefix = self.log.file_prefix
  
      fn = os.path.join(
        self.outer.get_dir(prefix),
        self._fn_series.format(self.meta_info_id)
      )

      self.P("Saving index '{}' of dataset to '{}' ...".format(index, fn))
      save_dict_to_hdf5(dataset, fn, root=index, write_mode=write_mode)
      return
    #enddef
    
    def _load_sparse_series_keys(self, prefix):
      """
      Loads the subset keys from a series file and returns them as a list.
      The file must have the name `prefix`_series_file.hdf5.
      If no prefix is specified, it will use log.file_prefix
  
      Parameters:
      -----------
        1. `prefix`: the filename prefix
  
      Returns:
      --------
        A list of subset keys
      """
      import h5py

      datafiles = os.listdir(self.outer.get_dir(prefix))
      datafiles = [x for x in datafiles if
                   x == self._fn_series.format(self.meta_info_id)]
  
      if len(datafiles) != 1:
        raise ValueError("Datafiles content: {}".format(datafiles))
  
      fn = os.path.join(
        self.outer.get_dir(prefix),
        datafiles[0]
      )

      data_file = h5py.File(fn, 'r')
      keys = list(data_file.keys())
      if False:
        self.P("Loaded sparse series keys from '{}':\n{}".format(fn, keys))
      return keys
    #enddef
  
    def _load_sparse_series_subset(self, subset, prefix, sanity=True):
      """
      Generator that yields series data saved as sparse matrices
      and returns a dictionary with each dataset name as a key,
      and the dataset as value
      The file must have the name `prefix`_series_file.hdf5.
      If no prefix is specified, it will use log.file_prefix
  
      Parameters:
      -----------
        1. `prefix`: the filename prefix
        2. `subset`: the subset of the data to be loaded
  
      Returns:
      --------
        `data`: dictionary with datasets name as keys and
        datasets data as values
      """
      
      datafiles = os.listdir(self.outer.get_dir(prefix))
      datafiles = [x for x in datafiles if
                   x == self._fn_series.format(self.meta_info_id)]
  
      if len(datafiles) != 1:
        raise ValueError("Datafiles content: {}".format(datafiles))
      
      fn = os.path.join(
        self.outer.get_dir(prefix),
        datafiles[0]
      )
      if False:
        self.P("Loading sparse series (subset={}) from '{}' ...".format(subset, fn))

      dataset = load_dict_from_hdf5(fn, root=subset)

      if False:
        self.P(" ", t=True)
      
      if sanity:
        self._sanity_check_sparse_dataset(
          dataset,
          target=None,
          nr_series=3
        )
      
      return dataset
    #enddef
    
    def _load_sparse_series(self, prefix, sanity=True):
      keys = self._load_sparse_series_keys(prefix=prefix)
      datasets = []
      
      for k in keys:
        datasets.append(self._load_sparse_series_subset(
          k,
          prefix=prefix,
          sanity=sanity)
        )
      #endfor
      
      return datasets
    #enddef
    
    def _sparse_series_to_array(self, *datasets):
      """
      Converts series from sparse array representation to numpy arrays.
      Each row of the numpy array represents one series while each column
      represents one timestep
  
      Parameters:
      -----------
        1. `datasets`: a dictionary with the stored datasets.
        It must contain the following keys:
          - `row_ind`: the row index for each transaction
          - `col_ind`: the column index for each transaction
          - `mapping` : 
          - `quantity_field`: the quantity sold for each transaction
  
      Returns:
      --------
        1.`np_qty`: quantity series as a numpy array,
        each row is a series, each column is a timestamp
        3.`series_map`: mapping as a list from each row number to series_id
      """
      if False:
        self.P("Converting sparse series to numpy array ...")
      self.log.start_timer('_sparse_series_to_array_loop', section=self.outer._timers_section)
      lst_np_datasets = []
      lst_np_mappings = []
      for dataset in datasets:
        self.log.start_timer('_sparse_series_to_array_dataset', section=self.outer._timers_section)
        mapping = dataset[NumpyizeConstants.SPARSE_MAPPING]
        freq    = dataset[NumpyizeConstants.SPARSE_FREQ]
        
        arrays = {}
        for k in set(dataset.keys()) - set(NumpyizeConstants.SPARSE_DEFAULTS):
          values  = dataset[k][NumpyizeConstants.SPARSE_VALUES]
          row_ind = mapping[dataset[k][NumpyizeConstants.SPARSE_ROW_IND]]
          col_ind = dataset[k][NumpyizeConstants.SPARSE_COL_IND]
          data = (values, (row_ind, col_ind))
          shape = (len(self.outer.series2agg), len(self.dates2id))
          sparse_data = sparse.csr_matrix(data, shape=shape)
          
          np_values = np.squeeze(np.asarray(sparse_data.todense()))
          arrays[k] = np_values
        #endfor
        lst_np_datasets.append(arrays)
        lst_np_mappings.append(mapping)
        self.log.end_timer_no_skip('_sparse_series_to_array_dataset', section=self.outer._timers_section)
      #endfor
      
      self.log.end_timer_no_skip('_sparse_series_to_array_loop', section=self.outer._timers_section)
      if False:
        self.P("", t=True)
      
      assert len(lst_np_datasets) == len(lst_np_mappings)
      return lst_np_datasets, lst_np_mappings, freq
    #enddef
  #endclass Dataset
#endclass DataMaster

if __name__ == '__main__':

  # run it twice; the first time is slower because it has to compute everything;
  # the second time will run faster because everything is cached in the "PREFIX" folder

  from naeural_core import Logger
  # import pandas as pd

  # in our case we will work with the current cache, not dropbox
  log = Logger(lib_name='TST', base_folder='dropbox', app_folder='_local_data/_product_dynamics/_experiments', TF_KERAS=False)

  # set the fields accordingly
  # prefix is the folder in logger's _output where all the sparse series objects and metadata are saved.
  TARGET_FIELD = 'Qtty'
  PREFIX = '20220310'
  eng = TimeseriesDataMaster(
    log=log, prefix=PREFIX,
    aggregation_fields=['ItemId', 'SiteId'],
    time_field='TimeStamp',
    target_field=TARGET_FIELD,
  )

  # in our case, these will be in-memory
  df_train = log.load_dataframe(fn='train.csv', timestamps=['TimeStamp'])
  df_delta = log.load_dataframe(fn='delta.csv', timestamps=['TimeStamp'])

  ds_master = eng.create_dataset()
  ds_delta = eng.create_dataset()

  # ds_master.setup(
  #   column_names=[TARGET_FIELD],
  #   freq='w-mon', # set to 'h'
  #   df=df_train
  # )
  #
  # ds_delta.setup(
  #   column_names=[TARGET_FIELD],
  #   freq='w-mon', # set to 'h'
  #   df=df_delta
  # )

  dct_datasets_master, _, _ = ds_master()
  dct_datasets_delta,  _, _ = ds_delta()

  np_master = dct_datasets_master[TARGET_FIELD]
  np_delta = dct_datasets_delta[TARGET_FIELD]

  np_master.shape # (262933, 157)
  np_delta.shape # (262933, 19) - all the series that are not found at all in the delta dataset are filled with 0!

from naeural_core import DecentrAIObject
import pandas as pd
import time as tm

class AbstractConnector(DecentrAIObject):
  def __init__(self, **kwargs):
    self.reader = None
    self.df_item_mapping = None

    super().__init__(prefix_log='[CONN]', **kwargs)
    return

  def set_reader(self):
    self.D("Setting reader ...")
    return

  def chunk_generator(self):
    self.D("Chunck generator.")
    self.set_reader()
    for df_chunk in self.reader:
      d1, d2 = df_chunk.shape
      self.D("Chunk events: {} / Chunk columns: {}".format(d1, d2))
      yield df_chunk


class CSVConnector(AbstractConnector):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self.parse_dates = None
    return

  def set_data_connector_params(self,
                                dataset=None,
                                fn_data=None,
                                absolute_path_fn_data=None,
                                chunksize=None,
                                parse_dates=None):
    self._parse_config_data(
      dataset,
      fn_data=fn_data,
      absolute_path_fn_data=absolute_path_fn_data,
      chunksize=chunksize
    )
    self.parse_dates = parse_dates
    return

  def set_item_mapping(self, fn_nomenclator=None):
    if fn_nomenclator is not None:
      if fn_nomenclator != '':
        self.D("Setting item mapping ...")
        self.df_item_mapping = self.log.load_dataframe(fn_nomenclator)
    return

  def set_reader(self):
    assert self.fn_data is not None
    super().set_reader()

    if type(self.fn_data) is not list:
      if self.absolute_path_fn_data:
        fn = self.fn_data
      else:
        fn = self.log.get_data_file(self.fn_data)

      self.D("Created csv connector based on '{}' with chunksize={}"
             .format(fn, self.chunksize))
      self.reader = pd.read_csv(fn, iterator=True, chunksize=self.chunksize,
                                parse_dates=self.parse_dates)
    else:
      if not self.absolute_path_fn_data:
        files = list(map(lambda x: self.log.get_data_file(x), self.fn_data))

      self.D("Created csv connector based on multiple files '{}' with chunksize={}"
             .format(files, self.chunksize))

      self.reader = self.chunk_from_files(*files)
    return

  def _chunk_from_files(self, *files):
    for fn in files:
      for chunk in pd.read_csv(fn, iterator=True, chunksize=self.chunksize,
                               parse_dates=self.parse_dates):
        yield chunk

class DFConnector(AbstractConnector):
  def __init__(self, **kwargs):
    self.df = None
    super(DFConnector, self).__init__(**kwargs)
    return

  def set_data_connector_params(self, df):
    self.df = df
    return

  def _pseudo_gen(self):
    yield self.df

  def set_reader(self):
    super().set_reader()
    self.reader = self._pseudo_gen()
    return


class ODBCConnector(AbstractConnector):
  def __init__(self, **kwargs):
    self.sql_query = "SELECT * FROM {};"
    super().__init__(**kwargs)
    return

  def set_data_connector_params(self, dataset=None, table_data=None,
                                odbc_driver=None, server=None, port=None,
                                database=None, uid=None, password=None,
                                chunksize=None, nr_conn_retry=15):
    self._parse_config_data(
      dataset,
      table_data=table_data,
      odbc_driver=odbc_driver,
      server=server,
      port=port,
      database=database,
      uid=uid,
      password=password,
      chunksize=chunksize,
      nr_conn_retry=nr_conn_retry
    )

    for i, x in enumerate([self.table_data, self.odbc_driver,
                           self.server, self.port,
                           self.database, self.uid, self.password,
                           self.nr_conn_retry]):
      assert x is not None, 'Variable #{} is None'.format(i)

    str_conn = "DRIVER={}; ".format(self.odbc_driver)
    str_conn += "SERVER={}; ".format(self.server)
    str_conn += "PORT={}; ".format(self.port)
    str_conn += "DATABASE={}; ".format(self.database)
    str_conn += "UID={}; ".format(self.uid)
    str_conn += "PWD={};".format(self.password)

    count = 0
    connected = False

    while count < self.nr_conn_retry and connected == False:
      count += 1
      try:
        import pyodbc
        self.D("ODBC Conn try {}: {}...".format(count, str_conn))
        self.cnxn = pyodbc.connect(str_conn)
        self.D("Connection created.")
        connected = True
      except Exception as err:
        self.D("FAILED ODBC Conn on retry: {}!".format(count))
        self.D("ERROR: {}".format(err))
        tm.sleep(0.5)

    return

  def set_item_mapping(self, table_nomenclator=None):
    self.D("Setting item mapping ...")
    self.df_item_mapping = pd.read_sql(sql=self.sql_query.format(table_nomenclator),
                                       con=self.cnxn)
    return

  def set_reader(self):
    super().set_reader()

    sql_query = "SELECT COUNT(*) FROM {};".format(self.table_data)
    cursor = self.cnxn.cursor()
    cursor.execute(sql_query)
    nr_rows = cursor.fetchone()[0]

    if self.chunksize is None:
      self.chunksize = nr_rows

    self.reader = pd.read_sql(sql=self.sql_query.format(self.table_data),
                              con=self.cnxn,
                              chunksize=self.chunksize)
    self.D("Created ODBC connector based on '{}.{}' with chunksize={}"
           .format(self.database, self.table_data, self.chunksize))
    return


if __name__ == '__main__':
  from naeural_core import Logger

  log = Logger(lib_name='C',
               config_file='radiography/config.txt',
               TF_KERAS=False)
  c = ODBCConnector(log)
  c.set_data_connector_params()
  gen = c.chunk_generator()
import pyodbc
import pandas as pd

from naeural_core.local_libraries.db_conn.base import BaseConnector

class ODBCConnector(BaseConnector):
  def __init__(self, **kwargs):
    self._cnxn = None
    self._default_sql_query = "SELECT * FROM {};"
    super(ODBCConnector, self).__init__(**kwargs)
    return

  def _connect(self, **kwargs):
    str_conn = ''
    for k,v in kwargs.items():
      str_conn += '{}={};'.format(k,v)
    self._cnxn = pyodbc.connect(str_conn)
    return

  def check_connection(self):
    try:
      cursor = self._cnxn.cursor()
      cursor.execute("SELECT 1")
    except pyodbc.Error as e:
      return False
    return True

  def _create_reader(self, **kwargs):
    table_data = kwargs.get('table_data', '')
    chunksize = kwargs.get('chunksize', None)
    sql_query = kwargs.get('sql_query', '')
    assert len(table_data) > 0 or len(sql_query) >0, "Must either have table or SQL query"
    if len(sql_query) == 0:      
      sql_query = self._default_sql_query.format(table_data)

    reader = pd.read_sql(
      sql=sql_query,
      con=self._cnxn,
      chunksize=chunksize
     )
    return reader




if __name__ == '__main__':

  from naeural_core import Logger

  log = Logger(
    lib_name='DB', base_folder='dropbox', app_folder='_lens_data/_product_dynamics',
    TF_KERAS=False
  )

  config = {
    'CONNECT_PARAMS' : {
      'DRIVER' : '{ODBC Driver 17 for SQL Server}',
      'SERVER' : 'cloudifiersql1.database.windows.net',
      'PORT' : 1433,
      'DATABASE' : 'operational',
      'Uid' : 'damian',
      'Pwd' : 'MLteam2021!',
      'Encrypt' : 'yes',
      'TrustServerCertificate' : 'no',
      'Connection Timeout': 30,
    },

    'QUERY_PARAMS' : {
      'default' : {
        'table_data' : 'Invoices',
        'sql_query' : "", ### custom sql query on 'TABLE_DATA' (groupby etc etc); if empty it uses a default sql query
        'chunksize' : 200, ### if removed, then the generator conn.data_chunk_generator() will have only one step
      },

      'default2' : {
        'table_data' : 'Invoices',
        "sql_query" : "",
        'chunksize' : 200,
      }
    }
  }

  conn = ODBCConnector(log=log, config=config)
  conn.connect(nr_retries=5)
  dct_data = conn.get_all_data()
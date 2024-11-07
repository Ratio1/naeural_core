import pymssql
import pandas as pd

from naeural_core.local_libraries.db_conn.base import BaseConnector

class PyMSSQLConnector(BaseConnector):
  def __init__(self, **kwargs):
    self._cnxn = None
    self._default_sql_query = "SELECT * FROM {};"
    super(PyMSSQLConnector, self).__init__(**kwargs)
    return

  def _connect(self, **kwargs):
    self._cnxn = pymssql.connect(**kwargs)
    return

  def check_connection(self):
    try:
      cursor = self._cnxn.cursor()
      cursor.execute("SELECT 1")
    except pymssql.Error as e:
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
    lib_name='DB', base_folder='.', app_folder='_local_cache',
    TF_KERAS=False
  )
  sql_query = "SELECT * FROM [CAVI_EVENTS].[dbo].[EV_202210]"
  query_params = {'sql_query': sql_query,
      'chunksize': 1000000,}
  config = {
    'CONNECT_PARAMS' : {
      "password": "3edc$RFV",
      "server": "172.16.16.27",
      "user": "caviuser"
    },


  }

  conn = PyMSSQLConnector(log=log, config=config)
  conn.connect(nr_retries=5)
  dct_data = conn.get_data(**query_params)
  print(dct_data)
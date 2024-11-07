from naeural_core import constants as ct

from naeural_core.data.mixins_libs import _SqlQueryAcquisitionMixin
from naeural_core.data.base import DataCaptureThread

from dateutil.relativedelta import relativedelta
from datetime import datetime

_CONFIG = {
  **DataCaptureThread.CONFIG,
  
  'SAVE_LAST_STATE' : True,

  'VALIDATION_RULES' : {
    **DataCaptureThread.CONFIG['VALIDATION_RULES'],
    'SAVE_LAST_STATE': {
      'TYPE': 'bool'
    }
  },


}


class SqlQueryDataCapture(DataCaptureThread, _SqlQueryAcquisitionMixin):
  CONFIG = _CONFIG
  def __init__(self, **kwargs):
    self.odbc_connector = None
    super(SqlQueryDataCapture, self).__init__(**kwargs)
    return

  def startup(self):
    super().startup()
    if not self.cfg_save_last_state:
      self.P("WARNING!!! Sql query DCT is running with `save_last_state` False")

  @property
  def cfg_nr_retries(self):
    return self.cfg_stream_config_metadata.get('NR_RETRIES', 3)

  @property
  def cfg_connector_type(self):
    return self.cfg_stream_config_metadata.get('CONNECTOR_TYPE', 'PYMSSQL')

  def _init(self):
    self._metadata.update(
      # TODO
    )

    self.connection_info = self.cfg_stream_config_metadata['CONNECTION_INFO']
    self.queries = self.cfg_stream_config_metadata.get('QUERY', None)
    self.stored_procedures =  self.cfg_stream_config_metadata.get('STORED_PROCEDURE', None)
    assert self.queries is not None or self.stored_procedures is not None, "A stored procedure or query must be configured"

    if self.stored_procedures is not None and type(self.stored_procedures) is dict:
      self.stored_procedures = [self.stored_procedures]

    if self.queries is not None and type(self.queries) is dict:
      self.queries = [self.queries]

    self._load_state()

    if self.cfg_connector_type == 'PYMSSQL':
      from naeural_core.local_libraries.db_conn.pymssql_conn import PyMSSQLConnector
      config = {
        'CONNECT_PARAMS': {
          **{k.lower():v for k,v in self.connection_info.items()}
        },
      }
      self.conn = PyMSSQLConnector(log=self.log, config=config)
    else:
      from naeural_core.local_libraries.db_conn.odbc_conn import ODBCConnector
      config = {
        'CONNECT_PARAMS': {
          'Encrypt': 'yes',
          'TrustServerCertificate': 'yes',
          'Connection Timeout': 30,
          **self.connection_info
        },
      }
      self.conn = ODBCConnector(log=self.log, config=config)
    #endif

    self.conn.connect(nr_retries=self.cfg_nr_retries)


    # TODO: Check with andrei, to add validation rules / self.cfg_backlog
    if self.cfg_stream_config_metadata.get("BACKLOG") == "FULL":
      self._backlog_start_time = datetime.strftime(datetime(1900, 1, 1, 0, 0), "%Y-%m-%d %H:%M:%S")
    elif self.cfg_stream_config_metadata.get("BACKLOG") == 'YEAR':
      self._backlog_start_time = datetime.strftime(datetime.now() - relativedelta(years=1), "%Y-%m-%d %H:%M:%S")
    elif self.cfg_stream_config_metadata.get("BACKLOG") == 'MONTH':
      self._backlog_start_time = datetime.strftime(datetime.now() - relativedelta(months=1), "%Y-%m-%d %H:%M:%S")
    elif self.cfg_stream_config_metadata.get("BACKLOG") == 'WEEK':
      self._backlog_start_time = datetime.strftime(datetime.now() - relativedelta(weeks=1), "%Y-%m-%d %H:%M:%S")
    elif self.cfg_stream_config_metadata.get("BACKLOG") == 'DAY':
      self._backlog_start_time = datetime.strftime(datetime.now() - relativedelta(days=1), "%Y-%m-%d %H:%M:%S")
    else:
      raise ValueError("Invalid configuration value for 'BACKLOG' : '{}'".format(self.cfg_stream_config_metadata.get("BACKLOG")))
    return

  def _maybe_reconnect(self):
    if self.conn.check_connection(): ### TODO: maybe redo implementation
      return

    self.conn.connect(nr_retries=self.cfg_max_retries)

    if self.conn.check_connection():
      self.P('Done init')
    else:
      msg = 'Data capture could not be initialized after {} retries'.format(self.cfg_max_retries)
      self.P(msg)
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
      )
    return

  def _run_data_aquisition_step(self): ### TODO: refactor typo; it's acquisition
    current_datetime = datetime.now().replace(second=0, microsecond=0)

    # self.P('Preparing sql queries ...')
    sql_queries = self._get_sql_queries(current_datetime)
    if len(sql_queries) == 0:
      return
    
    self.P("Data acquisition step started ...")

    inputs = []
    dct_dfs = {}
    for datasource_name, sql_query in sql_queries.items():
      self.P("Running query for datasource '{}' ...".format(datasource_name))
      query_params = {
          'sql_query' : sql_query,
          'chunksize' : 1000000, ### if removed, then the generator conn.data_chunk_generator() will have only one step
        }
      df_data = self.conn.get_data(**query_params)
      self.P("Query ran successfully")
      self.P("Extracted {} rows".format(len(df_data)))
      if df_data is not None and len(df_data) > 0:
        dct_dfs[datasource_name] = df_data
      #endif
    #endfor

    if len(dct_dfs) > 0:
      inputs.append(
        self._new_input(struct_data=dct_dfs, metadata=self._metadata.__dict__.copy())
      )
      self._add_inputs(inputs)
      self._save_state()
    #endif
    self.P("Data acquisition step finished")
    return

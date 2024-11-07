import urllib3
import minio
from minio import Minio


from naeural_core import constants as ct
from naeural_core.business.base import BasePluginExecutor

__VER__ = '0.2.2'

class MINIO_STATES:
  IN_PROC = 'IN_PROC'
  DONE = 'DONE'

_CONFIG = {
  **BasePluginExecutor.CONFIG,
  'MINIO_HOST'          : None,
  'MINIO_ACCESS_KEY'    : None,
  'MINIO_SECRET_KEY'    : None,
  'MINIO_SECURE'        : None,
  
  'MINIO_TIMEOUT'       : 20,
  
  'ENV_HOST'            : 'EE_MINIO_ENDPOINT',
  'ENV_ACCESS_KEY'      : 'EE_MINIO_ACCESS_KEY',
  'ENV_SECRET_KEY'      : 'EE_MINIO_SECRET_KEY',
  'ENV_SECURE'          : 'EE_MINIO_SECURE',

  'ALLOW_EMPTY_INPUTS'  : True,
  
  'PROCESS_DELAY'       : 5,
  
  'MINIO_IDLE_SECONDS'  : 600,
  
  'MAX_FILES_PER_ITER'  : 2_000,

  'ALERT_DATA_COUNT'    : 1,
  'ALERT_RAISE_VALUE'   : 0.8,
  'ALERT_LOWER_VALUE'   : 0.75,
  'ALERT_MODE'          : 'min',

  'MAX_SERVER_QUOTA'    : 90,
  
  'QUOTA_UNIT'          : ct.FILE_SIZE_UNIT.GB,
  
  'MINIO_DEBUG_MODE'    : True,
  
  'MIN_TIME_BETWEEN_PAYLOADS' : 60 * 5, # 5 minutes

  'VALIDATION_RULES': {
    **BasePluginExecutor.CONFIG['VALIDATION_RULES'],
  },
}

class MinioMonit01Plugin(BasePluginExecutor):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(MinioMonit01Plugin, self).__init__(**kwargs)
    return

  
  def on_init(self):
    infos = f"""
    - Minio v{minio.__version__}
    - Plugin version: {__VER__}
    - Configured server quota: {self.cfg_max_server_quota} {self.cfg_quota_unit}
    """
    if self.is_supervisor_node:
      self.P(f"{self.__class__.__name__} initializing on SUPERVISOR node: {infos}")
    else:
      self.P(f"{self.__class__.__name__} initializing on simple worker node: {infos}")
    self.__global_iter = 0
    self.__last_display = 0
    self.__errors_during_iteration = 0
    self.__minio_idle_wait_time = self.cfg_minio_idle_seconds
    
    self.__iter_start = self.time()
    
    
    self.__default_host = self.cfg_minio_host
    self.__default_access_key = self.cfg_minio_access_key
    self.__default_secret_key = self.cfg_minio_secret_key
    self.__default_secure = self.cfg_minio_secure

    self.__host = None
    self.__access_key = None
    self.__secret_key = None
    self.__secured = None


    self.__minio_client = None
    self.__plugin_state = None
    self.__idle_start = None
    self.__buckets_list = []
    self.__current_bucket_objects_generator = None
    self.__last_minio_payload_time = 0
    self.__reset_size()
    return

  
  def __reset_size(self):
    self.__server_size = 0
    self.__bucket_size = {}
    self.__current_bucket_no = 0
    self.__errors_during_iteration = 0
    return
  
  
  def show_minio_config(self, error=False):
    view_access = self.__access_key[:3] + "*" * (len(self.__access_key) - 3)
    view_secret = self.__secret_key[:3] + "*" * (len(self.__secret_key) - 3)
    infos = f"""
    MINIO VER:    {minio.__version__}
    MINIO_HOST:   {self.__default_host}
    SECURE:       {self.__default_secure}
    ACCESS_KEY:   {view_access}
    SECRET_KEY:   {view_secret}  
    SERVER_QUOTA: {self.cfg_max_server_quota} {self.cfg_quota_unit}
    VERBOSE:      {self.cfg_minio_debug_mode}
    """
    self.P(f"Minio config params: {infos}", color='r' if error else None)
    return
    
  
  
  def __maybe_get_env_config(self):
    if self.is_supervisor_node and self.__default_host is None:
      self.P("Configuring supervisor node using environment variables for Minio connection...")
      self.__default_host = self.os_environ.get(self.cfg_env_host)
      self.__default_access_key = self.os_environ.get(self.cfg_env_access_key)
      self.__default_secret_key = self.os_environ.get(self.cfg_env_secret_key)
      self.__default_secure = self.json_loads(self.os_environ.get(self.cfg_env_secure))
    #endif
    return
  
  
  def __get_connection_config(self):
    self.__maybe_get_env_config()
    self.__host = self.cfg_minio_host or self.__default_host
    self.__access_key = self.cfg_minio_access_key or self.__default_access_key
    self.__secret_key = self.cfg_minio_secret_key or self.__default_secret_key
    self.__secured = self.cfg_minio_secure or self.__default_secure
    return self.__host, self.__access_key, self.__secret_key, self.__secured
  
      

  def __get_next_bucket(self):
    if len(self.__buckets_list) == 0:
      # reset size and get new buckets
      self.__reset_size()
      self.__buckets_list = self.__minio_client.list_buckets()  
      for bucket in self.__buckets_list:
        self.__bucket_size[bucket.name] = {
          'size': 0,
          'objects': 0,
          'minio_errors' : 0,
        }        
      str_buckets = ", ".join(map(lambda x: x.name, self.__buckets_list))    
      DISPLAY_EVERY = 15 * 60 
      if (self.time() - self.__last_display) > DISPLAY_EVERY:
        self.__last_display = self.time()
        self.P("Analysing {} (secured: {}). Iterating through {} buckets: {}".format(
          self.__host, self.__secured,
          len(self.__buckets_list), 
          str_buckets,
          )
        )
    self.__current_bucket_no += 1
    return self.__buckets_list.pop()


  def __maybe_get_new_objects_generator(self):
    if self.__current_bucket_objects_generator is None:
      self.__current_bucket = self.__get_next_bucket()
      self.__current_bucket_objects_generator = self.__minio_client.list_objects(
        bucket_name=self.__current_bucket.name, 
        recursive=True,
      )
      if self.cfg_minio_debug_mode:
        self.P("Iteration {}/{} through bucket '{}'".format(
          self.__current_bucket_no, len(self.__bucket_size),
          self.__current_bucket.name, 
          )
        )
    return


  def __maybe_create_client_connection(self):
    host, access_key, secret_key, secured = self.__get_connection_config()    
    if (
      self.__minio_client is None and
      host is not None and
      access_key is not None and
      secret_key is not None and
      secured is not None
      ):
        self.P("Creating Minio client connection at iteration {} to {}...".format(self.__global_iter, host))
        self.show_minio_config()
        http_client = urllib3.PoolManager(
          timeout=urllib3.Timeout(
            connect=self.cfg_minio_timeout,             
            read=self.cfg_minio_timeout,
          ),
          cert_reqs='CERT_NONE'  # Disable SSL certificate verification
        )
        self.__minio_client = Minio(
          endpoint=host,
          access_key=access_key,
          secret_key=secret_key,
          secure=secured,
          http_client=http_client,
          cert_check=False,  # disable SSL certificate check   
        )
    else:
      if self.__minio_client is None and self.__global_iter < 2:
        self.P(f"Missing Minio connection parameters (is_supervisor_node: {self.is_supervisor_node}, host: {host}, access_key: {access_key}, secret_key: {secret_key}, secure: {secured})", color='r')
    return


  def __process_iter_files(self):
    MAX_ITER_TIME = 10
    start_time = self.time()
    max_nr_files = self.cfg_max_files_per_iter
    if self.cfg_minio_debug_mode:
      self.P("Processing batch of {} files in bucket '{}'".format(
        max_nr_files, self.__current_bucket.name
      ))
    local_count = 0
    for _ in range(max_nr_files):
      try:
        obj = next(self.__current_bucket_objects_generator)
        self.__server_size += obj.size
        local_count += 1
        self.__bucket_size[self.__current_bucket.name]['size'] += obj.size
        self.__bucket_size[self.__current_bucket.name]['objects'] += 1
        if (self.time() - start_time) > MAX_ITER_TIME:
          self.P("WARNING: iterated through {} files out of batch({}) in {:.1f}s on bucket {}".format(
            local_count, max_nr_files, self.time() - start_time, self.__current_bucket.name
          ), color='r')
          break
      except StopIteration:
        self.__current_bucket_objects_generator = None
        self.P("Finished processing bucket '{}'".format(self.__current_bucket.name))
        break
      except Exception as e:
        tb_info = self.trace_info()
        self.P("Error processing bucket '{}' at file no {} of batch({}): {}\n{}".format(
          self.__current_bucket.name, local_count, max_nr_files, e, tb_info), color='r'
        )
        self.show_minio_config(error=True)
        self.__errors_during_iteration += 1
        self.__bucket_size[self.__current_bucket.name]['minio_errors'] += 1
        break
    elapsed_time = self.time() - start_time + 1e-10
    if self.cfg_minio_debug_mode:
      if local_count > 0:
        self.P("  Processed {} objects in {:.1f}s, {:.0f} files/s (total time: {:.1f})".format(
          local_count, elapsed_time, local_count / elapsed_time, self.time() - self.__iter_start,) 
        )
        n_o = self.__bucket_size[self.__current_bucket.name]['objects']
        cut = self.cfg_max_files_per_iter * 3
        if n_o > 0 and (n_o % cut) == 0:
          self.P("  Bucket '{}' size: {:.2f} {} (so far for {} objs)".format(
            self.__current_bucket.name,
            self.convert_size(self.__bucket_size[self.__current_bucket.name]['size'], self.cfg_quota_unit),
            self.cfg_quota_unit,
            self.__bucket_size[self.__current_bucket.name]['objects'],
            )
          )
      else:
        self.P("  No objects processed in bucket '{}'".format(self.__current_bucket.name))
    return


  def _process(self):
    self.__global_iter += 1
    payload = None
    
    # if the plugin has finished a full cycle wait some time done, just return the payload
    if self.__plugin_state == MINIO_STATES.DONE:
      if (self.time() - self.__idle_start) > self.__minio_idle_wait_time:
        self.__plugin_state = MINIO_STATES.IN_PROC
        self.__idle_start = None
        self.__iter_start = self.time()
        self.P("Restarting S3 full inspection iteration..")
      else:
        return payload

    self.__maybe_create_client_connection()
    if self.__minio_client is None:
      return

    self.__maybe_get_new_objects_generator()

    self.__process_iter_files()

    # when the plugin iterated through all buckets send payload
    if self.__current_bucket_objects_generator is None and len(self.__buckets_list) == 0:
      # set done state
      self.__plugin_state = MINIO_STATES.DONE
      self.__idle_start = self.time()
      
      converted_size = self.convert_size(self.__server_size, self.cfg_quota_unit)
      percentage_used = converted_size / self.cfg_max_server_quota

      self.alerter_add_observation(percentage_used)
      
      for b in self.__bucket_size:
        self.__bucket_size[b]['size_h'] = "{:,.2f} {}".format(
          self.convert_size(self.__bucket_size[b]['size'], self.cfg_quota_unit),
          self.cfg_quota_unit,
        )
      
      
      if (self.time() - self.__last_minio_payload_time) > self.cfg_min_time_between_payloads:
        self.__last_minio_payload_time = self.time()
        elapsed = self.time() - self.__iter_start
        msg = """Server size: 
        Total size: {:.1f} {} 
        Configured quota: {:.1f} {}
        Quota percentage: {:.1f} %
        Minio errors during iteration: {}
        Time during analysis: {:.1f}s
        Alerter: {}""".format(
          converted_size, self.cfg_quota_unit, self.cfg_max_server_quota, self.cfg_quota_unit,
          percentage_used * 100, self.__errors_during_iteration, elapsed,
          self.get_alerter_status(),
        )
        color = 'r' if self.alerter_is_alert() else None
        # only log-show detailed info in debug mode
        if self.cfg_minio_debug_mode:
          self.P("{}\nResults:\n{}".format(
            msg, self.json_dumps(self.__bucket_size, indent=2),
            ), color=color
          )
        else:
          self.P(msg, color=color)
        # alerts are handled automatically by above code: adding used percentage
        payload = self._create_payload(
          server_size=self.__server_size,
          buckets=self.__bucket_size,
          status=msg,
          minio_errors_during_analisys=self.__errors_during_iteration,
        )

      if self.__errors_during_iteration > 0:
        self.__minio_idle_wait_time = 10
        self.P("Errors during iteration: {}, lowering idle time to {}s".format(
          self.__errors_during_iteration, self.__minio_idle_wait_time), color='r'
        )
        self.__minio_idle_wait_time = 10
      else:
        self.__minio_idle_wait_time = self.cfg_minio_idle_seconds
      #endif time check
    return payload
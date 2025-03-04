import sys
import platform
import os
import signal
import subprocess
import json
import requests
import psutil

import flask

from functools import partial
from time import sleep, time
from datetime import timedelta

from naeural_core import Logger
from ratio1 import BaseDecentrAIObject
from ratio1.logging.logger_mixins.json_serialization_mixin import NPJson
from naeural_core.local_libraries.model_server_v2.request_utils import get_api_request_body, MSCT

from naeural_core.main.ver import __VER__ as __CORE_VER__

DEFAULT_NR_WORKERS = 5
DEFAULT_HOST = '127.0.0.1'

DEFAULT_SERVER_PATHS = [
  MSCT.RULE_RUN,
  MSCT.RULE_NOTIF,
  MSCT.RULE_UPDATE_WORKERS
]


def get_packages():
  import pkg_resources
  packs = [x for x in pkg_resources.working_set]
  maxlen = max([len(x.key) for x in packs]) + 1
  packs = [
    "{}{}".format(x.key + ' ' * (maxlen - len(x.key)), x.version) for x in packs
  ]
  packs = sorted(packs)  
  return packs  

class FlaskGateway(BaseDecentrAIObject):

  app = None

  def __init__(self, log : Logger,
               workers_location,
               server_names=None,
               workers_suffix=None,
               host=None,
               port=None,
               first_server_port=None,
               server_execution_path=None,
               **kwargs
              ):

    """
    Parameters:
    -----------
    log : Logger, mandatory

    workers_location: str, mandatory
      Dotted path of the folder where the business logic of the workers is implemented

    server_names: List[str], optional
      The names of the servers that will be run when the gateway is opened. This names should be names of .py files
      found in `workers_location`
      The default is None (it takes all the keys in MSCT.CONFIG_ENDPOINTS)

    workers_suffix: str, optional
      For each worker, which is the suffix of the class name.
      e.g. if the worker .py file is called 'get_similarity.py', then the name of the class is GetSimilarity<Suffix>.
      If `workers_suffix=Worker`; then the name of the class is GetSimilarityWorker
      The default is None

    host: str, optional
      Host of the gateway
      The default is None ('127.0.0.1')

    port: int, optional
      Port of the gateway
      The default is None (5000)

    first_server_port: int, optional
      Port of the first server (the ports are allocated sequentially starting with `first_port_server`)
      The default is None (port+1)

    server_execution_path: str, optional
      The API rule where the worker logic is executed.
      The default is None (MSCT.RULE_DEFAULT)
    """

    self.__version__ = __CORE_VER__

    self._start_server_names = server_names
    self._host = host or '127.0.0.1'
    self._port = port or 5000
    self._server_execution_path = server_execution_path or MSCT.RULE_DEFAULT
    self._workers_location = workers_location
    self._workers_suffix = workers_suffix

    self._first_server_port = first_server_port or self._port + 1
    self._current_server_port = self._first_server_port

    self._config_endpoints = None
    
    self._start_time = time()

    self._servers = {}
    self._paths = None
    super(FlaskGateway, self).__init__(log=log, prefix_log='[FSKGW]', **kwargs)
    return
  
  
  def get_response(self, data):
    if MSCT.DOWNLOAD_FILE_COMMAND in data:
      # MSCT.DOWNLOAD_FILE_COMMAND is a special field in responses that allows the creation of a file download from the
      # gateway directly to the client
      self.P("Received {} command:\n{}".format(MSCT.DOWNLOAD_FILE_COMMAND, json.dumps(data, indent=4)))
      dct_download = data[MSCT.DOWNLOAD_FILE_COMMAND]
      fn = dct_download[MSCT.DOWNLOAD_FILE_PATH]
      return flask.send_file(fn)
    else:
      if isinstance(data, dict):
        dct_result = data
      else:
        dct_result = {
          MSCT.DATA : data,        
        }
      dct_result[MSCT.VER] = __CORE_VER__
      dct_result[MSCT.TIME] = self.log.time_to_str()
      dct_result[MSCT.GW_UPTIME] = self._elapsed_to_str(time() - self._start_time)
      return flask.jsonify(dct_result)

  def _log_banner(self):
    _logo = "FlaskGateway v{} started on '{}:{}'".format(
      self.__version__, self._host, self._port
    )

    lead = 5
    _logo = " " * lead + _logo
    s2 = (len(_logo) + lead) * "*"
    self.log.P("")
    self.log.P(s2)
    self.log.P("")
    self.log.P(_logo)
    self.log.P("")
    self.log.P(s2)
    self.log.P("")
    return

  def startup(self):
    super().startup()
    self._log_banner()
    self._no_startup_wait = self.config_data.get(MSCT.NO_STARTUP_WAIT, False)
    self._config_endpoints = self.config_data.get(MSCT.CONFIG_ENDPOINTS, {})

    if self._start_server_names is None:
      self._start_server_names = list(self._config_endpoints.keys())

    if not self._server_execution_path.startswith('/'):
      self._server_execution_path = '/' + self._server_execution_path

    self.start_servers()

    if self._paths is None:
      self.kill_servers()
      raise ValueError("Gateway cannot start because no paths were retrieved from endpoints.")

    self.app = flask.Flask('FlaskGateway')
    self.app.json_encoder = NPJson
    for rule in self._paths:
      partial_view_func = partial(self._view_func_worker, rule)
      partial_view_func.__name__ = "partial_view_func_{}".format(rule.lstrip('/'))
      self.P("Registering {} on `{}`".format(rule, partial_view_func.__name__), color='g')
      self.app.add_url_rule(
        rule=rule,
        view_func=partial_view_func,
        methods=['GET', 'POST', 'OPTIONS']
      )
    #endfor

    self.app.add_url_rule(
      rule=MSCT.RULE_START,
      endpoint='StartServerEndpoint',
      view_func=self._view_func_start_server,
      methods=['GET', 'POST']
    )

    ### THIS SHOULD BE USED WITH CARE IN PROD
    if True:
      self.app.add_url_rule(
        rule=MSCT.RULE_SHUTDOWN,
        endpoint='ShutdownGateway',
        view_func=self._view_shutdown,
        methods=['GET', 'POST']
      )

    self.app.add_url_rule(
      rule=MSCT.RULE_KILL,
      endpoint='KillServerEndpoint',
      view_func=self._view_func_kill_server,
      methods=['GET', 'POST']
    )
    
    self.app.add_url_rule(
      rule=MSCT.RULE_LIST,
      endpoint='ListServersEndpoint',
      view_func=self._view_list_servers,
      methods=['GET', 'POST']
    )

    self.app.add_url_rule(
      rule=MSCT.RULE_SYS,
      endpoint='SystemHealthStatus',
      view_func=self._view_system_status,
      methods=['GET', 'POST']
    )

    self.P("Starting gateway server after all endpoints have been defined...", color='g')
    self._get_system_status(display=True)
    self.app.run(
      host=self._host,
      port=self._port,
      threaded=True
    )
    return
  
  def _get_system_status(self, display=True):
    mem_total = round(self.log.get_machine_memory(gb=True),2)
    mem_avail = round(self.log.get_avail_memory(gb=True),2)
    mem_gateway = round(self.log.get_current_process_memory(mb=False),2)
    mem_servers = 0
    dct_servers = {
    }
    for svr in self._servers:
      proc = psutil.Process(self._servers[svr][MSCT.PROCESS].pid)
      proc_mem = round(proc.memory_info().rss / (1024**3), 2)
      mem_servers += proc_mem
      dct_servers[svr] = proc_mem
    #endfor calc mem
    mem_used = round(mem_gateway + mem_servers, 2)
    mem_sys = round((mem_total - mem_avail) - mem_used,2)
    self.P("  Total server memory:    {:>5.1f} GB".format(mem_total), color='g')
    self.P("  Total server avail mem: {:>5.1f} GB".format(mem_avail), color='g')
    self.P("  Total allocated mem:    {:>5.1f} GB".format(mem_used), color='g')
    self.P("  System allocated mem:   {:>5.1f} GB".format(mem_sys), color='g')
    dct_stats = dict(
      mem_total=mem_total,
      mem_avail=mem_avail,
      mem_gateway=mem_gateway,
      mem_used=mem_used,
      mem_sys=mem_sys,
      mem_servers=dct_servers,
      system=platform.platform(),
      py=sys.version,
      packs=get_packages(),
      info='Memory Size is in GB. Total and avail mem may be reported inconsistently in containers.'
    )
    return dct_stats


  def _start_server(self, server_name, port, execution_path, host=None, nr_workers=None, verbosity=1):
    config_endpoint = self._config_endpoints.get(server_name, {})
    
    desc = config_endpoint.get(MSCT.DESCRIPTION)
    self.P('Attempting to start server with "SIGNATURE" : "{}"'.format(server_name))
    self.P('  Description: "{}"'.format(desc))
    
    if config_endpoint.get(MSCT.DISABLED):
      self.P("Skipping server '{}' due to its {} status: {}".format(server_name, MSCT.DISABLED, config_endpoint))
      return False
    
    if MSCT.SERVER_CLASS in config_endpoint:
      server_class = config_endpoint[MSCT.SERVER_CLASS]
      self.P("Found {} '{}' in endpoint '{}' definition - using this class as worker".format(
        MSCT.SERVER_CLASS, server_class, server_name,
      ))
    else:
      server_class = server_name

    if MSCT.HOST in config_endpoint:
      host = config_endpoint[MSCT.HOST]
    else:
      host = host or DEFAULT_HOST
      self.P("WARNING: '{}' not provided in endpoint configuration for {}.".format(MSCT.HOST, server_name), color='r')
    #endif

    if MSCT.NR_WORKERS in config_endpoint:
      nr_workers = config_endpoint[MSCT.NR_WORKERS]
    else:
      nr_workers = nr_workers or DEFAULT_NR_WORKERS
      self.P("WARNING: MSCT.NR_WORKERS not provided in endpoint configuration for {}.".format(server_name), color='r')
    #endif

    msg = "Creating server `{} <{}>` at {}:{}{}".format(server_name, server_class, host, port, execution_path)
    self.P(msg, color='g')
    self._create_notification('log', msg)

    popen_args = [
      'python',
      'libraries/model_server_v2/run_server.py',
      '--base_folder', self.log.root_folder,
      '--app_folder', self.log.app_folder,
      '--config_endpoint', json.dumps(config_endpoint),
      '--host', host,
      '--port', str(port),
      '--execution_path', execution_path,
      '--workers_location', self._workers_location,
      '--worker_name', server_class,
      '--worker_suffix', self._workers_suffix,
      '--microservice_name', server_name,
      '--nr_workers', str(nr_workers),
      '--use_tf',
    ]

    process = subprocess.Popen(popen_args)

    self._servers[server_name] = {
      MSCT.PROCESS   : process,
      MSCT.HOST      : host,
      MSCT.PORT      : port,
      MSCT.START     : time(),
    }

    sleep(1)

    msg = "Successfully created server '{}' with PID={}".format(server_name, process.pid)
    self.P(msg, color='g')
    self._create_notification('log', msg)
    #endif

    return True
  
  def _elapsed_to_str(self, t):
    return str(timedelta(seconds=int(t)))
  
  def _get_server_status(self, server_name):
    online = False
    urls = []
    _error = None
    paths = None
    try:
      url = 'http://{}:{}{}'.format(
        self._servers[server_name][MSCT.HOST],
        self._servers[server_name][MSCT.PORT],
        MSCT.RULE_PATHS
      )
      response = requests.get(url=url)
      paths = response.json()[MSCT.PATHS]
      urls = [url]
      for path in paths:
        url = 'http://{}:{}{}'.format(
          self._servers[server_name][MSCT.HOST],
          self._servers[server_name][MSCT.PORT],
          path
        )
        urls.append(url)
      online = True
    except Exception as exc:
      _error = str(exc)
  
    result = {
      MSCT.ONLINE  : online,
      MSCT.ERROR   : _error,
      MSCT.URLS    : urls,
      MSCT.PATHS   : paths,
      MSCT.PORT    : self._servers[server_name][MSCT.PORT],
      MSCT.UPTIME  : self._elapsed_to_str(time() - self._servers[server_name][MSCT.START])
    }
    return result

  def _get_paths_from_server(self, server_name):
    self.P("Requesting `get_paths` from server '{}' in order to map available paths...".format(server_name))
    
    resp = self._get_server_status(server_name)
    if not resp[MSCT.ONLINE]:
      raise ValueError('Server not yet online: {}'.format(resp[MSCT.ERROR]))
    else:
      self._paths = resp[MSCT.PATHS]
      self.P("  Responded with paths={}".format(self._paths), color='g')
    return

  def start_servers(self):
    for i,server_name in enumerate(self._start_server_names):
      if self._start_server(
        server_name=server_name,
        port=self._current_server_port,
        execution_path=self._server_execution_path,
        verbosity=1
      ):
        self._current_server_port += 1
    #endfor

    if self._no_startup_wait:
      self.P("Fast startup enabled, using default paths: {}".format(DEFAULT_SERVER_PATHS), color='g')
      self._paths = DEFAULT_SERVER_PATHS 
    else:
      nr_tries = 0    
      svr = self.config_data.get(MSCT.DEFAULT_SERVER,  self._start_server_names[0])
      self.P("")
      WAIT_ON_ERROR = 10
      while True:
        try:
          nr_tries += 1
          self._get_paths_from_server(svr)
          self.P("  Done getting paths.", color='g')
          break
        except Exception as exc:
          self.P("  Error: {}".format(exc), color='r')
          if nr_tries >= (120 / WAIT_ON_ERROR):
            raise ValueError("Could not get paths from server '{}'".format(svr))
          sleep(WAIT_ON_ERROR)
        #end try-except
      #endwhile
    #endif no startup wait or wait for paths

    return


  def _get_server_process(self, server_name):
    return self._servers[server_name][MSCT.PROCESS]

  def _server_exists(self, server_name):
    return server_name in self._servers

  @property
  def active_servers(self):
    return list(self._servers.keys())

  def _kill_server_by_name(self, server_name):
    TIMEOUT = 5
    process = self._get_server_process(server_name)
    process.terminate()
    process.wait(TIMEOUT)
    if process.returncode is None:
      self.P("Terminating '{}:{}' with kill signal after {}s".format(
        server_name, process.pid, TIMEOUT))
      process.kill()
      sleep(1)
    self.P("  '{}' terminated with code: {}".format(server_name, process.returncode))
    self._servers.pop(server_name)
    return

  def kill_servers(self):
    names = list(self._servers.keys())
    for server_name in names:
      self.P("Terminating server '{}' ...".format(server_name))
      self._kill_server_by_name(server_name)
      sleep(2)
      self.P("  Server '{}' deallocated.".format(server_name))
    return
  

  def _view_func_worker(self, path):
    request = flask.request
    params = get_api_request_body(request, self.log)
    signature = params.pop(MSCT.SIGNATURE, None)
    if signature is None:
      return self.get_response({MSCT.VER : __CORE_VER__, MSCT.ERROR : "Bad input. MSCT.SIGNATURE not found"})

    if signature not in self._servers:
      return self.get_response({
        MSCT.VER : __CORE_VER__, 
        MSCT.ERROR : "Bad signature {}. Available signatures/servers: {}".format(
          signature, 
          self.active_servers
        )
      })

    url = 'http://{}:{}{}'.format(
      self._servers[signature][MSCT.HOST],
      self._servers[signature][MSCT.PORT],
      path
    )

    response = requests.post(url, json=params)
    return self.get_response(response.json())

  def _view_func_start_server(self):
    request = flask.request
    params = get_api_request_body(request, self.log)
    signature = params.get(MSCT.SIGNATURE, None)

    if signature is None:
      return self.get_response({MSCT.VER : __CORE_VER__, MSCT.ERROR : f"Bad input. {MSCT.SIGNATURE} not found"})

    if self._server_exists(signature):
      return self.get_response({MSCT.VER : __CORE_VER__, MSCT.ERROR : "Signature {} already started".format(signature)})

    resp = self._start_server(
      server_name=signature,
      port=self._current_server_port,
      execution_path=self._server_execution_path,
      verbosity=0
    )
    if resp:
      self._current_server_port += 1
      return self.get_response({'MESSAGE': 'OK.'})
    else:
      return self.get_response({'MESSAGE': 'Server DISABLED.'})

  def _view_func_kill_server(self):
    request = flask.request
    params = get_api_request_body(request, self.log)
    signature = params.get(MSCT.SIGNATURE, None)

    if signature is None:
      return self.get_response({MSCT.VER : __CORE_VER__, MSCT.ERROR : f"Bad input. {MSCT.SIGNATURE} not found"})
    
    if signature == '*':
      self.kill_servers()      
    elif not self._server_exists(signature):
      return self.get_response({MSCT.VER : __CORE_VER__, MSCT.ERROR : "Bad signature {}. Available signatures: {}".format(signature, self.active_servers)})
    else:
      process = self._get_server_process(signature)
      self._kill_server_by_name(signature)
      return self.get_response({'MESSAGE' : 'OK. Killed PID={} with return_code {}.'.format(
        process.pid,
        process.returncode
      )})


  def _view_list_servers(self):
    return self.get_response({
      MSCT.AVAIL_SERVERS : {
        svr_name : self._get_server_status(svr_name)
        for svr_name in self._servers
      },
      MSCT.VER : __CORE_VER__,
    })
  
  
  def _view_system_status(self):
    return self.get_response({
      MSCT.SYSTEM_STATUS : self._get_system_status(display=True)
    })
    
    


  def _view_shutdown(self):
    request = flask.request
    params = get_api_request_body(request, self.log)
    signature = params.get(MSCT.SIGNATURE, None)

    if signature is None:
      return self.get_response({MSCT.VER : __CORE_VER__, MSCT.ERROR : f"Bad input. {MSCT.SIGNATURE} not found"})
    
    if signature.upper() == MSCT.KILL_CMD:
      self.kill_servers()
      _pid = os.getpid()
      _signal = signal.SIGKILL
      self.P("Terminating gateway server v{} with pid {} with signal {}...".format(__VER__, _pid, _signal))
      os.kill(_pid, _signal)
      self.P("Running _exit() ...")
      os._exit(1)

    if not self._server_exists(signature):
      return self.get_response({MSCT.ERROR : "Bad signature {}. Available signatures: {}".format(signature, self.active_servers)})


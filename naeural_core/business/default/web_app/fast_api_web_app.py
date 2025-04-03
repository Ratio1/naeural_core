import importlib
import os
import shutil
import tempfile

from jinja2 import Environment, FileSystemLoader

from naeural_core.business.base.web_app.base_web_app_plugin import BaseWebAppPlugin as BasePlugin
from naeural_core.utils.uvicorn_fast_api_ipc_manager import get_server_manager
from naeural_core.utils.fastapi_utils import PostponedRequest

#TODO: move __sign and __get_response from dauth_manager to base_web_app_plugin or fastapi_web_app or utils
#  all responses should contain the data from __get_response

__VER__ = '0.0.0.0'

_CONFIG = {
  **BasePlugin.CONFIG,
  'NGROK_ENABLED': True,
  'NGROK_DOMAIN': None,
  'NGROK_EDGE_LABEL': None,

  'PORT': None,

  'ASSETS': None,
  'JINJA_ARGS': {},
  'TEMPLATE': 'basic_server',

  'API_TITLE': None,  # default is plugin signature
  'API_SUMMARY': None,  # default is f"FastAPI created by {plugin signature}"
  'API_DESCRIPTION': None,  # default is plugin docstring

  'PAGES': [],
  'STATIC_DIRECTORY': 'assets',
  'DEFAULT_ROUTE': None,

  # In case of wrapped response, the response will be wrapped in a json with 2 keys:
  # 'result' and 'node_addr', where 'result' is the actual response and 'node_addr' is the node address
  # In case of raw response, the response will be the actual response provided by the endpoint method.
  # The default is 'WRAPPED'.
  'RESPONSE_FORMAT': 'WRAPPED',

  'PROCESS_DELAY': 0,

  'VALIDATION_RULES': {
    **BasePlugin.CONFIG['VALIDATION_RULES']
  },
}


class FastApiWebAppPlugin(BasePlugin):
  """
  A plugin which exposes all of its methods marked with @endpoint through
  fastapi as http endpoints.

  The @endpoint methods can be triggered via http requests on the web server
  and will be processed as part of the business plugin loop.
  """

  CONFIG = _CONFIG

  @staticmethod
  def endpoint(func=None, *, method="get", require_token=False):
    """
    Decorator that marks a method as an HTTP endpoint. Optionally enforces a Bearer token.
    
    Parameters
    ----------
    method : str
        HTTP method (e.g. "get", "post").
        
    require_token : bool
        Whether this endpoint should require a Bearer token.
        
    """
    if func is None:
      def wrapper(func):
        return FastApiWebAppPlugin.endpoint(func, method=method, require_token=require_token)

      return wrapper

    func.__endpoint__ = True
    if isinstance(method, str):
      method = method.lower()
    func.__http_method__ = method
    func.__require_token__ = require_token
    return func

  def get_web_server_path(self):
    return self.script_temp_dir

  def get_package_base_path(self, package_name):
    """
    Return the file path of an installed package parent directory.
    This method was copied from the _PluginsManagerMixin class from ratio1 SDK.

    Parameters
    ----------
    package_name : str
        The name of the installed package.

    Returns
    -------
    str
        The path to the package parent.
    """
    spec = importlib.util.find_spec(package_name)
    if spec is not None and spec.submodule_search_locations:
      return os.path.dirname(spec.submodule_search_locations[0])
    else:
      self.P("Package '{}' not found.".format(package_name), color='r')
    return None

  def validate_static_directory(self):
    """
    Validate the `STATIC_DIRECTORY` value.
    This will check if the value is trying to reference a restricted directory.
    """
    # Creating a temporary directory to check if the static directory is valid
    tmp_directory = tempfile.mkdtemp()
    static_directory = self.cfg_static_directory

    full_path = os.path.join(tmp_directory, static_directory)
    # Check if the full path is a subdirectory of the temporary directory
    if not full_path.startswith(tmp_directory):
      # If the full path is not a subdirectory of the temporary directory, it is invalid
      self.add_error(f"Invalid `STATIC_DIRECTORY`: {static_directory}")
    # endif path is restricted
    # Clean up the temporary directory
    os.rmdir(tmp_directory)
    return

  def validate_default_route(self):
    """
    Validate the `DEFAULT_ROUTE` value.
    It should be a valid URL path if specified.

    """
    default_route = self.cfg_default_route
    if default_route is None:
      # If not provided, return without validation
      return

    if not isinstance(default_route, str):
      self.add_error(f"Invalid `DEFAULT_ROUTE`: {default_route}. It should be a string")
      return

    if not default_route.startswith('/'):
      self.add_error(f"Invalid `DEFAULT_ROUTE`: {default_route}. It should start with '/'")
    # endif default route is invalid
    return

  def initialize_assets(self, src_dir, dst_dir, jinja_args):
    """
    Initialize and copy fastapi assets, expanding any jinja templates.
    All files from the source directory are copied to the
    destination directory with the following exceptions:
      - are symbolic links are ignored
      - files named ending with .jinja are expanded as jinja templates,
        .jinja is removed from the filename and the result copied to
        the destination folder.
    This maintains the directory structure of the source folder.
    In case src_dir is None, only the jinja templates are expanded.

    Parameters
    ----------
    src_dir: str or None, path to the source directory
    dst_dir: str, path to the destination directory
    jinja_args: dict, jinja keys to use while expanding the templates

    Returns
    -------
    None
    """
    self.prepared_env['PYTHONPATH'] = '.:' + os.getcwd() + ':' + self.prepared_env.get('PYTHONPATH', '')

    super(FastApiWebAppPlugin, self).initialize_assets(src_dir, dst_dir, jinja_args)

    package_base_path = self.get_package_base_path('naeural_core')
    if package_base_path is None:
      self.P("Skipping `main.py` rendering, package 'naeural_core' not found.", color='r')
      self.failed = True
      return
    # endif package base path not found
    static_directory = self.jinja_args.get('static_directory')

    if self.cfg_template is not None:
      env = Environment(loader=FileSystemLoader(package_base_path))

      # make sure static directory folder exists
      os.makedirs(self.os_path.join(dst_dir, static_directory), exist_ok=True)

      # Finally render main.py
      template_dir = self.os_path.join('naeural_core', 'business', 'base', 'uvicorn_templates')
      app_template = self.os_path.join(template_dir, f'{self.cfg_template}.j2')
      # env.get_template expects forward slashes, even on Windows.
      app_template = app_template.replace(os.sep, '/')
      app_template = env.get_template(app_template)
      rendered_content = app_template.render(jinja_args)

      with open(self.os_path.join(dst_dir, 'main.py'), 'w') as f:
        f.write(rendered_content)
    # endif render main.py

    # Here additional generic assets can be added if needed
    favicon_path = self.os_path.join(package_base_path, 'naeural_core', 'utils', 'web_app', 'favicon.ico')
    favicon_dst = self.os_path.join(dst_dir, static_directory, 'favicon.ico')
    if self.os_path.exists(favicon_path):
      self.P(f'Copying favicon from {favicon_path} to {favicon_dst}')
      os.makedirs(self.os_path.dirname(favicon_dst), exist_ok=True)
      shutil.copy2(favicon_path, favicon_dst)
    # endif favicon exists

    return

  def _init_endpoints(self) -> None:
    """
    Populate the set of jinja arguments with values needed to create http
    endpoints for all methods of the plugin marked with @endpoint. Since
    there should be at least one such method, this method is always invoked
    via the on_init hook

    Parameters
    ----------
    None

    Returns
    -------
    None
    """
    import inspect
    self._endpoints = {}
    jinja_args = []

    def _filter(obj):
      try:
        return inspect.ismethod(obj)
      except Exception as _:
        pass
      return False

    for name, method in inspect.getmembers(self, predicate=_filter):
      if not hasattr(method, '__endpoint__'):
        continue
      self._endpoints[name] = method
      http_method = method.__http_method__
      require_token = getattr(method, '__require_token__', False)
      signature = inspect.signature(method)
      doc = method.__doc__ or ''
      all_params = [param.name for param in signature.parameters.values()]
      all_args = [str(param) for param in signature.parameters.values()]
      if self.cfg_debug_web_app:
        self.P(f'Endpoint {name}[{require_token=}] with {all_args=} and {all_params=}')
      if not require_token:
        args = all_args
        params = all_params
      else:
        if all_params[0] != 'token':
          raise ValueError(f"First parameter of method {name} must be 'token' if require_token is True.")
        params = all_params[1:]
        args = all_args[1:]
        if self.cfg_debug_web_app:
          self.P(f'Endpoint {name}[{require_token=}] left with {args=} and {params=}')
      # endif require_token   
      jinja_args.append({
        'name': name,
        'method': http_method,
        'args': args,
        'params': params,
        'endpoint_doc': doc,
        'require_token': require_token
      })
      str_function = f"{name}({', '.join(args)})"
      self.P(f"Registered endpoint {str_function} with method {http_method}. Require token: {require_token}")
    # endfor all methods
    self._node_comms_jinja_args = jinja_args
    return

  def on_init(self):
    # Register all endpoint methods.
    self._init_endpoints()

    # FIXME: move to setup_manager method
    self.manager_auth = b'abc'
    self._manager = get_server_manager(self.manager_auth)
    self.postponed_requests = self.deque()

    self.P("manager address: {}", format(self._manager.address))
    _, self.manager_port = self._manager.address

    # Start the FastAPI app
    self.P('Starting FastAPI app...')
    super(FastApiWebAppPlugin, self).on_init()
    return

  def create_postponed_request(self, solver_method, method_kwargs={}):
    """
    Create a postponed request to be processed by the plugin in the next loop.
    Parameters
    ----------
    solver_method : method
        The method that will solve the postponed request.
    method_kwargs : dict
        The keyword arguments to be passed to the solver_method.
    Returns
    -------
    res : PostponedRequest
        The postponed request object.
    """
    return PostponedRequest(
      solver_method=solver_method,
      method_kwargs=method_kwargs
    )

  def get_postponed_dict(self, request_id, request_value, endpoint_name):
    return {
      'id': request_id,
      'value': request_value,
      'endpoint_name': endpoint_name
    }

  def parse_postponed_dict(self, request):
    return request['id'], request['value'], request['endpoint_name']

  def __fastapi_process_response(self, response):
    if self.cfg_response_format == 'RAW':
      return response
    return {
      'result': response,
      'server_node_addr': self.e2_addr, 
      'evm_network' : self.evm_network,
    }

  def __fastapi_handle_response(self, id, value):
    # TODO: add here message signing
    response = {
      'id': id,
      'value': self.__fastapi_process_response(value)
    }
    self._manager.get_client_queue().put(response)
    return

  def _process(self):
    super(FastApiWebAppPlugin, self)._process()
    new_postponed_requests = []
    while len(self.postponed_requests) > 0:
      request = self.postponed_requests.popleft()
      id, value, endpoint_name = self.parse_postponed_dict(request)

      method = value.get_solver_method()
      kwargs = value.get_method_kwargs()

      try:
        value = method(**kwargs)
      except Exception as exc:
        self.P(
          f'Exception occurred while processing postponed request for {endpoint_name} with method {method.__name__} '
          f'and args:\n{kwargs}\nException:\n{self.get_exception()}',
          color='r'
        )
        value = {
          'error': str(exc)
        }

      if isinstance(value, PostponedRequest):
        new_postponed_requests.append(self.get_postponed_dict(
          request_id=id,
          request_value=value,
          endpoint_name=endpoint_name
        ))
      else:
        self.__fastapi_handle_response(id, value)
      # endif request is postponed
    # end while there are postponed requests
    for request in new_postponed_requests:
      self.postponed_requests.append(request)
    # endfor all new postponed requests
    while not self._manager.get_server_queue().empty():
      request = self._manager.get_server_queue().get()
      id = request['id']
      value = request['value']

      method = value[0]
      args = value[1:]

      try:
        value = self._endpoints.get(method)(*args)
      except Exception as exc:
        self.P("Exception occured while processing\n"
               "Request: {}\nArgs: {}\nException:\n{}".format(
                   method, args, self.get_exception()), color='r')
        value = {
          'error': str(exc)
        }

      if isinstance(value, PostponedRequest):
        self.P(f"Postponing request {id} for {method}.")
        self.postponed_requests.append(self.get_postponed_dict(
          request_id=id,
          request_value=value,
          endpoint_name=method
        ))
      else:
        self.__fastapi_handle_response(id, value)
      # endif request is postponed
    # end while

    return None

  def on_close(self):
    self._manager.shutdown()
    super(FastApiWebAppPlugin, self).on_close()
    return

  def __get_uvicorn_process_args(self):
    return f"uvicorn --app-dir {self.script_temp_dir} main:app --host 0.0.0.0 --port {self.port}"

  def get_default_description(self):
    return self.__doc__

  @property
  def jinja_args(self):
    cfg_jinja_args = self.deepcopy(self.cfg_jinja_args)

    dct_pages = cfg_jinja_args.pop('html_files', self.cfg_pages)
    for page in dct_pages:
      page['method'] = 'get'

    static_directory = cfg_jinja_args.pop('static_directory', self.cfg_static_directory)
    default_route = self.cfg_default_route

    return {
      'static_directory': static_directory,
      'html_files': dct_pages,
      'manager_port': self.manager_port,
      'manager_auth': self.manager_auth,
      'api_title': repr(self.cfg_api_title or self.get_signature()),
      'api_summary': repr(self.cfg_api_summary or f"Ratio1 WebApp created with {self.get_signature()} plugin"),
      'api_description': repr(self.cfg_api_description or self.get_default_description()),
      'api_version': repr(self.__version__),
      'node_comm_params': self._node_comms_jinja_args,
      'debug_web_app': self.cfg_debug_web_app,
      'default_route': default_route,
      **cfg_jinja_args,
    }

  def get_start_commands(self):
    super_start_commands = super(FastApiWebAppPlugin, self).get_start_commands()
    return super_start_commands + [self.__get_uvicorn_process_args()]

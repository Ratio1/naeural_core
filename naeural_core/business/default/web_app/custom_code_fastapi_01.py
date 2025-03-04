from naeural_core.business.default.web_app.fast_api_web_app import FastApiWebAppPlugin

__VER__ = '0.1.0.0'

# This will be shown as the description of the API in case the user does not specify one.
TEMPLATE_API_DESCRIPTION = """
**Custom code WebAPI deployment: Deploy Your Own FastAPI Endpoint on Ratio1 Edge Nodes**

This is the Ratio1 plugin system that empowers developers to define custom FastAPI endpoints and remotely deploy them to one or more Ratio1 Edge Nodes, all through the Ratio1 Python SDK (currently listed on PyPI as `ratio1`, soon to be renamed `ratio1`). By leveraging this plugin system, you can convert virtually any Python function into a scalable RESTful endpoint—no dedicated server required. Simply write your custom code, point the SDK to the Edge Node, and watch as `CUSTOM_CODE_FASTAPI_01` seamlessly builds and deploys a FastAPI application behind the scenes.

### Key Benefits
- **Custom Functionality**: Turn your Python functions into POST, GET, or other HTTP endpoints in just a few lines of code.  
- **Decentralized Scaling**: Deploy your new endpoint across multiple Edge Nodes for redundancy, load balancing, or specialized domain handling.  
- **Easy Setup**: Use the familiar Python environment and the Ratio1 SDK to configure routes, define request parameters, and manage outputs.  
- **Resource Efficient**: Offload tasks to Edge Nodes, reducing local resource consumption and improving overall performance.  


### SDK Code Example

```python
from ratio1 import Session, CustomPluginTemplate, PLUGIN_TYPES

def some_endpoint(plugin: CustomPluginTemplate, message: str) -> list:
  result = [message] * 5   
  return result

if __name__ == '__main__':
  
  session = Session(silent=True)
  my_node = "0xai_ApM1AbzLq1VtsLIidmvzt1Nv4Cyl5Wed0fHNMoZv9u4X"
  
  # Create a web application on the specified node
  app, _ = session.create_web_app(
    node=my_node,
    name="ratio1_simple_webapp",
    endpoints=[
      {
        "function": some_endpoint
        "method": "post",
      },        
    ]
  )
  
  # Deploy and retrieve the public URL
  try:
    url = app.deploy()
    print("Webapp deployed at:", url)
  except Exception as e:
    print("Error deploying webapp:", e)

```
"""

_CONFIG = {
  **FastApiWebAppPlugin.CONFIG,
  'NGROK_ENABLED': True,
  'NGROK_DOMAIN': None,
  'NGROK_EDGE_LABEL': None,

  'PORT': None,

  'ENDPOINTS': [],

  'ASSETS': '_custom_code',
  'JINJA_ARGS': {},
  'VALIDATION_RULES': {
    **FastApiWebAppPlugin.CONFIG['VALIDATION_RULES'],
  },
}


class CustomCodeFastapi01Plugin(FastApiWebAppPlugin):
  """
  Plugin that can define custom endpoints and with code for a FastAPI web server.

  The endpoints are specified in the `ENDPOINTS` configuration parameter, and should
  look like this:

  ```
  "ENDPOINTS": [
    {
      "NAME": "__ENDPOINT_NAME__",
      "METHOD": "__ENDPOINT_METHOD__", # Optional, default is "get", can be "post", "put", "delete", etc.
      "CODE": "__BASE64_ENCODED_ENDPOINT_CODE__",
      "ARGS": "__ENDPOINT_ARGS__",
    },
    ...
  ]

  """
  CONFIG = _CONFIG

  def get_default_description(self):
    # This description could also be put in this class __doc__ attribute, but
    # was put here in order for the documentation to this actual plugin to be
    # available for the user in case it's needed.
    return TEMPLATE_API_DESCRIPTION

  def __register_custom_code_endpoint(self, endpoint_name, endpoint_method, endpoint_base64_code, endpoint_arguments):
    # First check that i do not have any attribute with the same name
    import inspect
    existing_attribute_names = (name for name, _ in inspect.getmembers(self))
    if endpoint_name in existing_attribute_names:
      raise Exception("The endpoint name '{}' is already in use.".format(endpoint_name))

    custom_code_method, errors, warnings = self._get_method_from_custom_code(
      str_b64code=endpoint_base64_code,
      self_var='plugin',
      method_arguments=["plugin"] + endpoint_arguments
    )

    if errors is not None:
      raise Exception("The custom code failed with the following error: {}".format(errors))

    if len(warnings) > 0:
      self.P("The custom code generated the following warnings: {}".format("\n".join(warnings)))

    # Now register the custom code method as an endpoint
    import types
    setattr(self, endpoint_name, types.MethodType(FastApiWebAppPlugin.endpoint(custom_code_method, method=endpoint_method), self))
    return

  def on_init(self, **kwargs):
    for dct_endpoint in self.cfg_endpoints:
      endpoint_name = dct_endpoint.get('NAME', None)
      endpoint_method = dct_endpoint.get('METHOD', "get")
      endpoint_base64_code = dct_endpoint.get('CODE', None)
      endpoint_arguments = dct_endpoint.get('ARGS', None)
      self.__register_custom_code_endpoint(endpoint_name, endpoint_method, endpoint_base64_code, endpoint_arguments)

    super(CustomCodeFastapi01Plugin, self).on_init(**kwargs)
    return

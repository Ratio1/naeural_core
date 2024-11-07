import json

try:
  from gql import gql, Client
  from gql.transport.requests import RequestsHTTPTransport
  GQL_INSTALL = True
except:
  GQL_INSTALL = False

def gql_to_config(dct_gql, STREAMS_KEY=None):
  if STREAMS_KEY is None:
    STREAMS_KEY = 'pluginStreamConfigByFields'

  if STREAMS_KEY in dct_gql:
    lst_streams = dct_gql[STREAMS_KEY]
  else:
    lst_streams = dct_gql
  assert isinstance(lst_streams, list)
  return lst_streams

if __name__ == '__main__':
  if not GQL_INSTALL:
    raise ValueError("Please run `pip install gql`")
    
  SERVER_ADDR = "https://graphql-server-jx-staging.jenkinsx.globalintelligence.ro/"
  #SERVER_ADDR = "http://graphql.gts-ws-test.globalintelligence.ro"
  QUERY = """
    query PluginStreamConfigByFields($boxId: String) {
        pluginStreamConfigByFields(boxId: $boxId) {
          NAME          
          DESCRIPTION          
          LIVE_FEED          
          RECONNECTABLE          
          TYPE          
          URL          
          STREAM_CONFIG_METADATA {          
            DATA_HELPER_NAME
            DATA_HELPER_PARAMS
            TRANSCODER_H
            TRANSCODER_W          
            }      
          CAP_RESOLUTION
          PLUGINS {        
        SIGNATURE        
        INSTANCES {
          PLUGIN_INSTANCE_PARAMETER_LIST {
            NAME        
            VALUE        
            TYPEVALUE
            }
          }
        }
        }
      }
    """
  VARIABLE_VALUES = {
    "boxId": None#"gts_ws_test"
    }
    
  STREAMS_KEY = 'pluginStreamConfigByFields'
    
  
  http_transp=RequestsHTTPTransport(
      url=SERVER_ADDR,
      use_json=True,
      headers={
          "Content-type": "application/json",
      },
      verify=False,
      retries=3,
  )
  
  client = Client(
      transport=http_transp,
      fetch_schema_from_transport=True,
  )
  
  query = gql(QUERY)
  
  resp = client.execute(query, variable_values=VARIABLE_VALUES)
  
  streams = resp[STREAMS_KEY]
  print('**********************************\n'*3)
  s_streams = json.dumps(streams, indent=4)
  print(s_streams)
  
  converted_streams = gql_to_config(streams)

  print('**********************************\n'*3)
  s_streams = json.dumps(converted_streams, indent=4)
  print(s_streams)
      
  
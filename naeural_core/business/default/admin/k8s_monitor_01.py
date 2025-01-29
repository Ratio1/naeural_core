"""

Mar 01 08:38:13 k8s-m1 nerdctl[2450178]: Traceback (most recent call last):
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:   File "/opt/conda/lib/python3.10/site-packages/kmonitor/mixins/nodes_mixin.py", line 64, in get_nodes_metrics
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:     memory_usage_gib = int(node['usage']['memory'].rstrip('Ki')) / (1024**2)  # Convert KiB to GiB
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]: ValueError: invalid literal for int() with base 10: '6524M'
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]: During handling of the above exception, another exception occurred:
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]: Traceback (most recent call last):
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:   File "/exe_eng/core/business/base/base_plugin_biz_loop.py", line 234, in execute
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:     self._on_idle()
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:   File "/exe_eng/core/business/base/base_plugin_biz_loop.py", line 205, in _on_idle
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:     self.high_level_execution_chain()
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:   File "/exe_eng/core/business/base/base_plugin_biz_loop.py", line 314, in high_level_execution_chain
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:     self.process_wrapper()
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:   File "/exe_eng/core/business/base/base_plugin_biz.py", line 1607, in process_wrapper
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:     self._payload = self._process()
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:   File "/exe_eng/core/business/base/base_plugin_biz_api.py", line 63, in _process
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:     return self.process()
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:   File "/exe_eng/core/business/default/admin/k8s_monitor_01.py", line 147, in process
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:     dct_nodes = self.__get_status()
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:   File "/exe_eng/core/business/default/admin/k8s_monitor_01.py", line 111, in __get_status
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:     nodes = self.__km.get_nodes_metrics()
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:   File "/opt/conda/lib/python3.10/site-packages/kmonitor/mixins/nodes_mixin.py", line 79, in get_nodes_metrics
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:     self._handle_exception(exc)
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:   File "/opt/conda/lib/python3.10/site-packages/kmonitor/base.py", line 98, in _handle_exception
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]:     error_message += f"  Reason: {exc.reason}\n"
Mar 01 08:38:13 k8s-m1 nerdctl[2450178]: AttributeError: 'ValueError' object has no attribute 'reason'

"""
from naeural_core.business.base import BasePluginExecutor
try:
  import kmonitor
  K8S_PACKAGE_AVAIL = True
except:
  K8S_PACKAGE_AVAIL = False

FAKE_DATA =  [{
    "name": "k8s-m1-fake",
    "status": "Ready",
    "conditions": {
      "NetworkUnavailable": "False",
      "MemoryPressure": "False",
      "DiskPressure": "False",
      "PIDPressure": "False",
      "Ready": "True"
    },
    "cpu_usage_mili": 711,
    "memory_usage_gib": 7.64,
    "total_memory_gib": 15.61,
    "total_cpu_cores": 4,
    "memory_load": "48.9%",
    "cpu_load": "17.8%"
  },
  {
    "name": "k8s-w1-fake",
    "status": "Ready",
    "conditions": {
      "NetworkUnavailable": "False",
      "MemoryPressure": "False",
      "DiskPressure": "False",
      "PIDPressure": "False",
      "Ready": "True"
    },
    "cpu_usage_mili": 168,
    "memory_usage_gib": 3.05,
    "total_memory_gib": 7.75,
    "total_cpu_cores": 3,
    "memory_load": "39.3%",
    "cpu_load": "5.6%"
  },
  {
    "name": "k8s-w2-fake",
    "status": "Ready",
    "conditions": {
      "NetworkUnavailable": "False",
      "MemoryPressure": "False",
      "DiskPressure": "False",
      "PIDPressure": "False",
      "Ready": "True"
    },
    "cpu_usage_mili": 259,
    "memory_usage_gib": 3.25,
    "total_memory_gib": 7.75,
    "total_cpu_cores": 3,
    "memory_load": "41.9%",
    "cpu_load": "8.7%"
  }]



_CONFIG = {
  **BasePluginExecutor.CONFIG,

  'ALLOW_EMPTY_INPUTS'  : True,
  
  'PROCESS_DELAY'       : 5,
  

  'ALERT_DATA_COUNT'    : 1,
  'ALERT_RAISE_VALUE'   : 0.8,
  'ALERT_LOWER_VALUE'   : 0.79,
  'ALERT_MODE'          : 'min',
  
  'ARTIFICIAL'          : True,
  
  'DEBUG_MODE'          : False,

  'VALIDATION_RULES': {
    **BasePluginExecutor.CONFIG['VALIDATION_RULES'],
  },
}



class K8sMonitor01Plugin(BasePluginExecutor):
  
  def __init__(self, **kwargs):
    super(K8sMonitor01Plugin, self).__init__(**kwargs)
    return
  
  
  def __get_fake_data(self):
    results = []
    for fake_node in FAKE_DATA:
      v = fake_node.copy()
      v['cpu_usage_mili'] = self.np.random.randint(0, v['total_cpu_cores'] * 1000)
      v['memory_usage_gib'] = self.np.random.uniform(0, v['total_memory_gib'])
      v['memory_load'] = f"{v['memory_usage_gib'] / v['total_memory_gib'] * 100:.1f}%"
      v['cpu_load'] = f"{v['cpu_usage_mili'] / v['total_cpu_cores'] / 1000 * 100:.1f}%"
      results.append(v)
    return results
      
  def __get_status(self):
    result = {
      'nodes': {},
      'pods': [],
      'namespaces': [],
    }
    if self.__km is None:
      if self.cfg_artificial:
        nodes = self.__get_fake_data()
        self.P("Sending artificial data for testing with {} nodes".format(len(nodes)))
    else:
      if hasattr(self.__km, 'summary'):
        result = self.__km.summary()
      else:
        nodes = self.__km.get_nodes_metrics()
        result['nodes'] = {x['name']: x for x in nodes}
    return result
  
  
   
  def on_init(self):
    is_ok = None
    self.__km = None
    self.__last_node_status = None
    if not K8S_PACKAGE_AVAIL:
      if self.cfg_artificial:
        s1 = "KubeMonitor package not available. Using artificial data for testing."
      else:
        s1 = "KubeMonitor package not available."  
    try:      
      self.__km = kmonitor.KubeMonitor(log=self.log)
      self.__last_node_status = self.__km.get_nodes_metrics()
      s1 = "KubeMonitor v{} initialized and monitoring {} nodes".format(
        kmonitor.__version__,
        len(self.__last_node_status)
      )
      is_ok = True
    except Exception as exc:
      self.__km = None
      if self.cfg_artificial:
        s1 = f"Failed to initialize KubeMonitor: {exc}. Using artificial data for testing."
      else:
        s1 = f"Failed to initialize KubeMonitor: {exc}"
    #end try
    if is_ok:
      self.P(s1, boxed=True)
    else:
      self.P(s1, color='r')
    self.__status = s1
    return
  
  
  def on_command(self, data, **kwargs):
    if isinstance(data, dict):
      cmd = data.get('cmd', None)
      args = data.get('args', None)
      if cmd == 'delete':
        if isinstance(args, list):
          pod_names = args[0]
          namespace = args[1] if len(args) > 1 else "default"
          self.P(f"Received delete pods command for wilcard '{pod_names}' on namespace {namespace}")
          self.__km.delete_pods_from_namespace(pod_names, namespace)
        #end if has args
      #end if cmd is delete
    return None
 
  
  def process(self):
    payload = None
    if self.is_supervisor_node:
      dct_result = self.__get_status()
      dct_nodes = dct_result.get('nodes', {})
      lst_pods = dct_result.get('pods', [])
      lst_namespaces = dct_result.get('namespaces', [])
      if len(dct_nodes) > 0:
        msg = "Node status for {} nodes: {}".format(len(dct_nodes), self.__status)
        payload = self._create_payload(
          k8s_nodes=dct_nodes,
          k8s_pods=lst_pods,
          k8s_namespaces=lst_namespaces,
          status=msg,
        )
    return payload
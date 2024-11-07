"""
 "HEAVY_OPS_CONFIG" : {
    "ACTIVE_COMM_ASYNC" : [
      'send_mail',
      'debug_save',
      'save_image_dataset',
      'a_dummy'
    ],
    
    "ACTIVE_ON_COMM_THREAD" : [
      'image_compression',
    ]
  }
  
"""

DEFAULT_HEAVY_OPS_CONFIG = {
  "ACTIVE_COMM_ASYNC" : [
    "send_mail",
    "save_image_dataset",
  ],

  "ACTIVE_ON_COMM_THREAD" : [
  ]     
}

from naeural_core.manager import Manager
from naeural_core import constants as ct


class HeavyOpsManager(Manager):

  def __init__(self, log, shmem, **kwargs):
    self.shmem = shmem
    self._dct_ops = None
    super(HeavyOpsManager, self).__init__(log=log, prefix_log='[HOPSM]', **kwargs)
    return

  def startup(self):
    super().startup()
    self.config_data = self.config_data.get('HEAVY_OPS_CONFIG', DEFAULT_HEAVY_OPS_CONFIG)
    self._dct_ops = self._dct_subalterns
    # 1st category as heavy ops that run on separate individual threads without (usually)
    # affecting inplace the payload
    for operation_name in self.config_data.get("ACTIVE_COMM_ASYNC", []):
      self.create_heavy_operation(operation_name, comm_async=True)
      
    # 2nd category are heavy ops that do not use separate thread and run on comms thread
    # this second category is usually for plugins that change inplace the payload
    for operation_name in self.config_data.get("ACTIVE_ON_COMM_THREAD", []):
      self.create_heavy_operation(operation_name, comm_async=False)
    return

  def _get_plugin_class(self, name):
    _module_name, _class_name, _class_def, _class_config = self._get_module_name_and_class(
      locations=ct.PLUGIN_SEARCH.LOC_HEAVY_OPS_PLUGINS,
      name=name,
      suffix=ct.PLUGIN_SEARCH.SUFFIX_HEAVY_OPS_PLUGINS,
      safe_locations=ct.PLUGIN_SEARCH.SAFE_LOC_HEAVY_OPS_PLUGINS,
      safety_check=True, # perform safety check           
    )

    if _class_def is None:
      msg = "Error loading heavy_ops plugin '{}'".format(name)
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        info="No code/script defined for heavy_ops plugin '{}' in {}".format(name, ct.PLUGIN_SEARCH.LOC_HEAVY_OPS_PLUGINS)
      )
    #endif

    return _class_def, _class_config

  def create_heavy_operation(self, name, comm_async):
    _cls, _config = self._get_plugin_class(name)

    try:
      op = _cls(log=self.log, shmem=self.shmem, config=_config, comm_async=comm_async)
      self._dct_ops[name] = op
    except Exception as exc:
      msg = "Exception '{}' when initializing heavy_ops plugin {}".format(exc, name)
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        autocomplete_info=True
      )
    #end try-except
    return

  def run_all_comm_async(self, msg):
    for name, op in self._dct_ops.items():
      if not op.comm_async:
        continue
      op.process_payload(msg)

    return

  def run_all_on_comm_thread(self, msg):
    for name, op in self._dct_ops.items():
      if op.comm_async:
        continue
      op.process_payload(msg)

    return
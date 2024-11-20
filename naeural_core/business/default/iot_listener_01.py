from naeural_core.business.base import BasePluginExecutor as BaseClass

_CONFIG = {
  **BaseClass.CONFIG,

  'ALLOW_EMPTY_INPUTS': False,
  'TEST_NETMON': False,
  'NETMON_CHECK_INTERVAL': 30,

  'VALIDATION_RULES': {
    **BaseClass.CONFIG['VALIDATION_RULES'],
  },
}


class IotListener01Plugin(BaseClass):
  def on_init(self):
    self.last_netmon_check = None
    return

  def message_prefix(self, struct_data):
    event_type = struct_data.get('EE_EVENT_TYPE', None)
    node_addr = struct_data.get('EE_SENDER', 'MISSING_ADDRESS')
    node_id = struct_data.get('EE_ID', 'MISSING_ID')
    pipeline = struct_data.get('PIPELINE', None)
    signature = struct_data.get('SIGNATURE', None)
    instance_id = struct_data.get('INSTANCE_ID', None)

    return f"{event_type} event from <{node_id}:{node_addr}> on route [{pipeline}, {signature}, {instance_id}]"

  def maybe_test_netmon(self):
    if not self.cfg_test_netmon:
      return
    if self.last_netmon_check is not None and self.time() - self.last_netmon_check < self.cfg_netmon_check_interval:
      return
    self.last_netmon_check = self.time()
    lst_available = self.netmon.available_nodes
    self.P(f"Available nodes: {lst_available}")
    lst_accessible = self.netmon.accessible_nodes
    self.P(f"Accessible nodes: {lst_accessible}")
    known_configs = self.netmon.network_known_configs()
    self.P(f"Known configurations: {self.json_dumps(known_configs, indent=2)}")
    accessible_pipelines = [self.netmon.network_node_pipelines(node) for node in lst_accessible]
    self.P(f"Accessible pipelines: {accessible_pipelines}")
    return

  def process(self):
    self.maybe_test_netmon()
    struct_data = self.dataapi_struct_datas()

    self.P(f"Received {len(struct_data)} events.", boxed=False)

    for idx, event in struct_data.items():
      keys = list(event.keys())
      prefix = self.message_prefix(event)

      msg = f"\t{idx}. {prefix} containing {len(keys)} keys: {keys}."
      # msg += f" Detailed data:\n{struct_data}"
      self.P(msg, boxed=False)
    # endfor events

    return



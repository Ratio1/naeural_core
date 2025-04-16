from naeural_core.business.base.network_processor import NetworkProcessorPlugin

_CONFIG = {
  **NetworkProcessorPlugin.CONFIG,

  'NUMBER_OF_PAYLOADS': 5,
  'PAYLOAD_PERIOD': 5,
  'ACCEPT_SELF': True,
  "ALLOW_EMPTY_INPUTS": True,
  'CONVERT_TO_STR': False,

  'VALIDATION_RULES' : {
    **NetworkProcessorPlugin.CONFIG['VALIDATION_RULES'],
  },
}


class NetworkListenerDebugPlugin(NetworkProcessorPlugin):
  _CONFIG = _CONFIG
  def get_payload_dict(self):
    faulty_stuff = {
      0: {'something': 'wrong', 'number': 22, 'sss': [222, 'ppp']},
      4: {'something': 'wrong', 'number': 22, 'sss': [222, 'ppp']},
      5: {'something': 'wrong', 'number': 22, 'sss': [222, 'ppp']},
      6: {'something': 'wrong', 'number': 22, 'sss': [222, 'ppp']},
      3333: {'something': 'wrong', 'number': 22, 'sss': [222, 'ppp']},
    }
    if self.cfg_convert_to_str:
      faulty_stuff = {str(k): str(v) for k, v in faulty_stuff.items()}
    return {
      'AA_network_debug_counter': self.total_payload_count,
      'AA_network_debug_sender': self.ee_id,
      'AA_network_debug_sender_full': '|'.join([self.ee_id, self._stream_id, self.get_instance_id()]),
      'AA_network_debug_path': (self.ee_id, self.ee_addr, self._stream_id, self.get_instance_id()),
      'maybe_faulty_data': faulty_stuff
    }

  @NetworkProcessorPlugin.payload_handler
  def on_payload_debug(self, data: dict):
    payload_number = data.get('AA_network_debug_counter', -1)
    payload_sender = data.get('AA_network_debug_sender', 'unknown')
    payload_sender_full = data.get('AA_network_debug_sender_full', 'unknown')
    self.P(f'Payload number {payload_number} received from {payload_sender_full}.')
    return

  def process(self):
    if self.time() - self.last_payload_time > self.cfg_payload_period:
      self.P(f'Adding {self.cfg_number_of_payloads} payloads.')
      for i in range(self.cfg_number_of_payloads):
        self.add_payload_by_fields(**self.get_payload_dict())
      # endfor each payload
    # endif time to add payloads
    return

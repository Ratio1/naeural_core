"""

This plugin uses external info (the hb data) in order to check the health of the system. 


"""

from naeural_core.business.base import BasePluginExecutor

__VER__ = '0.1.1.0'

_CONFIG = {
  **BasePluginExecutor.CONFIG,

  'ALLOW_EMPTY_INPUTS'            : True,
  
  "PROCESS_DELAY"                 : 5,
  
  "DISK_LOW_PRC"                  : 0.15,
  "MEM_LOW_PRC"                   : 0.15,
  
  
  "ALERT_RAISE_CONFIRMATION_TIME" : 1,
  "ALERT_LOWER_CONFIRMATION_TIME" : 2,
  "ALERT_DATA_COUNT"              : 2,
  "ALERT_RAISE_VALUE"             : 0.5,
  "ALERT_LOWER_VALUE"             : 0.4,
  "ALERT_MODE"                    : 'mean',
  "ALERT_MODE_LOWER"              : 'max',
  
  
  
  # debug stuff
  "LOG_INFO"            : False,

  'VALIDATION_RULES': {
    **BasePluginExecutor.CONFIG['VALIDATION_RULES'],
  },
}


class SelfCheck01Plugin(BasePluginExecutor):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(SelfCheck01Plugin, self).__init__(**kwargs)
    return

  def startup(self):
    super().startup()
    return
  
  def on_command(self, data, **kwargs):
    self.P("Self check monitor on {} received: {}".format(self.eeid, data))
    return
  
  def check_all(self):
    is_issue = False
    dct_results = self.OrderedDict()
    dct_check_data = self.OrderedDict()
    for method_name, func in self._get_methods(self.__class__, include_parent=False):
      if method_name.startswith('_self_check_'):
        try:
          is_check_alert, msg, dct_alert_info = func(self)
          is_issue = is_issue or is_check_alert
          alerter_name = self.get_alert_name(method_name)
          check_name = self.get_check_name(method_name).upper()
          dct_alerter = self.alerter_status_dict(alerter=alerter_name)
          dct_alerter = {k.upper(): v for k, v in dct_alerter.items()}
          dct_check_data[check_name] = {
            "INFO" : msg,
            **dct_alert_info,
            **dct_alerter
          }
        except:
          err = "Programming error in self-check method '{}'.\n{}".format(method_name, self.trace_info())
          raise ValueError(err)
      #endif check method name
    #endfor check methods
    dct_results['CHECK_DATA'] = dct_check_data
    return is_issue, dct_results
  
  def get_result(self, avail : float, alert : float, check : str):
    return {
      "AVAIL_PRC": round(avail, 4),
      "ALERT_PRC": round(alert, 4),
      "CHECK": check,
    }    
    
  def get_check_name(self, func_name):
    return func_name.replace('_self_check_', '')
  
  def get_alert_name(self, func_name):
    return "check_" + self.get_check_name(func_name)
            
  def _self_check_memory(self):
    """
    Function for checking memory. As any `_self_check_` function it must return:
      - is_alert: True if alert is raised, False otherwise
      - msg: string with alert message
      - dct_result: dict with info for UI/BE
    """
    # start mandatory
    dct_result = self.OrderedDict()
    self_name = self.inspect.currentframe().f_code.co_name 
    alerter_name = self.get_alert_name(self_name)
    is_alert, msg = False, ""
    # end mandatory
    is_ok_mem = self.netmon.network_node_is_ok_available_memory_prc(
      addr=self.e2_addr, 
      min_prc_available=self.cfg_mem_low_prc,
    )      
    self.alerter_add_observation(not is_ok_mem, alerter=alerter_name) 
    
    if self.alerter_is_alert(alerter=alerter_name): #check if current alert exists
      mem_avail_prc = self.netmon.network_node_available_memory_prc(addr=self.e2_addr)      
      msg = "Memory low (free mem {:.1f}% < {:.1f}%) on node {} ({})".format(
          100 * mem_avail_prc, 100 * self.cfg_mem_low_prc, self.e2_addr, self.eeid,
      )
      is_alert = True
      dct_result = self.get_result(
        avail=mem_avail_prc, alert=self.cfg_mem_low_prc, check="Available RAM"
      )
    return is_alert, msg, dct_result
  
  
  def _self_check_disk(self):
    # start mandatory
    dct_result = self.OrderedDict()
    self_name = self.inspect.currentframe().f_code.co_name 
    alerter_name = self.get_alert_name(self_name)
    is_alert, msg = False, ""
    # end mandatory
    is_ok_disk = self.netmon.network_node_is_ok_available_disk_prc(
      addr=self.e2_addr, 
      min_prc_available=self.cfg_disk_low_prc
    )
    self.alerter_add_observation(not is_ok_disk, alerter=alerter_name) 
    
    if self.alerter_is_alert(alerter=alerter_name):  #check if current alert exists
      disk_avail_prc = self.netmon.network_node_available_disk_prc(addr=self.e2_addr)      
      msg = "Disk low (free disk {:.1f}% < {:.1f}%) on node {} ({})".format(
          100 * disk_avail_prc, 100 * self.cfg_mem_low_prc, self.e2_addr, self.eeid,
      ) 
      is_alert = True
      dct_result = self.get_result(
        avail=disk_avail_prc, alert=self.cfg_disk_low_prc, check="Available disk"
      )
    return is_alert, msg, dct_result
  
  
  def log_info(self, msg):
    if self.cfg_log_info:
      self.P(msg)
    return


  def _process(self):
    payload = None
    has_alerts = False

    self.log_info("Self check iteration...")    
    # run all chekers
    if not self.net_mon.network_node_info_available(addr=self.e2_addr):
      self.P("No hb info available for self at this moment {} ({})".format(self.e2_addr, self.eeid), color='r')
    else:
      has_alerts, dct_status = self.check_all()
      self.log_info("Self check iteration done. Has alerts: {}, Status:\n{}".format(
        has_alerts, self.json.dumps(dct_status, indent=4))
      )
      self.alerter_add_observation(int(has_alerts)) # default alerter
        
      if self.alerter_status_changed(): # check new raise or new lower
        # for this plugin only ALERTS should be used in UI/BE
        payload = self._create_payload(
          
          status="Self check alert raised" if has_alerts else "Self check alert lowered",
          **dct_status
        )
      #endif alert changed
    #endif info available    
    return payload
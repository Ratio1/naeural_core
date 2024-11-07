import json
import traceback

from datetime import datetime
from PyE2.logging.logger_mixins.datetime_mixin import _DateTimeMixin as dt_utils


def get_config_from_code(fn, log=None):
  if log is None:
    class _L:
      def P(self, *args, **kwargs):
        print(args[0])
    log = _L()
  result = "{"
  try:
    with open(fn,'rt') as fh:
      str_code = fh.read()
  except:
    return None
  start_pos = str_code.index("_CONFIG")
  str_to_parse = str_code[start_pos:]
  start_pos = str_to_parse.index("{")
  str_to_parse = str_to_parse[start_pos+1:]
  opened = 1
  cnt = 0
  while opened:
    ch = str_to_parse[cnt]
    if ch == '{':
      opened += 1
    elif ch == '}':
      opened -= 1
    result = result + ch    
    cnt += 1
  try:
    dct_result = eval(result)
  except:
    str_err = traceback.format_exc()
    log.P("Error deconding _CONFIG data from {}:\n{}".format(fn, str_err), color='r')
    return None
  return dct_result


def get_now_value_from_time_dict(dct_hours):
  conf_thr = None
  for hours in dct_hours:
    t1, t2 = hours.split('-')
    if dt_utils.now_in_interval_hours(start=t1, end=t2):
      conf_thr = dct_hours[hours]
      break
    # endif dt_utils.now_in_interval_hours(start=t1, end=t2)
  # endfor hours in dct_hours
  return conf_thr
  
if __name__ == '__main__':
  d ={
    "10:00-17:00" : 0.3,
    "17:00-10:00" : 0.5,
    }
  
  print(get_now_value_from_time_dict(d)) ### TODO L this returns None!!
  
  loc1 = [
         {
           'NAME' : 'TOP',
           'VALUE' : 100,
         },
         {
           'NAME' : 'LEFT',
           'VALUE' : 100,
         },
         {
           'NAME' : 'INSTANCE_ID',
           'VALUE' : 'TEST1',
         },
      ]
  
  loc2 = {
    # 'BLABLA1' : 1,
    # 'BLABLA2' : 2,
    # 'DESCRIPTION' : "12312312321",
    
    'PLUGIN_INSTANCE_PARAMETER_LIST' : [
         {
           'NAME' : 'TOP',
           'VALUE' : 100,
         },
         {
           'NAME' : 'LEFT',
           'VALUE' : 100,
         },
         {
           'NAME' : 'INSTANCE_ID',
           'VALUE' : 'TEST1',
         },         
         {
           'NAME'  : 'POINTS',
           'VALUE' :  '[[100,100], [0,0]]',
           'TYPE'  : 'LIST'
         }
      ]    
    }

  from plugins.io_formatters.cavi2 import process_instance_config
  print(json.dumps(process_instance_config(loc2), indent=4))
  

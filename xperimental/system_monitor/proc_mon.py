import sys

from naeural_core import Logger
from naeural_core.utils.sys_mon import SystemMonitor


if __name__ == '__main__':
  l = Logger('PMON', base_folder='.', app_folder='_cache')
  pid = None
  str_pid = ''
  if len(sys.argv) >= 2:
    str_pid = sys.argv[1]
    try:
      pid = int(str_pid)
    except:
      pass
  l.P("Analysing {}...".format('given pid {}'.format(pid) if str_pid != '' else 'default pid'))
  sys_mon = SystemMonitor(
    monitored_prefix='',
    log=l,
  )
  
  sys_mon.log_status(full_log=False)
  
import numpy as np
import psutil
import threading
import sys
import os

from collections import deque
from time import sleep, time, localtime, strftime

from naeural_core import constants as ct


from naeural_core import DecentrAIObject

__VER__ = '0.5.0'

class SysMonCt:
  PID = 'pid'
  NAME = 'name'
  UTIME = 'usr_time'
  STIME = 'sys_time'
  P_PRC = 'cpu'
  P_TIME = 'total'
  P_CUSR = 'children_user'
  P_CSYS = 'children_system'
  P_MB = 'memory_mb'
  P_MEM = 'memory_info'
  P_CWD = 'cwd'
  P_USR = 'username'
  
  LAST_SEEN = 'last_seen'

  K_THR = 'threads'
  K_CHL = 'CHILDREN'
  K_ID  = 'id'

  # attributes accpted by process inspection  
  P_ATTRS = [
    NAME,
    P_CWD,
    P_USR,
    'status',
    'cmdline',
  ]
  
  NON_TIMESERIES = [
    LAST_SEEN,
  ]
   


class SystemMonitor():
  
  def __init__(self, 
               monitored_prefix, 
               log, 
               max_size=1000, 
               tick=10, 
               DEBUG=False,
               run_processes_each=10,
               ping_interval=300,
               name='S_SysMon',
               **kwargs):
    super(SystemMonitor, self).__init__(**kwargs)    
    self.name = name
    self.monitored_prefix = monitored_prefix
    self.done = True
    self._DEBUG = DEBUG
    self.__version__ = __VER__
    self.log = log
    self.max_size = max_size
    self.main_proc = psutil.Process() 
    self.main_pid = self.main_proc.pid
    self.tick = tick
    self._loop_count = -1
    self._ping_interval = ping_interval
    self._last_ping_time = time()
    self._run_processes_each = run_processes_each
    self.threads_data = {}
    self.archived_threads = {}
    self._debug_state_dict = {
      'p_threads' : set(),
      'targets' : set()
    }
    self.process_tree = {}
    self.other_python_processes = []
    self.children_processes = []
    self._setup_thread()
    return
  
  
  def _setup_thread(self):
    self.P("Initializing {} v{} - monitoring prefix '{}' each {}s".format(
      self.__class__.__name__, self.__version__, self.monitored_prefix, self.tick
    ))
    if not hasattr(threading.enumerate()[0], 'native_id'):
      self.P("  SYSMON ERROR: Python {} does not support native thread IDs.".format(
        sys.version
      ), color='error')
    self.git_branch = self.log.git_branch
    self.os_name = self.log.get_os_name()
    self.python_version  = self.log.python_version
    self.conda_env = self.log.conda_env
    self.run_step()      
    return
    
  
  def P(self, s, **kwargs):
    self.log.P('[SYSMON] ' + s, **kwargs)
    return
  
  def P(self, s, color=None, **kwargs):
    if color is None or (isinstance(color,str) and color[0] not in ['e', 'r']):
      color = ct.COLORS.STATUS
    self.log.P('[SYSMON] ' + s, color=color,**kwargs)
    return      
  
  
  def __add_threads_info(self, threads):
    # first archive old threads
    all_threads = list(self.threads_data.keys())
    for thr in all_threads:
      if thr not in threads:
        # probably a old thread, lets archive it
        self.archived_threads[thr] = self.threads_data[thr].copy()
        del self.threads_data[thr]
    # now add or update threads
    for thr in threads:
      if thr not in self.threads_data:
        self.threads_data[thr] = {}
        # first get proc std attributes (non-ts)
        for fld in SysMonCt.P_ATTRS:
          if fld in threads[thr]:
            self.threads_data[thr][fld] = threads[thr][fld]
        # then create other non-timeseries attrs
        for fld in SysMonCt.NON_TIMESERIES:
          if fld in threads[thr]:
            self.threads_data[thr][fld] = threads[thr][fld]
      for k,v in threads[thr].items():        
        if k not in self.threads_data[thr]: 
          # if not create alread then must be a timeserie attr
          self.threads_data[thr][k] = deque(maxlen=self.max_size)
        if isinstance(self.threads_data[thr][k], deque):
          self.threads_data[thr][k].append(v)
        else:
          self.threads_data[thr][k] = v
    return
  

  ### PUBLIC

  @staticmethod
  def timestamp():
    return strftime('%Y-%m-%d %H:%M:%S', localtime(time()))

  @staticmethod
  def get_python_processes():
    targets = []
    piter = psutil.process_iter(SysMonCt.P_ATTRS + [SysMonCt.PID, SysMonCt.P_MEM])
    for proc in piter:
      if 'python' in proc.info[SysMonCt.NAME]:
        d = {k : v if v is not None else "" for k, v in proc.info.items() if k != SysMonCt.P_MEM}
        d[SysMonCt.P_MB] = proc.info[SysMonCt.P_MEM].rss // 1024**2
        d[SysMonCt.LAST_SEEN] = SystemMonitor.timestamp()
        targets.append(d)
    return targets
    
  
  def get_monitored_threads_info(self, prefix, proc=None, pid=None, interval=0.1, state_dict=None):
    dct_result = {}
    try:
      if proc is None:  
        p = psutil.Process(pid)    
      else:
        p = proc
      total_percent = p.cpu_percent(interval)
      p_times = p.cpu_times()
      total_time = sum(p_times)
      process_info = {
        p.pid : {
          SysMonCt.NAME : 'HOST_PROCESS',
          SysMonCt.P_PRC : total_percent,
          SysMonCt.UTIME : p_times.user,
          SysMonCt.P_TIME : total_time,
          SysMonCt.STIME : p_times.system,        
          SysMonCt.P_CUSR : p_times.children_user,        
          SysMonCt.P_CSYS : p_times.children_system,    
          SysMonCt.P_USR  : p.username(),
          SysMonCt.P_CWD  : p.cwd(),
          
          SysMonCt.LAST_SEEN : SystemMonitor.timestamp(),
        }
      }

      pst = p.threads()  
      thrs = threading.enumerate()
      threads = {}
      if hasattr(thrs[0], 'native_id'):
        prefix_len = len(prefix)
        targets = {x.native_id: x for x in thrs if prefix == x.name[:prefix_len]}
        if state_dict is not None and isinstance(state_dict, dict):
          state_dict['targets'].update(['{}:{}'.format(v.name,k) for k,v in targets.items()])
          state_dict['p_threads'].update([x.id for x in pst])
        threads = { 
          x.id : {
            SysMonCt.NAME : targets[x.id].name,
            SysMonCt.UTIME : x.user_time,
            SysMonCt.STIME : x.system_time,
            
            SysMonCt.LAST_SEEN : strftime('%Y-%m-%d %H:%M:%S', localtime(time()))
          }
          for x in pst if x.id in targets
        }    
      dct_result = {**process_info, **threads}
    except:      
      self.P("Error generating monitored threads info for pid: {}".format(pid), color='r')
      dct_result = None
    return dct_result  
  
  
  
  def display_process_tree_info(self, info=None):
    if info is None:
      info = self.get_process_tree()
    s = self.log.dict_pretty_format(info)
    self.log.P("Process tree:\n{}".format(s))
    return


  def get_process_tree(self, pid=None):
    """
    This is a one-shot process tree depth (recurse) analysis for a given 
    particular pid (current pid by default)

    Parameters
    ----------
    pid : int, optional
      the target process pid - current if None. The default is None.

    Returns
    -------
    dct_res : dict
      tree dict data structure containing process including child (recurse) 
      and threads.
      
    """
    def _generate_tree(dct_data, pid):
      try:
        p = psutil.Process(pid)
      except:
        self.P("Error generating process sub-tree for pid: {}".format(pid), color='r')
        return
      chlds = p.children()
      dct_info = p.as_dict(attrs=SysMonCt.P_ATTRS)
      for k,v in dct_info.items():
        dct_data[k] = " ".join(dct_info[k]) if isinstance(v, list) else v
      dct_data[SysMonCt.P_MB] = p.memory_info().rss // 1024**2
      for k,v in p.cpu_times()._asdict().items():
        dct_data[k] = v
      thrs = p.threads()
      if len(thrs) > 0:
        dct_data[SysMonCt.K_THR] = {}
        for thr in thrs:
          dct_data[SysMonCt.K_THR][thr.id] = {}
          for k,v in thr._asdict().items():
            if k != SysMonCt.K_ID:
              dct_data[SysMonCt.K_THR][thr.id][k] = v
              
      if len(chlds) > 0:
        dct_data[SysMonCt.K_CHL] = {}
        for i, chld in enumerate(chlds):
          dct_data[SysMonCt.K_CHL][chld.pid] = {}
          _generate_tree(dct_data[SysMonCt.K_CHL][chld.pid], chld.pid) 
      return
    #end _generate_tree
    dct_res = {}
    try:
      p = psutil.Process(pid)  
      dct_res = {p.pid : {}}
      _generate_tree(dct_res[p.pid], pid=p.pid)
    except:
      self.P("Error generating FULL process tree for pid: {}".format(pid), color='r')
    return dct_res


  def get_status_log(self, descending=True):
    """
    Generates the System Monitor status log as a list of strings

    Parameters
    ----------
    descending : bool, optional
      Sort the list of threads descending. The default is True.

    Returns
    -------
    buff : list
      list of strings.

    """
    buff = []    
    try:
      main_pid = self.main_pid 
      total_percent_last = self.threads_data[main_pid][SysMonCt.P_PRC][-1]
      total_percent_mean = np.mean(self.threads_data[main_pid][SysMonCt.P_PRC])
      total_time = self.threads_data[main_pid][SysMonCt.P_TIME][-1]
      user = self.threads_data[main_pid][SysMonCt.P_USR][:10]
      cwd = self.threads_data[main_pid][SysMonCt.P_CWD]
      git = self.git_branch
          
      buff.append('=' * 40 + ' System Monitor ' + '=' * 40)
      buff.append("Proc: {} [{}]  CPU mean/last: {:.1f}%/{:.1f}% ({:.1f}s)  Git:'{}'  Cwd: '{}'".format(
        main_pid, user, total_percent_mean, total_percent_last, total_time, 
        git, cwd)
      )

      lst_pids_and_times = [(
        _pid, 
        self.threads_data[_pid][SysMonCt.UTIME][-1] + self.threads_data[_pid][SysMonCt.STIME][-1]
        ) for _pid in self.threads_data]
      
      lst_pids_and_times = sorted(lst_pids_and_times, key=lambda x: x[1], reverse=descending)
      
      if len(lst_pids_and_times) == 1:
        buff.append("  Missing thread information:")
        buff.append("    - Maybe current process does not use ANY threads...")
        buff.append("    - Most likely wrong python version is installed. For linux please use py38 or above")
        buff.append("    - Detected OS: {}".format(self.os_name))
        buff.append("    - Detected Py: {}".format(self.python_version))
      
      for pid,thr_time in lst_pids_and_times:
        if pid == main_pid:
          continue
        name = self.threads_data[pid][SysMonCt.NAME]
        _utime = self.threads_data[pid][SysMonCt.UTIME][-1]
        _stime = self.threads_data[pid][SysMonCt.STIME][-1]
        thr_time1 = _utime + _stime
        time_is_ok = thr_time1 == thr_time
        if not time_is_ok:
          buff.append("  Something is WRONG with the time inside SysMon")
        local_perc = thr_time / (total_time + 1e-14)
        thr_overall_prc = total_percent_mean * local_perc
        _n = name[:36] + ' [{:<7}'.format(str(pid)+']')
        _n = _n + ' ' * (45 - len(_n[:45]))
        buff.append('  {} {:>4.1f}% (glb: {:>4.1f}%) Total: {:>4.1f} (stime: {:>5.2f} utime: {:>5.2f})'.format(
          _n, 
          local_perc * 100, thr_overall_prc, thr_time, 
          _stime, _utime)
        )
      if self._DEBUG:
        buff.append('')
        for k in self._debug_state_dict:
          ss = ", ".join([str(x) for x in sorted(self._debug_state_dict[k])])
          buff.append('  ## {:<10} {}'.format('{}:'.format(k), ss))

      if len(self.archived_threads) > 0:
        lst_archived = list(self.archived_threads.keys())
        n_archived = len(lst_archived)
        str_archived = ''
        for thr in lst_archived:
          str_archived += '{}...@{}, '.format(
            self.archived_threads[thr][SysMonCt.NAME][:10],
            self.archived_threads[thr][SysMonCt.LAST_SEEN]
          )
        buff.append('  Total {} archived threads\n'.format(n_archived))
        
      if len(self.children_processes) > 0:
        buff.append("Children processes:")
        for child in self.children_processes:
          chl_name = child[SysMonCt.NAME]
          buff.append("  PID:{:>20}  Mem:{:>5} MB  Name: '{}'  CWD: '{}'".format(
            "{} [{}]".format(child[SysMonCt.PID],child[SysMonCt.P_USR][:10]),
            child[SysMonCt.P_MB], chl_name,
            child[SysMonCt.P_CWD]
          ))
      
      if len(self.other_python_processes) > 0:
        buff.append("Other python processes:")
        for other_py in self.other_python_processes:
          try:
            other_pid = other_py[SysMonCt.PID]
            other_usr = other_py[SysMonCt.P_USR]
            if other_usr is not None and len(other_usr) > 10:
              other_usr = other_usr[:10]
            other_mb  = other_py[SysMonCt.P_MB]
            other_cwd = other_py[SysMonCt.P_CWD]
            buff.append("  PID:{:>20}  Mem:{:>5} MB  CWD: '{}'".format(
              "{} [{}]".format(other_pid, other_usr),
              other_mb, other_cwd          
            ))
          except:
            # do nothing if the data is not available - 
            self.P("Issue with python process analysis for: {}".format(other_py), color='r')
    except Exception as exc:
      buff.append("  EXCEPTION <{}> while generating sys-mon log.".format(str(exc)))
    return buff
  
  
  def log_status(self, full_log=False):
    """
    Outputs to log the current status of the System Monitor

    Parameters
    ----------
    full_log : bool, optional
      will display extended info if required. The default is False.

    Returns
    -------
    None.

    """
    buff = self.get_status_log()
    s = "SysMon info:\n" + buff[0]
    if len(buff) > 1:
      s += ':'
      for l in buff[1:]:
        s += '\n' + l 
    self.P(s)
    if full_log:
      s_tree = self.log.dict_pretty_format(self.process_tree)
      s_py = "\n".join([self.log.dict_pretty_format(x) for x in self.other_python_processes])
      self.P("Process tree:\n{}".format(s_tree))                       
      self.P("Other pythons running:\n{}".format(s_py))
    return
  
  
  def run_step(self):
    """
    Runs a full system analysis step

    Returns
    -------
    None.

    """
    self._loop_count += 1 # it starts at -1 :)
    self.log.start_timer('run_step', section='system_monitor')
    self.log.start_timer('get_monitored_threads_info', section='system_monitor')
    # extract all current threds within current process and update a simple debug state dict
    threads = self.get_monitored_threads_info(
      prefix=self.monitored_prefix,
      pid=self.main_pid,
      state_dict=self._debug_state_dict,
    )
    self.log.stop_timer('get_monitored_threads_info', section='system_monitor')
    if threads is not None:
      self.__add_threads_info(threads)
        
      if (self._loop_count % self._run_processes_each) == 0:
        self.log.start_timer('get_process_tree', section='system_monitor')
        # get current process tree
        self.process_tree = self.get_process_tree()
        self.log.stop_timer('get_process_tree', section='system_monitor')

        self.log.start_timer('get_python_processes', section='system_monitor')
        # get all python processes 
        python_processes = self.get_python_processes()
        # filter other running python(s)
        self.other_python_processes = [x for x in python_processes if x[SysMonCt.PID] != self.main_pid]
        # get current process children
        children = self.process_tree[self.main_pid].get(SysMonCt.K_CHL,{})
        self.children_processes = []
        if len(children) > 0:
          # copy children processes to a simple internal list
          for chl in children:
            children[chl][SysMonCt.PID] = chl
            self.children_processes.append(children[chl])
        self.log.stop_timer('get_python_processes', section='system_monitor')
      
      if (time() - self._last_ping_time) > self._ping_interval:
        self.P("System Monitor is alive.", color='g')
        self._last_ping_time = time()
    self.log.stop_timer('run_step', section='system_monitor')
    return  
  
  def start(self):
    self._thread = threading.Thread(
      target=self.run,
      name=self.name,
      daemon=True,
    )
    self._thread.start()
    return
   
    
  def run(self):
    """
    Main thread loop.

    Returns
    -------
    None.

    """
    self.P("Starting {} thread @ {}s monitoring interval...".format(
      self.__class__.__name__, self.tick), 
      color='g'
    )
    self.done = False
    while not self.done:
      self.run_step()
      sleep(self.tick)
    return
      
  
  def stop(self):
    self.P("Shutting down {}...".format(self.__class__.__name__), color='y')
    self.done = True
    self._thread.join()
    return
  
  
  def shutdown(self):
    self.stop()
    return
import json
import platform
import numpy as np
import gc

from time import time

from naeural_core import constants as ct

from collections import deque, defaultdict
from naeural_core import DecentrAIObject
from naeural_core.utils.sys_mon import SystemMonitor

MAX_LOG_SIZE = 10_000

MAX_LOCAL_HISTORY = int(30 * 60 / 10) # 30 minutes

BASE_INCREMENT_MB = 250


MIN_GLOBAL_INCREASE_GB = BASE_INCREMENT_MB * 2 / 1000
MIN_PROCESS_INCREASE_MB = BASE_INCREMENT_MB
MIN_PROCESS_INCREASE_GB = MIN_PROCESS_INCREASE_MB / 1000

WARMUP_READINGS = 5

class ApplicationMonitor(DecentrAIObject):
  def __init__(self, log, owner, **kwargs):
    self.owner = owner
    self.avail_memory_log = deque(maxlen=MAX_LOG_SIZE)
    self.process_memory_log = deque(maxlen=MAX_LOG_SIZE)
    self._process_memory_log_inited = False
    self._avail_memory_log_inited = False
    self.gpu_log = {}
    self.cpu_log = deque(maxlen=MAX_LOG_SIZE)
    self.start_avail_memory = None
    self._max_avail_increase = MIN_GLOBAL_INCREASE_GB
    self._max_process_increase = MIN_PROCESS_INCREASE_MB
    self.start_process_memory = None
    self.alert = False
    self.alert_avail = 0 # 0: no alert, 1: increase alert, 2: overall low mem alert    
    self.alert_process = False
    self.critical_alert = False
    self.owner_mem = -1
    self.__first_payload_prepared = False
    self.__first_ram_alert_raised = False
    self.__first_ram_alert_raised_time = 0
    self.__logged_hb_with_pipelines_status = False
    self.__logged_hb_with_active_plugins_status = False
    self.__last_delivered_error_notifs = defaultdict(int)
    self._done_first_smi_error = False
    self.dct_curr_nr = defaultdict(lambda:0)
    self.__last_temperature_info = None
    self.__local_data_history = {
      'cpu_load'            : deque(maxlen=MAX_LOCAL_HISTORY),
      'cpu_temp'            : deque(maxlen=MAX_LOCAL_HISTORY),
      'total_memory'        : deque(maxlen=MAX_LOCAL_HISTORY),
      'occupied_memory'     : deque(maxlen=MAX_LOCAL_HISTORY),

      'gpu_load'            : deque(maxlen=MAX_LOCAL_HISTORY),
      'gpu_temp'            : deque(maxlen=MAX_LOCAL_HISTORY),
      'gpu_occupied_memory' : deque(maxlen=MAX_LOCAL_HISTORY),
      'gpu_total_memory'    : deque(maxlen=MAX_LOCAL_HISTORY),
      
      'timestamps'          : deque(maxlen=MAX_LOCAL_HISTORY),      
    }
    super(ApplicationMonitor, self).__init__(log=log, prefix_log='[AMON]', **kwargs)
    return
  
  @property
  def first_payload_prepared(self):
    return self.__first_payload_prepared
  
  def P(self, s, color=None, **kwargs):
    if color is None or (isinstance(color,str) and color[0] not in ['e', 'r']):
      color = ct.COLORS.STATUS
    super().P(s, prefix=False, color=color, **kwargs)
    return      
  
  def startup(self):
    super().startup()
    self.sys_mon = SystemMonitor(log=self.log, monitored_prefix=ct.THREADS_PREFIX, name='S_SysMon')
    if self.owner.cfg_system_monitor:
      self.sys_mon.start()
    
    if self.owner.cfg_system_temperature_check:
      self.P("INFO: System temperatures strict checking enabled...", color='r')
      self.get_temperature_status()
    else:
      self.P("WARNING: System temperatures checking disabled...", color='r')    
    return
  
  
  def get_temperature_status(self, return_max=True):
    temperature_info = self.log.get_temperatures(as_dict=True)
    self.__last_temperature_info = temperature_info
    temps = temperature_info['temperatures']
    msg = temperature_info['message']
    max_temp = None
    critical_temp = False
    max_val = 0
    max_sensor = ''
    if temps in [None, {}]:
      if self.owner.cfg_system_temperature_check:
        self.P(msg, color='r')
      temps = None
    else:
      for sensor, status in temps.items():
        current = status['current']
        critical_temp = status['current'] >= status['high']
        if max_val < current or critical_temp:
          max_val = current
          max_temp = "{} at {}°C".format(sensor, current)
          max_sensor = sensor
          if critical_temp:
            self.P("ALERT: High temperature detected: {}".format(max_temp), color='r')
            max_temp += ' ERROR/CRITICAL!'
            break
          #endif high temp
        #endfor values
    #endif temps
    self.__last_temperature_info['max_temp'] = max_val
    self.__last_temperature_info['max_temp_sensor'] = max_sensor
    if return_max:      
      return max_temp    
    else:        
      return temps
  
  
  def get_opencv_info(self):
    result = ""
    try:
      import cv2
      result = cv2.getBuildInformation()
    except:
      pass
    return result
  
  def is_ram_alert(self):
    is_alert = self.avail_memory_log[-1] < (self.owner.cfg_min_avail_mem_thr * self.log.total_memory)
    if is_alert and not self.__first_ram_alert_raised:
      self.__first_ram_alert_raised = True
      self.__first_ram_alert_raised_time = self.log.time_to_str()
      self.P("ALERT: First memory alert raised. Dumping logs", color='r')
      stats = gc.get_stats()
      self.P("GC stats:\n{}".format(json.dumps(stats, indent=4)))      
      self.P("GC garbage:\n{}".format(json.dumps(gc.garbage, indent=4)))
      self.owner._save_object_tree(
        fn='obj_mem_alert.txt', 
        save_top_only=True, 
        top=150
      )
      gc.collect()
    return is_alert
  
  
  def is_critical_ram_alert(self):
    is_critical_alert = False
    if len(self.avail_memory_log) >= 2:
      is_critical_alert = self.avail_memory_log[-1] < (self.owner.cfg_critical_restart_low_mem * self.log.total_memory)
    return is_critical_alert
      
  
  @property
  def slow_process_memory_log(self):
    result = []
    l = len(self.process_memory_log)
    if l > 0:
      for i in list(reversed(list(range(1,100,10)))):
        if l > i:
          result.append(round(self.process_memory_log[-i], 4))    
    return result
  
  def get_owner_version(self):
    app_ver = self.owner.__version__
    core_ver = self.owner.core_version

    _ver = '{}{}{}'.format(
      '{} | '.format(app_ver) if core_ver is not None else '',
      '{} | '.format(app_ver) if core_ver is None else '{} |'.format(core_ver),
      '{}'.format(self.log.version),
    )
    return _ver
  
  def log_status(self, color=None):
    if self.start_process_memory is None or self.start_avail_memory is None:      
      return
    delta_avail_start = self.start_avail_memory - self.avail_memory_log[-1]
    delta_avail_last = self.avail_memory_log[-2] - self.avail_memory_log[-1]
    delta_process_start = self.process_memory_log[-1] - self.start_process_memory
    delta_process_last =  self.process_memory_log[-1] -  self.process_memory_log[-2]
    avail_mem = self.avail_memory_log[-1]
    total_mem = self.log.total_memory
        
    np_a = np.array(self.process_memory_log)
    avg = np.mean(np_a[1:] - np_a[:-1])

    text = 'OK'
    if self.is_ram_alert():
      color = 'r'
      text = ' * * * LOW MEMORY: {:>6,.2f} < {:>6,.2f} ({} x {:>6,.2f}) Initial: {} * * * '.format(
        self.avail_memory_log[-1],  self.owner.cfg_min_avail_mem_thr * self.log.total_memory, self.owner.cfg_min_avail_mem_thr, self.log.total_memory,
        self.__first_ram_alert_raised_time,
      )
      # Check critical alert
      self.critical_alert = self.is_critical_ram_alert()
      # end critical alerting
    elif color is not None and color[0] == 'y':
      text = 'Potential memory leak'
 
      

    str_log  = "Memory status for '{}' <{}> (aa/ap: {}/{}):".format(      
      self.owner.cfg_eeid, self.get_owner_version(),
      self.alert_avail, self.alert_process,
    )
    str_log += "\n\r==========================================================================================="
    str_log += "\n\r==========================================================================================="
    str_log += "\n\r                       Status: {}".format(text + ' ({} readings)'.format(len(self.avail_memory_log)))
    str_log += "\n\r                       Global mem status/increase:"
    str_log += "\n\r                         Avail / Total             : {:>6,.2f} / {:>6,.2f} (GB)".format(avail_mem, total_mem)
    str_log += "\n\r                         Avail Start / Last decr.  : {:>6,.2f} / {:>6,.2f} (GB)".format(self.start_avail_memory, delta_avail_last)
    str_log += "\n\r                         Out-of-process mem loss   : {:>6,.2f} (GB)".format(delta_avail_start - delta_process_start)
    str_log += "\n\r                        -------------------------------------------------------------"
    str_log += "\n\r                       Process memory status and increase:"
    str_log += "\n\r                         Start / Now               : {:>6,.2f} / {:>6,.2f} (GB)".format(self.start_process_memory, self.process_memory_log[-1])
    str_log += "\n\r                         Inc / Last Inc / Mean Inc : {:>6,.2f} / {:>6,.2f} / {:6,.2f} (GB)".format(delta_process_start, delta_process_last, avg)
    if self.owner_mem > 0:
      str_log += "\n\r                         Object tree mem eval      :{:>3,.3f} GB".format(self.owner_mem / 1024**3)
    str_log += "\n\r==========================================================================================="
    str_log += "\n\r==========================================================================================="
    self.P(str_log, color=color)
    return
      
  
  def log_avail_memory(self):
    avail_memory = self.log.get_avail_memory(gb=True)
    self.avail_memory_log.append(avail_memory)
    if not self._avail_memory_log_inited:
      if len(self.avail_memory_log) >= WARMUP_READINGS:
        self.start_avail_memory = avail_memory
        self._avail_memory_log_inited = True
    else:
      if avail_memory < (self.owner.cfg_min_avail_mem_thr * self.log.total_memory):
        self.alert_avail = 2
      else:
        self.alert_avail = 0 
        if avail_memory < self.start_avail_memory:
          # check for abnormal increase
          delta_start = self.start_avail_memory - avail_memory        
          if delta_start >= (self._max_avail_increase + MIN_GLOBAL_INCREASE_GB):
            self.alert_avail = 1
            self._max_avail_increase = delta_start
          #endif
        #endif
      #endif
    #endif
    return avail_memory


  def log_process_memory(self):
    self.log.start_timer('log_proc_mem', section='app_mon')
    process_memory = self.log.get_current_process_memory(mb=False)
    self.process_memory_log.append(process_memory)
    if not self._process_memory_log_inited:
      if len(self.process_memory_log) >= WARMUP_READINGS :
        self.start_process_memory = process_memory
        self._process_memory_log_inited = True
    else:
      if process_memory > self.start_process_memory:
        # check for abnormal increase
        delta_start = process_memory - self.start_process_memory 
        if delta_start > (self._max_process_increase + MIN_GLOBAL_INCREASE_GB):
          self.alert_process = True
          self._max_process_increase = delta_start
        else:
          self.alert_process = False

    if False:
      # self.P("Calculating overall process memory...")
      self.log.start_timer('get_obj_size', section='app_mon')    
      self.owner_mem = self.log.get_obj_size(self.owner)
      self.log.end_timer('get_obj_size', section='app_mon')
      # self.P("Done calculating overall process memory.")
    self.log.end_timer('log_proc_mem', section='app_mon')
    return process_memory


  def add_data_info(self, val, stage):
    self.dct_curr_nr[stage] += val
    return
  
  def get_basic_perf_info(self):
    str_info = ''
    str_info += '\n\n'+ 40 * '=' + ' Basic load Info ' + 40 * '='
    cpu_loads = np.array(self.cpu_log).astype('float16')
    str_cpu_loads = ' '.join('{:>4.1f}'.format(x) for x in cpu_loads[-10:])
    str_info += "\n  Used CPU: '{}'".format(self.log.get_processor_platform())
    str_info += "\n    Avg. CPU load:      {:>4.1f}% ({} %load)".format(
      np.mean(self.cpu_log),
      str_cpu_loads,
    )
    str_info += "\n    Proc mem over time: {} (GB))".format(self.slow_process_memory_log)
    cuda = self.owner.serving_manager.default_cuda if self.owner.serving_manager is not None else 'cuda:0'
    if isinstance(cuda, str):
      cuda = cuda.lower().replace('cuda:','')
      try:
        cuda = int(cuda)
      except:
        cuda = 0
    if cuda in self.gpu_log:
      dct_gpu = self.gpu_log[cuda]
      gpu_name = dct_gpu.get(ct.GPU_INFO.NAME)
      gpu_used = dct_gpu.get(ct.GPU_INFO.GPU_USED)
      gpu_mem = dct_gpu.get(ct.GPU_INFO.ALLOCATED_MEM)
      gpu_total_mem = dct_gpu.get(ct.GPU_INFO.TOTAL_MEM)
      gpu_temp = dct_gpu.get(ct.GPU_INFO.GPU_TEMP)
      fan_speed = dct_gpu.get(ct.GPU_INFO.GPU_FAN_SPEED)
      if (gpu_used is not None) and (None not in gpu_used):
        str_info += "\n  Used GPU: [{}] '{}'".format(cuda, gpu_name)
        cuda_loads = np.array(gpu_used).astype('float16')
        mem_loads = np.array(gpu_mem).astype('float16')
        gpu_temp = np.array(gpu_temp).astype('float16')
        str_cuda_loads = ' '.join('{:>4.1f}'.format(x) for x in cuda_loads[-10:])
        str_mem_loads = ' '.join('{:>4.1f}'.format(x) for x in mem_loads[-10:])
        str_gpu_temp = ' '.join('{:>4.1f}'.format(x) for x in gpu_temp[-10:])
        str_info += "\n    Avg. GPU core used: {:>4.1f}% ({} %load)".format(
          np.mean(gpu_used),
          str_cuda_loads,
        )
        str_info += "\n    Avg. GPU mem load:  {:>4.1f}% ({} GB)".format(
          np.mean(gpu_mem) / gpu_total_mem * 100,
          str_mem_loads,
        )
        str_info += "\n    GPU temp:           {:>4.1f}°C ({} °C)".format(
          np.mean(gpu_temp), 
          str_gpu_temp
        )
      else:
        str_info += "\n  GPU issue: No GPU present or driver issue"
    return str_info

  def get_summary_perf_info(self, last_n=10):
    gpu_info_list = []

    cpu_mean = round(np.mean(list(self.cpu_log)[-last_n:]), 1)
    ee_occupied_mem = round(self.process_memory_log[-1], 1)
    pc_occupied_mem = round(self.log.total_memory - self.avail_memory_log[-1], 1)
    pc_total_mem = round(self.log.total_memory, 1)

    gpu_load, gpu_total_mem, gpu_occupied_memory, gpu_temp, gpu_fan, gpu_fan_unit = None, None, None, None, None, None

    for i, dct_gpu in self.gpu_log.items():
      if gpu_load is None:
        lst_gpu_load = list(dct_gpu[ct.GPU_INFO.GPU_USED])
        if any(isinstance(x, str) or x is None for x in lst_gpu_load):
          lst_gpu_load = [-1] * len(lst_gpu_load)
        gpu_load = round(lst_gpu_load[-1], 1)

        gpu_total_mem = dct_gpu[ct.GPU_INFO.TOTAL_MEM]
        gpu_total_mem = round(gpu_total_mem, 1) if isinstance(gpu_total_mem, (int, float)) else None

        lst_gpu_occupied_memory = list(dct_gpu[ct.GPU_INFO.ALLOCATED_MEM])
        if any(isinstance(x, str) or x is None for x in lst_gpu_occupied_memory):
          lst_gpu_occupied_memory = [-1] * len(lst_gpu_occupied_memory)
        gpu_occupied_memory = round(lst_gpu_occupied_memory[-1], 1)

        lst_gpu_temp = list(dct_gpu[ct.GPU_INFO.GPU_TEMP])
        if any(isinstance(x, str) or x is None for x in lst_gpu_temp):
          lst_gpu_temp = [-1] * len(lst_gpu_temp)
        gpu_temp = round(lst_gpu_temp[-1], 1)

        lst_gpu_fan = list(dct_gpu[ct.GPU_INFO.GPU_FAN_SPEED])
        if any(isinstance(x, str) or x is None for x in lst_gpu_fan):
          lst_gpu_fan = [-1] * len(lst_gpu_fan)
        gpu_fan = round(lst_gpu_fan[-1], 1)

        gpu_fan_unit = dct_gpu[ct.GPU_INFO.GPU_FAN_SPEED_UNIT]

      gpu_load_mean = round(np.mean(lst_gpu_load[-last_n:]), 1)
      str_gpu_info = "cuda:{} {}%, mem {}/{}GB, {}°C, gpu fan {}{}".format(
        i,
        gpu_load_mean,
        gpu_occupied_memory,
        gpu_total_mem,
        gpu_temp,
        gpu_fan,
        gpu_fan_unit
      )
      gpu_info_list.append(str_gpu_info)

    str_gpu_info_list = ", ".join(gpu_info_list) if len(gpu_info_list) > 0 else "no gpu"

    str_info = "cpu {}%, ram(EE) {}({})/{} GB, {}".format(
      cpu_mean,
      pc_occupied_mem, ee_occupied_mem,
      pc_total_mem,
      str_gpu_info_list
    )
    
    temp = self.get_temperature_status(return_max=True)
    cpu_temp = None
    if temp is not None:
      str_info += ", {}".format(temp)
      cpu_temp = self.__last_temperature_info['max_temp']

    self.add_local_history_and_save(
      cpu_load=cpu_mean,
      cpu_temp=cpu_temp,
      total_memory=pc_total_mem,
      occupied_memory=pc_occupied_mem,
      gpu_load=gpu_load,
      gpu_temp=gpu_temp,
      gpu_fan=gpu_fan,
      gpu_fan_unit=gpu_fan_unit,
      gpu_occupied_memory=gpu_occupied_memory,
      gpu_total_memory=gpu_total_mem,
      timestamps=self.log.now_str(nice_print=True, short=False),
    )

    return str_info

  def _add_gpu_data(self, lst_info):
    if not isinstance(lst_info, list):
      return
    for i,gpu in enumerate(lst_info):
      if i not in self.gpu_log:
        self.gpu_log[i] = {
          ct.GPU_INFO.TOTAL_MEM : gpu[ct.GPU_INFO.TOTAL_MEM],
          ct.GPU_INFO.NAME : gpu[ct.GPU_INFO.NAME],
          ct.GPU_INFO.GPU_USED : deque(maxlen=MAX_LOG_SIZE),
          ct.GPU_INFO.ALLOCATED_MEM : deque(maxlen=MAX_LOG_SIZE),
          ct.GPU_INFO.GPU_TEMP : deque(maxlen=MAX_LOG_SIZE),
          ct.GPU_INFO.GPU_FAN_SPEED : deque(maxlen=MAX_LOG_SIZE),
          ct.GPU_INFO.GPU_FAN_SPEED_UNIT : gpu[ct.GPU_INFO.GPU_FAN_SPEED_UNIT],
        }
      self.gpu_log[i][ct.GPU_INFO.GPU_USED].append(gpu[ct.GPU_INFO.GPU_USED])
      self.gpu_log[i][ct.GPU_INFO.ALLOCATED_MEM].append(gpu[ct.GPU_INFO.ALLOCATED_MEM])
      self.gpu_log[i][ct.GPU_INFO.GPU_TEMP].append(gpu[ct.GPU_INFO.GPU_TEMP])
      self.gpu_log[i][ct.GPU_INFO.GPU_FAN_SPEED].append(gpu[ct.GPU_INFO.GPU_FAN_SPEED])      
    return
      

  def get_gpu_info(self):
    # self.log.start_timer('gpu_info', section='async_comm')
    info = self.log.gpu_info(mb=False) or "Pytorch or NVidia-SMI issue"
    # self.log.end_timer('gpu_info', skip_first_timing=False, section='async_comm')
    
    if isinstance(info, list):
      self._add_gpu_data(info)
    return info

  def maybe_send_error_notification(
    self, error_type, msg, info, displayed=True, time_threshold=300
  ):
    last_send = self.__last_delivered_error_notifs[error_type]
    if (time() - last_send) > time_threshold:
      self.__last_delivered_error_notifs[error_type] = time()
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING, 
        msg=msg, 
        info=info,
        displayed=displayed,
      )
    return
  
  def add_local_history_and_save(self, **kwargs):
    # TODO: refactor this code to use constants
    dct_to_save = {
      'address' : self.owner.e2_addr,
      'eth_address' : self.owner.eth_address,
      'alias' : self.owner.cfg_eeid,
    }    
    current_epoch = self.owner._network_monitor.epoch_manager.get_current_epoch()
    dct_to_save['current_epoch'] = current_epoch
    dct_to_save['current_epoch_avail'] = self.owner._network_monitor.epoch_manager.get_current_epoch_availability()
    dct_to_save['uptime'] = self.log.elapsed_to_str(self.owner.running_time)
    dct_to_save['version'] = self.get_owner_version().split(' ')[0]
    epochs = self.owner._network_monitor.epoch_manager.get_node_epochs(
      node_addr=self.owner.e2_addr, autocomplete=True, as_list=True
    )
    if isinstance(epochs, list):
      dct_to_save['last_epochs'] = epochs[-10:]
    else:
      dct_to_save['last_epochs'] = []
    #endif epochs
    dct_to_save['last_save_time'] = self.log.now_str(nice_print=True, short=True)

    for k,v in kwargs.items():
      if k in self.__local_data_history:
        self.__local_data_history[k].append(v)
        dct_to_save[k] = list(self.__local_data_history[k])

    self.log.save_json_to_data(dct_to_save, 'local_history.json', indent=False)    
    return
  

  def get_status(self, status=None, full=True, send_log=False):
    machine_ip = self.log.get_localhost_ip()
    machine_memory = round(self.log.total_memory,3)
    cpu_usage = self.log.get_cpu_usage()
    self.cpu_log.append(cpu_usage)
    
    is_docker = self.owner.runs_in_docker
    docker_env = self.owner.docker_env
    docker_source = self.owner.docker_source
    
    # check & get mem statuses required for log_status
    avail_memory = round(self.log_avail_memory(),3)
    process_memory = round(self.log_process_memory(),3)
    # log status if required
    if (not self.alert_avail) and (not self.alert_process):
      self.alert = False
    else:
      self.log_status(color=None if self.alert_avail == 0 else 'r')    
    
    
    # hard check avail disk
    # TODO: automatic checker of info data post gathering
    total_disk = round(self.log.total_disk, 3)
    avail_disk = round(self.log.get_avail_disk(gb=True), 3)
    display = True
    if avail_disk < self.owner.cfg_min_avail_disk_thr:
      info = "WARNING: Avail disk on '{}' current volume is {:.1f} GB below minimum of {:.1f} GB".format(
        self.owner.cfg_eeid,
        avail_disk,
        self.owner.cfg_min_avail_disk_thr
      )
      if display:
        self.P(info, color='error')
        
      self.maybe_send_error_notification(
        error_type="disk",
        msg="LOW DISK SPACE ON '{}'".format(self.owner.cfg_eeid),
        info=info,
        displayed=display,          
      )
    
    if avail_memory < self.owner.cfg_min_avail_mem_thr * self.log.total_memory:
      info = "WARNING: available memory {:.2f} GB ({:.1f}%) below {:.2f} GB ({:.1f}%) threshold of total memory ({:.1f} GB)".format(
        avail_memory, avail_memory / self.log.total_memory * 100,
        self.owner.cfg_min_avail_mem_thr * self.log.total_memory, self.owner.cfg_min_avail_mem_thr * 100,
        self.log.total_memory,
      )
      if display:
        self.P(info, color='error')

      self.maybe_send_error_notification(
        error_type="memory",
        msg="LOW MEMORY ON '{}'".format(self.owner.cfg_eeid),
        info=info,
        displayed=display,
      )
      
    # now get basic info
    
    str_cpu = self.log.get_processor_platform()
    
    total_time = self.owner.running_time
    timestamp = self.log.now_str(nice_print=False)
    current_time = self.log.now_str(nice_print=True, short=False) # leave with milis - modify downstream if necessary

    n_payloads = self.dct_curr_nr[ct.NR_PAYLOADS]
    n_inferences = self.dct_curr_nr[ct.NR_INFERENCES]
    n_stream_data = self.dct_curr_nr[ct.NR_STREAMS_DATA]
    self.dct_curr_nr = defaultdict(lambda:0)
    
    if status is None:
      status = ct.DEVICE_STATUS_ONLINE
    
    lst_gpus = self.get_gpu_info()
    
    loops_timings = {}
    if self.owner.comm_manager is not None:
      loops_timings = {
        'main_loop_avg_time' : self.owner.avg_loop_timings,
        'comm_loop_avg_time' : self.owner.comm_manager.avg_comm_loop_timings,
      }

    active_serving_processes = []
    serving_pids = []
    default_cuda = ''
    if self.owner.serving_manager is not None:
      active_serving_processes = self.owner.serving_manager.get_active_servers(show=False)
      default_cuda = self.owner.serving_manager.default_cuda
      serving_pids = self.owner.serving_manager.serving_pids
    str_active_serving_processes = '\n\nActive serving processes (in-process: {}):'.format(self.owner.in_process_serving)  
    if len(active_serving_processes) == 0:
      str_active_serving_processes += '\n  --  No running serving processes --'
    for svr in active_serving_processes:
      str_active_serving_processes += '\n'
      for k,v in svr.items():
        str_active_serving_processes += "\n    {:<15}{}".format(k + ':', v)

    str_gpu_info = self.get_basic_perf_info()
    
    str_timers = None
    if full:
      str_timers = '\n'.join(self.log.format_timers())
      # now get the sysmon info
      str_timers += '\n\n' + '\n'.join(self.get_sys_mon_info())
      # add package info
      str_timers +='\n\nPackage information (Python {}):\n{}'.format(
        self.log.python_version,
        self.log.get_packages(as_text=True, indent=4)
      )
      # add serving processes
      str_timers += str_active_serving_processes
      # add open-cv if available
      str_timers += "\n\n" + self.get_opencv_info()
    #endif prepare timers if necessary
    
    dct_cap_info = None
    if self.owner.capture_manager is not None:
      dct_cap_info = self.owner.capture_manager.get_captures_status(display=False, as_dict=True)
    
    dct_comm_info = None  
    inkB, outkB = 0, 0
    if self.owner.comm_manager is not None:
      dct_comm_info = self.owner.comm_manager.get_comms_status()
      for _, dct_c in dct_comm_info.items():
        inkB += dct_c[ct.HB.COMM_INFO.IN_KB]
        outkB += dct_c[ct.HB.COMM_INFO.OUT_KB]
      
    str_git_branch = self.sys_mon.git_branch
    str_conda_branch = self.log.conda_env
    
    if is_docker:
      str_git_branch = 'Docker:' + docker_source
      str_conda_branch = 'Env:' + docker_env      
    
    if send_log:
      error_log = self.log.err_log
      dev_log = self.log.app_log
    else:
      dev_log   = 'Device log available only upon request or in special situations'
      error_log = 'Error log available only upon request or in special situations'
    #endif send log

    hb_contains_pipelines = self.owner.cfg_hb_contains_pipelines
    hb_contains_active_plugins = self.owner.cfg_hb_contains_active_plugins
    
    # The following is required to update the internal state of the business manager
    _active_plugin = self.owner.business_manager.get_active_plugins_instances(
        as_dict=True # send business plugin instances info as objects (NOT as lists)
      ) # this call is REQUIRED for internal state update
    active_business_plugins = []
    if self.owner.business_manager is not None and hb_contains_active_plugins:
      active_business_plugins = _active_plugin
    

    s_os = platform.platform()
    s_os = s_os.replace('Windows', 'W').replace('Linux','L')
    _ver = '{} {}'.format(self.get_owner_version(), s_os)
        
    lst_config_streams = []    
    if self.owner.config_manager is not None and hb_contains_pipelines:      
      lst_config_streams = self.owner.get_pipelines_view()
      
    if (not self.__logged_hb_with_pipelines_status) and (self.owner.config_manager is not None):
      if hb_contains_pipelines:
        self.P(f"INFO: Heartbeat with pipelines status. Current: {len(lst_config_streams)}", boxed=True)
      else:
        self.P("INFO: Heartbeat WITHOUT pipelines status", boxed=True)
      self.__logged_hb_with_pipelines_status = True
      
      
    if (not self.__logged_hb_with_active_plugins_status) and (self.owner.business_manager is not None):
      if hb_contains_active_plugins:
        self.P(f"INFO: Heartbeat with active plugins status. Current: {len(active_business_plugins)}", boxed=True)
      else:
        self.P("INFO: Heartbeat WITHOUT active plugins status", boxed=True)
      self.__logged_hb_with_active_plugins_status = True
    
    

    address = self.owner.e2_addr      
    is_supervisor = self.owner.is_supervisor_node
    
    whitelist = self.owner.whitelist
    
    

    dct_status = {
      #mandatory
      ct.HB.CURRENT_TIME      : current_time,
      
      ct.HB.EE_ADDR           : address,
      ct.HB.EE_WHITELIST      : whitelist,
      ct.HB.EE_IS_SUPER       : is_supervisor,
      ct.HB.EE_FORMATTER      : self.owner.cfg_io_formatter,
      
      # ALERTS
      ct.HB.IS_ALERT_RAM      : self.is_ram_alert(),
      # End ALERTS

      ct.HB.NR_INFERENCES     : n_inferences,
      ct.HB.NR_PAYLOADS       : n_payloads,
      ct.HB.NR_STREAMS_DATA   : n_stream_data,
      ct.HB.PY_VER            : self.log.python_version,

      ct.HB.SECURED           : self.owner.is_secured,

      ct.HB.EE_HB_TIME        : self.owner.cfg_app_seconds_heartbeat,
      ct.HB.DEVICE_STATUS     : status,
      ct.HB.MACHINE_IP        : machine_ip,
      ct.HB.MACHINE_MEMORY    : machine_memory,
      ct.HB.AVAILABLE_MEMORY  : avail_memory,
      ct.HB.PROCESS_MEMORY    : process_memory,      
      ct.HB.CPU_USED          : cpu_usage,
      ct.HB.GPUS              : lst_gpus,
      ct.HB.GPU_INFO          : str_gpu_info,
      ct.HB.DEFAULT_CUDA      : default_cuda,
      ct.HB.CPU               : str_cpu,
      ct.HB.TIMESTAMP         : timestamp,
      ct.HB.UPTIME            : total_time,
      ct.HB.VERSION           : _ver,
      ct.HB.LOGGER_VERSION    : self.log.version,
      ct.HB.TOTAL_DISK        : total_disk,
      ct.HB.AVAILABLE_DISK    : avail_disk,
      ct.HB.ACTIVE_PLUGINS    : active_business_plugins,
      ct.HB.STOP_LOG          : self.owner.main_loop_stop_log,
      
      ct.HB.R1FS_ID           : self.owner.r1fs_id,
      ct.HB.R1FS_ONLINE       : self.owner.r1fs_started,
      
      ct.HB.TEMPERATURE_INFO  : self.__last_temperature_info,
      
      ct.HB.GIT_BRANCH        : str_git_branch,
      ct.HB.CONDA_ENV         : str_conda_branch,      
        
      ct.HB.CONFIG_STREAMS    : lst_config_streams,
      ct.HB.DCT_STATS         : dct_cap_info,
      ct.HB.COMM_STATS        : dct_comm_info,
      ct.HB.COMM_INFO.IN_KB   : round(inkB, 3),
      ct.HB.COMM_INFO.OUT_KB  : round(outkB, 3),
        
      ct.HB.SERVING_PIDS      : serving_pids, 
      ct.HB.LOOPS_TIMINGS     : loops_timings,

      ct.HB.TIMERS            : 'Timers not available in summary',
      ct.HB.DEVICE_LOG        : dev_log,
      ct.HB.ERROR_LOG         : error_log
    }
    
    dct_ext_status = {    
      ct.HB.TIMERS            : str_timers,
    }
    
    
    if full:
      for k, v in dct_ext_status.items():
        dct_status[k] = v
        
    if self.owner.cfg_compress_heartbeat:
      str_text = "{}"
      try:
        str_text = json.dumps(dct_status)
      except Exception as exc:
        self.P("ERROR: Could not compress heartbeat: {}\n{}".format(exc, dct_status), color='error')
      str_encoded = self.log.compress_text(str_text)
      dct_status = {
        ct.HB.ENCODED_DATA : str_encoded,
        # needed in caller...
        ct.HB.NR_INFERENCES     : n_inferences,
        ct.HB.NR_PAYLOADS       : n_payloads,
        ct.HB.NR_STREAMS_DATA   : n_stream_data,        
        ct.HB.HEARTBEAT_VERSION : ct.HB.V2, 
      }
    else:
      dct_status[ct.HB.HEARTBEAT_VERSION] = ct.HB.V1
      
    if not self.first_payload_prepared:
      self.__first_payload_prepared = True
      self.P("First hb for {}".format(address), boxed=True)

    return dct_status
  
  
  def get_sys_mon_info(self):
    if hasattr(self, 'sys_mon') and self.sys_mon is not None:
      return self.sys_mon.get_status_log(descending=True)
    return []

  def log_sys_mon_info(self, color='n'):
    info = self.get_sys_mon_info()
    s = "\n" + "\n".join(info)
    self.log.P(s, color=color)
    return


  def _maybe_stop_sys_mon(self):
    if hasattr(self, 'sys_mon') and self.sys_mon is not None:
      self.sys_mon.stop()
      self.sys_mon.log_status()
    return
  

  def shutdown(self):
    super().shutdown()
    self._maybe_stop_sys_mon()
    
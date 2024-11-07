import numpy as np
from collections import deque
from time import time

__VER__ = '1.3.0'

MAX_QUEUE_SHOW = 8

class AlertHelper:
  # atomic & thread-safe class, no logging inside it so no subclassing of DecentrAIObject
  def __init__(self, 
               name,
               values_count, 
               raise_confirmation_time, 
               lower_confirmation_time,
               raise_alert_value=0.5, 
               lower_alert_value=0.4,
               alert_mode='mean',
               alert_mode_lower='max',
               reduce_value=False,
               reduce_threshold=0.5,
               show_version=True,
               ):
    """
    Create a AlertHelper object
    
    The pseudo-code is as follows:
      
      1. add data to data_train
      2. if eval(data_train) >= raise_threshold and status is not-alerted:
        2.1. raise alert
      3. else if status is alerted and eval(data_train) < lower_threshold
        3.1 lower alert
      
      

    Parameters
    ----------
    
    values_count : int
      the number of observations that are required for a state change evaluation.
    
    raise_confirmation_time : int
      seconds that need to pass for the confirmation of a state change. if during this time
      the queue buffer is overrun then it will be extended for accurate evaluation. 
      OBSERVATION: 
        please do not make any confusions with acquisition time. This parameter 
        controls the finite state machine behaviour from the moment the available date 
        in queue is eligible for a state change
        
    lower_confirmation_time: int
      seconds that need to pass in order to lower alert from 1 to 0.
      Observation:  it is preferable to have longer wait time as well as smaller threshold when 
                    lowering alarms

    
    raise_alert_value : int, optional
      the result that triggers the True state. Depends on 'alert mode. The default is 0.5.
      the transition is triggered at first eval >= raise_alert_value
      
          
    lower_alert_value: int, optional
      the results that enables transition from alert mode to non-alert mode. Default 0.4.
      as long as the eval is >= lower_alert_value the raised status will be kept and no transition will
      be triggered      
      Observation:  it is preferable to have longer wait time as well as smaller threshold when 
                    lowering alarms. For example it is better that if the raise is done with a 
                    0.5 threshold and the current eval is around that value to have a lower threshold 
                    that will ensure a "certainty" of the alert lowering - eg I will raise the allarm when
                    I have 0.5 (50%) eval but if the alarm is already raised I will not lower it unless we have
                    below a 0.4 (40%) evaluation
          
    reduce_value: bool, optional,
      this flag reduces the added observation to 0 or 1 for non zero values, default `False`
    
    alert_mode : str, optional
      The alert mode - 'mean' will compute mean of values and compare it with `alert_value` 
      while other 'sum' will generate a comparison of the `sum(queue)` with `alert_value`. 
      The default is 'mean'.

    alert_mode_lower : str, optional
      The same as alert_mode with the exception that this will be applied when trying to lower an alert.
      The default is 'max'.

    Returns
    -------
    None.

    """
    self.name = name + '_AH'
    self.__version__ = __VER__
    self._show_version = show_version
    self.version = self.__version__
    self._eval_func, self._eval_method = self.get_eval_func_method(alert_mode=alert_mode)
    self._eval_func_lower, self._eval_method_lower = self.get_eval_func_method(alert_mode=alert_mode_lower)
    self._queue_size = values_count
    self._raise_wait_time = raise_confirmation_time
    self._lower_wait_time = lower_confirmation_time
    self._raise_alert_value = raise_alert_value
    self._lower_alert_value = lower_alert_value
    self._alert_mode = alert_mode
    self._alert_mode_lower = alert_mode_lower
    self._reduce_value = reduce_value
    self._reduce_threshold = reduce_threshold
    self._eval_queue = None
    self._times_queue = None
    self._change_time = None
    self._eval_queue_raw = None
    self._eval_times = None
    self._changing_state = None
    
    self._last_raise_timestamp = None
    self._last_lower_timestamp = None

    self.reset()
    return

  def get_eval_func_method(self, alert_mode):
    alert_mode = alert_mode.lower()
    if alert_mode == 'sum':
      return np.sum, 'S'
    elif alert_mode == 'mean':
      return np.mean, 'A'
    elif alert_mode == 'median':
      return np.median, 'M'
    elif alert_mode == 'min':
      return np.min, 'mi'
    elif alert_mode == 'max':
      return np.max, 'Mx'
    raise ValueError("Evaluation function unknown '{}'".format(alert_mode))

  def _reset_queue(self, hard=False):    
    new_queue = deque(maxlen=self._queue_size)
    new_time = deque(maxlen=self._queue_size)
    if hasattr(self, 'queue') and len(self.queue) > 0 and not hard:
      new_queue.append(self.queue[-1])
      new_time.append(self._times_queue[-1])
    self.queue = new_queue
    self._times_queue = new_time

    new_values_queue = deque(maxlen=self._queue_size)
    if hasattr(self, '_raw_values_queue') and len(self._raw_values_queue) > 0 and not hard:
      new_values_queue.append(self._raw_values_queue[-1])
    self._raw_values_queue = new_values_queue

    self._queue_time = time() # FIXME: this can be set to None
    self._first_change = True
    self.in_confirmation = False
    self._confirmation_wait = 0
    self._last_changing_state = self._changing_state
    self._changing_state = None
    return
  
  def _increase_queue(self):
    curr_len = self.queue.maxlen
    new_queue = deque(maxlen=curr_len + 1)
    new_queue.extend(self.queue)
    self.queue = new_queue
    
    new_values_queue = deque(maxlen=curr_len + 1)
    new_values_queue.extend(self._raw_values_queue)
    self._raw_values_queue = new_values_queue    

    new_times_queue = deque(maxlen=curr_len + 1)
    new_times_queue.extend(self._times_queue)
    self._times_queue = new_times_queue    
    return
  
  
  def _queue_full(self):
    return len(self.queue) >= self._queue_size
  
  
  def _confirmation_time_passed(self, return_time=False):
    # TODO: review if there we should use self._queue_time or self.get_queue_time()
    _time = time() - self._queue_time
    self._confirmation_wait = _time
    self._last_confirmation_wait = _time
    if self._state:
      # if alert then (bigger) wait before lowering
      _passed = _time >= self._lower_wait_time      
    else:
      # wait before raising alert
      _passed = _time >= self._raise_wait_time
    if return_time:
      return _passed, _time
    return _passed
  
  
  def reset(self, default=False, hard_reset=False):
    self._state = default
    self._last_state = self._state
    self._reset_queue(hard=hard_reset)
    return
  
  
  def hard_reset(self, default=False):
    self.reset(
      default=default,
      hard_reset=True,
    )
    return
  
  
  def get_queue_time(self):
    if self._eval_times is not None and len(self._eval_times) > 1:
      qt = self._eval_times[-1] - self._eval_times[0]
      return qt
    return None
  
  def get_last_raw_value(self):
    if len(self._raw_values_queue) > 0:
      return self._raw_values_queue[-1]
    return None
  
  def get_last_eval_queue(self):
    return self._eval_queue if self._eval_queue is not None else []

  def get_last_eval_queue_raw(self):
    return self._eval_queue_raw if self._eval_queue_raw is not None else []
      
  def get_last_confirmation_time(self):
    return round(self._last_confirmation_wait,2)
 
  
  def add_observation(self, value, reduce_threshold=None):
    if reduce_threshold is None:
      reduce_threshold = self._reduce_threshold
    if value is None:
      return
    # add observation    
    self._raw_values_queue.append(value)
    self._times_queue.append(time())
    if self._reduce_value:
      self.queue.append(int(value >= reduce_threshold))
    else:
      self.queue.append(value)
    self._eval_queue = self.queue.copy()
    self._eval_times = self._times_queue.copy()
    self._eval_queue_raw = self._raw_values_queue.copy()
        
    # check if enough data is accumulated
    if self._queue_full():
      # compute current status
      alert_value = self._lower_alert_value if self._state else self._raise_alert_value
      eval_func = self._eval_func_lower if self._state else self._eval_func
      current_state = bool(eval_func(self.queue) >= alert_value)
        
      # check if this was first change after reset
      if current_state != self._state and self._first_change:
        self._first_change = False
        # now we can wait until enough time has passed
        self._queue_time = time()
      #endif
    
      last_state = self._state
      # check if new proposed state is different than last proposed state 
      # and enough time has passed from last change passed (that forces a extension
      # of the queue if required)
      if (current_state != self._state):
        self._changing_state = current_state
        confirmed, waited = self._confirmation_time_passed(True)
        self.in_confirmation = not confirmed
        if confirmed:
          self._state = current_state
          self._reset_queue()
          self._change_time = self._times_queue[-1]
          if self._state:
            self._last_raise_timestamp = self._change_time
          else:
            self._last_lower_timestamp = self._change_time
        #endif

      # we do this check after each iteration because we need to extend the queue
      # no matter what the current_state is. If we extend the queue only when the
      # current state == last state then we discard observations that enforce the change
      # after it has taken place (see the spike neg test)
      if self.in_confirmation:
        if self._confirmation_time_passed():
          self._reset_queue()
        else:
          # we extend the buffer in order to evaluate all observations from the
          # first change until now. If we increase the queue only if the state 
          # is changed then we can have spikes that trigger false alerts 
          # Run the spike test with the previous version
          self._increase_queue()
      #endif
      self._last_state = last_state
    else:
      self._last_state = self._state
    return
  
  def status_changed(self):
    return self._last_state != self._state
  
  def is_alert(self):
    return self._state
  
  def is_new_alert(self):
    return self.is_alert() and self.status_changed()

  def is_new_raise(self):
    return self.is_new_alert()

  def is_new_lower(self):
    return not self.is_alert() and self.status_changed()
  
  def get_time_from_change(self):
    return time() - self._change_time if self._change_time is not None else -1
  
  
  def get_last_alert_duration(self):
    """
    This function returns the time spent during the last alert. Function workd only if the alert is not active

    Returns
    -------
      float

    """
    result = None
    last_raise = self._last_raise_timestamp
    last_lower = self._last_lower_timestamp
    has_raise = last_raise is not None
    has_lower = last_lower is not None
    if has_raise and has_lower and last_lower > last_raise:
      result = round(last_lower - last_raise, 2)
    return result
  
  

  def get_setup_values(self):
    dct = {
      'queue_size': self._queue_size,
      'raise_wait_time': self._raise_wait_time,
      'lower_wait_time': self._lower_wait_time,
      'raise_alert_value': self._raise_alert_value,
      'lower_alert_value': self._lower_alert_value,
      'alert_mode': self._alert_mode,
      'alert_mode_lower': self._alert_mode_lower,
      'reduce_value': self._reduce_value,
      'reduce_threshold': self._reduce_threshold
    }
    return dct

  def __repr__(self):
    _q = [round(x, 3) for x in self.get_last_eval_queue()]
    _q_raw = [round(x, 3) for x in self.get_last_eval_queue_raw()]
        
    if self._reduce_value:
      s_q = "["+ ", ".join(["{}:{}".format(v1, v2) for v1,v2 in zip(_q, _q_raw)])
      s_q = s_q + "]"
    else:
      if len(_q) > MAX_QUEUE_SHOW:
        s_q = "<{}>[...,".format(len(_q))
        s_q = s_q + ",".join([str(xx) for xx in _q[-7:]])
        s_q = s_q + "]"
      else:
        s_q = str(_q)
    
    _ct = 'NA'
    if self.status_changed():
      _ct = "{}/{}".format(
        self.get_last_confirmation_time(), 
        self._raise_wait_time if self._state else self._lower_wait_time,
        )
    elif self.in_confirmation:
      _ct = "{}/{}".format(
        self.get_last_confirmation_time(), 
        self._raise_wait_time if not self._state else self._lower_wait_time,
        )
    else:
      # no confirmation, no status changed ... but we need to test if we just droped
      # a false status changed
      if len(_q) > self._queue_size:
        _ct = "{}/{}".format(
          self.get_last_confirmation_time(), 
          self._raise_wait_time if self._last_changing_state else self._lower_wait_time,
          )

    params_idx = self.is_alert() if self.status_changed() else not self.is_alert()
    alert_value = [self._lower_alert_value, self._raise_alert_value][params_idx]
    vs_oper = ['<', '>='][params_idx]
    eval_method = [self._eval_method_lower, self._eval_method][params_idx]
    eval_func = [self._eval_func_lower, self._eval_func][params_idx]
    # endif long code vs short code
    
    queue_time = self.get_queue_time()
    last_change = self.get_time_from_change()

    _s = "A={}, N={}, CT={}, E={}{}={:.2f} {}vs {}{:.2f} {}{}".format(
      int(self.is_alert()),
      int(self.status_changed()),
      _ct,
      eval_method,
      s_q,
      eval_func(_q) if len(_q) > 0 else 0,
      "" if queue_time is None else "(in {:.1f}s) ".format(queue_time),
      vs_oper,
      alert_value,
      "" if last_change == -1 else "LstCh:{:.1f}s ".format(last_change),
      "({} v{})".format(self.name, self.version) if self._show_version else '',
      )
    return _s
    
    
  
if __name__ == '__main__':
  from time import sleep, strftime, localtime
  
  def time_to_str(self, t=None, fmt='%Y-%m-%d %H:%M:%S'):
    if t is None:
      t = time()
    return strftime(fmt, localtime(t))
  LOOP_RESOLUTION = 4
  LOOP_TIME = 1 / LOOP_RESOLUTION
  DATA_TRAIN = [0,4,3,1,0,0,0,5,9,8,7,1,1,1,1,0,0,0,0,0,0]
  DATA_TRAIN_PRC = [
    0.31,0.6,0.5,0.5,0.2,0.3,0.4,0.515,
    0.9,0.8,0.71,0.51,0.61,0.71,0.49,
    0.35,0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.2,0.4,
    0.5,0.9,0.8,0.71,0.51,0.61,0.71,0.51,
    ]
  DATA_TRAIN_SPIKE = [
    100, 100, 100, # trigger a spike
    *([0] * (LOOP_RESOLUTION - 1)),
    *([0] * 5 * LOOP_RESOLUTION),
    0,
    *([70] * 4),
    45,
    70,
    *([0] * (LOOP_RESOLUTION - 1)),
    ]
  
  DATA_TRAIN_SPIKE_NEG = [
    *([100] * LOOP_RESOLUTION),
    *([100] * LOOP_RESOLUTION),
    *([0] * LOOP_RESOLUTION),
    *([100] * LOOP_RESOLUTION),
    *([100] * LOOP_RESOLUTION),
    *([100] * LOOP_RESOLUTION),
    *([0] * LOOP_RESOLUTION),
    *([0] * LOOP_RESOLUTION),
    *([100] * LOOP_RESOLUTION),
  ]

  TEST_COUNTS = False
  TEST_BOOLS = False
  TEST_PERCENT = False
  TEST_SPIKE = False
  TEST_SPIKE_NEG = True
  
  if TEST_PERCENT:
    print("Testing percent alerts ...")
    asm = AlertHelper(
      name='TEST_PLUGIN_01',
      values_count=1,
      raise_confirmation_time=2,
      lower_confirmation_time=2,
      reduce_value=False,
      reduce_threshold=0.5,
      alert_mode='mean',
      raise_alert_value=0.5,
      lower_alert_value=0.4,
      show_version=False,
      )
    
    for i, value in enumerate(DATA_TRAIN_PRC):
      t = time_to_str(time())
      asm.add_observation(value)
      if asm.is_new_alert():
        msg = "[New alert]"
      elif asm.is_new_lower():
        msg = "[Lwr alert]"
      elif not asm.is_alert():
        msg = "[No change]"
      print(f"{t} {msg} {asm.get_last_alert_duration()} {asm}")
      sleep(LOOP_TIME)
  
  if TEST_SPIKE:
    # in this test, there is a spike in the data train 
    # that triggers a confirmation time check and another spike
    # when the confirmation is almost over
    
    # this test checks that spikes do not trigger false alerts
    # the alert should not be raised after the confirmation time
    print("Testing spike alerts ...")
    asm = AlertHelper(
      name='TEST_PLUGIN_01',
      values_count=5,
      raise_confirmation_time=7,
      lower_confirmation_time=30,
      reduce_value=False,
      reduce_threshold=0.5,
      alert_mode='mean',
      raise_alert_value=60,
      lower_alert_value=30,
      show_version=False,
      )
    
    for i, value in enumerate(DATA_TRAIN_SPIKE):
      t = time_to_str(time())
      asm.add_observation(value)
      if asm.is_new_alert():
        msg = "[New alert]"
      elif asm.is_new_lower():
        msg = "[Lwr alert]"
      elif not asm.is_alert():
        msg = "[No change]"
      print(f"{t} {msg} {asm.get_last_alert_duration()} {asm}")
      sleep(LOOP_TIME)

  if TEST_SPIKE_NEG:
    # in this test, there is a spike in the data train 
    # that temporarily lowers the alert status
    
    # this test checks that once a confirmation time starts, 
    # all observations are added to the queue, regardless of 
    # the current state of the alert, and the alert is computed 
    # on the whole sequence
    # the alert should be raised after the confirmation time
    print("Testing negative spike alerts ...")
    asm = AlertHelper(
      name='TEST_PLUGIN_01',
      values_count=5,
      raise_confirmation_time=7,
      lower_confirmation_time=30,
      reduce_value=False,
      reduce_threshold=0.5,
      alert_mode='mean',
      raise_alert_value=60,
      lower_alert_value=30,
      show_version=False,
      )
    
    for i, value in enumerate(DATA_TRAIN_SPIKE_NEG):
      t = time_to_str(time())
      asm.add_observation(value)
      if asm.is_new_alert():
        msg = "[New alert]"
      elif asm.is_new_lower():
        msg = "[Lwr alert]"
      elif not asm.is_alert():
        msg = "[No change]"
      print(f"{t} {msg} {asm.get_last_alert_duration()} {asm}")
      sleep(LOOP_TIME)

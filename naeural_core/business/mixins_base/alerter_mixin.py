#local dependencies
from naeural_core import constants as ct

from naeural_core.utils.alerts import AlertHelper
    

class _AlerterMixin(object):
  def __init__(self):
    self.__alert_helpers = {}
    super(_AlerterMixin, self).__init__()
    return

  def _create_alert_state_machine(self):
    self.__alert_helpers = {}
    self.alerter_create()
    return
  

  @property
  def alerters_names(self):
    return list(self.__alert_helpers.keys())
  
  def alerter_maybe_create(self, alerter, **kwargs):
    if alerter not in self.__alert_helpers:
      self.alerter_create(alerter=alerter, **kwargs)
    return

  def alerter_create(self, alerter='default', 
                     raise_time=None, lower_time=None,
                     value_count=None,
                     raise_thr=None, lower_thr=None,
                     alert_mode=None,
                     alert_mode_lower=None,
                     reduce_value=None, reduce_threshold=None,
                     show_version=False):
    if alerter in self.__alert_helpers:
      raise ValueError("`AlertHelper` '{}' already exists".format(alerter))

    raise_time = self.cfg_alert_raise_confirmation_time if raise_time is None else raise_time
    lower_time = self.cfg_alert_lower_confirmation_time if lower_time is None else lower_time
    value_count = self.cfg_alert_data_count if value_count is None else value_count
    raise_thr = self.cfg_alert_raise_value if raise_thr is None else raise_thr
    lower_thr = self.cfg_alert_lower_value if lower_thr is None else lower_thr
    alert_mode = self.cfg_alert_mode if alert_mode is None else alert_mode
    alert_mode_lower = self.cfg_alert_mode_lower if alert_mode_lower is None else alert_mode_lower
    reduce_value = self.cfg_alert_reduce_value if reduce_value is None else reduce_value

    reduce_threshold = self._get_alerter_reduce_threshold() if reduce_threshold is None else reduce_threshold

    self.P("  Creating alerter '{}' for {}: data, thr_raise/time_raise, thr_lower/time_lower: {}, {}/{} , {}/{}".format(
      alerter,
      self._signature, value_count,
      raise_thr, raise_time, lower_thr, lower_time), color='y')

    if not (raise_time < lower_time):
      self.P("    WARNING: Alerter raise time should be below lowering time: raise={} vs lower={}".format(
        raise_time, lower_time), color='r')

    self.__alert_helpers[alerter] = AlertHelper(
      name=self._signature,
      values_count=value_count,
      raise_confirmation_time=raise_time,
      lower_confirmation_time=lower_time,
      raise_alert_value=raise_thr,
      lower_alert_value=lower_thr,
      alert_mode=alert_mode,
      alert_mode_lower=alert_mode_lower,
      reduce_value=reduce_value,
      reduce_threshold=reduce_threshold,
      show_version=show_version,
    )
    return
  
  def _check_alerter(self, alerter, maybe_create=True):
    if maybe_create:
      self.alerter_maybe_create(alerter)
    if alerter not in self.__alert_helpers:
      raise ValueError("`AlertHelper` '{}' does NOT exist".format(alerter))
    return
      

  def add_alerter_observation(self, value, alerter='default'):
    self._check_alerter(alerter)
    self.__alert_helpers[alerter].add_observation(
      value=value,
      # maybe threshold changes during night and we decided to "reduce" procs in the alerter
      # we can still use the alert_value but we need to reduce based on another threshold
      reduce_threshold=self._get_alerter_reduce_threshold(),
      )
    return


  
  def get_alerter(self, alerter='default'):
    # TODO: Either remove this one - no acces to actual object or put `alerter` as public
    self._check_alerter(alerter)
    return self.__alert_helpers[alerter]
  
  def get_alerter_status(self, alerter='default'):
    if alerter in self.__alert_helpers:
      return str(self.__alert_helpers[alerter])
    return
  
  
  def alerter_status_dict(self, alerter='default'):
    return dict(
      is_alert=self.alerter_is_alert(alerter=alerter),
      is_new_raise=self.alerter_is_new_raise(alerter=alerter),
      is_new_lower=self.alerter_is_new_lower(alerter=alerter),
      is_alert_new_raise=self.alerter_is_new_alert(alerter=alerter),
      is_alert_new_lower=self.alerter_is_new_lower(alerter=alerter),
      is_alert_status_changed=self.alerter_status_changed(alerter=alerter),
    )
 
  
  def alerter_get_last_value(self, alerter='default'):
    self._check_alerter(alerter)
    return self.__alert_helpers[alerter].get_last_raw_value()
  
  
  def alerter_in_confirmation(self, alerter='default'):
    self._check_alerter(alerter)
    return self.__alert_helpers[alerter].in_confirmation


  def alerter_setup_values(self, alerter='default'):
    self._check_alerter(alerter)
    return self.__alert_helpers[alerter].get_setup_values()


  def alerter_hard_reset(self, state=False, alerter='default'):
    self._check_alerter(alerter)
    self.P("WARNING: Alerter '{}' reset requested!".format(alerter), color='r')
    return self.__alert_helpers[alerter].hard_reset(default=state)
  
  
  def alerter_hard_reset_all(self):
    for alerter_name in self.__alert_helpers:
      self.alerter_hard_reset(alerter=alerter_name)
      
      
  # PUBLIC and DOCUMENTED methods
      
  def alerter_add_observation(self, value, alerter='default'):
    """
    Add a new numerical value observation to the given alerter state machine instance

    Parameters
    ----------
    value : float
      The value to be added that can be a percentage or a number or elements - depeding of the configuration of the alerter
      that has been given via "ALERT_MODE".
    alerter : str, optional
      The identifier of the given alerter state machine. The default is 'default'.

    Returns
    -------
    TYPE
      None.

    """
    return self.add_alerter_observation(value, alerter=alerter)     
  
  
  def alerter_is_new_alert(self, alerter='default'):
    """
    Returns `True` if the current state of the given `alerter` state machine has just changed from "lowered" to "raised"

    Parameters
    ----------
    alerter : str, optional
      Identifier of the given alerter instance. The default is 'default'.

    Returns
    -------
    TYPE
      bool.

    """
    self._check_alerter(alerter)
    return self.__alert_helpers[alerter].is_new_raise()


  def alerter_is_new_raise(self, alerter='default'):
    """
    Returns `True` if the current state of the given `alerter` state machine has just changed from "lowered" to "raised"

    Parameters
    ----------
    alerter : str, optional
      Identifier of the given alerter instance. The default is 'default'.

    Returns
    -------
    TYPE
      bool.

    """
    return self.alerter_is_new_alert(alerter=alerter)


  def alerter_is_new_lower(self, alerter='default'):
    """
    Returns `True` if the current state of the given `alerter` state machine has just changed from "raised" to "lowered"

    Parameters
    ----------
    alerter : str, optional
      Identifier of the given alerter instance. The default is 'default'.

    Returns
    -------
    TYPE
      bool.

    """
    self._check_alerter(alerter)
    return self.__alert_helpers[alerter].is_new_lower()


  def alerter_is_alert(self, alerter='default'):
    """
    Returns `True` if the current state of the given `alerter` state machine is "raised"

    Parameters
    ----------
    alerter : str, optional
      Identifier of the given alerter instance. The default is 'default'.

    Returns
    -------
    TYPE
      bool.

    """
    self._check_alerter(alerter)
    return self.__alert_helpers[alerter].is_alert()


  def alerter_status_changed(self, alerter='default'):
    """
    Returns `True` if the current state of the given `alerter` state machine has just changed

    Parameters
    ----------
    alerter : str, optional
      Identifier of the given alerter instance. The default is 'default'.

    Returns
    -------
    TYPE
      bool.

    """
    self._check_alerter(alerter)
    return self.__alert_helpers[alerter].status_changed()  
      
      
  def alerter_time_from_last_change(self, alerter='default'):
    """
    Returns the number of seconds from the last change of the given alerter state machine

    Parameters
    ----------
    alerter : str, optional
      Identifier of the given alerter instance. The default is 'default'.

    Returns
    -------
    TYPE
      bool.

    """
    self._check_alerter(alerter)
    return self.__alert_helpers[alerter].get_time_from_change()
  
  def alerter_maybe_force_lower(self, max_raised_time=0, alerter='default'):
    """
    Forces the given alerter to reset to "lowered" status if the current state is "raised"

    Parameters
    ----------
    alerter : str, optional
      Identifier of the given alerter instance. The default is 'default'.
      
    max_raised_time: float, optional
      The number of seconds after the raised alerter is forced to lower its status

    Returns
    -------
    TYPE
      bool.

    """
    was_reset = False
    self._check_alerter(alerter)
    if self.alerter_is_alert(alerter) and self.alerter_time_from_last_change(alerter) > max_raised_time:
      self.alerter_hard_reset(state=False, alerter=alerter)
      was_reset = True
    return was_reset
  
  def alerter_get_last_alert_duration(self, alerter='default'):
    """
    
    """
    self._check_alerter(alerter)
    alerter : AlertHelper = self.__alert_helpers[alerter]
    duration = alerter.get_last_alert_duration()
    if not alerter.is_new_lower():
      duration = None
    return duration
  
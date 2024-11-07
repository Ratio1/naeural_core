"""
INFO:
  aspects in any config_data using class including DecentrAIObject !!!
  as a rule-of-the-thumb this mixin should be used as:
    - add_config_data(dct_config) - this will append/update new config to the config_data
    - create_config_handlers - after we have final config_data we can create implicit `cfg_` handlers
    - validation  & run_validation_rules - finally check if all is ok

TODO:
  
  
  
"""
from copy import deepcopy
from collections import deque
import traceback

class VALIDATION_KEYS:
  TYPE = 'TYPE'
  MIN_VAL = 'MIN_VAL'
  MAX_VAL = 'MAX_VAL'
  MIN_LEN = 'MIN_LEN'
  EXCLUDED_LIST = 'EXCLUDED_LIST'
  ACCEPTED_VALUES = 'ACCEPTED_VALUES'
  
def get_validation_keys():
  res = []
  for k in VALIDATION_KEYS.__dict__:
    if k.isupper():
      res.append(VALIDATION_KEYS.__dict__[k])
  return res

class CONST(VALIDATION_KEYS):
  RULES = 'VALIDATION_RULES'
  FUNC = '_cfg_validate_'
  PROP_PREFIX = 'cfg_'
  

from functools import partial
def getter(slf, key=None):
  val = slf.config_data.get(key)
  if isinstance(val, dict):
    return val.copy()
  else:
    return val

def get_all_elements_from_deque(deq):
  lst = []
  while len(deq) > 0:
    x = deq.popleft()
    lst.append(x)
  return lst
#enddef

class _ConfigHandlerMixin(object):

  def __init__(self):
    self._valid_errors = deque(maxlen=100)
    self._valid_warnings = deque(maxlen=100)
    self._valid_infos = deque(maxlen=100)
    self.__cfg_ready = False
    super(_ConfigHandlerMixin, self).__init__()
    return
  
  @property
  def ready_cfg_handlers(self):
    return self.__cfg_ready
  
  
  def needs_update(self, dct_newdict, except_keys):
    """Check if we need to perform a config update: if a new dict is different from current config_data

    Parameters
    ----------
    dct_newdict : dict
        The new dict to be checked
    except_keys : list
        list of keys to be excluded from check

    Returns
    -------
    bool, list
        need to update or not and list of keys that are different
    """
    result = False
    updates = []
    if isinstance(dct_newdict, dict):      
      for k in dct_newdict:
        if self.config_data.get(k) != dct_newdict[k]:
          updates.append(k)
        if (k not in except_keys) and ((k not in self.config_data) or (self.config_data.get(k) != dct_newdict[k])):
            result = True            
    return result, updates
  

  def _merge_prepare_config(
    self, 
    default_config=None, 
    delta_config=None, 
    uppercase_keys=True, 
    verbose=0,
    debug=False
  ):
    if default_config is None:
      default_config = vars(self).get('_default_config')
      msg_src = '_default_config '
    else:
      msg_src = ''

    if delta_config is None:
      delta_config = vars(self).get('_upstream_config')
      if delta_config is None:
        return default_config
      msg_delta = "using `_upstream_config` ({} keys)".format(len(delta_config))
    else:
      msg_delta = "using new cfg ({} keys)".format(len(delta_config))

    if debug:
      msg_delta += ": {}".format(list(delta_config.keys()))
    
    # start delta_config prepare
    # now deepcopy delta_config to a local variable to ensure mutable values will not
    # be modified in _upstream_config thus triggering a diff from actual config    
    delta_config = deepcopy(delta_config)
    # convert to upper default    
    if uppercase_keys:
      delta_config = {k.upper():v for k,v in delta_config.items()}
    # end delta_config prepare 
    lst_msg = []
    
    lst_msg.append("Updating {} {}config {}:".format(
      self.__class__.__name__, msg_src,
      msg_delta
      )
    )
    if debug and hasattr(self, 'log'):
      str_init = '\n{}'.format(self.log.dict_pretty_format(default_config))
      str_delta = '\n{}'.format(self.log.dict_pretty_format(delta_config))
      self.P("Update info:\n************ Initial:\n{}\n************ Modifications:\n{}".format(str_init, str_delta))
      
    if default_config is None:
      self.P("WARNING: no default config was provided at {} startup!".format(
        self.__class__.__name__), color='r')
      final_config = {}
    else:
      final_config = deepcopy(default_config)
    #endif
    all_keys = set(final_config.keys()).union(set(delta_config.keys()))
    for k in all_keys:
      if final_config.get(k) == delta_config.get(k) or k not in delta_config:
        if debug:
          lst_msg.append("  {}={}".format(k, final_config.get(k)))
      else:
        if k not in final_config:
          if verbose > 1:
            lst_msg.append("  {}={} [NEW]".format(k, delta_config[k]))
          else:
            lst_msg[-1] += k + '; '
          #endif verbose
        elif final_config[k] != delta_config[k]:
          if verbose > 1:
            lst_msg.append("  {}={} -> {}={}".format(
              k, final_config[k], k, delta_config[k])
            )
          else:
            lst_msg[-1] += k + '; '
          #endif verbose
        final_config[k] = delta_config[k]
        #endif new key or value
      #endif key not in delta_config
    #endfor all keys
    if len(lst_msg) > 0 and verbose > 0:      
      full_dump = "\n".join(["    " + x for x in lst_msg])        
      self.P(full_dump.lstrip(), color='b')
    return final_config
  
  def update_config_data(self, new_config_data):
    return self.add_config_data(new_config_data=new_config_data)

  def add_config_data(self, new_config_data):
    lst_msg = []
    if not isinstance(new_config_data, dict):
      self.P("`config_data` must be updated with a dict. Received '{}'".format(type(new_config_data)))
      return False
    
    lst_msg.append("Updating '{}' configuration...".format(self.__class__.__name__))
    
    if not hasattr(self, 'config_data'):
      self.config_data = {}

    all_keys = set(self.config_data.keys()).union(set(new_config_data.keys()))
    for k in all_keys:
      if (self.config_data.get(k) == new_config_data.get(k)) or (k not in new_config_data):
        lst_msg.append("  {}={}".format(k, self.config_data[k]))
      else:
        if k not in self.config_data:
          lst_msg.append("  {}={} [NEW]".format(k, new_config_data[k]))
        elif self.config_data[k] != new_config_data[k]:
          lst_msg.append("  {}={} -> {}={}".format(
            k, self.config_data[k], k, new_config_data[k]
            )
          )
        self.config_data[k] = new_config_data[k]
    if len(lst_msg) > 0:
      full_dump = "\n".join(["    " + x for x in lst_msg])        
      self.P(full_dump.lstrip(), color='b')
    return True

  
  def create_config_handlers(self, verbose=0):    
    if hasattr(self, 'config_data') and isinstance(self.config_data, dict) and len(self.config_data) > 0:
      res = []
      for k in self.config_data:
        func_name = self._get_prop(k)
        if not hasattr(self, func_name):
          # below is a bit tricky: using a lambda generates a non-deterministic abnormal behavior
          # the ideea is to create a global func instance that wil be then loaded on the class (not instance)
          # as a descriptor object - ie a `property`. "many Bothans died to bring the plans of the Death Star..."
          fnc = partial(getter, key=k) # create the func
          cls = type(self) # get the class
          fnc_prop = property(fget=fnc, doc="Get '{}' from config_data".format(k)) # create prop from func 
          setattr(cls, func_name, fnc_prop) # set the prop of the class
          res.append(func_name)
      if len(res) > 0 and verbose > 1:
        self.P("Created '{}' config_data handlers: {}".format(self.__class__.__name__, res), color='b')
      self.__cfg_ready = True
    return
  
  
  def _cfg_check_type(self, cfg_key, types):
    val = self._get_val_by_prop(cfg_key)
    if not isinstance(val, types):
      msg = "`{}` config key '{}={}' requires type `{}` - found `{}`".format(
        self.__class__.__name__, 
        cfg_key, val, [x.__name__ for x in types] if isinstance(types, tuple) else types.__name__,
        type(val).__name__
      )
      return False, msg
    return True, ''
  
  def _cfg_check_min_max(self, cfg_key, dct_rules):
    res = True
    msg = None
    _min = dct_rules.get(CONST.MIN_VAL)
    _max = dct_rules.get(CONST.MAX_VAL)
    val = self._get_val_by_prop(cfg_key)
    if _min is not None and val < _min:
      msg = "`{}` config key '{}={}' of type `{}` requires value > {}".format(
        self.__class__.__name__, cfg_key, val, dct_rules.get(CONST.TYPE), _min,
      )
      return False, msg
    
    if _max is not None and val > _max:
      msg = "`{}` config key '{}={}' of type `{}` requires value < {}".format(
        self.__class__.__name__, cfg_key, val, dct_rules.get(CONST.TYPE),  _max,
      )
      return False, msg
    return res, msg

  def _cfg_check_exclusions(self, cfg_key, dct_rules):
    _excl_lst = dct_rules.get(CONST.EXCLUDED_LIST)
    val = self._get_val_by_prop(cfg_key)
    if _excl_lst is not None and val in _excl_lst:
      msg = "`{}`  key '{}={}' in exclutions {}".format(
        self.__class__.__name__, cfg_key, val, _excl_lst,
      )
      return False, msg
    return True, ''


  def _cfg_check_accepted(self, cfg_key, dct_rules):
    _accept_lst = dct_rules.get(CONST.ACCEPTED_VALUES)
    val = self._get_val_by_prop(cfg_key)
    if _accept_lst is not None and val not in _accept_lst:
      msg = "`{}`  key '{}={}' not in accepted values {}".format(
        self.__class__.__name__, cfg_key, val, _accept_lst,
      )
      return False, msg
    return True, ''
  
  def _cfg_validate_int(self, cfg_key, dct_rules):
    is_ok, msg = self._cfg_check_type(cfg_key=cfg_key, types=(int,))
    if is_ok:
      is_ok, msg = self._cfg_check_min_max(cfg_key=cfg_key, dct_rules=dct_rules)
    return is_ok, msg
  
  def _cfg_validate_float(self, cfg_key, dct_rules):    
    is_ok, msg = self._cfg_check_type(cfg_key=cfg_key, types=(int,float))
    if is_ok:
      is_ok, msg = self._cfg_check_min_max(cfg_key=cfg_key, dct_rules=dct_rules)
    return is_ok, msg
  
  
  def _cfg_validate_str(self, cfg_key, dct_rules):
    res1, msg1 = self._cfg_check_type(cfg_key=cfg_key, types=(str,))
    res, msg = res1, msg1
    if res1:
      res2, msg2 = self._cfg_check_exclusions(cfg_key=cfg_key, dct_rules=dct_rules)
      res3, msg3 = self._cfg_check_accepted(cfg_key=cfg_key, dct_rules=dct_rules)
      val = self._get_val_by_prop(cfg_key)
      _min_len = dct_rules.get(CONST.MIN_LEN, 0)
      res4 = True
      msg4 = None
      if res1: # must have valid type to check next
        if _min_len is not None and len(val) < _min_len:
          msg4 = "`{}` config key '{}={}' of type `{}` must have at least {} chars".format(
            self.__class__.__name__, cfg_key, val, dct_rules.get(CONST.TYPE), _min_len,
          )
          res4 = False
      res = res1 and res2 and res3 and res4
      msg = msg1 if not res1 else (msg2 if not res2 else (msg3 if not res3 else msg4))
    return res, msg      

  def _cfg_validate_list(self, cfg_key, dct_rules):
    res1, msg1 = self._cfg_check_type(cfg_key=cfg_key, types=(list,))
    res2, msg2 = self._cfg_check_exclusions(cfg_key=cfg_key, dct_rules=dct_rules)
    res3 = True
    msg3 = None
    if res1: # must have valid type to check next
      val = self._get_val_by_prop(cfg_key)
      _min_len = dct_rules.get(CONST.MIN_LEN, 0)
      if _min_len is not None and len(val) < _min_len:
        msg3 = "`{}` config key '{}={}' of type `{}` must have at least {} elements, found {}".format(
          self.__class__.__name__, cfg_key, val, dct_rules.get(CONST.TYPE), _min_len, len(val)
        )
        res3 = False
    res = res1 and res2 and res3
    msg = msg1 if not res1 else (msg2 if not res2 else msg3)
    return res, msg      
  
    
  def _get_prop(self, k):
    return CONST.PROP_PREFIX + k.lower().replace('#','_')
  
  def _get_val_by_prop(self, k):
    f = getattr(self, self._get_prop(k), None)
    val = f
    return val
    
  
  def run_validation_rules(self, verbose=0, debug_verbose=False):
    result_state = {'RESULT': True, 'MSGS' : []}
    def __fail(s):
      result_state['RESULT'] = False
      result_state['MSGS'].append(s)
      if debug_verbose:
        self.P("  " + s, color='r')
      return
    if verbose:
      self.P("Validating configuration for '{}'...".format(self.__class__.__name__), color='b')
    if hasattr(self, 'config_data') and isinstance(self.config_data, dict):
      dct_validation = self.config_data.get(CONST.RULES, {})
      for k in dct_validation:
        # run each config key present in validation area
        prop_getter = self._get_prop(k)
        has_prop_getter = prop_getter in dir(self) #getattr(self, prop_getter, None) # get the getter
        if not has_prop_getter:
          __fail("No config getter `{}` for VALIDATION key '{}' - please run `create_config_handlers` before validation or check maybe there is no CONFIG key defined".format(
            prop_getter, k)
          )
          continue
        if k not in self.config_data: 
          # have getter but no key in json - bad programming?
          self.P("  Key '{}' found in validation not present in `config_data` however `{}` has getter `{}`. This is not a accepted approach, please define '{}' key-val in `_CONFIG_DATA` ".format(
            k, self.__class__.__name__, prop_getter, k.upper(), ), color='r'
          )
        dct_rules = dct_validation.get(k, {})
        if len(dct_rules) > 0:
          # now we get the actual type of the value 
          configured_type = dct_rules.get(CONST.TYPE)
          _type = None
          if isinstance(configured_type, str) and len(configured_type) > 1:
            try:
              _type = eval(configured_type)
              if type(_type) != type:
                _type = None                
            except:
              _type = None
            if _type is None:
              __fail("TYPE '{}' of '{}' pre-validation failed - unknown type!".format(configured_type, k))
              continue
          else:
            # not type info maybe not big error
            if verbose > 0:
              if configured_type is None:
                self.P("  No type information found for '{}': {}".format(k, configured_type), color='r')
              else:
                self.P("  Complex type information found for '{}': {}".format(k, configured_type))
          # endif type extraction
          if _type in [int, float, str, list]:            
            # create validation function out of this mixin available funcs
            str_func = CONST.FUNC + _type.__name__
            func = getattr(self, str_func, None)
            res = True # assume good
            if func is None:
              # if we use a predefined type then we must have the validation
              self.P("  No default handler for type '{}' config key validation".format(_type.__name__), color='r')
              # maybe we can put res = False here?
            else:
              msg = ''
              # now we run the validation function
              res = func(k, dct_rules)
              if isinstance(res, tuple):
                res, msg = res
            # end run-or-fail validation
            if not res:
              # validation failed
              __fail("Config validation for '{}={}' of '{}' failed: {}".format(
                k, getattr(self, prop_getter, None), self.__class__.__name__, msg)
              )
              # here we can break for but we leave to see what other error we have   
            elif verbose:
              self.P("  Config validation for key '{}' succeeded".format(k), color='b')
            #endif failed or not
              
          # end of known types
          else:
            # if other type than known ones
            if verbose > 1:
              self.P("  Unavailable basic handler for type '{}' for config key '{}' - running auto check".format(
                configured_type, k,
                ), 
                color='y'
              )
            # but we can still run the type check without special handler
            # no need to check if _type is not None         
            if _type is None and isinstance(configured_type, list):
              _type = tuple([eval(x) for x in configured_type])
            res, msg = self._cfg_check_type(cfg_key=k, types=_type)
            if not res:
              __fail("Automatic checking of unhandled type `{} <{}>`: {}".format(
                configured_type,
                _type,
                msg
                )
              )
        else:
          self.P("  Empty rules info for '{}'".format(k), color='y')
        # end if rules parsing
      # end for each key validation 
      if result_state['RESULT']:
        self.P("Automatic validation for instance of `{}` is successful".format(
          self.__class__.__name__), 
          color='g'
        )
      else:
        __fail('Automatic validation for instance of `{}` FAILED.'.format(self.__class__.__name__))
    # end if we have config and validation date
    else:
      __fail("No `config_data` for '{}'".format(self.__class__.__name__))
    
    result = result_state['RESULT']
    fail_msgs = result_state['MSGS']
    return result, fail_msgs

  def get_errors(self):
    return get_all_elements_from_deque(self._valid_errors)

  def get_warnings(self):
    return get_all_elements_from_deque(self._valid_warnings)

  def get_infos(self):
    return get_all_elements_from_deque(self._valid_infos)

  def add_error(self, msg):
    self._valid_errors.append(msg)
    return

  def add_warning(self, msg):
    self._valid_warnings.append(msg)
    return

  def add_info(self, msg):
    self._valid_infos.append(msg)
    return

  def validate(self, raise_if_error=True, verbose=0):
    _, fail_msgs = self.run_validation_rules(verbose=verbose)

    for msg in fail_msgs:
      self.add_error(msg)

    for method_name, func in self.log.get_class_methods(self.__class__, include_parent=True):
      if method_name.startswith('validate_'):
        try:
          func(self)
        except:
          self.add_error("Programming error in validation method '{}'.\n{}".format(method_name, traceback.format_exc()))

    lst_errors = self.get_errors()
    lst_warnings = self.get_warnings()
    lst_infos = self.get_infos()

    has_errors = len(lst_errors) > 0
    is_valid = not has_errors

    for x in lst_infos:
      self.P(x)

    for x in lst_warnings:
      self.P(x, color='y')

    for x in lst_errors:
      self.P(x, color='r')

    if raise_if_error and has_errors:
      raise ValueError("Errors occured while validating: {}".format(lst_errors))

    return is_valid
  
  def setup_config_and_validate(self, dct_config, verbose=0):
    if verbose:
      self.P("Resetting config_data ...")
    self.config_data = dct_config
    if verbose:
      self.P("Running config handler creation...")
    self.create_config_handlers(verbose=verbose)
    if verbose:
      self.P("Running validation...")
    self.validate(verbose=verbose)
    return

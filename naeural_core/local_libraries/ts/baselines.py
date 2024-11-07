import json
import pickle
import os
from collections import namedtuple
from functools import partial

__VER__ = '0.3.1.0'


_AVAIL_baselines = [
  # fast
  'avgh_0', 'avgh_1',
  'seas_7_8', 'seas_30_6', 'seas_182_2', 'seas_365_2', 
  'seas_4_8', 'seas_8_13', 'seas_13_26', 

  'sea2_7_30','sea2_30_180','sea2_180_365','sea2_30_365',
  'sea4_7_30','sea4_30_180','sea4_180_365','sea4_30_365',
  
  'cor1_0_1', 'cor1_1_9', 'cor1_1_5', 'cor1_1_4', 'cor1_1_3', 'cor1_1_2', 'cor1_1_1',
  'cor1_5_1', 'cor1_4_1', 'cor1_3_1', 'cor1_2_1',

  'lif1_7'  , 'lif1_14'  , 'lif1_30'  , 'lif1_90', 'lif1_182','lif1_365',
  'lif1_7_2'  , 'lif1_14_2'  , 'lif1_30_2'  , 'lif1_90_2', 'lif1_182_2','lif1_365_2',
  'lif1_7_3'  , 'lif1_14_3'  , 'lif1_30_3'  , 'lif1_90_3', 'lif1_182_3','lif1_365_3',


  'lin1_7'  , 'lin1_14'  , 'lin1_30'  , 'lin1_90', 'lin1_182','lin1_365',
   
  'lin2_7_7',  'lin2_5_14', 'lin2_5_30', 
  'lin2_30_7',  'lin2_30_14', 'lin2_30_30', 'lin2_30_93', 'lin2_30_183', 
    
  'lin3_30_7', 'lin3_30_14',  
   
  # 'larx_15_345_365', # 20 days target last year
  # 'larx_15_365_380', # last 15 days period last year
  # 'larx_31_350_380', # 15 days target + 15 days period (both last year)
  # 'larx_31_355_385', # 10 days target + 20 days period (both last year)
  # 'larx_30_335_365', # same 30 days target period last year
  # 'larx_30_365_395', # last 30 days period in last year
  'larx_30_360_370', # 5 days target/prev in last year
  # 'larx_15_000_000', 
  # 'larx_31_000_000', 

  'larx_15_345_365_390', # 20 days target last year
  'larx_15_365_380_390', # last 15 days period last year
  'larx_31_350_380_390', # 15 days target + 15 days period (both last year)
  'larx_31_355_385_390', # 10 days target + 20 days period (both last year)
  'larx_30_335_365_390', # same 30 days target period last year
  'larx_30_365_395_400', # last 30 days period in last year
  'larx_30_360_370_390', # 5 days target/prev in last year
  'larx_15_000_000_390', 
  'larx_31_000_000_395', 
  
  # weekly models:
  'larx_4_53_57_65', # last 4 weeks and similar last year
  'larx_8_53_61_65', # last 8 weeks and similar last year
  'larx_26_000_000_104', # last 26 weeks 
  'larx_13_000_000_104', # last 13 weeks 
  'larx_4_000_000_104', # last 4 weeks 
  'larx_8_000_000_104', # last 8 weeks 
  'larx_26_000_000_52', # last 26 weeks 
  'larx_13_000_000_52', # last 13 weeks 
  'larx_4_000_000_52', # last 4 weeks 
  'larx_8_000_000_52', # last 8 weeks 

  'larx_4_000_000_13', # last 4 weeks 
  'larx_13_000_000_26', # last 4 weeks 

    

  # last period
  # 'larx_31_000_000_100', 
  # 'larx_15_000_000_100', 
  # 'larx_30_360_370_100', # 5 days target/prev in last year

  
  'lxco',
  'lxco_2_2',
  'lxco_3_3',
  'lxco_3_2',
  
  'cofe',
  
  # slow
  #   'lin3_30_30',
  
  # very-slow,
  #   'lin2_10_365',
 
  # ultra-slow
  #   'lin3_30_93', 'lin3_30_183', 'lin3_30_365', 

  # NOW ENSEMBLES:
  'lxco_2_2+seas_30_6', 
]

# _AVAIL_baselines_codes = ['se', 'li' ,'ar', 'av', 'co']



def get_avail_baselines_old(log):
  dct_result = {}
  # # start debug
  # # return 'generator' dict if available
  # if os.path.isfile('generator/generate.py'):
  #   from generator.generate import __AVAIL_baselines_defs as dct_result
  #   return dct_result
  # # end debug

  import sys
  info = sys.version_info
  sver = '_{}{}{}'.format(info.major, info.minor, info.micro)  

  FOLDER = 'libraries/ts/params'
  CFG_BASE = '_data_config'
  EXT = '.lib'
  cfg_files = [x for x in os.listdir(FOLDER) if CFG_BASE in x]
  for cfg in cfg_files:
    with open(os.path.join(FOLDER, cfg), 'rt') as fh:
      dct_AVAIL_baselines_defs = json.load(fh)
    for model in dct_AVAIL_baselines_defs:
      dct_result[model] = dct_AVAIL_baselines_defs[model]
    for base_model in dct_AVAIL_baselines_defs:
      name = dct_AVAIL_baselines_defs[base_model]['FUNCF']
      funcf = name + sver + EXT
      fn = os.path.join(FOLDER, funcf)
      if not os.path.isfile(fn):
        avails = [x for x in os.listdir(FOLDER) if name in x]
        raise ValueError("'{}' not found. Found only: {}".format(
          fn, avails))
      ver = dct_AVAIL_baselines_defs[base_model]['VER']
      log.P("Loading params for {} ver {} from {}".format(
        base_model, ver, funcf
        ))
      with open(fn, 'rb') as fh:      
        dct_result[base_model]['FUNC'] = pickle.load(fh)
  return dct_result

def _cleanup(verbose=False):
  FOLDER = 'libraries/ts/params'
  xt = ''.join([chr(x) for x in [46, 112, 121]])
  fns = [x for x in os.listdir(FOLDER) if xt in x]
  for fn in fns:
    ff = os.path.join(FOLDER, fn)
    os.remove(ff)
    if verbose: print(ff)
  return
    
def get_avail_baselines(log, **kwargs):
  """
  Returns all basic baselines (with no pre-configured parameters)
  """
  log.P("Baselines generator v{} generating available weights for baselines".format(__VER__), color='g')
  FOLDER = 'libraries/ts/params'
  FN_BASE = 'base_'
  EXT = '.lib'
  WN = 'FUNC'
  all_files = [x for x in os.listdir(FOLDER) if FN_BASE in x and EXT in x]
  dct_results = {}
  for fn_weights in all_files:
    weights = log.load_deployed_library(os.path.join(FOLDER, fn_weights), **kwargs)
    dct = weights._DEFS
    for k in dct:
      dct_results[k] = dct[k]
  if len(dct_results) == 0:
    raise ValueError('No baseline weights found!')
  log.P("Weights available for {}".format(
    ['{}.v{}[{}]'.format(k,v.get('VER',0), v[WN].__name__) for k,v in dct_results.items()]), color='y')
  return dct_results


def _multi_model(func_list, **kwargs):
  pass

  
def get_preconfigured_baselines(log,  return_as_object=True, **kwargs):
  """
  Will return all pre-configured baselines either as a namedtuple or as a dict
  if return_as_object == True then returns tuple of two items:
    (1) all pre-configured baselines as a named tuple 
    (2) a named tuple with model descriptions
  else will return a dict where keys are model names and values are dicts 
    with 'FUNC' (the actual callable), string 'DESC' with the description/version 
    as well as a 'CONFIGURED' bool that informs if is a pre-configured baseline
    or just the basic call
  """
  
  s_units = kwargs.get('step_label', 'steps')  
  dct_basic = get_avail_baselines(log=log, **kwargs)
  lst_names = []
  lst_models = sorted(set(_AVAIL_baselines + list(dct_basic.keys())))
  for config_base in lst_models:
    if type(config_base) != str:
      raise ValueError("One of the preconfigured baselines is not a string... {}".format(config_base))
    lst_names.append(config_base.replace('+','_'))
  
  BaseLib = namedtuple('BaseLib', lst_names)
  InfoLib = namedtuple('InfoLib', lst_names)
  dct_lib = {}
  dct_info = {}
  dct_all = {}
  for model_name in lst_models:
    #
    #  now we transform each model name into a function
    #
    if '+' not in model_name:
      #
      # we deal with a non-ensemble
      #
      log.P(" Generating function {}".format(model_name))
      params = []
      model_info = model_name.split('_')
      model_code = model_info[0]
      if model_code not in dct_basic:
        raise ValueError("Something is wrong. Please check baselines. {} is not available".format(model_code))
      for i in range(1, len(model_info)):
        params.append(float(model_info[i])) 
      s_n1 = params[0] if len(params) >= 1 else 'ANY'
      s_n2 = params[1] if len(params) >= 2 else 'ANY'
      func = partial(dct_basic[model_code]['FUNC'], params=params)
      dct_lib[model_name] = func
      info_str = dct_basic[model_code]['DESC'] 
      info_str += '. v'+ dct_basic[model_code]['VER'] if 'VER' in dct_basic[model_code] else ''
      dct_info[model_name] = info_str.format(s_units=s_units, s_n1=s_n1, s_n2=s_n2)
      dct_all[model_name] = {
        'FUNC': dct_lib[model_name], 
        'DESC': dct_info[model_name], 
        'CONFIGURED': len(params) >= 1, 
        }
    else:
      #
      # now we deal with multiple models
      #
      ens_model_list = model_name.split('+')
      func_list = []
      model_codes = []
      for ens_model in ens_model_list:
        params = []
        model_info = ens_model.split('_')
        model_code = model_info[0]
        model_codes.append(model_code)
        params = [float(model_info[i]) for i in range(1, len(model_info))]
        func = partial(dct_basic[model_code]['FUNC'], params=params)
        func_list.append(func)
      new_model_name = model_name.replace('+','_')
      dct_lib[new_model_name] = partial(func, func_list=func_list)
      dct_info[new_model_name] = 'enseble of ' + ','.join(model_codes)
      dct_all[new_model_name] = {
        'FUNC':dct_lib[new_model_name], 
        'DESC':dct_info[new_model_name],
        'CONFIGURED' : True
        }

  if return_as_object:
    lib = BaseLib(**dct_lib)
    info = InfoLib(**dct_info)
    ret = lib, info
  else:
    ret = dct_all
  return ret

# TODO: Next Period Model
def next_period_model(X, steps, freq):
  
  pass
      
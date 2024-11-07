import numpy as np
from collections import OrderedDict
import os
from functools import partial
import pandas as pd
from time import time as tm
from datetime import datetime as dt
from datetime import timedelta
import textwrap



from naeural_client import BaseDecentrAIObject
from .baselines import _AVAIL_baselines, get_avail_baselines, _cleanup
from . import metrics

__TIMESERIES_VER__ = '0.9.0.0'   

def clip_to_history(X, y_hat):
  if len(y_hat.shape) == 2:
    y_hat = np.expand_dims(y_hat, -1)
  maxes = X.max(axis=1).reshape(-1,1)
  return np.expand_dims(np.minimum(y_hat.squeeze(-1), maxes), axis=-1)  
  

class TimeseriesBenchmarker(BaseDecentrAIObject):
  """
  This is the timeseries benchmarker swiss-knife
  Please use either `calc_timeseries_error` or `debug_series` if your are just 
  interested in debugging/calculating regression errors
  """
  def __init__(self, DEBUG=False, dct_baselines=None, **kwargs):    
    self.__version__ = __TIMESERIES_VER__
    self.__dct_baselines = dct_baselines
    self.version = self.__version__    
    self.__name__ = "Time-series benchmarker"
    self.__extra_params = kwargs
    super().__init__(**kwargs)
    self.DEBUG = DEBUG
    self._AVAIL_baselines = _AVAIL_baselines
    self.P("Please use either `calc_timeseries_error` or `debug_series` if your are just interested in debugging/calculating regression errors")
    return
  
  def cleanup(self, verbose=False):
    _cleanup(verbose=verbose)
    return
  
  def __exit__(self):
    self.cleanup(verbose=False)
    return
  
  def __del__(self): 
    self.cleanup(verbose=False)
    return    
  
  def startup(self):
    super().startup()
    self._setup_benchmarker()
    return


  def show_params(self):
    self.P("  Predictions scaling: {:5.1f}".format(self.AR_scale_preds))
    self.P("  Nr. of targed {}:  {:5}".format(self.AR_step_label, self.AR_steps))
    self.P("  Nr. X hist {}:     {:5}".format(self.AR_step_label, self.AR_base_x_hist))
    self.P("  Fast baselines:      {:5}".format(self.AR_fast_baselines))
    zero_sales = self.AR_y_test_real.squeeze().sum(1) == 0
    if zero_sales.sum() > 0:
      self.P("  WARNING: you have {} ({:.1f}%) TARGET series with zero sales".format(
        zero_sales.sum(), 100 * zero_sales.sum() / zero_sales.shape[0]))
    if self.AR_last_x_date is not None:
      self.P("  Prediction period information:")
      self.P("    Years:             {}".format(self.AR_x_years))
      self.P("    First history day: {}".format(self.AR_x_dates.index[0]))
      self.P("    Last history day:  {}".format(self.AR_x_dates.index[-1]))
      self.P("    First pred day:    {}".format(self.AR_y_dates.index[0]))
      self.P("    Last pred day:     {}".format(self.AR_y_dates.index[-1]))
    else:
      self.P("  No last_x_date reference date received.")
    return

  def _setup_benchmarker(self, ACC_THR=0.1, ERR_THR=0.35):
    self.AR_metrics = None
    
    self._PKEY  = 'PRED_QTY'
    self._SKEY  = 'SERIES_ID'
    self._RKEY  = 'REAL_QTY'
    self._GIKEY = 'G&I'
    self._TARGET_KEY = '__TARGET__'
    self._SCORE_KEY = 'CONFIDENCE'

    self._GOOD = 'NG_'
    self._MED = 'MG_'
    self._AVG = 'AG_'
    self._BEST = 'BG_'
    self._EP = 'E_'    
    
    self._REALITY = 'REALITY'
    
    self._WEEKDAYS = ['Mo','Tu','We','Th','Fr','Sa','Su']
    
        

    self._ACC_METRICS = {
              'r2' : {
                      'ACC_THR': ACC_THR, 
                      'ERR_THR': ERR_THR,
                      'DESC' : 'Percentual closeness of the predictions to the target values',
                      'PERIOD_ONLY' : False,
                      'AGGREGATE' : self._agg_mean,
                    },

              'isc' : {
                      'ACC_THR': ACC_THR, 
                      'ERR_THR': ERR_THR,
                      'DESC' : 'Important series percent coverage. 1.15=15% over-stock',
                      'PERIOD_ONLY' : True,
                      'AGGREGATE' : self._agg_mean,
                    },
                            

              'por' : {
                      'ACC_THR': ACC_THR, 
                      'ERR_THR': 0.5,
                      'DESC' : 'Percentage of revenue in good series that have minimal overstock (eg -10% to 50%)',
                      self._MED + 'por' : 'Median value of period revenue',
                      'PERIOD_ONLY' : True,
                      'AGGREGATE' : self._agg_good_revenue,
                      'MAIN_KEY' : 'por',
                  },
                }
    self._ERR_METRICS = {  

              'ismape' : {
                      'ACC_THR': ACC_THR, 
                      'ERR_THR': 0.3, #ERR_THR,
                      'DESC' : 'Important series percentage where step (daily, etc) prediction error is below a threshold',
                      'PERIOD_ONLY' : False,
                      'AGGREGATE' : self._agg_mean,
                    },
      
              'mape' : {
                      'ACC_THR': ACC_THR, 
                      'ERR_THR': ERR_THR,
                      'DESC' : 'Mean absolute percentage error',
                      'PERIOD_ONLY' : False,
                      'AGGREGATE' : self._agg_mean,
                    },

              'cme1' : {
                      'ACC_THR': ACC_THR, 
                      'ERR_THR': 0.5, #ERR_THR,
                      'DESC' : 'Clipped (100%) mean absolute percentage error',
                      'PERIOD_ONLY' : False,
                      'AGGREGATE' : self._agg_mean,
                    },


              'ppd' : {
                      'ACC_THR': ACC_THR, 
                      'ERR_THR': ERR_THR,
                      'DESC' : 'Period prediction-based deviation. 0.29=29% out of prediction - focus on OOS',         
                      'PERIOD_ONLY' : True,
                      'AGGREGATE' : self._agg_mean,
                    },

              'pdt' : {
                      'ACC_THR': ACC_THR, 
                      'ERR_THR': ERR_THR,
                      'DESC' : 'Period deviation from target (normalized deviation) - focus on OVS',                   
                      'PERIOD_ONLY' : True,
                      'AGGREGATE' : self._agg_mean,
                    },
                  
              'ppd5' : {
                      'ACC_THR': ACC_THR, 
                      'ERR_THR': ERR_THR,
                      'DESC' : 'Period prediction-based deviation with error margin',                    
                      'PERIOD_ONLY' : True,
                      'AGGREGATE' : self._agg_mean,
                    },

              'mape2' : {
                      'ACC_THR': ACC_THR, 
                      'ERR_THR': ERR_THR,
                      'DESC' : 'Mean absolute percentage error relative to predictions',                   
                      'PERIOD_ONLY' : False,
                      'AGGREGATE' : self._agg_mean,
                    },
                  
              'mape21' : {
                      'ACC_THR': ACC_THR, 
                      'ERR_THR': ERR_THR,
                      'DESC' : 'Mean absolute percentage error relative to predictions (with margin)',                    
                      'PERIOD_ONLY' : False,
                      'AGGREGATE' : self._agg_mean,
                    },

              'smape' : {
                      'ACC_THR': ACC_THR, 
                      'ERR_THR': ERR_THR,
                      'DESC' : 'Simetrical mean absolute percentage error',                    
                      'PERIOD_ONLY' : False,
                      'AGGREGATE' : self._agg_mean,
                    },

              'rmse' : {
                      'ACC_THR': ACC_THR, 
                      'ERR_THR': ERR_THR,
                      'DESC' : 'Overall prediction error (rooted square)',                    
                      'PERIOD_ONLY' : False,
                      'AGGREGATE' : self._agg_mean,
                    },
                  
              'pqo' : {
                      'ACC_THR': ACC_THR, 
                      'ERR_THR': 0.5,
                      'DESC' : 'Period quantity overstock percentual error',                    
                      'PERIOD_ONLY' : True,
                      'AGGREGATE' : self._agg_capped_99_mean,
                  },
              
              'pvo' : {
                      'ACC_THR': ACC_THR, 
                      'ERR_THR': 0.5,
                      'DESC' : 'Period valoric overstock for all series. OBS: Good series have small overstock (eg -10% to 50%)',
                      'PERIOD_ONLY' : True,
                      'AGGREGATE' : self._agg_capped_overstock,
                      'MAIN_KEY' : 'pvo',
                  },
                                
                }
              
    self._STOCK_METRIC = 'pqo'    
    self._STOCK_METRICS = ['pqo','pvo','por']          
    
    for k in self._ACC_METRICS:
      if 'MAIN_KEY' not in self._ACC_METRICS[k]:
        self._ACC_METRICS[k]['MAIN_KEY'] = self._GOOD + k

    for k in self._ERR_METRICS:
      if 'MAIN_KEY' not in self._ERR_METRICS[k]:
        self._ERR_METRICS[k]['MAIN_KEY'] = self._GOOD + k
              
    self._METRICS = {}  
    for k,v in self._ACC_METRICS.items() :
      self._METRICS[k] = v
    for k,v in self._ERR_METRICS.items() :
      self._METRICS[k] = v
      
    self._AVAIL_ACC = [x for x in self._ACC_METRICS]
    self._AVAIL_ERR = [x for x in self._ERR_METRICS]
      
    self._AVAIL_METRICS = self._AVAIL_ACC + self._AVAIL_ERR
    
    kwargs = self.__extra_params
    self._AR_AVAIL_BASELINES_DETAILS = get_avail_baselines(**kwargs) if self.__dct_baselines is None else self.__dct_baselines

    return
  
  def _agg_capped_overstock(self, values, **kwargs):
    _val = np.sum(values)
    if self.AR_real_val_overstock > 0:
      return _val if int(_val) <= self.AR_real_val_overstock else self.AR_max_val_overstock
    else:
      return _val
  
  def _agg_sum(self, values, **kwargs):
    return np.sum(values)
  
  def _agg_mean(self, values, **kwargs):
    return np.mean(values)
  
  def _agg_capped_99_mean(self, values, **kwargs):
    return min(99.0, np.mean(values))
  
  
  
  def get_extra_metric_desc(self, metric):
    if '_' in metric:
      metr = metric.split('_')[1]
    else:
      metr = metric
    if metr not in self._METRICS:
      return ''
    maxing = self._metric_is_maxing(metr)
    res = ''
    if maxing:
      add = 'accuracies above {:.2f}'.format(self._METRICS[metr]['ACC_THR'])
    else:
      add = 'errors below {:.2f}'.format(self._METRICS[metr]['ERR_THR'])
    if self._GOOD in metric:
      if (self._GOOD + metr) in self._METRICS[metr]:
        res = self._METRICS[metr][self._GOOD + metr]
      else:
        res = "Percentage of series that have {} based on '{}' metric (see metric desc)".format(add, metr)
    elif self._MED in metric:
      if (self._MED + metr) in self._METRICS[metr]:
        res = self._METRICS[metr][self._MED + metr]
      else:
        res = "Median value of '{}' for all good series (see '{}')".format(metr, self._GOOD + metr)
    elif self._EP in metric:
      if (self._EP + metr) in self._METRICS[metr]:
        res = self._METRICS[metr][self._EP + metr]
      else:
        res = "Best epoch for metric '{}'".format(metr)
    elif metr == metric:
      res = "Average value of all series for '{}': {}".format(metr, self.get_metric_desc(metr))
    return res
  
  
  def get_baseline_desc(self, baseline, s_units=None):
    """
    gets the baseline description. will use `s_units` as the time-unit
    """
    if not isinstance(baseline, str):
      self.raise_error("`baseline` param is not str")
    if baseline[:2].isnumeric():
      baseline = baseline[3:]
    if s_units is None:
      s_units = self.AR_step_label
    l_parts = baseline.split('_')
    s_base = l_parts[0]
    s_n1 = l_parts[1] if len(l_parts)>1 else None
    s_n2 = l_parts[2] if len(l_parts)>2 else None
    if s_base not in self._AR_AVAIL_BASELINES_DETAILS:
      return "Non-baseline model"
    s_desc = self._AR_AVAIL_BASELINES_DETAILS[s_base]['DESC']
    if s_n1 is not None:
      if s_n2 is not None:
        s_desc = s_desc.format(s_n1=s_n1, s_n2=s_n2, s_units=s_units)
      else:
        s_desc = s_desc.format(s_n1=s_n1,s_units=s_units)
    else:
      s_desc = s_desc.format(s_units=s_units)
      
    return s_desc
  
  
  def _is_baseline(self, str_name):
    if str_name[:2].isnumeric():
      str_name = str_name[3:]
    if str_name[:4] in self._AR_AVAIL_BASELINES_DETAILS:
      return str_name
    else:
      return None
    
    
    
  
  def compare_timeseries_results(self, 
                                 report_name, 
                                 analysis='qty_over',
                                 dct_results=None, 
                                 lst_files=None, 
                                 folder_name=None,
                                 location='output',
                                 save_analysis=True,
                                 count_based=True,
                                 simplified=False,
                                 
                                 output_to_folder='',
                                 return_series=False,
                                 debug_model=None,
                                 debug_only_n_ser=None,
                                 
                                 thresholds=[33],
                                 ):
    """
    Compares multiple time-series results for analytics business KPI extraction
    
    
    Inputs:
      
      report_name : report name
      
      analysis : `qty_over`, `qty_under` ...

      folder_name : folder (also dependent of `location`) where to analyze ALL files
      
      dct_results : dictionary with model keys and values consisting of 
                    dictionaries with series and other data
                    
      
      lst_files : list of files 
      
      
      location : location of input files ['output', 'data', 'path']
      
      save_analysis : save resulting dataframes or return JSON
      
      count_based : use count percentages (default) or use sums coverages
      
      simplified : leave out some features/KPIs
      
      return_series : will return a 'SERIES' dict with '__TARGET__' and predictions data
                      including good/bad (True/False) for each series for each KPI for 
                      each model. Also includes a '__SCORE__' for each series
                      
                      
      output_to_folder: full qualified path to the output folder
      
      debug_model : a particular model name used as stop condition for debug
      
      thresholds : error (prediction over reality) thresholds
      
      OBSERVATION: either one of the params must be not `None`
    """

    if analysis == 'qty_over':
      func = partial(self.compare_timeseries_results_qty, overstock=True)
    elif analysis == 'qty_under':
      func = partial(self.compare_timeseries_results_qty, overstock=False)
    else:
      self.raise_error("Uknown analysis '{}'".format(analysis))
      
    if output_to_folder in ['', None]:
      output_to_folder = dt.now().strftime("%Y%m%d_%H%M")[2:] + '_' + 'comparison'
      

    locations = ['output', 'data', 'path']
    
    if dct_results is None:
      dct_results = OrderedDict({})
      
    if location == 'output':
      load_fun = self.log.load_output_dataframe
    elif location == 'data':
      load_fun = self.log.load_dataframe
    elif location == 'path':
      load_fun = self.log.load_abs_dataframe
    else:
      self.raise_error("Uknown location {}".format(location))
      
          

    if len(dct_results) > 0:
      self.D("Received dictionary with {} models".format(len(dct_results)))    
    elif (folder_name is not None) or (lst_files is not None):
      if folder_name is not None:
        if location == 'output':
          path = self.log.get_output_folder()
        elif location == 'data':
          path = self.log.get_data_folder()
        else:
          path = ''
        path = os.path.join(path, folder_name)
        lst_all_files = sorted(os.listdir(path)        )
        lst_files = [os.path.join(path,x) for x in lst_all_files if x[-4:]=='.csv']
        self.D("Loading {} files from {}".format(len(lst_files), path))
        load_fun = self.log.load_abs_dataframe
        
      self.D("Received list of {} files (from {})".format(len(lst_files), location))
      for _file in lst_files:
        df = load_fun(_file)
        if debug_only_n_ser:
          self.D("  DEBUG Restricting to first {} series".format(debug_only_n_ser))
          df = df.iloc[:debug_only_n_ser]
        model_name = os.path.splitext(os.path.split(_file)[-1])[0]
        dct_mres = df.to_dict(orient='list')        
        dct_results[model_name] = {k:np.array(v) 
                                    for k,v in dct_mres.items()}
    else:
      self.raise_error("Must supply either dict or output CSVs or full path CSVs")
    self.D("Performing analysis '{}'...".format(analysis.upper()))
    return func(report_name=report_name, 
                dct_results=dct_results,
                save_analysis=save_analysis,
                debug_model=debug_model,
                count_based=count_based,
                return_series=return_series,
                output_to_folder=output_to_folder,
                simplified=simplified,
                thresholds=thresholds)
    
  
  def get_metric_desc(self, metric):
    return self._METRICS[metric]['DESC']
    
  def _get_model_result_dict(self, np_series, np_preds, np_reality):
    if len(np_series.shape) != 1 and np_series.shape[0] < 2:
      self.raise_error("Series must be numpy vector and must contain at least 2 series")
   
    if len(np_preds.shape) < 2:
      self.raise_error("np_preds shape is {} and full series are expected".format(np_preds.shape))
    if len(np_reality.shape) < 2:
      self.raise_error("np_reality shape is {} and full series are expected".format(np_reality.shape))
      
    np_pred = np_preds.sum(axis=1).ravel()
    np_real = np_reality.sum(axis=1).ravel()
    PKEY = self._PKEY
    SKEY = self._SKEY
    RKEY = self._RKEY
    d_res = OrderedDict()
    d_res[SKEY] = np_series.tolist()
    d_res[PKEY] = np_pred.round(2).tolist()
    d_res[RKEY] = np_real.round(2).tolist()
    return d_res

  
  def compare_timeseries_results_qty(self, 
                                     report_name, 
                                     dct_results, 
                                     save_analysis=True,  
                                     overstock=True,
                                     count_based=True,
                                     simplified=False,
                                     
                                     output_to_folder='',
                                     return_series=False,
                                     debug_model=None,
                                     
                                     use_prc=False,
                                     thresholds=[33,20],
                                     ):
    """
    Compares multiple time-series results for analytics business KPI extraction
    based on sum of predicted values
    
    Inputs:
      
      name : report name
      
      dct_results : dictionary with model keys and values consisting of 
                    dictionaries with series and other data
                    
      
    """
    PKEY = self._PKEY
    SKEY = self._SKEY
    RKEY = self._RKEY
    GIKEY = self._GIKEY
    
    
    v = []
    prev_ids = None
    prev_qty = None
    self.D("  Sanity-checking...")
    for model_name in dct_results:
      ids = np.array(dct_results[model_name][SKEY])
      real_qty = np.array(dct_results[model_name][RKEY])
      v.append(len(dct_results[model_name][PKEY]))
      self.D("    Model: {:<20} Series: {}".format(model_name, v[-1]))
      if not np.all(np.array(v) == v[0]):
        self.raise_error("Cannot compare series of different len")
      if prev_qty is not None and not np.all(real_qty == prev_qty):
        self.raise_error("Series have different ground truth values!")
      if prev_ids is not None and not np.all(ids == prev_ids):
        self.raise_error("Series have different IDs")
      prev_qty = real_qty
      
    # now lets begin comparison    
    # first create basic dataframes
    d_all = OrderedDict() # the series comparison
    d_tmp = OrderedDict() # the stats temp dict
    d_met = OrderedDict() # metadata dict
    
    d_all['ID'] = ids
    d_all['REAL_QTY'] = real_qty
    n_series = real_qty.shape[0]
    q25, q50, q75 = np.quantile(real_qty, q=[0.25, 0.5, 0.75])
    if n_series > 10:
      mover_thr = self._calculate_series_importance_thr(real_qty)
    else:
      mover_thr = 0
    important_series = real_qty >= mover_thr
    important_qtys = real_qty[important_series]
    important_qty = important_qtys.sum()
    
    _mult = 100 if use_prc else 1
    _round = 2 if use_prc else 3
    
    thrs = thresholds
    ppd_margin = 0
    
    GI_KPI  = GIKEY    # GI Good series percentage over all Important series
    PQI_KPI = 'PQI'
    WOT_KPI = 'WOT'
    PQG_KPI = 'GQ'
    PGA_KPI = 'PGA'
    ACP_KPI = 'AC%'
    OOT_KPI = 'OVL_OT'
    PQ_KPI  = 'OVL_PC'
    
    if count_based:
      main_KPI = GI_KPI 
    else:
      main_KPI = PQI_KPI
    
    if overstock:
      _metric = 'ppd'
    else:
      _metric = 'pdt'
    
    sort_KPI = '{}_{}'.format(main_KPI, thrs[0])    
    d_met['Metric: {}'.format(self._METRICS[_metric]['DESC'])] = _metric.upper()
    d_met[_metric.upper() + ' margin'] = ppd_margin
    d_met['Error thresholds'] = "".join([" {}%".format(x) for x in thrs])
    d_met['Analyzed nr. series'] = n_series
    d_met['Good qty threshold'] = round(mover_thr,1)
    d_met['Worst 25% series qty thr'] = q25
    d_met['Median 50% series qty thr'] = q50
    d_met['Best 25% series qty thr'] = q75
    d_met['Average qty thr'] = real_qty.mean()
    d_met["Important ser. w. qty >{:.1f}".format(mover_thr)] = "{:.0f} %".format(important_series.sum() / n_series * _mult)
    d_met["Importnat ser qty over all"] = "{:.0f} %".format(round(important_qty / real_qty.sum() * _mult,_round))
    d_met['Good series out of important'] = GI_KPI
    d_met['Wrong preds but over target'] =  WOT_KPI    
    #d_met['Good series with XX% max err thr'] = 'GOOD_XX'
    d_met['Good series with 33% max err thr'] = 'GOOD_33'
    d_met['Main performance indicator'] = sort_KPI

    d_met['Total % good pred over real imp ser'] = PQI_KPI
    d_met['Total % good pred of all series'] = PGA_KPI
    d_met['Total % good pred over own targets)'] = PQG_KPI

    if not simplified:
      d_met['Overall total % pred over real (both bad/good)'] = PQ_KPI
      d_met['Avg coverage % of pred over real'] = ACP_KPI
      d_met['Overall over-target predictions'] = OOT_KPI
      
    
    d_series = {}
    d_series[self._TARGET_KEY] = real_qty
    
    self.D("  Processing...")
    for i, model_name in enumerate(dct_results):
      self.D("    Analysing {}".format(model_name))
      preds = np.array(dct_results[model_name][PKEY])
      d_series[model_name] = {}
      d_series[model_name][self._PKEY] = preds
      
      zeros = preds == 0
      if zeros.sum() > 0:
        self.D("      Model {} contains {} zero predictions!".format(model_name, zeros.sum()))
        preds[zeros] = 1e-7
      mcode = 'M{:02}'.format(i)
      d_all[mcode+'_PRED'.format(i)] = preds
      d_tmp[model_name] = OrderedDict({'MCODE' : mcode})  
      positive_error = preds > real_qty
      p_ovr_tgt = positive_error.sum() / real_qty.shape[0] * _mult      
      mins = np.minimum(real_qty, preds)
      p_total_qty = mins.sum() / real_qty.sum() * _mult

      if not simplified:
        d_tmp[model_name][OOT_KPI] = round(p_ovr_tgt,_round)
        d_tmp[model_name][PQ_KPI] = round(p_total_qty, _round)
      
      if model_name == debug_model:
        self.D("      DEBUG MODE:")
      

      for thr in thrs:
        sthr = '_{:02}'.format(thr)
        GI_THR_KPI = GI_KPI + sthr
        err_thr = thr / 100
        
        if overstock:
          errs, good = self._calc_ppd(y_true_s=real_qty,
                                      y_pred_s=preds,
                                      thr=err_thr,
                                      margin=ppd_margin,)
        else:
          errs, good = self._calc_pdt(y_true_s=real_qty,
                                      y_pred_s=preds,
                                      thr=err_thr,
                                      margin=ppd_margin,)

        # now add score to model/series
        if self._SCORE_KEY not in d_series[model_name]:
          # this is one-shot and this score should be taken over
          # by the higher level API, record and average over time
          # in order to obtain multi-period, multi-sample results
          # OBS: obviously the results must me "accumulated within the
          # same series ...
          scores = 1 - errs
          d_series[model_name][self._SCORE_KEY] = scores
        
        bad = ~good
        p_good = good.sum() / real_qty.shape[0] * _mult
              
        bad_over_target = bad & positive_error
        p_bad_over_target = bad_over_target.sum() / bad.sum() * _mult     


        _full_cover, _good_imp = self._calc_isc(y_true_s=real_qty,
                                                y_pred_s=preds,
                                                thr=err_thr,
                                                margin=0,
                                                mover_thr=mover_thr,
                                                overstock=overstock,
                                                )
        
        
        
        _cover = np.clip(_full_cover, None, 1.0)
        
        p_cover = _cover.mean() * _mult

        
        p_good_important = _good_imp.sum() / important_series.sum() * _mult

        good_imp_pred = preds[_good_imp]
        good_imp_real = real_qty[_good_imp]
        mins_imp = np.minimum(good_imp_real, good_imp_pred)
        p_imp_qty = mins_imp.sum() / important_qty * _mult
        
        good_pred = preds[good]
        good_real = real_qty[good]
        mins_good = np.minimum(good_pred, good_real)
        p_good_qty = mins_good.sum() / good_real.sum() * _mult
        p_good_all_qty = mins_good.sum() / real_qty.sum() * _mult

        if count_based:
          d_tmp[model_name][GI_THR_KPI] = round(p_good_important, _round)
          d_tmp[model_name][PQI_KPI+sthr] = round(p_imp_qty, _round)
        else:
          # change order in table ...
          d_tmp[model_name][PQI_KPI+sthr] = round(p_imp_qty, _round)
          d_tmp[model_name][GI_THR_KPI] = round(p_good_important, _round)
          
        d_tmp[model_name][PGA_KPI+sthr] = round(p_good_all_qty, _round)
        if not simplified:
          d_tmp[model_name][ACP_KPI+sthr] = int(p_cover)
        d_tmp[model_name]['GOOD'+sthr] = round(p_good, _round)
        d_tmp[model_name][PQG_KPI+sthr] = round(p_good_qty, _round)
        if not simplified:
          d_tmp[model_name][WOT_KPI+sthr] = round(p_bad_over_target,_round)
      
        # add main indicator for each series - for the current model
        if PQI_KPI not in d_series[model_name]:
          d_series[model_name][PQI_KPI] = _good_imp
      
      
    # now the final comparison dict
    all_models = [m for m in d_tmp]    
    d_cmp = OrderedDict({
                'MODEL' : all_models, 
                'DESC'  : [self.get_baseline_desc(m) for m in all_models],
              })
    for kk in d_tmp[all_models[0]]:
      d_cmp[kk] = [d_tmp[m][kk] for m in d_tmp]
    
    
    df_res = pd.DataFrame(d_all)
    df_cmp = pd.DataFrame(d_cmp).sort_values(sort_KPI)
    lst_meta = [(k,v) for k,v in d_met.items()]
    df_met = pd.DataFrame({
          'VARIABLES' : [x[0] for x in lst_meta],
          'VALUES' : [x[1] for x in lst_meta]
        })
    
    self.D("Analysis results:\n\n  Legend/Info:\n{}\n\n  Comparison:\n{}\n".format(
        df_met,
        df_cmp.drop('DESC',axis=1)))
    
    
    self.D("Results summary:")
    lst_models = None
    for thr in thrs:
      srt_key = '{}_{:02}'.format(main_KPI, thr)
      df_thr = df_cmp.sort_values(srt_key)
      mname = df_thr.iloc[-1,0]
      prc = df_thr[srt_key].values[-1]
      self.D("  For '{}' thr best model '{}' predicts well on {:.1f}% of important series".format(
          thr,  mname, prc * 100))     
      if lst_models is None:
        lst_models = df_thr.MODEL.values.tolist()
    
    lst_best_baselines = []
    for i in range(len(lst_models)-1,-1,-1):
      if len(lst_best_baselines) < 3:
          strb = self._is_baseline(lst_models[i])
          if strb is not None:
            lst_best_baselines.append(strb)
      else:
        break
    
    
    if save_analysis:
      self.save_compare_timeseries_results(report_name, output_to_folder, df_cmp, df_res, df_met)
      
    d_info = OrderedDict()
    d_info['INFO'] = d_met
    d_info['BEST_BASELINES'] = lst_best_baselines
    d_info['STATS'] = d_tmp
    if return_series:
      d_info['SERIES'] = d_series
    return d_info
  

  def save_compare_timeseries_results(self, report_name, output_to_folder, df_cmp, df_res, df_met):
    fn1 = report_name+'_models_compare'
    fn2 = report_name+'_models_overall'
    fn3 = report_name+'_models_metadata'      
    if output_to_folder not in ['', None]:
      if not os.path.isabs(output_to_folder):
        output_to_folder = os.path.join(self.log.get_output_folder(), output_to_folder)
      if not os.path.isdir(output_to_folder):
        os.makedirs(output_to_folder)
      fn1 = os.path.join(output_to_folder, fn1)
      fn2 = os.path.join(output_to_folder, fn2)
      fn3 = os.path.join(output_to_folder, fn3)
      full_path=True
    else:
      full_path=False
    
    _path, _ = os.path.split(fn1)
    self.D("Saving comparison results to '.{}'".format(output_to_folder[-40:]))
      
    self.log.save_dataframe(df_cmp, fn=fn1, to_data=False, full_path=full_path)
    self.log.save_dataframe(df_res, fn=fn2, to_data=False, full_path=full_path)      
    self.log.save_dataframe(df_met, fn=fn3, to_data=False, full_path=full_path)
    return
    

  
  def multiperiod_compare_timeseries_results(self,                                  
                                             report_name, 
                                             analysis='qty_ovr',
                                             folder_name=None,
                                             location='output',
                                             save_analysis=True,
                                             count_based=True,
                                             simplified=False,                                             
                                             
                                             debug_model=None,   
                                             debug_only_n_ser=None,
                                             ):
    """
     applies `compare_timeseries_results` over a set of individual folders
     
     location : either `output`, `data' or `path`
     
     folder_name : the root where all the period subfolders are available, here it is mandatory but was left `None`
                   for signaturecompatibility 
    """
    if folder_name is None:
      self.raise_error("`folder_name` must point to root of the periods data. Received {}".format(folder_name))
            
      
    if location == 'output':
      path = self.log.get_output_folder()
    elif location == 'data':
      path = self.log.get_data_folder()
    elif location == 'path':
      path = ''
    else:
      self.raise_error("Uknown location {}".format(location))
      
    
    save_folder = os.path.join('multi',report_name)
    
    self.D("Starting multi-period timeseries model comparision")
    root_path = os.path.join(path, folder_name)
    lst_all_files = [os.path.join(root_path, x) for x in os.listdir(root_path)]    
    lst_periods_subdirs = [x for x in lst_all_files if os.path.isdir(x)]
    lst_periods = [os.path.split(x)[-1] for x in lst_periods_subdirs]
    self.D("Found {} periods in ...{}".format(lst_periods, root_path[-30:]))
    
    model_results = {}
    period_gold = {}
    
    for i, str_period in enumerate(lst_periods):
      self.D("Period '{}' analysis".format(str_period))
      sub_folder_name = lst_periods_subdirs[i]
      d_res = self.compare_timeseries_results(report_name=report_name+'_'+str_period,
                                              analysis=analysis,
                                              folder_name=sub_folder_name,
                                              location='path',
                                              save_analysis=save_analysis,
                                              count_based=count_based,
                                              simplified=simplified,
                                              return_series=True,
                                              output_to_folder=save_folder,
                                              debug_model=debug_model,
                                              debug_only_n_ser=debug_only_n_ser,
                                              )
      # now d_res contains also the series results for each model
      period_gold[str_period] = d_res['SERIES'][self._TARGET_KEY]
      for model_name in d_res['SERIES']:
        if model_name == self._TARGET_KEY:
          continue
        if model_name not in model_results:
          model_results[model_name] = {}
        model_results[model_name][str_period] = d_res['SERIES'][model_name]
    for model_name in model_results:
      self.D("Analyzing model '{}'".format(model_name))
      self._multiperiod_model_analysis(model_results[model_name], dct_targets=period_gold)
    return
    

  def _multiperiod_model_analysis(self, dct_model, dct_targets):
    """
    """
    _kpis = [x for x in dct_model[list(dct_model.keys())[0]]]
    kpis = [x for x in _kpis if x not in [self._PKEY, self._SCORE_KEY]]
    periods = [x for x in dct_model]

    scores_per_period = [dct_model[x][self._SCORE_KEY] for x in dct_model]
    preds_per_period = [dct_model[x][self._PKEY] for x in dct_model]
    
    self.D("  Analysing {} KPIs: {}".format(len(kpis), kpis))
    for kpi in kpis:
      good_imp_list = [dct_model[x][kpi] for x in dct_model]
      n_per = len(good_imp_list)
      n_sers = good_imp_list[0].shape[0]
      np_res = np.vstack(good_imp_list)
      diffs = np.diff(np_res, axis=0).reshape(n_sers,-1)
      n_diffs = diffs.shape[1]
      is_zero = diffs == 0
      is_z_agg = is_zero.sum(axis=1)
      all_zeros = (is_z_agg == n_diffs)
      self.D("  For KPI '{}' we have {:.1f}% match over {} periods {}".format(
          kpi, all_zeros.sum() / n_sers * 100,n_per, periods))
    return
    
  
### METRICS SECTION

  @staticmethod
  def _calculate_series_importance_thr(series):
    return metrics.calculate_series_importance_thr(series)

  def _calc_ppd(self, y_true_s, y_pred_s, thr, margin):
    """
    period prediction based percentual deviation
    """
    return metrics.calc_ppd(y_true_s, y_pred_s, thr, margin)


  def _calc_pdt(self, y_true_s, y_pred_s, thr, margin):
    """
    period percentual deviation from target
    """
    return metrics.calc_pdt(y_true_s, y_pred_s, thr, margin)

      
  def _calc_isc(self, y_true_s, y_pred_s, thr, margin, mover_thr=None, overstock=True):
    """
    important series coverage indicator
    """
    return metrics.calc_isc(y_true_s, y_pred_s, thr, margin, mover_thr, overstock)


  def _calc_ismape(self, y_true_s, y_pred_s, thr=0.35, margin=1, mover_thr=None, target_oos=True):
    """
    important series coverage indicator
    """
    return metrics.calc_ismape(y_true_s, y_pred_s, thr, margin, mover_thr, target_oos)
  

  def _calc_pvo(self, y_true_s, y_pred_s, zerocap=True, capped=None, thr1=-0.1, thr2=0.5):
    """
    calculates PVO - Period Value-based Overstock
    """
    return metrics.calc_pvo(
      y_true_s=y_true_s,
      y_pred_s=y_pred_s,
      avg_prices=self.AR_avg_prices,
      zerocap=zerocap,
      capped=capped,
      thr1=thr1,
      thr2=thr2,
      )
  
  
  def _calc_por(self, y_true_s, y_pred_s, zerocap=True, capped=None, thr1=-0.1, thr2=0.5):
    return metrics.calc_por(
      y_true_s=y_true_s, 
      y_pred_s=y_pred_s, 
      avg_prices=self.AR_avg_prices, 
      day_prices=self.AR_day_prices, 
      zerocap=zerocap, 
      capped=capped, 
      thr1=thr1, 
      thr2=thr2
      )
    
  def _agg_good_revenue(self, values, good_series, **kwargs):
    ser_rev = (self.AR_y_test.squeeze() * self.AR_stock_prices.squeeze()).sum(axis=1)    
    good_predicted_revenue = values[good_series]    
    good_series_revenue = ser_rev[good_series]
    return good_series_revenue.sum() / ser_rev.sum()

  
  def _calc_ovs_per(self, y_true_s, y_pred_s, 
                    zerocap=True, 
                    capped=None, 
                    zero_target_overstock=9.,
                    thr1=-0.1, 
                    thr2=0.5, 
                    return_qty=False,
                    small_qty_thr=1,
                    ):
    return metrics.calc_ovs_per(
      y_true_s=y_true_s, 
      y_pred_s=y_pred_s,
      zero_target_overstock=zero_target_overstock,
      thr1=thr1,
      thr2=thr2,
      return_qty=return_qty,
      small_qty_thr=small_qty_thr,
      )
    
    
  def _calc_oos(self, y_true_s, y_pred_s,):
    _y_pred_s = np.clip(y_pred_s, 1e-7, None).squeeze()
    _y_true_s = np.clip(y_true_s, 1e-7, None).squeeze()
    is_oos = self.AR_stock_flash == 0
    ser_has_oos = is_oos.sum(1) > 0
    # TODO: must finish this
    
    
      


  def calc_timeseries_error(self, y_true, y_pred, metric, ERR_MAX=2, 
                            return_good_series=False,
                            model=None,
                            ERR_THR=0.25, ACC_THR=0.1,
                            check_shapes=True,
                            func_aggregate_dict=None,
                            **kwargs):
    """
    Calculate errors for a batch of time-series
    
      y_true : ground truth batch of time-series 
      
      y_pred : predicted batch of time-series
    
      metric: 
        "mape2": mean of residual/predicted - mape that penelizes more the lower predictions, 

        "cmape1": Clipped (100%) mean of residual/predicted - mape that penelizes more the lower predictions, 

        "mape21": mean of residual/predicted - mape that penelizes more the lower predictions
                  and similar to "ppd5" uses a threshold to ignore small differences (up to 1 unit)
        
        "smape" : simetric mape
        
        "mape": mean of residual/truth 
        
        "ppd" : period prediction deviation = sum(pred)-sum(truth) / sum(preds),     
                
        "ppd5" : variant of ppd        

        "pdt" : period deviation from target= sum(pred)-sum(truth) / sum(truth) or 'normalized deviation',     
        
        "isc" :  Important Series Covergage based on prediction quantity coverage over important series
        
        "r2" : classic R2
        
        "rmse" : classic RMSE

        "pqo" : Period Quantity Overstock indicator will range between negative (understock) and positive (overstock)
        
        "pvo" : Period valoric overstock. OBS: Good series have -10% to 50% overstock
          
        "por" : Percentage of overall revenue of good series that have -10% to 50% overstock
        
      return_good_series : (default `False`) return the list of good series or not

    returns:
        (tuple) (`score`,`errors`,`good_series`) where `score` is the aggregated score (based on metric aggregation func),
        `errors` is the per serie error and `good_series` (if `return_good_series==True`) bool vector

    """
    return metrics.calc_timeseries_error(
      y_true=y_true, 
      y_pred=y_pred, 
      metric=metric, 
      ERR_MAX=ERR_MAX, 
      return_good_series=return_good_series,
      model=model,
      ERR_THR=ERR_THR, 
      ACC_THR=ACC_THR,
      check_shapes=check_shapes,
      func_aggregate_dict=self._METRICS,
      **kwargs,
      )

  
  def autoregression(self, model, x_tensors, start, steps, autoregression_tensor,
                     y_test=None, input_full_tensors=None, classification_threshold=None,
                     metric='ppd', DEBUG=False, y_mean=0, y_std=1):
    """
    
    Performs either autoregression test (if `y_test` is not None) or returns the autoregression
    values for all steps
    
    inputs:
      `model`: time-series based auto-regression model
      
     `x_tensors`: list of input tensors, each must be of size start+steps minimum. They will be sliced
              from 0:(start+current_pos) if not included in input_full_tensors (see below).
              IMPORTANT: the tensor that receives T-1 autoregression value must be full size
      
      `start`: point where we start to autogress. if `start == 0` then it means we are using seq2seq
             
      
      `steps`: how many autoregression steps

      `autoregression_tensor`: index of the input tensor that receives the output at next step for
                      autoregression purposes, if this tensor is missing then it is assumed the 
                      model is one-shot-prediction (such as enc-dec)
    
    inputs for testing:
      
      `y_test`: ground truth (batch, steps, 1) default `None` 
      
      input_full_tensors : list of tensor indices that must be supplied in full to the
                           model (such as for the encoder part of seq2seq models). For most
                           time-series models this should include all tensors 
                           (default `None` includes all tensors)
                           
                           
      classification_threshold : if supplied will threshold the predictions for classification 
                                  purpose
                             
      
      metric: 'mape2', 'mape', 'r2', 'ppd', 'smape'
      
      y_mean, y_std : (default 0 and 1) used for the autoregression tensor particularly when/if 
                      no preprocessing has been done in advance
      
    returns: 
      overall aggregated metric score and each series score if `y_test` is not None
      or the predictions if `y_test is None`
      
    """
    if y_test is not None:
      if len(y_test.shape) != 3:
        raise ValueError("y_test must be (batch, steps, 1)")
      if y_test.shape[1] != steps:
        raise ValueError("Number of steps {} must be equal to number of y_test {} steps".format(
            steps, y_test.shape))
      if classification_threshold:
        raise ValueError("Classification metrics not implemented. Cannot run in testing mode")
    if type(x_tensors) not in [list, tuple]:
      raise ValueError("X_list must be supplied as a list/tuple of tensors")
    if input_full_tensors is None:
      input_full_tensors = np.arange(len(x_tensors))
    n_fixed = len(input_full_tensors)
    stype = "seq2seq" if start == 0 else "simple"
    current = start
    n_series = x_tensors[0].shape[0]
    y_preds = np.zeros((n_series, steps, 1))
    # if seq2seq then assume first tensors are for encoder
    n_history = start if stype == 'simple' else x_tensors[0].shape[1]
    self.D("Performing autoregression {}on {} series defined by {} tensors ({} fixed / {} model):".format(
        'testing ' if y_test is not None else '',
        n_series, len(x_tensors), n_fixed, stype))
    self.D("  Series pred size {}. Using hist size  {}{}. Scaling std:{:.3f} mean:{:.3f}".format(
        steps, n_history, " (from encoder)" if stype == 'seq2seq' else '',
        y_std, y_mean))
    _lens = [x.shape[0] for x in x_tensors]
    if np.unique(_lens).size > 1:
      raise ValueError("All tensors must have same batch size - {}".format(_lens))
    if stype == 'simple':
      _lens = [x.shape[1] for x in x_tensors]
      if np.unique(_lens).size > 1:
        raise ValueError("For simple autoreg models all tensor must match timesteps  - {}".format(_lens))
    
    if autoregression_tensor is not None:
      X_list = [x.copy() for x in x_tensors]
      X_list[autoregression_tensor] = (X_list[autoregression_tensor] - y_mean) / y_std
      for step in range(steps):
        current = start + step
        X = []
        for i in range(len(X_list)):
          if i in input_full_tensors:
            X.append(X_list[i])
          else:
            X.append(X_list[i][:,:current] if len(X_list[i].shape) == 2 else X_list[i][:,:current,:])
        if DEBUG:
          self.D("x_autoreg pre predict:\n{}".format(
              X[autoregression_tensor][:5,-16:].squeeze()))
        if hasattr(model, 'predict'):
          y_hat = model.predict(X)
        else:
          y_hat = model(X)
        if classification_threshold:
          y_hat = (y_hat > classification_threshold).astype(int)
        y_vals = y_hat[:,current,0]
        y_preds[:,step,0] = y_vals
        if step < (steps-1):
          X[autoregression_tensor][:,current+1,0] = y_vals
        if DEBUG:
          self.D("y_hat, y_preds,x_autoreg:\n{}\n{}\n{}".format(
              y_hat[:5,-16:].squeeze(), 
              y_preds[:5,-16:].squeeze(),
              X[autoregression_tensor][:5,-16:].squeeze()))
    else:
      self.D("  Ignoring `start_point` and `steps` & executing NON-autoregressive prediction")
      # we do NOT have a actual auto-regression
      if hasattr(model, 'predict'):
        y_preds = model.predict(x_tensors)
      else:
        y_preds = model(x_tensors)
      expected_shape = (x_tensors[0].shape[0], steps, 1)
      if y_preds.shape != expected_shape:
        raise ValueError("Resulted preds shape {} differ from expected (nr_series, step, 1)={}".format(
            y_preds.shape, expected_shape))
        
        
    if y_test is not None:
      score, series_err = self.calc_timeseries_error(y_test, y_preds, metric=metric)
      self.D("  Autoregression test finalized. Overall '{}': {:.2f}".format(
          metric, score))
      return score, series_err
    else:
      return y_preds
    
    
  def _debug_good_series(self, good, errs, y_pred, y_test, metric, rescale=False):
    m_p = 0
    s_p = 1
    m_t = 0
    s_t = 1
    if rescale:
      m_p = self.AR_reverse_mean_preds
      s_p = self.AR_reverse_std_preds
      m_t = self.AR_reverse_mean_test
      s_t = self.AR_reverse_std_test
    self.__debug_timeseries_results(good=good,
                                    errs=errs,
                                    y_test=y_test,
                                    y_pred=y_pred,
                                    metric=metric, 
                                    mean_preds=m_p, std_preds=s_p, 
                                    mean_test=m_t, std_test=s_t,
                                    ACC_THR=self.AR_thresholds[metric]['ACC_THR'],
                                    ERR_THR=self.AR_thresholds[metric]['ERR_THR']
                                    )
    return
  
  
  def _add_model_benchmark_history(self, model_name, metric, value):
    if model_name not in self.AR_history:
      self.AR_history[model_name] = {}
    if metric not in self.AR_history[model_name]:
      self.AR_history[model_name][metric] = []
      
    self.AR_history[model_name][metric].append(value)
    return
    
  def _get_model_benchmark_status(self, model_name):
    if model_name not in self.AR_best_preds:
      self.AR_best_preds[model_name] = {}
    if model_name not in self.AR_results.keys():
      dct_res = OrderedDict({})
      for i, metric in enumerate(self.AR_metrics):
        metric_ser = self._GOOD + metric
        metric_med = self._MED + metric
        metric_best = self._BEST + metric
        metric_avg = self._AVG + metric
        metric_ep  = self._EP + metric
        dct_res[metric_ser] = 0
        if i == 0:
          dct_res['OVR_T'] = 0        
        dct_res[metric_med] = -np.inf if self._metric_is_maxing(metric) else np.inf
        dct_res[metric_ep]  = -1
        if not self.simplified_results:
          dct_res[metric_best] = -np.inf if self._metric_is_maxing(metric) else np.inf
          dct_res[metric_avg] = -np.inf if self._metric_is_maxing(metric) else np.inf
        dct_res[metric] = 0
      self.AR_results[model_name] = dct_res
    return self.AR_results[model_name]
    
    
  def _add_benchmark_results(self, 
                             model_name, 
                             y_pred, 
                             epoch,
                             metric,
                             dct_user_params=None,
                             return_sums=False,
                             DEBUG=False):

    
    for param in self.AR_user_params:
      if param not in dct_user_params:
        raise ValueError("User param '{}' not found in `dct_user_params`")
        
    
    y_test = self.AR_y_test.copy()
    _y_preds = y_pred.copy()
    
    maxing = self._metric_is_maxing(metric)
    thr_name = 'ACC_THR' if maxing else 'ERR_THR'
    if y_test.shape != _y_preds.shape:
      raise ValueError("Provided y_test {} is different from generated baseline y_pred {}".format(
          y_test.shape, _y_preds.shape))
    self.D("[BP]   Calculating '{}' on model '{}' thr={:.2f} yh_s={:.4f} yh_m={:.4f}  yt_s={:.4f} yt_m={:.4f}...".format(
        metric, model_name, self.AR_thresholds[metric][thr_name],
        self.AR_reverse_std_preds, self.AR_reverse_mean_preds,
        self.AR_reverse_std_test, self.AR_reverse_mean_test,
        ))
    
    y_test = y_test * self.AR_reverse_std_test + self.AR_reverse_mean_test
    _y_preds = _y_preds * self.AR_reverse_std_preds + self.AR_reverse_mean_preds
    
    if self.AR_reverse_mean_preds != 0:
      # assume we must zero-threshold
      _y_preds = np.clip(_y_preds, 0, None)

    ### DEBUG
    self._dm = model_name
    if model_name == 'seas_365_2':
      self._dy = _y_preds
    ### END DEBUG

    
    _score, _errs, _good  = self.calc_timeseries_error(
        y_test, _y_preds, 
        metric=metric, 
        model=model_name,
        return_good_series=True,
        ACC_THR=self.AR_thresholds[metric]['ACC_THR'],
        ERR_THR=self.AR_thresholds[metric]['ERR_THR']
        )

    ### DEBUG
    if model_name == 'seas_365_2':
      self._dg = _good
    ### END DEBUG

    
    n_series = y_test.shape[0]
    nr_good = _good.sum()
    prc_good = round(nr_good / n_series, 3)
    self._add_model_benchmark_history(model_name, metric, nr_good)


    direct_error = _score
    
    if nr_good > 0:
      med = np.median(_errs[_good]) 
      best = _errs[_good].max() if maxing else _errs[_good].min()
      avg = _errs[_good].mean() 
    else:
      med = np.nan
      best = np.nan
      avg = np.nan
      
    metric_ser = self._GOOD + metric
    metric_med = self._MED + metric
    metric_best = self._BEST + metric
    metric_avg = self._AVG + metric
    metric_ep  = self._EP + metric
    
    dct_res = self._get_model_benchmark_status(model_name)
      
    _status_nr = dct_res[metric_ser] 
    _status_direct = dct_res[metric]
    _status_med = dct_res[metric_med] 
    _status_ep = dct_res[metric_ep] 
    if not self.simplified_results:
      _status_best = dct_res[metric_best]
      _status_avg = dct_res[metric_avg] 
    _new_best = False
    # now compute sums vs sums
    y_test_sum = y_test.sum(axis=1).squeeze()
    y_pred_sum = _y_preds.sum(axis=1).squeeze()
    if ((prc_good > _status_nr) or 
          (((prc_good == _status_nr) and (maxing and _status_med < med)) or
            ((prc_good == _status_nr) and (not maxing and _status_med > med)))):
      self.AR_best_preds[model_name][metric] = _y_preds
      _status_nr  = prc_good
      _status_med = med
      _status_ep  = epoch
      _status_direct = direct_error
      if not self.simplified_results:
        _status_avg = avg
        _status_best = best
      _new_best = True
      self.D("[BP]     Found new best state for metric '{}' at training iteration {}:".format(
          metric, _status_ep))
      self.D("[BP]       Nr good series:  {:>5}".format(nr_good))
      self.D("[BP]       Median {:<6}     {:>5.3f}".format(metric+':',_status_med))
      
      if metric == self.AR_metrics[0]:
        over = (y_pred_sum > y_test_sum).sum()
        over = over / y_test.shape[0]
        self.D("[BP]       Preds > target:  {:>5.1f}%".format(over * 100))
        dct_res['OVR_T'] = over
      
      
      if DEBUG:
        self._debug_good_series(_good, _errs, _y_preds, y_test, metric=metric, 
                                rescale=False)
    else:
      self.D("[BP]     No best state. Good series for metric '{}': {}<={}".format(
          metric, prc_good, _status_nr))
            
    dct_res[metric_ser] = _status_nr
    dct_res[metric_med] = _status_med
    dct_res[metric_ep]  = _status_ep
    dct_res[metric] = _status_direct
    
    if not self.simplified_results:
      dct_res[metric_best] = _status_best
      dct_res[metric_avg] = _status_avg
    
    # now add user params
    for param in self.AR_user_params:
      dct_res[param] = dct_user_params[param]
    self.AR_results[model_name] = dct_res
    if return_sums:
      return _good, _errs, _new_best, y_pred_sum, y_test_sum
    else:
      return _good, _errs, _new_best
    
    
  def _metric_is_maxing(self, metric):
    if metric in self._AVAIL_ACC: 
      return True
    elif metric in self._AVAIL_ERR:
      return False
    else:
      raise ValueError("Uknown metric '{}'".format(metric))
      
  def metric_is_maxing(self, metric):
    if metric in self._METRICS:
      return self._metric_is_maxing(metric)
    else:
      if (self._GOOD) in metric:
        return True
      else:
        metr = metric.split('_')[1]
        return self._metric_is_maxing(metr)

      
  def _maybe_set_cross_benchmark(self, model_name, nr_intersect, epoch):
    new_result = None
    if model_name not in self.AR_cross_results:
      self.AR_cross_results[model_name] = {"VALUE":0,"EP":0}
    if nr_intersect > self.AR_cross_results[model_name]['VALUE']:
      self.AR_cross_results[model_name]['VALUE'] = nr_intersect
      self.AR_cross_results[model_name]['EP'] = epoch
      new_result = nr_intersect
    return new_result
    
  
  def __check_x_tensors(self, x_tensors):
    if type(x_tensors) != list:
      self.raise_error('x_tensors must be a list of tensors')
    for i, tensor in enumerate(x_tensors):
      if type(tensor) != np.ndarray:
        self.raise_error('x_tensors must be a list a numpy arrays. {} is {}'.format(i, type(tensor)))
    return
        
  
  def __show_AR_metrics(self):
    self.P("[BP]   Using metrics & thresholds:")   
    for i, _m in enumerate(self.AR_metrics):      
      self.P("[BP]     Metric #{}: {}".format(i+1,_m))
      thr_name = 'ACC_THR' if _m in self._ACC_METRICS else 'ERR_THR'
      self.P("[BP]       {}:  {}".format(thr_name, self.AR_thresholds[_m][thr_name]))
      self.P("[BP]       Descr:  {}".format(self.AR_thresholds[_m]['DESC']))
    return
  
  def _calc_reality_stock_error(self):
    y_test = self.AR_y_test * self.AR_reverse_std_test + self.AR_reverse_mean_test
    y_test = y_test.squeeze()
#    stock_flash = self.AR_stock_flash * self.AR_reverse_std_test + self.AR_reverse_mean_test
    stock_start = self.AR_stock_start * self.AR_reverse_std_test + self.AR_reverse_mean_test
    stock_adds  = self.AR_stock_add * self.AR_reverse_std_test + self.AR_reverse_mean_test
    per_preds = stock_start + stock_adds.sum(axis=1)
    self.AR_stock_pred = self.AR_stock_start + self.AR_stock_add.sum(axis=1)
    errs, best, qtys = self._calc_ovs_per(
        y_true_s=y_test,
        y_pred_s=per_preds,
        return_qty=True,
        )    
    return errs, best, qtys
  
  def _save_dict(self, dct, name):
    fld = os.path.join(self.AR_folder, name)
    self.log.save_json(dct, fld)
    return
    
  
  def _setup_stocks(self, stocks_restocks_prices):
    labels = ['initial stock', 'flash stock', 'replenishments','prices']
    assert type(stocks_restocks_prices) == tuple
    assert len(stocks_restocks_prices) == 4
    sstock, fstock, astock, prices = stocks_restocks_prices 
    for _v in stocks_restocks_prices:
      assert type(_v) == np.ndarray

    assert len(sstock.shape) == 1
    assert len(fstock.shape) == 2
    assert len(astock.shape) == 2
    assert len(prices.shape) == 2
    
    for i, _v  in enumerate(stocks_restocks_prices):
      assert np.isinf(_v).sum() == 0, "We have `inf` in {}:{}".format(labels[i], _v.shape)
      assert np.isnan(_v).sum() == 0, "We have `nan` in {}:{}".format(labels[i], _v.shape)
    self.AR_stock_flash = (fstock - self.AR_mean) / self.AR_std
    self.AR_stock_start = (sstock - self.AR_mean) / self.AR_std
    self.AR_stock_add = (astock - self.AR_mean) / self.AR_std
    
    self.AR_day_prices = prices
    self.AR_stock_prices = prices
    self.AR_avg_prices = prices.mean(axis=1)    
       
    errs, best, qtys = self._calc_reality_stock_error()

    y_test = self.AR_y_test * self.AR_reverse_std_test + self.AR_reverse_mean_test
    y_test = y_test.squeeze()
    y_sums = y_test.sum(1) 
    zero_series = y_sums == 0
    
    qtys[qtys < 0] = 0
    
    ovs_vals = qtys * self.AR_avg_prices
    self.AR_real_val_overstock = int(ovs_vals.sum()) + 1
    self.AR_max_val_overstock = int('9' * len(str(self.AR_real_val_overstock)))


    # Now BEST SERIES     
    vals = y_test * self.AR_day_prices
    ser_vals = vals.sum(axis=1)
    ser_val_thr = self._calculate_series_importance_thr(ser_vals)
    top_series = ser_vals > ser_val_thr
    top_and_best = top_series & best
    ser_has_oos = (fstock == 0).sum(1) > 0
    last_5_steps_adds = astock[:,-5:].sum()
    last_5_steps_adds_day = astock[:,-5:].mean()
    self.AR_top_series = top_series
    

    
    dct_info = OrderedDict()

    dct_info["[BP] Stock information stats"] = OrderedDict({
        "Total number of zero-sale series": int(zero_series.sum()),
        "Mean stock daily replenishments" : astock.mean().round(2),
        "Last 5 days of period restocks"  : last_5_steps_adds.round(2),
        "Last 5 days daily mean restock"  : last_5_steps_adds_day.round(2),
        "Mean initial stocks for all ser" : sstock.mean().round(2),
        "Number of zero-flash days (oos)" : int((fstock==0).sum()),
        "Percentage of series with oos"  : (ser_has_oos.sum() / ser_has_oos.shape[0] * 100).round(1),
        "Target period monetary revenue"  : (vals.sum()/10**6).round(2),        
            })


    dct_info["[BP] Overstock reality check info:"] = OrderedDict({
        "Average percentual overstock"     : (errs.mean() * 100).round(1),
        "Median percentual overstock"      : (np.median(errs) * 100).round(1),
        "Total ovs monetary value for all" : round(self.AR_real_val_overstock/10**6,2),
        "Good ovs (-10% to 50%) ser perc"  : (best.sum() / best.shape[0] * 100).round(1),
        "Overstock value vs revenue perc"  : (ovs_vals.sum() / ser_vals.sum() * 100).round(1),
        })
    
    
    dct_info["[BP] TOP series analysis ({} ser)".format(top_series.sum())] = OrderedDict({
        "Top series perc of full revenue"  : (ser_vals[top_series].sum() / ser_vals.sum() * 100).round(1),
        "Top series ovs mean percentage"    : (errs[self.AR_top_series].mean() * 100).round(1),
        "Top series ovs monetary value"     : (ovs_vals[top_series].sum() / 10**6).round(2),
        "Overstock perc by top ser vs rev"    : (ovs_vals[top_series].sum() / ovs_vals.sum() * 100).round(1),
        "Top series perc ovs by revenue"   : (ovs_vals[top_series].sum() / ser_vals.sum() * 100).round(1),
        "Top good series perc of all ser"    : (top_and_best.sum() / top_and_best.shape[0] * 100).round(1),
        })

    for section in dct_info:
      self.P(section + ':')
      for inf in dct_info[section]:
        v = dct_info[section][inf]
        if 'perc' in inf.lower():
          v = str(v) + '%'
        elif 'value' in inf.lower():
          v = str(v) + 'M'
        self.P("  {:<33} {:>7}".format(inf+':', v))
        
    self._save_dict(dct_info, 'stocks_info.txt')
    
    
#    self.P("  Overstock percentual distrib:")
#    self.log.distrib(self.AR_ovs_prc)
#    self.P("  Overstock qty distrib:")
#    self.log.distrib(self.AR_ovs_qty)
          
    return
    
  def start_autoregression_benchmark(self,**kwargs):
    self.P("WARNING `start_autoregression_benchmark` is deprecated - use `start`")
    return self.start(**kwargs)
  
  
  
  def _add_baselines_benchmark(self, 
                               X_data,
                               value_tensor_idx, 
                               n_steps, 
                               DEBUG, verbose_baselines=True):
    #
    # baselines will be calculated using x_tensors that has to contain y_train FULL
    # data can be scaled or not
    #
    dct_base = self.autoregression_baselines(X_data=X_data, 
                                             value_tensor_idx=value_tensor_idx,
                                             n_steps=n_steps,
                                             fast=self.AR_fast_baselines,
                                             ceil_preds=self.AR_ceil_preds,
                                             verbose=verbose_baselines,
                                             clip_to_max_history=self.AR_base_clip_to_hist,
                                             )
    b_metrics = {
        m : {'MAX':-np.inf if self.metric_is_maxing(self._METRICS[m]['MAIN_KEY']) else np.inf} 
        for m in self.AR_metrics
        }

    for baseline in dct_base:
      y_base = dct_base[baseline]
      if self.AR_ceil_preds:
        y_base = np.ceil(y_base)
      y_base = y_base * self.AR_scale_preds
      if self.AR_ceil_preds:
        y_base = np.ceil(y_base)
      self.AR_baselines.append(baseline)
      good_pos = []
      for metric in self.AR_metrics:
        if DEBUG:
          self.D("[BP] debug {} y_pred / y_test:\n{}\n{}".format(
              baseline, 
              y_base.squeeze()[:5,-15:],
              self.AR_y_test.squeeze()[:5,-15:]))
        if self.AR_user_params is not None and len(self.AR_user_params) > 0:
          dct_user_params = {k:None for k in self.AR_user_params}
        else:
          dct_user_params = None
        
        _good, _errs, _ = self._add_benchmark_results(model_name=baseline,
                                                      y_pred=y_base,
                                                      epoch=0,
                                                      dct_user_params=dct_user_params,
                                                      metric=metric)
          
        good_pos.append(_good)
        metric_key = self._METRICS[metric]['MAIN_KEY']
        is_maxing = self.metric_is_maxing(metric_key)
        new_best = False
        if ((is_maxing and b_metrics[metric]['MAX'] <= self.AR_results[baseline][metric_key]) or
            (not is_maxing and b_metrics[metric]['MAX'] >= self.AR_results[baseline][metric_key])):
          new_best = True
        if new_best:
          b_metrics[metric]['MAX']  = self.AR_results[baseline][metric_key]
          b_metrics[metric]['BASELINE']  = baseline
          b_metrics[metric]['GOOD']  = _good
          b_metrics[metric]['ERRS']  = _errs
          b_metrics[metric]['YhB'] = y_base
      if len(good_pos) >= 2:
        n_inter = (good_pos[0] & good_pos[1]).sum() / good_pos[0].shape[0]        
        self._maybe_set_cross_benchmark(baseline, 
                                        nr_intersect=n_inter,
                                        epoch=0)

    self._setup_output_metrics()
    _descr = ''
    for m in self._dct_metrics_descr:
      _descr +=" - {:<9} {}\n".format(m+':', self._dct_metrics_descr[m])
    self.P("[BP] Initial results based on {} baselines:\n\nResults info & legend:\n{}\nResults table:\n{}\n".format(
        len(dct_base),
        _descr,
        self.get_benchmark_results()
        ))
    for metric in self.AR_metrics:
      self.AR_best_base[metric] = {}      
      best_baseline = b_metrics[metric]
      _baseline = best_baseline['BASELINE']
      _y_best   = best_baseline['YhB']
      _max_good = best_baseline['GOOD']
      _max_errs = best_baseline['ERRS']

      self.AR_best_base[metric]['BASELINE'] = _baseline
      self.AR_best_base[metric]['GOOD_SERIES'] = _max_good
      self.AR_best_base[metric]['ERRORS'] = _max_errs
      self.P("[BP] Best baseline for metric '{}' is '{}'".format(metric, _baseline))
      self.P("[BP] Presenting results for best baseline '{}' on metric '{}':".format(
          _baseline, metric))
      self.P("[BP]   Baseline desc: {}".format(self.get_baseline_desc(_baseline)))
      self._debug_good_series(_max_good, _max_errs, 
                              y_pred=_y_best, y_test=self.AR_y_test, 
                              metric=metric, rescale=True)
      _y_p = _y_best * self.AR_reverse_std_preds + self.AR_reverse_mean_preds
      _y_t = self.AR_y_test * self.AR_reverse_std_test + self.AR_reverse_mean_test
      _pred_sums = _y_p.sum(axis=1).squeeze() 
      self.AR_best_base[metric]['STEP_PREDS'] = _y_p.squeeze()
      self.AR_best_base[metric]['PERIOD_PREDS'] = _pred_sums
      _test_sums = _y_t.sum(axis=1).squeeze()
      _prc = _max_good.sum() / _max_good.shape[0]
      _baseline_idx = 0
      _fn = "{:02}_{}_{}{:.2f}".format(_baseline_idx, _baseline, metric, _prc)
      self._save_benchmark_series_results(model_name=_fn,
                                          metric=metric,
                                          good_series=_max_good,
                                          errors=_max_errs,
                                          pred_sums=_pred_sums,
                                          real_sums=_test_sums,
                                          pred_tag=_baseline,
                                          )
            
  
  
  def _add_stocks_as_baseline(self):
    baseline_name = self._REALITY
    _y_base = self.AR_stock_pred.reshape(-1,1,1)
    _y_test = self.AR_y_test.squeeze().sum(axis=1).reshape(-1,1,1)
    self.AR_baselines.append(baseline_name)
    self.AR_results[baseline_name] = {}
    for metric in self.AR_metrics:
      metric_ser = self._GOOD + metric
      metric_med = self._MED + metric
      metric_ep =self._EP + metric
      if self._METRICS[metric]['PERIOD_ONLY']:
        _score, _errs, _good = self.calc_timeseries_error(
            y_true=_y_test,
            y_pred=_y_base,
            metric=metric,
            return_good_series=True,
            ACC_THR=self.AR_thresholds[metric]['ACC_THR'],
            ERR_THR=self.AR_thresholds[metric]['ERR_THR']
            )
        
        self.AR_results[baseline_name][metric_ser] = _good.sum() / _good.shape[0]
        self.AR_results[baseline_name]['OVR_T'] = (_y_base.squeeze() >= _y_test.squeeze()).sum() / _y_test.shape[0]
        self.AR_results[baseline_name][metric_med] = np.median(_errs[_good])
        self.AR_results[baseline_name][metric_ep] = 0
        self.AR_results[baseline_name][metric] = _score
      else:
        self.AR_results[baseline_name][metric_ser] = 0
        self.AR_results[baseline_name][metric_med] = -1
        self.AR_results[baseline_name][metric_ep] = 0
        self.AR_results[baseline_name][metric] = -1
        
    return
    
  
  def start(self,
            n_steps, 
            X_data, 
            y_test, 
            value_tensor_idx,
            last_x_date=None,
            stocks_restocks_prices=None,
            benchmark_name='benchmark',
            metrics=['isc','ppd'], # 'pdt','ppd','isco'
            scale_preds=1.0,
            ceil_preds=False,
            clip_base_to_max_history=True,
            
            verbose_baselines=False,
            show_cross_metrics=False,
            fast_baselines=False,
            simplified_results=True,
            mean=0,
            std=1,
            reverse_mean=0,
            reverse_std=1,
            reverse_mean_test=None,
            reverse_std_test=None,
            thresholds=None,
            user_params=None,
            step_label='days',
            THR_ACC=0,
            THR_ERR=0.35,
            expected_iterations=None,
            DEBUG=False
            ):
    """
    
      Initializes the autoregression benchmarking process and adds the default baselines
    
      inputs:
        
        benchmark_name : simply the name of the customer or something similar
        
        nr_steps : number of steps to test
        
        X_data : list of input tensors that can be even 1 tensor with the historical values 
                    with shape (series, history_steps, 1). 
                    MORE PRECISELY: this should be `y_train` tensor !
        
        y_test : the test data in shape (series, nr_steps, 1) that will be used by the process
        
        `last_x_date` :  the `str` date of the last known (training) day. Important for baselines and not only
        
        `stocks_restocks_prices`: tuple `(v, S, R, P)` where:
                                     - `v` is a vector with test-period start stocks
                                     - `S` is a matrix with end-day stock values for each target in `y_test` 
                                       and has the same dimensionality as `y_test` 
                                     - `R` is a matrix similar with `S` that contains the daily replenishments for each product
                                     - `P` is a price-per-day matrix
                           
                    CAUTION: if `y_test` is already scalled then quantities variable must
                             be also scalled and values recoverable with `reverse_mean` and `reverse_std`
                             
        `scale_preds` : will scale preds with certain value (such as a 30% markup) before any calculation / saves
        
        `fast_baselines` : use only very fast baselines (no weight based models)
        
        
        mean, std : (default 0) if provided y_test data needs scaling or provided `val_tensor`
                    data needs scaling will transform `x = (x - mean) / std`
                    
        reverse_mean, reverse_std : when data is already scaled we might need to recover 
                                    the initial real values. We have to sets of 'reverse' 
                                    one for the predicted values and one for the 
                                    very rare occasion when we need 'special; de-scaling of
                                    y_test data. `reverse_mean_test` and `reverse_std_test` will
                                    default to `reverse_mean` and `reverse_std`
                                    
                    
        value_tensor_idx : the index of the value tensor in X_data list
        
        user_params : default `None` will create additional params in output dictionary if required 
        
        metrics : the metric or list of metrics that will be used in the process
        
        
        thresholds : dict of threshold for each metric in `metrics` in the form {metric: {'ACC_THR':v1, 'ERR_THR':v2}}
        
        expected_iterations : the total number of calls to add_autoregression_benchmark usefull in order to
                              time the process so far and evaluate the remaining time each time the 
                              `add_autoregression_benchmark` is called (and it is considered 1 iteration)
                              
                              
    """
    self.__check_x_tensors(X_data)

    
    self.AR_expected_iterations = expected_iterations
    self.AR_counter = 0
    self.AR_scale_preds = scale_preds
    self.AR_ceil_preds = ceil_preds
    self.AR_base_clip_to_hist = clip_base_to_max_history
    self.AR_step_label = step_label
    self.AR_base_x_hist = X_data[value_tensor_idx].shape[1]
    self.simplified_results = simplified_results
    self._show_cross_metrics = show_cross_metrics
    
        
    
    self.AR_folder = os.path.join(self.log.get_output_folder(), benchmark_name)
    _folder = self.AR_folder
    i = 1
    while os.path.isdir(_folder):
      self.P("WARNING: folder '{}' already exists and will be cleared!".format(self.AR_folder))
      import shutil
      shutil.rmtree(_folder)
      _folder = self.AR_folder + str(i)
    self.AR_folder = _folder
    os.mkdir(self.AR_folder)      
      
    
    if benchmark_name == 'benchmark':
      self.P("[BP] WARNING: PLEASE NAME YOUR BENCHMARK! (`benchmark_name='customer_x'` or similar)")
      benchmark_name =  dt.now().strftime("%Y%m%d_%H%M%S") + '_' + benchmark_name
    self.AR_benchmark_name = benchmark_name
    self.P("[BP] Starting autoregression benchmark '{}' v.{} including standard baselines".format(
        self.AR_benchmark_name, self.version ))
        
    self.AR_thresholds = self._METRICS.copy()

    if thresholds is not None:
      # overwrite the thresholds
      for _metric in thresholds:
        self.AR_thresholds[_metric] = thresholds[_metric]
        
    if type(metrics) is str:
      metrics = [metrics]
      
#    if len(metrics) > 2:
#      raise ValueError("[BP] Max 2 metrics can be used at a time")

    if stocks_restocks_prices is not None and len(set(self._STOCK_METRICS) & set(metrics)) == 0:
      self.P('[BP] Adding period percentual overstock as main indicator!')
      metrics = [self._STOCK_METRIC] + metrics
      

    
    for metric in metrics:
      if metric not in self._AVAIL_METRICS:
        raise ValueError("[BP] Uknown metric '{}' available metrics are: {}".format(
            metric, self._AVAIL_METRICS))
        

    self.AR_main_metric = metrics[0]
    self.AR_mean = mean
    self.AR_std = std
    self.AR_reverse_mean_preds = reverse_mean
    self.AR_reverse_std_preds = reverse_std
    self.AR_reverse_mean_test = reverse_mean_test
    self.AR_reverse_std_test = reverse_std_test
    if self.AR_reverse_mean_test is None:
      self.AR_reverse_mean_test = self.AR_reverse_mean_preds
      self.AR_reverse_std_test = self.AR_reverse_std_preds
    self.AR_steps = n_steps
    self.AR_metrics= metrics
    
    self.AR_results = {}   
    self.AR_cross_results = {}
    self.AR_history = {}
    self.AR_hist_models = {}
    self.AR_SSBM = {} # Sums of Series for Best Models

    self.AR_start_time = tm()
    self.AR_baselines = []
    self.AR_best_base = {}
    self.AR_best_preds = {}
    self.AR_fast_baselines = fast_baselines
    self.AR_user_params = sorted(user_params) if user_params is not None else {}
    self.AR_y_test = (y_test - self.AR_mean) / self.AR_std
    self.AR_y_test_real = self.AR_y_test * self.AR_reverse_std_test + self.AR_reverse_mean_test
    self.AR_stock_flash, self.AR_stock_add, self.AR_stock_start = None, None, None
    
    self.AR_last_x_date = None
    if last_x_date is not None:
      if type(last_x_date) == str and len(last_x_date) == 10:
        self._setup_past_dates(
          last_x_date=last_x_date,
          X_data=X_data,
          value_tensor_idx=value_tensor_idx,
          n_steps=n_steps,
          )
      else:
        raise ValueError("`last_x_date` must be 'YYYY-MM-DD' string - received '{}'".format(last_x_date))
    

    self.show_params()
    
    if stocks_restocks_prices is not None:
      self._setup_stocks(stocks_restocks_prices)
      
    
    self.__show_AR_metrics()
      
    self.P("[BP]   Reverse scaling std={} mean={}".format(
              self.AR_reverse_std_preds, 
              self.AR_reverse_mean_preds))
    _nobs = self.AR_y_test.shape[0]
    self.D("[BP]   Received y_test:\n{}".format(self.AR_y_test.reshape(_nobs,-1)))
    self.log.set_nice_prints(np_precision=2, df_precision=3, suppress=True)

    if hasattr(self, "AR_stock_pred") and self.AR_stock_pred is not None:
      self._add_stocks_as_baseline()
      
    self._add_baselines_benchmark(
        X_data=X_data,
        value_tensor_idx=value_tensor_idx,
        n_steps=n_steps,
        DEBUG=DEBUG,
        verbose_baselines=verbose_baselines,
        )  
     
    return
  
  def _setup_past_dates(self, last_x_date, X_data, value_tensor_idx, n_steps):
    if type(last_x_date) != str:
      raise ValueError("Please provide `last_x_date` as string. Thank you :)")
    _date = dt.strptime(last_x_date, "%Y-%m-%d")
    _nr_x_days = X_data[value_tensor_idx].shape[1]
    self.AR_last_x_date = _date
    self.AR_x_dates = pd.Series(
      index=[_date - timedelta(days=x) for x in range(_nr_x_days-1,-1,-1)],
      data=range(_nr_x_days)
      )
    self.AR_y_dates = pd.Series(
      index=[_date + timedelta(days=x) for x in range(1, n_steps + 1)],
      data=range(n_steps)
      )
    self.AR_x_years = sorted(list(np.unique([x.year for x in self.AR_x_dates.index])))
    
  
  def get_best_baselines_results(self):
    """
    Returns a dict with the following structure:
      {"METRIC" :{
        "BASELINE" : "...",   # name of best series for METRIC
        "GOOD_SERIES" : [...] # bool vector with true/false for each series
        "ERROR" : [....]      # error (based on METRIC) for each series
        "STEP_PREDS" : [[]]   # 2D Tensor with preds per steps
        "PERIOD_PREDS" : []   # vector with sums per series
        }
        
    Observations:
      - output.keys() will return the used metrics
      - each metric has exactly the same structure
    """
    return self.AR_best_base
  
  def add_autoregression_benchmark(self, **kwargs):
    self.P("WARNING: `add_autoregression_benchmark` is obsolete, please use `add_checkpoint`")
    return self.add_checkpoint(**kwargs)


  def add_model_results(self,
                        model_name, 
                        y_preds, 
                        epoch, 
                        return_metric=None,
                        dct_user_params=None,
                        DEBUG_AUTOREG=False,
                        DEBUG_RESULTS=True,
                        save_best=True,
                        save_type='both',
                        ):
    """
    Adds model predictions to benchmarking process

    Parameters
    ----------
    model_name : str
      name of the model.
    y_preds : ndarray
      predictions generatd at current epoch.
    epoch : int
      current epoch for history purposes.
    return_metric : bool, optional
      DESCRIPTION. The default is None.

    Returns
    -------

    """
    return self._add_checkpoint(
      model=None, 
      y_preds=y_preds,
      model_name=model_name, 
      x_tensors=None, 
      autoreg_tensor=None, 
      autoreg_pos=None, 
      epoch=epoch, 
      return_metric=return_metric,
      dct_user_params=dct_user_params,
      DEBUG_AUTOREG=DEBUG_AUTOREG,
      DEBUG_RESULTS=DEBUG_RESULTS,
      save_best=save_best,
      save_type=save_type,
      )


  def add_model_checkpoint(self,
                           model, 
                           model_name, 
                           x_tensors, 
                           autoreg_tensor, 
                           autoreg_pos, 
                           epoch, 
                           return_metric=None,
                           dct_user_params=None,
                           DEBUG_AUTOREG=False,
                           DEBUG_RESULTS=True,
                           save_best=True,
                           save_type='both',
                           ):
    """
    Adds a new model to the benchmarking process
    
    inputs:
      model : the model object that must provide a `predict` method
      
      model_name : the unique name of the model
      
      x_tensors : the list of data tensors that this model uses for `predict` method
      
      autoreg_tensor : the index of the tensor where `T-1` values are stored 
      
      autoreg_pos : the position where the autoregression starts - should be 0 for 
                    seq2seq models where autoreg tensors starts from timestep 0 or the
                    actual timestep for simple time-series models
      
      epoch : the number of the epoch when this benchmark test is run
      
      return_metric: the method will output a certain metric of the benchmark for current
                model or de most important one. 
                IMPORTANT: for a particular metric the returned value can be a percentage 
                of good series ("bigger is better") or the actual metric value - as it is 
                with 'pvo' where the total value of overstock is returned ("smaller is better")
                You can use `metric_is_maxing` function to check if a returned key is maxing or not
                
      
      dct_user_params : supply dict with user params (defined at b-process start)
      
      save_best : true/false
      save_type : save the best model for each benchmarked model. 
                  Can be either ['weights', 'model', 'both']
                  
                  
        
      DEBUG_RESULTS  : (True) show a few top predictions after this validation iteration  
    """
    return self._add_checkpoint(
      model=model, 
      model_name=model_name, 
      x_tensors=x_tensors, 
      autoreg_tensor=autoreg_tensor, 
      autoreg_pos=autoreg_pos, 
      epoch=epoch, 
      y_preds=None,
      return_metric=return_metric,
      dct_user_params=dct_user_params,
      DEBUG_AUTOREG=DEBUG_AUTOREG,
      DEBUG_RESULTS=DEBUG_RESULTS,
      save_best=save_best,
      save_type=save_type,
      )
  
  def _add_checkpoint(self, 
                     model, 
                     model_name, 
                     x_tensors, 
                     autoreg_tensor, 
                     autoreg_pos, 
                     epoch, 
                     y_preds,
                     return_metric=None,
                     dct_user_params=None,
                     DEBUG_AUTOREG=False,
                     DEBUG_RESULTS=True,
                     save_best=True,
                     save_type='both',
                     ):

    SAVE_TYPES = ['model', 'weights', 'both']
    
    assert model_name != self._REALITY, "'{}' is reserved model name".format(self._REALITY)
    
    if return_metric is not None and return_metric not in self.AR_metrics:
      raise ValueError("return metric '{}' not benchmarker instance metrics".format(
          return_metric))
    
    if return_metric is None:
      return_metric = self.AR_metrics[0]
    
    if save_best:
      if save_type not in SAVE_TYPES:
        raise ValueError("Best model param should be in {}.".format(SAVE_TYPES))
      save_type = save_type.lower()
    
    if self.AR_metrics is None:
      raise ValueError("Autoregression Benchmark Process not started - please use `start_autoregression_benchmark`")
      
    if hasattr(model,'name'):
      mname = model.name
    else:
      mname = ''
    self.start_timer('ADD_AR_BENCH')
    self.D("[BP] Testing model '{}' ({}/{}) at epoch {} {}:".format(
        model_name, model.__class__.__name__, mname, epoch,
        'with already generated predictions' if y_preds is not None else "by generating predictions"
        ))
    if y_preds is None:
      self.start_timer('AUTOREG')
      y_preds = self.autoregression(model=model, 
                                    x_tensors=x_tensors,
                                    start=autoreg_pos, 
                                    steps=self.AR_steps, 
                                    autoregression_tensor=autoreg_tensor,
                                    DEBUG=DEBUG_AUTOREG,
                                    y_mean=self.AR_mean,
                                    y_std=self.AR_std,
                                    )
      if self.AR_ceil_preds:
        y_preds = np.ceil(y_preds)
      y_preds = y_preds * self.AR_scale_preds
      if self.AR_ceil_preds:
        y_preds = np.ceil(y_preds)
      elapsed_autoreg = self.end_timer('AUTOREG')
    
    self.start_timer("METRICS")
    good_pos = []
    return_value = None
    for i, metric in enumerate(self.AR_metrics):
      _results  = self._add_benchmark_results(
                                          model_name=model_name,
                                          y_pred=y_preds,
                                          epoch=epoch,
                                          metric=metric,
                                          dct_user_params=dct_user_params,
                                          return_sums=True,
                                          DEBUG=DEBUG_RESULTS,
                                          )
      _good, _errs, _new_best, _pred_sums, _test_sums = _results
      good_pos.append(_good)
      
      if return_metric == metric:
        return_key = self._METRICS[metric]['MAIN_KEY']
        return_value = self.AR_results[model_name][return_key]

      if save_best and _new_best:
        # save model & overwrite
        new_score = self.AR_results[model_name][self._GOOD + "_" + metric]
        s_label = "{}_ep{}_{}{:.2f}".format(model_name, epoch, metric, new_score)
        
        self._save_benchmarked_model(model_name, 
                                     model, 
                                     s_label,
                                     save_type=save_type)
        self._save_benchmark_series_results(model_name=model_name,
                                            metric=metric,
                                            good_series=_good,
                                            errors=_errs,
                                            pred_sums=_pred_sums,
                                            real_sums=_test_sums,
                                            pred_tag=model_name,
                                            )
        
        self.get_autoregression_model_history(model_name, 
                                              save_history=True,
                                              show_history=False)


    if len(good_pos) >= 2:
      n_inter = (good_pos[0] & good_pos[1]).sum() / good_pos[0].shape[0]        
      new_result = self._maybe_set_cross_benchmark(model_name, 
                                                   nr_intersect=n_inter,
                                                   epoch=epoch)
      if new_result is not None:
        self.D("[BP]   Found new best series intersection of {:.2f} between metric '{}' and '{}'".format(
            new_result, self.AR_metrics[0], self.AR_metrics[1]))
    elapsed_metrics = self.end_timer("METRICS")
    elapsed = self.end_timer('ADD_AR_BENCH')
    self.AR_counter += 1
    curr_time = tm()
    total_elapsed_time = curr_time - self.AR_start_time
    time_per_lap = total_elapsed_time / self.AR_counter
    total_remaining_time = 0
    total_time = 0
    if self.AR_expected_iterations:
      total_time = self.AR_expected_iterations * time_per_lap
      total_remaining_time = total_time - total_elapsed_time #(self.AR_expected_iterations - self.AR_counter) * time_per_lap
    self.D("[BP]   Done testing '{}' in {:.2f}s  (AR: {:.2f}s  Metr: {:.2f}s) Total/Elapsed/Remaining time: {:.1f} h / {:.1f} h / {:.1f} h".format(
        model_name, elapsed, elapsed_autoreg, elapsed_metrics,
        total_time / 3600, total_elapsed_time / 3600, total_remaining_time / 3600))
    
    return {return_key: return_value}
    
  
  def _save_benchmarked_model(self, model_name, model, filename, save_type='model'):
    if (not hasattr(model, "save")) or (not hasattr(model, "save_weights")):
      self.D("[BP] WARNING: {} does not have `save` method".format(
          model.__class__.__name__))
      return      
    if model_name not in self.AR_hist_models:
      self.AR_hist_models[model_name] = []
    deleted_files = []
    for prev_file in self.AR_hist_models[model_name]:
      if os.path.isfile(prev_file):
        try:
          os.remove(prev_file)
          deleted_files.append(prev_file)
        except:
          pass
    self.AR_hist_models[model_name] = [x for x in self.AR_hist_models[model_name] 
                                         if x not in deleted_files]
    fn = filename 
    only_weights = save_type == 'weights'
    save_all = save_type == 'both'
    self.log.save_keras_model(model=model,
                              label=fn,
                              use_prefix=False, #otherwise we can not keep track
                              only_weights=only_weights,
                              save_model_and_weights=save_all,
                              delete_previous=False, # we take care of this
                              record_previous=True,  # so we record what we saved
                              ) 
    for fn in self.log._model_save_last:
      self.AR_hist_models[model_name].append(fn)
    return
      
  def _save_benchmark_series_results(self, 
                                     model_name,
                                     metric,
                                     good_series,
                                     errors,
                                     pred_sums,
                                     real_sums,
                                     pred_tag='',
                                     no_subfolder=False):

    if pred_tag != '':
      pred_tag += '_'
    d_res = {
          "SERIES_ID" : np.arange(len(errors)),
          "{}IS_GOOD".format(pred_tag) : good_series,
          "{}{}".format(pred_tag,metric.upper()) : errors.round(4).tolist(),
          "PRED_QTY".format(pred_tag) : pred_sums.round(2).tolist(),
          "REAL_QTY" : real_sums.round(2).tolist(),
        }
    if hasattr(self, 'AR_SSBM'):
      self.AR_SSBM[model_name] = d_res
    
    df = pd.DataFrame(d_res)
    if no_subfolder:
      self.log.save_dataframe(df=df,fn=model_name,full_path=False, to_data=False)
    else:
      fn = os.path.join(self.AR_folder, model_name + metric)
      self.log.save_dataframe(df=df,fn=fn,folder=None)
    return
  
  
  def get_model_results(self, model_name):
    """
    returns dict with the model status
    """
    dct_res = {
        'MODEL' : model_name,
        'PREDS_BY_METRIC_BEST_EPOCH' : self.AR_best_preds[model_name],
        'MODEL_METRICS' : self.AR_results[model_name]
        }          
    return dct_res
  
  
  def get_all_baselines_results(self):
    """
    Returns all baselines results generated in the last BP.

    Returns
    -------
    dct_res : dict
      each key is a baseline
    """
    if not hasattr(self,'AR_best_preds') or self.AR_best_preds is None:
      raise ValueError('`get_all_baselines_results` called with no previous BP started! You need at least to start a Benchmarking Process first so that baselines are calculated.')
    dct_res = {}
    for model in self.AR_best_preds:
      if model in self.AR_baselines:
        dct_res[model] = self.AR_best_preds[model][self.AR_main_metric]
    return dct_res
  
  
  def get_baselines_results(self):
    """
    Returns ALL baselines results generated in the last BP.

    Returns
    -------
    dct_res : dict
      each key is a baseline
    """
    return self.get_all_baselines_results()
    

  def get_baseline_results(self, model_name):
    """
    Returns the predictions of a SINGLE baseline generated during the last benchmarking process

    Parameters
    ----------
    model_name : str
      the baseline name.

    Returns
    -------
    ndarray with predictions.

    """  
    self.P("WARNING: `get_baseline_results` is deprecated, use `get_single_baseline_result`",
           color='r')
    return self.get_single_baseline_result(model_name)

  def get_baseline_result(self, model_name):
    """
    Returns the predictions of a SINGLE baseline generated during the last benchmarking process

    Parameters
    ----------
    model_name : str
      the baseline name.

    Returns
    -------
    ndarray with predictions.

    """  
    self.P("WARNING: `get_baseline_result` is deprecated, use `get_single_baseline_result`",
           color='r')
    return self.get_single_baseline_result(model_name)
  
  
  def get_single_baseline_result(self, model_name):
    """
    Returns the predictions of a SINGLE baseline generated during the last benchmarking process

    Parameters
    ----------
    model_name : str
      the baseline name.

    Returns
    -------
    ndarray with predictions.

    """
    if model_name not in self.AR_baselines:
      raise ValueError("{} is not a known baseline model".format(model_name))
    if model_name not in self.AR_best_preds:
      raise ValueError("{} not calculated. Available are {}".format(
        model_name, list(self.AR_best_preds.keys())))
    _m = list(self.AR_best_preds[model_name].keys())[0]
    preds = self.AR_best_preds[model_name][_m]
    return preds
  
  
  def get_best_models(self, include_baselines=True, save_output=True):
    """
    returns dict with the best model status for each metric
    """
    dct_res = {}
    for metric in self.AR_metrics:
      metric_key = self._METRICS[metric]['MAIN_KEY']
      dct_res[metric] = {}
      best_score =-np.inf if self.metric_is_maxing(metric_key) else np.inf
      best_model = None
      for model in self.AR_results:
        if not include_baselines and model in self.AR_baselines:
          continue
        if self._REALITY == model:
          continue
          
        score = self.AR_results[model][metric_key]
        if self.metric_is_maxing(metric_key):
          if score > best_score:
            best_model = model
            best_score = score
            best_epoch = self.AR_results[model].get(metric+'@')
        else:
          if score < best_score:
            best_model = model
            best_score = score
            best_epoch = self.AR_results[model].get(metric+'@')
      if best_model is not None:
        dct_res[metric] = OrderedDict({
            'MODEL' : best_model,
            'GOOD_SER_PRC' : best_score,
            'EPOCH' : best_epoch,
            'MODEL_METRICS' : self.AR_results[best_model],
            'PREDS' : self.AR_best_preds[best_model][metric].squeeze(),
            })
    if save_output:
      _d = dct_res.copy()
      for m in _d:
        _d[m]['PREDS'] = _d[m]['PREDS'].round(4).tolist()
      self._save_dict(_d, 'best_models.txt')          
    return dct_res
    

  def get_autoregression_benchmark(self,  
                                   save_history=False, 
                                   show_history=False,
                                   save_stats=True,
                                   save_file_label=None,):
    self.P("WARNING: `get_autoregression_benchmark` is obsolete. Please use `get_benchmark_results`")
    return self.get_benchmark_results(
        save_history=save_history, 
        show_history=show_history,
        save_stats=save_stats,
        save_file_label=save_file_label
        )


  def _setup_output_metrics(self):
    dct_descr = OrderedDict()
    all_models = list(self.AR_results.keys())
    l_pars = [len(self.AR_results[x]) for x in self.AR_results]
    idx = np.argmax(l_pars)    
    params = self.AR_results[all_models[idx]].keys()
    dct_descr['Pred scale'] = self.AR_scale_preds
    for metric in params:
      _s = self.get_extra_metric_desc(metric)
      if _s != '':
        dct_descr[metric] = _s
    self._dct_metrics_descr = dct_descr
    self._save_dict(dct_descr, 'metrics.txt')
    return
    

  def get_results(self,  
                  save_history=False, 
                  show_history=False,
                  save_stats=True,
                  save_file_label=None,):
    """
    see `get_benchmark_results` docstrig
    """
    return self.get_benchmark_results(
      save_history=save_history,
      show_history=show_history,
      save_stats=save_stats,
      save_file_label=save_file_label,
      )
    

  def get_results_dict(self):
    return self.AR_results
  
  def get_benchmark_results(self,  
                            save_history=False, 
                            show_history=False,
                            save_stats=True,
                            save_file_label=None,):
    """
     Returns a Pandas DataFrame with the current status of the autoregression benchmark
     
     inputs:       
       `save_file_label` :  if not none and is a string will save the dataframe in _data folder
       
       `show_history`  : (default `False`) will plot benchmarking history
       
       `save_stats` : (default `True`) will save results dataframe into a CSV
       
       `save_file_label` :  custom file name
              
    """
    dct_res = OrderedDict({'MODEL':[]})
    all_models = list(self.AR_results.keys())
    l_pars = [len(self.AR_results[x]) for x in self.AR_results]
    idx = np.argmax(l_pars)    
    params = self.AR_results[all_models[idx]].keys()
    
    sort_col1 = self._METRICS[self.AR_metrics[0]]['MAIN_KEY']
    sort_col2 = self._MED + self.AR_metrics[0]

    best_score = 0
    best_model = None
    self.D("[BP] Preparing results...")
    self.__show_AR_metrics()
    for model in self.AR_results:
      dct_res['MODEL'].append(model)
      score = self.AR_results[model][sort_col1]
      if best_score < score and model not in self.AR_baselines:
        best_score = score
        best_model = model
      for k in params:
        if k in dct_res.keys():
          dct_res[k].append(self.AR_results[model].get(k))
        else:
          dct_res[k] = [self.AR_results[model].get(k)]
      _val = 0
      _ep = 0
      if len(self.AR_metrics) >= 2 and model in self.AR_cross_results:
        _val = self.AR_cross_results[model]['VALUE']
        _ep = self.AR_cross_results[model]['EP']
      if 'CROSS@EP' not in dct_res.keys():
        dct_res['CROSS@EP'] = []
      dct_res['CROSS@EP'].append("{:.2f} @ {}".format(_val,_ep))
    if not self._show_cross_metrics:
      dct_res.pop('CROSS@EP')
    df = pd.DataFrame(dct_res).sort_values([sort_col1, sort_col2], 
                                           ascending=[
                                               self.metric_is_maxing(sort_col1), 
                                               self.metric_is_maxing(sort_col2)
                                               ])
    
    fn = self.AR_benchmark_name + '_results' if save_file_label is None else save_file_label
    
    if save_stats:
      _fn = os.path.join(self.AR_folder, fn)
      self.log.save_dataframe(df, fn=_fn, folder=None)
      
    if best_model is not None:
      self.D("[BP] Best non-baseline model so far is '{}' with {}={:.3f}".format(
          best_model, sort_col1, best_score))  
      self.get_autoregression_model_history(best_model, 
                                            save_history=save_history,
                                            show_history=show_history)
      self.save_autoregression_models_histories()
    if len(self.AR_SSBM) > 0:
      self.compare_timeseries_results(report_name=self.AR_benchmark_name, 
                                      dct_results=self.AR_SSBM,
                                      output_to_folder=os.path.join(self.AR_folder, '_compare'))
    return df
  
  
  def save_autoregression_models_histories(self):
    self.D("[BP] Generating all models histories so far...")
    for model_name in self.AR_history:
      if len(self.AR_history[model_name][self.AR_main_metric]) > 1:
        self.get_autoregression_model_history(model_name, save_history=True, show_history=False)
    return
  
  
  def get_autoregression_model_history(self, model_name, save_history=True, show_history=False):
    """
    returns the main validation score history
    """
    hist = np.array(self.AR_history[model_name][self.AR_main_metric])
    rng = np.arange(1, len(hist)+1)
    if hist.size > 40:
      step = hist.size//20
      disp_hist = hist[::step]
      rng = np.arange(1, len(hist)+1, step=step)
    else:
      disp_hist = hist
    self.D("[BP]   Validation/testing history for '{}':\nScore: {}\nIter.: {}".format(
        model_name,
        disp_hist, rng))
    if save_history:
      import matplotlib.pyplot as plt
      from matplotlib.ticker import MaxNLocator
      plt.style.use('ggplot')
      ax = plt.figure().gca()
      plt.plot(range(1, len(hist)+1), hist,'b-')
      plt.title(model_name)
      plt.xlabel('Iteration')
      plt.ylabel('% good series')
      ax.xaxis.set_major_locator(MaxNLocator(integer=True))
      _fn = os.path.join(self.AR_folder, model_name+"_hist.png")
      self.log.save_plot(plt, full_path=_fn)
      if show_history:
        plt.show()
      else:
        plt.close()
    return hist

  
  
  def end_autoregression_benchmark(self):
    """
    Ends the autoregression benchmark process
    """
    self.AR_results = None
    self.AR_y_test = None
    return
          



  def debug_series(self, y_test, y_pred, metric, ERR_THR=0.35, ACC_THR=0,
                   mean_preds=0, std_preds=1, mean_test=0, std_test=1,
                   save_label=None, return_errors=False):
    """
    STAND ALONE (not part of benchmarker process)
    Does performance analysis on a pre-predicted time-series vs ground truth
    Please scale to original range before calling or provide means and std
    
    You can use this function `debug_series` (that can also save results and display 
    high-level information) or the more straight-forward `calc_timeseries_error`
    
    inputs:
      `y_test` : gold time series `(batch, seq_len, 1)`
      `y_pred` : predicted time series `(batch, seq_len, 1)`
      `metric` : used metric 

        "mape2": mean of residual/predicted - mape that penelizes more the lower predictions, 

        "mape21": mean of residual/predicted - mape that penelizes more the lower predictions
                  and similar to "ppd5" uses a threshold to ignore small differences (up to 1 unit)
        
        "smape" : simetric mape
        
        "mape": mean of residual/truth 
        
        "ppd" : period prediction deviation = sum(pred)-sum(truth) / sum(preds),     
                
        "ppd5" : variant of ppd        

        "pdt" : period deviation from target= sum(pred)-sum(truth) / sum(truth) or 'normalized deviation',     
        
        "isc" :  Important Series Covergage based on prediction quantity coverage over important series
        
        "r2" : classic R2
        
        "rmse" : classic RMSE
      
      `return_error` : bool (False), if True return errors and good-series map beside aggregated error
    
    Outputs:
      returns the score scalar or (if `return_errors`) (score, errors, good_series_map)
      
    """
    _score, _errs, _good  = self.calc_timeseries_error(y_true=y_test, 
                                                     y_pred=y_pred, 
                                                     metric=metric, 
                                                     return_good_series=True,
                                                     ERR_THR=ERR_THR,
                                                     ACC_THR=ACC_THR)
    self.__debug_timeseries_results(good=_good, 
                                    errs=_errs, 
                                    y_test=y_test, 
                                    y_pred=y_pred, 
                                    metric=metric, 
                                    mean_preds=mean_preds, 
                                    std_preds=std_preds, 
                                    mean_test=mean_test, 
                                    std_test=std_test,
                                    ERR_THR=ERR_THR, 
                                    ACC_THR=ACC_THR)
    if save_label is not None:
      pred_sums = y_pred.sum(axis=1).ravel()
      real_sums = y_test.sum(axis=1).ravel()
      self._save_benchmark_series_results(model_name=save_label,
                                          metric=metric,
                                          good_series=_good,
                                          errors=_errs,
                                          pred_sums=pred_sums,
                                          real_sums=real_sums,
                                          pred_tag=save_label,
                                          no_subfolder=True)
    if return_errors:
      result = _score, _errs, _good
    else:
      result = _score
    return result
  
    
  def __debug_timeseries_results(self, good, errs, y_test, y_pred, metric='ppd', 
                                 mean_preds=0, std_preds=1, mean_test=0, std_test=1,
                                 ERR_THR=0.35, ACC_THR=0):    
    _thr = ACC_THR if self._metric_is_maxing(metric) else ERR_THR
    PREDS = 5
    zero_series = (y_test.sum(axis=1).squeeze() == 0)
    zero_series = zero_series & good
    final_good = good & (~zero_series)
    if final_good.sum() == 0:
      self.P("  WARN: No good series to show. Please check your data! ({} zero series proposed as good)".format(
          zero_series.sum()))
      return
    n_zero = zero_series.sum()
    if n_zero > 0:
      s_add = '\n  WARN: {}/{} null series!!!\n'.format(n_zero, good.sum())
      if n_zero == zero_series.shape[0]:
        self.P("  WARN: out of {} good all are null!".format(n_zero))
        return
    else:
      s_add = ''
    debug_idxs = np.argwhere(final_good).ravel()    

    y_pred_debug = y_pred[final_good].squeeze(axis=-1)
    y_test_debug = y_test[final_good].squeeze(axis=-1) 

    y_pred_debug = y_pred_debug * std_preds + mean_preds
    y_test_debug = y_test_debug * std_test + mean_test
  
    y_pred_min = y_pred_debug.min()
    y_pred_max = y_pred_debug.max()
    y_test_min = y_test_debug.min()
    y_test_max = y_test_debug.max()

    debug_errs = errs[final_good]
    
    # now let us analyze the values and display top stock movers
    
    np_values = y_test_debug.sum(axis=-1)
    idxs = np.argsort(np_values)[::-1][:PREDS]

    s_stats  = '  y_pred min: {:>7.1f}  y_pred max: {:>7.1f}\n'.format(y_pred_min, y_pred_max)
    s_stats += '  y_test min: {:>7.1f}  y_test max: {:>7.1f}\n'.format(y_test_min, y_test_max)
    s = '  {} analysis (thr={}) on {}/{} best-preds ({:.1f}%) of {} true series (value sorted)\n  Scaled pred std:{:.3f} mean:{:.3f}\n  Scaled test std:{:.3f} mean:{:.3f}\n\n{}\n{}'.format(
        metric.upper(), _thr, len(debug_idxs), good.sum(), good.sum() / y_test.shape[0] * 100, y_test.shape[0], 
        std_preds, mean_preds,
        std_test, mean_test,
        s_stats,
        s_add)
    for idx in idxs:
      s += '\n'
      _e = debug_errs[idx]
      pp = y_pred_debug[idx].sum()
      vv = np.abs(pp -  y_test_debug[idx].sum())
      vvp = vv / pp * 100 if pp != 0 else np.nan    
      if len(y_pred_debug[idx]) > 15:
        str_pred = str(y_pred_debug[idx][:7])[:-1] + ' ... ' + str(y_pred_debug[idx][-7:])[1:]
        str_test = str(y_test_debug[idx][:7])[:-1] + ' ... ' + str(y_test_debug[idx][-7:])[1:]
      else:
        str_pred = y_pred_debug[idx]
        str_test = y_test_debug[idx]
      s +="  Series {} with metric '{}' result of {:.3f}:\n    y_pred: {}\n    y_test: {}\n    sum_y_pred / sum_y_test / diff:  {:.2f} / {:.2f} / {:.2f} ({:.1f}%)\n".format(
          debug_idxs[idx], metric, _e,    
          str_pred, str_test, 
          y_pred_debug[idx].sum(), y_test_debug[idx].sum(), 
          vv, vvp)
    self.P(s)
    return 
  
  
  
  def get_baseline_prediction(self, X_tensors, value_tensor_idx, n_steps, baseline, start_pos=None):
    """
    generates predictions for time-series using a specific baseline model
    
    Inputs:
      X_tensors : list of tensors - only he value tensor will be used
      
      value_tensor_idx : index of the value tensor
      
      start_pos : position to start predicting from (None means will use full series as 'training' data)
      
      n_steps : how many steps to predict
      
      baseline : name of the baseline model
      
    Returns:
      predictions [series, steps, 1]
      
    IMPORTANT: Carefull to scale/translate output if the input was scaled
    """
    if type(baseline) != str:
      self.raise_error("Baseline param must be the name of the baseline. Available: {}".format(
          self._AR_BASELINES))
    if baseline[:4] not in self._AR_AVAIL_BASELINES_DETAILS:
      self.D("Unknown baseline '{}'. Using lin1_30 baseline".format(baseline))
      baselines = ['lin1_30']
    else:
      baselines = [baseline]
    
    X_data = [X_tensors[value_tensor_idx][:,:start_pos]]
    res = self.autoregression_baselines(X_data=X_data, value_tensor_idx=0,
                                        n_steps=n_steps,baselines=baselines)
    return res[baseline]     
  
  
  def _calc_base(self, 
                 model_name, 
                 X_data,
                 value_tensor_idx,
                 n_steps,
                 verbose,
                 **kwargs):
    model_info = model_name.split('_')      
    model_code = model_info[0]
    if model_code not in self._AR_AVAIL_BASELINES_DETAILS.keys():
      raise ValueError("Baseline '{}' unknown!".format(model_name))
    params = []
    for i in range(1, len(model_info)):
      params.append(float(model_info[i]))
    model_func = self._AR_AVAIL_BASELINES_DETAILS[model_code].get('FUNC')
    if model_func is None:
      raise ValueError('Something is broken. check loaded baselines - model_code: {}'.format(model_code))
    dct_params = self._AR_AVAIL_BASELINES_DETAILS[model_code].get('PARAMS')
    _ver = self._AR_AVAIL_BASELINES_DETAILS[model_code].get('VER')
    if verbose:
      self.P("  Computing {} ver {}".format(model_code, _ver))
      self.P("    Desc: {}".format(self.get_baseline_desc(model_name)))
      
    _res = model_func(X_data=X_data, 
                      value_tensor_idx=value_tensor_idx,
                      n_steps=n_steps,
                      params=params,
                      verbose=verbose,
                      DEBUG=False,
                      bp=self,
                      extra_params=dct_params,
                      **kwargs,
                      )
    return _res, model_code, _ver, params
    

  
  def autoregression_baselines(self, 
                               X_data, 
                               value_tensor_idx,
                               n_steps,
                               time_tensors_idxs=None,
                               baselines=None,
                               fast=False,
                               verbose=False,
                               last_x_date=None,
                               ceil_preds=False,
                               clip_to_max_history=True,
                               suppress_messages=False,
                               **kwargs,
                               ): 
    """
    Generates the baselines based autoregressions both for the BP as well as independently 
    from a actual benchmarking process.
    
    Parameters
    ----------
    X_data : list of ndarrays
      input tensors
      
    value_tensor_idx : int
      idx of values tensor
      
    n_steps : int
      number of prediction stes
      
    time_tensors_idxs : int, optional
      (UNUSED) id of the time tenors. The default is None.
      
    baselines : TYPE, optional
      list of baslines that we should use. The default is None and wil use ALL 
      available baselines
      
    fast : TYPE, optional
      Use only fast baselines. The default is False.
      
    verbose : TYPE, optional
      full verbosity if False. The default is False.
      
    last_x_date: str, optional 
      when using without the whole bechnmarking process you can use this param to 
      setup the dates of the time-series. Default None
      
    ceil_preds : bool, optional (default false)
      When true will ceil all baselines
      
    clip_to_max_history: bool, optional (default false)
      When true will clip to max historical values

    Raises
    ------
    ValueError
      DESCRIPTION.

    Returns
    -------
    results : ndarray
      the predictions [n_series, n_steps] where n_series == X_data.shape[0]

    """
    if suppress_messages and verbose:
      raise ValueError('Cannot supress and display messages in the same time...')
    if last_x_date is not None:
      if hasattr(self, 'AR_last_x_date') and self.AR_last_x_date is not None:
        if not suppress_messages:
          self.P("BP has already `last_x_date`={}. Skipping provided date {}".format(
            self.AR_last_x_date,
            last_x_date,
            ))
      else:
        self._setup_past_dates(
          last_x_date=last_x_date, 
          X_data=X_data, 
          value_tensor_idx=value_tensor_idx, 
          n_steps=n_steps,
          )
        
    if baselines is None:
      baselines = self._AVAIL_baselines
    
    if fast:
      baselines = [x for x in baselines if 'lin' not in x]

    MIN_TS_SIZE = 72
    if type(X_data) not in [list, tuple]:
      raise ValueError("X_data must be either list of tuple of numpy tensors")
    x_values = X_data[value_tensor_idx]
    if len(x_values.shape) != 3:
      raise ValueError("Values tensor must be (series, steps, 1)")
    n_series = x_values.shape[0]    
    n_history = x_values.shape[1]
    if n_history < MIN_TS_SIZE:
      raise ValueError("Time series must have at least {} history steps".format(MIN_TS_SIZE))
    
    if n_history < 500: 
      # approx 18 months
      self.P("WARNING: time-series aparently have less than 18 months (only {} steps). If you supplied week-steps please disregard this warning.".format(
        n_history),color='r')
    
    if not suppress_messages:
      self.P("Computing autoregression baselines on {} series with {} history steps...".format(
        n_series, n_history), color='y')
      self.P("  Ceil preds:  {}".format(ceil_preds), color='y')
      self.P("  Clip to max: {}".format(clip_to_max_history), color='y')
    time_start = tm()
    # _AVAIL = { k:self._AR_AVAIL_BASELINES_DETAILS[k]['FUNC'] 
    #           for k in self._AR_AVAIL_BASELINES_DETAILS }
    results = OrderedDict()
    dct_times = {'MODEL':[],'TIME':[]}
    for model_name in baselines:
      t_model_start = tm()
      if isinstance(model_name, str) and '+' not in model_name:
        # we deal with a standard model
        _res, model_code, _ver, params = self._calc_base(
          model_name=model_name,
          X_data=X_data,
          value_tensor_idx=value_tensor_idx,
          n_steps=n_steps,
          verbose=verbose,
          )
      elif type(model_name) == list or '+' in model_name:        
        # model is ensemble
        if isinstance(model_name, str):
          ens_model_list = model_name.split('+')
        else:
          ens_model_list = model_name
          model_name = '+'.join(ens_model_list)
        _ver = '0.0.0.0'
        params = None
        model_code = 'live_ens'
        # n_sub_models = len(ens_model_list)
        _res = np.zeros((n_series, n_steps, 1))
        if not suppress_messages:
          self.P("  Computing live ensemble {}".format(model_name))
        n_valid_models = 0
        for sub_model in ens_model_list:
          # now search for result and ensemble
          if sub_model not in results:
            # baseline not already computed 
            # so lets compute it
            self.P("WARNING: Model ensemble {} called before submodel {}. If you call such a model directly from `autoregression_baselines` add all submodels in list before the ensemble".format(
              model_name, sub_model))
            _res_temp, _, _, _ = self._calc_base(
              model_name=sub_model,
              X_data=X_data,
              value_tensor_idx=value_tensor_idx,
              n_steps=n_steps,
              verbose=verbose,
              )
            if _res_temp is not None:
              _res += _res_temp
              n_valid_models += 1
          else:
            if results[sub_model] is not None:
              _res += results[sub_model]
              n_valid_models += 1
        _res /= n_valid_models        
      else:       
        raise ValueError("Uknown model {}".format(model_name))
        
      if _res is not None:
        if not suppress_messages:
          self.P("    Computed {} v{} model '{}' with params={}...".format(
            model_code, _ver, model_name, params))
        _res = np.maximum(0,_res)
        if ceil_preds:
          _res = np.ceil(_res)
        if clip_to_max_history:
          _res = clip_to_history(X_data[value_tensor_idx], _res)
        results[model_name] = _res
        t_model = tm() - t_model_start
        dct_times['MODEL'].append(model_name)
        dct_times['TIME'].append(t_model)
      else:
        self.P("    Model {} returned `None`".format(model_code), color='r')
    # end for each model 
    time_elapsed = tm() - time_start
    df = pd.DataFrame(dct_times).sort_values('TIME')
    if not suppress_messages:
      self.P("Done computing timeseries autoregression baselines in {:.2f}s:\n{}".format(
        time_elapsed, 
        textwrap.indent(str(df), '    '))
        )
    self.AR_baselines_timings = df
    return results
   
  
  def get_best_baseline_per_serie(self, 
                                  dct_base_results=None, 
                                  y_test=None, 
                                  method='both',
                                  neg_scale=2.5,
                                  metric=None,
                                  exclusions=['avgh'],
                                  both_weight_for_aggr=0.8,
                                  ):        
    """
    Generates the best baseline for each series after a BP.start or given a set
    of results.

    Parameters
    ----------
    dct_base_results : dict, optional
      The dict with baseline/models results where each key is a model. 
      The default is None and the result is taken from the current benchmarking 
      process.
      
    y_test : ndarray, optional
      The test data. The default is None and it is taken from BP `start`.
      
    method : str, optional
      Control target period aggregation. Valid values are 'aggr' for per-period
      calculation, 'steps' for per-step calc and 'both' for using both methods. 
      The default is 'aggr'.
      
    neg_scale : int, optional
      Scaling of the negative residuals where res = pred - gold. 
      The default is 2.5.
      
    metric : str, optional
      we can use pre-defined metrics from BP such as 'cme1'. The default is None.
      
    exclusions: list[str], optional
      list of models (eg. 'avgh_0') or model classes (eg. 'avgh') to be excluded
      from the base list
      
    both_weight_for_aggr: float, optional
      The weight of the 'aggr' values vs 'steps' values. For 'steps' the weight 
      will be `(1 - both_weight_for_aggr)`

    Returns
    -------
    best_model_per_series : list[str]
      list of len == n_series with best model for each series

    """
    assert method in ['aggr', 'steps', 'both']
    self.P("Computing best baseline for each series with method='{}'...".format(
      method), color='y')
    y_true_b, y_pred_b, blist = self._get_series_base_data(
      dct_base_results=dct_base_results,
      y_test=y_test,
      exclusions=exclusions,
      )
    
    if metric is not None:
      _agg, _result = self.calc_timeseries_error(
        y_true=y_true_b,
        y_pred=y_pred_b,
        metric=metric,
        check_shapes=False,
        return_good_series=False
        )
    else:
      y_true_b = y_true_b.squeeze(-1)
      y_pred_b = y_pred_b.squeeze(-1)
      n_steps = y_true_b.shape[2]
      n_models = y_pred_b.shape[1]
      n_series = y_pred_b.shape[0]
      
      # compute aggr
      y_true_b_s = y_true_b.sum(-1)
      y_pred_b_s = y_pred_b.sum(-1)
      res_per_base = y_pred_b_s - y_true_b_s
      res_per = res_per_base.copy()
      # res_per[res_per<0] = res_per[res_per<0] * neg_scale
      res_per_s = np.abs(res_per)
      res_per_s = res_per_s / np.clip(y_pred_b_s, 1e-7, None)
      _result_period = res_per_s
      # end compute aggr
      
      # compute step-wise
      shifts = [-1, 0, 1]
      n_shifts = len(shifts)
      all_shifts = np.zeros((n_series, n_models, n_shifts))
      for i, shift in enumerate(shifts):
        src_s = max(0, shift)
        src_e = n_steps + shift
        dst_s = max(0, -1 * shift)
        dst_e = n_steps + (-1 * shift)
        y_true_b1 = np.zeros_like(y_true_b)
        y_true_b1[:,:, dst_s:dst_e] = y_true_b[:,:, src_s:src_e]
        res_base = y_pred_b - y_true_b1
        res = res_base.copy()
        # res[res<0] = res[res<0] * neg_scale
        _res = np.abs(res)
        _res_p = _res / np.clip(y_pred_b, 1e-7, None)
        _res_p_s = _res_p.mean(-1)
        all_shifts[:,:,i] = _res_p_s
      # end for shifts
      _result_steps = all_shifts.min(-1)    
      # end compute step-wise
      
      
      # compute agg vs non agg
      if method == 'aggr':
        _result = _result_period
      elif method == 'steps':
        _result = _result_steps
      elif method == 'both':
        agrr_p = both_weight_for_aggr
        step_p = 1 - both_weight_for_aggr
        _result = (_result_period * agrr_p + _result_steps * step_p) / 2
        
    
    best_per_series = _result.argmin(-1)
    best_model_per_series = [blist[x] for x in best_per_series]
    return best_model_per_series
  
  
  def get_best_combined_baseline(self, verbose=True):
    """
    Returns the best baseline taking into consideration all used metrics and the 
    percentage of good series!

    Returns
    -------
    str
      name of the baseline model

    """
    if not hasattr(self, "AR_results") or self.AR_results is None:
      self.P("The BP has not running history.", color='r')
      return None
    
    baselines = list(self.AR_results.keys())
    results = np.ones(len(baselines))
    for metric in self.AR_metrics:
      c_res = [self.AR_results[b]['NG_'+metric] for b in baselines]
      results *= c_res
      if verbose:
        self.P("Best baseline for metric '{}' is '{}'".format(
          metric, self.AR_best_base[metric]['BASELINE']
          ))
    best_base_idx = results.argmax()
    best_base = baselines[best_base_idx]
    if verbose:
      self.P("Best overall baseline is '{}'".format(best_base))
    return best_base
      
  
  
  def _get_series_base_data(self, 
                            dct_base_results=None, 
                            y_test=None,
                            exclusions=[]):
    if y_test is None:
      y_test = self.AR_y_test
      
    if dct_base_results is None:
      dct_base_results = self.get_all_baselines_results()
      
    if len(dct_base_results) == 0:
      raise ValueError("No baseline results provided. `dct_base_results` is empty.")
      
    base_list = []
    for k in dct_base_results:
      found = False
      for excl in exclusions:
        if excl in k:
          found = True
      if not found:
        base_list.append(k)
    self.P("Analysing results from {} baselines with exclusions {}".format(
      base_list,
      exclusions
      ))
    np_pred_by_base = np.array([dct_base_results[x] for x in base_list]).swapaxes(0,1)
    y_true_b = np.expand_dims(y_test, 1)
    
    assert y_true_b.shape[0] == np_pred_by_base.shape[0] and y_true_b.shape[2:] == np_pred_by_base.shape[2:]
    
    return y_true_b, np_pred_by_base, base_list
  
 
  def autoregression_baselines_per_serie(self,
                                       X_data,
                                       value_tensor_idx,
                                       n_steps,
                                       lst_baselines,
                                       last_x_date=None,
                                       ceil_preds=True,
                                       clip_to_max_history=True,
                                       verbose=True,
                                       show_summary=True,
                                       ):
    np_X = X_data[value_tensor_idx]
    if np_X.shape[0] != len(lst_baselines):
      raise ValueError('List of baselines != number of series')
    np_baselines = np.array(lst_baselines)
    np_preds = np.zeros((np_X.shape[0], n_steps, 1))
    dct_res = {
      'MODEL':[],
      'N_SER':[],
      'TIME':[],
      }
    unique_models = np.unique(np_baselines)
    self.P("Running {} unique baselines on {} series".format(
      len(unique_models), np_X.shape[0],
      ))
    self.log.start_timer('ar_base_per_serie')
    for model in unique_models:
      series = np_baselines == model
      np_slice_x = np_X[series]
      n_ser = np_slice_x.shape[0]
      if verbose:
        self.P("Running {} on {} series:".format(model, n_ser), color='y')
      _res = self.autoregression_baselines(
        X_data=[np_slice_x],
        baselines=[model],
        value_tensor_idx=0,
        n_steps=n_steps,
        last_x_date=last_x_date,
        ceil_preds=ceil_preds,
        clip_to_max_history=clip_to_max_history,
        verbose=verbose,
        suppress_messages=not verbose,
        )
      np_preds[series] = _res[model]
      dct_res['MODEL'].append(model)
      dct_res['N_SER'].append(n_ser)
      dct_res['TIME'].append(self.AR_baselines_timings[self.AR_baselines_timings.MODEL==model].TIME.values[0])
    ttt = self.log.stop_timer('ar_base_per_serie')
    self.P("Finished generating baselines per series in {:.1f}s{}".format(
      ttt, ":\n{}".format(pd.DataFrame(dct_res).sort_values('N_SER')) if show_summary else ''))
    return np_preds
      
    
    
    
  

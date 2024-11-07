import numpy as np

__VER__ = '0.1.0.0'

def agg_mean(self, values, **kwargs):
  return np.mean(values)

def calculate_series_importance_thr(series, top_q=0.75):
  """
  calculates the threshold between good (important) series and bad based on simple
  stats
  """
  if len(series.shape) != 1:
    raise ValueError('`series` must contain sum-values for each serie')
  real_qty = series.ravel()
#  _sum = real_qty.sum()
  _q25, _median, _q75 = np.quantile(real_qty, q=[0.25, 0.5, top_q])
  _mean = real_qty.mean()
#  mover_thr1 = (min(_median, _mean) + _q25) / 2
  mover_thr2 = (_median + _mean) / 2
#  mover_thr3 = max(_median,_mean)
  mover_thr4 = (mover_thr2 + _q75) / 2
  mover_thr = mover_thr4
  # p_imp = real_qty[real_qty > mover_thr].sum() / real_qty.sum()
  return mover_thr



def calc_ppd(y_true_s, y_pred_s, thr, margin):
  """
  period prediction based percentual deviation
  """
  _y_pred_s = np.clip(y_pred_s, 1e-7, None)
  _y_true_s = np.clip(y_true_s, 1e-7, None)
  _res = _y_pred_s - _y_true_s 
  _abs_res = np.abs(_res)
  errors = _abs_res/ _y_pred_s
  _margin_series = _abs_res < margin
  errors[_margin_series] = np.minimum(errors[_margin_series], thr - 1e-4)
  good_series = errors <= thr
  return errors, good_series

def calc_pdt(y_true_s, y_pred_s, thr, margin):
  """
  period percentual deviation from target
  """
  _y_pred_s = np.clip(y_pred_s, 1e-7, None)
  _y_true_s = np.clip(y_true_s, 1e-7, None)
  _res = _y_pred_s - _y_true_s 
  _abs_res = np.abs(_res)
  errors = _abs_res/ _y_true_s
  _margin_series = _abs_res < margin
  errors[_margin_series] = np.minimum(errors[_margin_series], thr - 1e-4)
  good_series = errors <= thr
  return errors, good_series
  

def calc_isc(y_true_s, y_pred_s, thr, margin, mover_thr=None, overstock=True):
  """
  important series coverage indicator
  """
  _y_pred_s = np.clip(y_pred_s, 1e-7, None)
  _y_true_s = np.clip(y_true_s, 1e-7, None)
  _res = _y_pred_s - _y_true_s 
  _abs_res = np.abs(_res)
  if overstock:
    errors = _abs_res/ _y_pred_s
  else:
    errors = _abs_res/ _y_true_s
    
  _margin_series = _abs_res < margin
  errors[_margin_series] = np.minimum(errors[_margin_series], thr - 1e-4)
  good_series = errors <= thr

  if mover_thr is None:
    mover_thr = calculate_series_importance_thr(_y_true_s)
  imp_series = _y_true_s > mover_thr
  good_series = good_series & imp_series
  #min_imp_ser = np.minimum(_y_pred_s[good_series], _y_true_s[good_series])
  coverages = _y_pred_s / _y_true_s
  
  return coverages, good_series

def calc_ismape(y_true_s, y_pred_s, thr=0.35, margin=1, mover_thr=None, target_oos=True):
  """
  important series coverage indicator
  """
  assert len(y_true_s.shape) == 2
  _y_pred_s = np.clip(y_pred_s, 1e-7, None)
  _y_true_s = np.clip(y_true_s, 1e-7, None)
  y_true_sums = y_pred_s.sum(1)
  _res = _y_pred_s - _y_true_s 
  _abs_res = np.abs(_res)
  if target_oos:
    errors = _abs_res/ _y_pred_s
  else:
    errors = _abs_res/ _y_true_s
    
  _margin_steps = _abs_res < margin
  errors[_margin_steps] = np.minimum(errors[_margin_steps], thr - 1e-4)
  error_per_serie = errors.mean(1)
  good_series = error_per_serie <= thr

  if mover_thr is None:
    mover_thr = calculate_series_importance_thr(y_true_sums)
    
  imp_series = y_true_sums >= mover_thr
  good_series = good_series & imp_series
  # min_imp_ser = np.minimum(_y_pred_s[good_series], _y_true_s[good_series])
  # coverages = _y_pred_s / _y_true_s
  
  return error_per_serie, good_series


def calc_ovs_per(y_true_s, y_pred_s, 
                  zerocap=True, 
                  capped=None, 
                  zero_target_overstock=9.,
                  thr1=-0.1, 
                  thr2=0.5, 
                  return_qty=False,
                  small_qty_thr=1):
  _y_pred_s = np.clip(y_pred_s, 1e-7, None).squeeze()
  _y_true_s = np.clip(y_true_s, 1e-7, None).squeeze()
  if len(_y_pred_s.shape) == 1:
    _pred_per = _y_pred_s
  else:
    _pred_per = _y_pred_s.sum(axis=1)
  if len(_y_true_s.shape) == 1:
    _true_per = _y_true_s
    zero_series = y_true_s.squeeze() == 0
  else:
    _true_per = _y_true_s.sum(axis=1)
    zero_series = y_true_s.squeeze().sum(1) == 0
  
  
  qty = _pred_per - _true_per
  errors = qty / _true_per
  if capped is not None:
    errors[errors > capped] = capped
  
  # all the series that have zero sales will have a capped overstock
  errors[zero_series] = zero_target_overstock    
  
  # extract good series before eliminating understocks
  good_series = (errors >= thr1) & (errors <= thr2)
  
  # extract series with small differences that
  small_series = (qty >= -small_qty_thr) & (qty <= small_qty_thr)
  
  good_series = good_series | small_series
  
  # eliminate understocks so we dont offset overstock
  if zerocap:
    errors[errors < 0] = 0
 
  if return_qty:
    return errors, good_series, qty
  else:
    return errors, good_series

def calc_pvo(y_true_s, y_pred_s, avg_prices=None, zerocap=True, capped=None, thr1=-0.1, thr2=0.5):
  """
  calculates PVO - Period Value-based Overstock
  """
  errors, good_series, qty = calc_ovs_per(
      y_true_s=y_true_s,
      y_pred_s=y_pred_s,
      zerocap=zerocap,
      capped=capped,
      thr1=thr1,
      thr2=thr2,
      return_qty=True,
      )
  overstocks = qty.copy()
  overstocks[overstocks < 0] = 0
  nr_series = y_true_s.shape[0]
  if avg_prices is None:
    avg_prices = np.ones((nr_series, 1))
  values = overstocks * avg_prices
  return values, good_series 



def calc_por(y_true_s, 
             y_pred_s, 
             avg_prices=None, 
             day_prices=None, 
             zerocap=True, 
             capped=None, 
             thr1=-0.1, 
             thr2=0.5
             ):
  errors, good_series, qty = calc_ovs_per(
      y_true_s=y_true_s,
      y_pred_s=y_pred_s,
      zerocap=zerocap,
      capped=capped,
      thr1=thr1,
      thr2=thr2,
      return_qty=True,
      )
  nr_series, nr_steps = y_true_s.shape[:2]
  if avg_prices is None:
    avg_prices = np.ones((nr_series, 1))
  if day_prices is None:
    day_prices = np.ones((nr_series, nr_steps))
  
  if y_pred_s.shape[1] == 1:
    _pred_revs = np.clip(y_pred_s, 1e-7, None).squeeze() * avg_prices
  else:
    _pred_revs = (np.clip(y_pred_s, 1e-7, None).squeeze() * day_prices).sum(1)
  
  return _pred_revs, good_series


def calc_timeseries_error(y_true, y_pred, metric, ERR_MAX=2, 
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
    
    func_aggregate_dict: dict where func_aggregate_dict[metric]['AGGREGATE'] contains a aggregation function
      that returns a scalar based on a batch of series individual errors


  returns:
      (tuple) (`score`,`errors`,`good_series`) where `score` is the aggregated score (based on metric aggregation func),
      `errors` is the per serie error and `good_series` (if `return_good_series==True`) bool vector

  """
  
  if check_shapes:
    if y_true.shape != y_pred.shape:
      raise ValueError("Shapes of y_true {} and y_pred {} do not match".format(
        y_true.shape, y_pred.shape))
    if ((len(y_true.shape) != 3) or (len(y_pred.shape) != 3) or
        (y_true.shape[-1] != 1) or (y_pred.shape[-1] != 1)):
      raise ValueError("Shapes of y_true and y_pred must be [batch, time-steps, 1]")
  
  metric = metric.lower()
  if y_pred.shape[-1] == 1:
    y_pred = np.squeeze(y_pred, axis=-1)
  if y_true.shape[-1] == 1:
    y_true = np.squeeze(y_true, axis=-1)
  residual = y_pred - y_true
  if metric == 'mape2':
    result = np.abs(residual) / np.clip(np.abs(y_pred), 1e-7, None)
    result = result.mean(axis=-1)
    good_series = result <= ERR_THR
  elif metric == 'mape21':
    _abs_res = np.abs(residual)
    _margin_positions = _abs_res <= 1
    result = _abs_res / np.clip(np.abs(y_pred), 1e-7, None)
    result[_margin_positions] = ERR_THR
    result = result.mean(axis=-1)
    good_series = result <= ERR_THR
  elif metric == 'rmse':
    result = np.sqrt(residual ** 2)
    result = result.mean(axis=-1)
    good_series = result <= ERR_THR
  elif metric == 'smape':
    result = (2 * np.abs(residual)) / np.clip(np.abs(y_true) + np.abs(y_pred), 1e-7, None)
    result = result.mean(axis=-1)
    good_series = result <= ERR_THR
  elif metric == 'mape':
    result = np.abs(residual) / np.clip(np.abs(y_true), 1e-7, None)
    result = result.mean(axis=-1)
    good_series = result <= ERR_THR
  elif metric == 'cme1':
    if model == 'lin3_30_7':
      __debug = True
    _result = np.abs(residual) / np.clip(np.abs(y_true), 1e-7, None)
    _result[_result > 1] = 1
    result = _result.mean(axis=-1)
    good_series = result <= ERR_THR
  elif metric == 'ppd':
    y_true_s = np.sum(y_true, axis=1)
    y_pred_s = np.sum(y_pred, axis=1)
    result, good_series = calc_ppd(
      y_true_s=y_true_s, 
      y_pred_s=y_pred_s,
      thr=ERR_THR,
      margin=0)

  elif metric == 'pdt':
    y_true_s = np.sum(y_true, axis=1)
    y_pred_s = np.sum(y_pred, axis=1)
    result, good_series = calc_pdt(
      y_true_s=y_true_s, 
      y_pred_s=y_pred_s,
      thr=ERR_THR,
      margin=0)

  elif metric == 'ppd5':
    y_true_s = np.sum(y_true, axis=1)
    y_pred_s = np.sum(y_pred, axis=1)
    result, good_series = calc_ppd(
      y_true_s=y_true_s, 
      y_pred_s=y_pred_s,
      thr=ERR_THR,
      margin=5)
    
  elif metric == 'ismape':
    result, good_series = calc_ismape(
      y_true_s=y_true,
      y_pred_s=y_pred,
      thr=ERR_THR,
      margin=1,
      target_oos=True,
      mover_thr=None,
      )
    
  elif metric == 'isc' or metric == 'isco':
    y_true_s = np.sum(y_true, axis=1).ravel()
    y_pred_s = np.sum(y_pred, axis=1).ravel()
    result, good_series = calc_isc(
      y_true_s=y_true_s, 
      y_pred_s=y_pred_s,
      thr=ERR_THR,
      margin=0,
      overstock=True)
    
  elif metric == 'iscu':
    y_true_s = np.sum(y_true, axis=1).ravel()
    y_pred_s = np.sum(y_pred, axis=1).ravel()
    result, good_series = calc_isc(
      y_true_s=y_true_s, 
      y_pred_s=y_pred_s,
      thr=ERR_THR,
      margin=0,
      overstock=False)
    
  elif metric == 'r2':
    SS_res =  np.sum(np.square(residual), axis=1)
    yt_means = np.mean(y_true, axis=1, keepdims=True)
    SS_tot = np.sum(np.square(y_true - yt_means), axis=1)
    SS_tot = np.clip(SS_tot, 1e-7, None)
    result = 1 - SS_res/SS_tot
    good_series = result >= ACC_THR
    
  elif metric == 'pqo':
    _errs, _good, _qty = calc_ovs_per(
        y_true_s=y_true,
        y_pred_s=y_pred,
        return_qty=True,
        thr1=-0.1,
        thr2=ERR_THR,
        zerocap=True,
        capped=None,
        )      
    result = _errs
    good_series = _good
    ERR_MAX = np.inf
  elif metric == 'pvo':
    result, good_series = calc_pvo(
        y_true_s=y_true,
        y_pred_s=y_pred,
        thr1=-0.1,
        thr2=ERR_THR,
        zerocap=True,
        capped=None
        )
    ERR_MAX = np.inf
  elif metric == 'por':
    result, good_series = calc_por(
        y_true_s=y_true,
        y_pred_s=y_pred,
        thr1=-0.1,
        thr2=ERR_THR,
        zerocap=True,
        capped=None
        )
    ERR_MAX = np.inf
  else:
    raise ValueError("Unknown metric '{}'".format(metric))
    
  result = np.clip(result,-ERR_MAX, ERR_MAX)
  if func_aggregate_dict is None:
    agg_func = agg_mean
  else:
    agg_func = func_aggregate_dict[metric]['AGGREGATE']
  if return_good_series:
    return agg_func(values=result, good_series=good_series), result, good_series
  else:
    return agg_func(values=result, good_series=good_series), result

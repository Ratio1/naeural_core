import numpy as np
import pandas as pd
from collections import namedtuple


__VER__ = '0.2.0.0'


###
### PLOT UTILS
###


def plot_autoreg(np_train, 
                 np_test, 
                 np_forecast,
                 title,
                 forecast_title='LumAR',
                 subtitles=None,
                 other_forecasts=[],
                 other_forecasts_titles=[],
                 save_with_log=None,
                 save_and_show=True,
                 log_y=False,
                 hist_vlines=None,
                 figsize=(21,13),
                 hspace=0.15,
                 ma_history=None,
                 ma_target=None
                 ):
  """
  Plot time-series with backlog, forecasted values and reality

  Parameters
  ----------
  np_train : ndarray
    the backlog data (training data).
    
  np_test : ndarray
    the test/reality of the forecast period.
    
  np_forecast : ndarray
    the predictions.
    
  title : str
    name of the whole graph.
    
  subtitles : list[str], optional
    name of each sub-plot. The default is None.
    
  other_forecasts : list of ndarrays, optional
    if we want to compare with another auto-regressors we can put here
    its predictions. The default is [].

  other_forecasts_titles: list, optional
    title for each other forecast.
    The default is [].
    
  save_with_log : Logger, optional
    the Logger object that will save the plot if supplied. The default is None.
    
  log_y: bool, optional,
    log y axis if True, default is False
    
  hist_vlines: list[list], optional
    where to draw vertical lines on x axis (for historical points in autoregs) in each 
    individual subgraph plot. Each point x in each list of lines represent -x point relative
    from the end of history. Default is None

  hspace: float, optional
    the size in fig percent of the space between subplots
    
  figsize: tuple floats, optional
    default width and height (height for each subgraph)

  ma_history: ...
    ...

  ma_target: ...
    ...

  Raises
  ------
  ValueError
    DESCRIPTION.

  Returns
  -------
  None.

  """

  static_list_colors_other_forecasts = [
    'c--', 'm--', 'p--', 'y--', 'k--'
  ]

  import matplotlib.pyplot as plt
  plt.style.use('ggplot')
  
  if type(np_train) != np.ndarray:
    raise ValueError("`np_train` must be ndarray")
  if type(np_test) != np.ndarray:
    raise ValueError("`np_test` must be ndarray")
  if type(np_forecast) != np.ndarray:
    raise ValueError("`np_forecast` must be ndarray")

  np_train = np_train.squeeze()
  np_test = np_test.squeeze()
  np_forecast = np_forecast.squeeze()  
  if len(np_train.shape) == 1:
    np_train = np_train.reshape(1,-1)
    np_test = np_test.reshape(1,-1)
    np_forecast = np_forecast.reshape(1, -1)    

  assert np_test.shape == np_forecast.shape, "test and forecasts must have same shape"
  assert np_train.shape[0] == np_test.shape[0], "train and test have different no of series"
  assert type(other_forecasts) is list

  if len(other_forecasts) > 0:
    for _np_forecast in other_forecasts:
      assert _np_forecast.shape == np_test.shape, "other forecast must match test data"
    
  if subtitles is not None and len(subtitles) != np_train.shape[0]:
    raise ValueError("Must have 1 subtitle per subgraph ({} != {}".format(subtitles, np_train.shape[0]))


  hist_len = np_train.shape[1]
  test_len = np_test.shape[1]  
  full_len = hist_len + test_len
  n_series = np_train.shape[0]

  np_train_show = np_train.copy()
  np_test_show = np_test.copy()
  np_forecast_show = np_forecast.copy()
  other_forecasts_show = other_forecasts

  if len(other_forecasts_titles) == 0:
    other_forecasts_titles = ['OthAR{}'.format(idx_other_f)
                              for idx_other_f in range(len(other_forecasts_show))]
  #endif

  other_forecasts_titles = list(map(lambda x: str(test_len)+'-steps-{}'.format(x), other_forecasts_titles))
  forecast_title = str(test_len)+'-steps-{}'.format(forecast_title)

  assert len(other_forecasts_titles) == len(other_forecasts_show)

  EPS_LOG = 0.5
  
  if log_y:
    np_train_show = np.log(np_train_show + EPS_LOG)
    np_test_show = np.log(np_test_show + EPS_LOG)
    np_forecast_show = np.log(np_forecast_show + EPS_LOG)
    other_forecasts_show = list(map(lambda x: np.log(x + EPS_LOG), other_forecasts_show))
  #endif

  FIG_W, FIG_H = figsize
  
  fig, axes =  plt.subplots(n_series,1, figsize=(FIG_W,FIG_H * n_series))
  if type(axes) not in [list, np.ndarray]:
    axes = [axes]
  for i in range(n_series):
    axes[i].plot(np.arange(hist_len), np_train_show[i], 'k-', label='real past')
    axes[i].plot(
      np.arange(hist_len, full_len), 
      np_test_show[i], 
      'r-', 
      linewidth=1.0,
      label='real future')
    axes[i].plot(
      np.arange(hist_len, full_len), np_forecast_show[i], 'b-',
      linewidth=2.0,
      label=forecast_title
      )
    if log_y:
      axes[i].set_ylabel('Log scale')
    if len(other_forecasts_show) > 0:
      for idx_other_f, np_other_forecast_show in enumerate(other_forecasts_show):
        axes[i].plot(
          np.arange(hist_len, full_len),
          np_other_forecast_show[i],
          '{}'.format(static_list_colors_other_forecasts[idx_other_f]),
          label=other_forecasts_titles[idx_other_f],
          linewidth=3.0,
          )
    _ymin = min(np_train[i].min(), np_forecast_show[i].min())
    _ymax = max(np_train[i].max(), np_forecast_show[i].max())
    axes[i].vlines(hist_len, ymin=_ymin, ymax=_ymax, linestyle='--', colors='g')
    if hist_vlines is not None:
      if len(hist_vlines) > i:
        ser_vlines = hist_vlines[i]
        for pnt in ser_vlines:
          axes[i].vlines(hist_len - pnt, ymin=_ymin, ymax=_ymax, linestyle='--', colors='k')
          axes[i].text(
            hist_len - pnt, np_train[i].max(), 
            str(- pnt), 
            ha="center", va="center",
            rotation=45
            )
          # axes[i].annotate(str(- pnt), (hist_len - pnt, np_train[i].max()))
    # axes[i].annotate(str(hist_len), (hist_len, np_train[i].max()))
    axes[i].text(
      hist_len, np_train[i].max(), str(0), 
      ha="center", va="center",
      rotation=45
      )
    if subtitles is not None:
      _subtitle = subtitles[i]
    else:
      _subtitle = 'Series {}/{}'.format(i+1, n_series)
    if '\n' not in _subtitle:
      _subtitle += '\n Total forecast={:.2f},  reality={:.2f}, max_hist={:.2f}'.format(
        np_forecast[i].sum(),
        np_test[i].sum(),
        np_train[i].max()
        )
    axes[i].set_title(_subtitle)
    axes[i].legend(loc='center left')
  fig.suptitle(title + '\n', fontsize=22)  
  if save_with_log is not None:
    save_with_log.add_copyright_to_plot(plt)
  else:
    plt.annotate(
      "ts v{} local".format(__VER__),
      xy=(1, -0.07), xytext=(0, 2),
      xycoords=('axes fraction'),
      textcoords='offset points',
      size=9, ha='right', va='top',   
      bbox=dict(boxstyle="round,pad=0.3", fc="lightgray", ec="lightgray", lw=1),
      )
  plt.tight_layout()
  offset = max(0,(3-n_series) * 0.06)
  fig.subplots_adjust(top=0.95 - offset, hspace=hspace)
  if save_with_log is not None:
    title_fn = title.replace(' ','_').replace('.','_').replace('%','').replace(':','')
    save_with_log.save_plot(plt, label=title_fn, include_prefix=False)
    if not save_and_show:
      plt.close()
      return
  plt.show()
  return
  

def plot_ts(np_values, ma=[7, 14, 30], trend_window=None):
  print("WARNING: `plot_ts` is obsolete. please use `plot_ts_ma`")
  plot_ts_ma(np_values, ma, trend_window)
  
def plot_ts_ma(np_values, ma=[7, 14, 30], trend_window=None):
  import matplotlib.pyplot as plt
  plt.style.use('ggplot')
  
  styles = ['b-', 'g-','k-']
  assert len(ma) <= 3
  n_obs = np_values.shape[0]
  plt.figure(figsize=(13,8)) # 8x5
  plt.plot(np_values, 'r-', label='signal')
  for i, _ma in enumerate(ma):
    if n_obs < _ma:
      continue    
    mavals = moving_averages(np_values, window=_ma)
    plt.plot(mavals, styles[i], label='ma-'+str(_ma))
  plt.title("Timeseries analysis")
  
  if trend_window is None:
    trend_len = min(np_values.shape[0], max(ma))
  else:
    assert trend_window < n_obs
    trend_len = trend_window
  
  # MUST PUT THE TREND WHERE IT SHOULD BE
  # must start from history not from first item
  np_trend_vals = get_series_trends(
    np_values.reshape(1,-1), 
    last_steps=trend_len)
  np_trend_ticks = np.arange(n_obs - trend_len, n_obs)
  np_trend_vals = np_trend_vals.squeeze()[-trend_len:]
  plt.plot(np_trend_ticks, np_trend_vals, 'k--', label='last_{}_trend'.format(trend_len))  
  plt.legend()
  plt.annotate(
    "local",
    xy=(1, -0.07), xytext=(0, 2),
    xycoords=('axes fraction'),
    textcoords='offset points',
    size=9, ha='right', va='top',   
    bbox=dict(boxstyle="round,pad=0.3", fc="lightgray", ec="lightgray", lw=1),
    )

  plt.show()
  return


###
### ARIMA UTILS
###


def get_max_lag(data):
  n_obs = len(data)
  n_ser = 1
  # max_k = np.floor(10 * (np.log10(n_obs) - np.log10(n_ser))).astype(int)
  max_k = n_obs    
  return max_k
  

def acf(data, k, max_len=None):
  """
  Autocorrelation function

  :param data: time series
  :param k: lag
  :return:
  """
  m = np.mean(data)
  s1 = 0
  if max_len is None:
    max_len = len(data)
  assert k < max_len
  for i in range(k, max_len):
      s1 = s1 + ((data[i] - m) * (data[i - k] - m))

  s2 = 0
  for i in range(0, max_len):
      s2 = s2 + ((data[i] - m) ** 2)

  return float(s1 / s2)

def acf_limit(acfs, series_len, k):
  """  
  Returns confidence interval for lag k based on precalculated ACFs up to k

  Parameters
  ----------
  acfs : TYPE
    list or ndarray with precalculated acfs
  series_len : TYPE
    len of series
  k : TYPE
    lag value

  Returns
  -------
  float - confidence value (to be compared with acf @ k)    

  """
  if k==0:
    return 0
  s = acfs[1]
  for i in range(2, k):
      s = s + (acfs[i] ** 2)
  limit = 1.645 * (np.sqrt((1 + 2 * s) / series_len))  
  return limit


def acf_plot(data, max_k=None, plot=True, seas_as_lag=False, max_len=None):
  return calc_acfs(
    np_data=data,
    max_k=max_k,
    plot=plot,
    seas_as_lag=seas_as_lag,
    max_len=max_len,
    )

def calc_acfs(np_data, max_k=None, plot=False, seas_as_lag=False, max_len=None):
  """
  Plots and returns auto-correlation function and 
  returns values, confidence intervals and seasonality signals

  Parameters
  ----------
  data : ndarray
    1d time-series
  max_k : int, optional
    max lag. The default is None.
  plot : bool, optional
    generate pyplot, default True
  seas_as_lag : bool, optional (False)
    will output `seas` as lag values

  Returns
  -------
    tuple(3): acfs, confs, seas:
      acfs: values of autocorr functions for lag=1...
      confs: conf values for autocorr lag=1...
      seas: bools or valid lag values 


  """
  if plot:
    import matplotlib.pyplot as plt
    plt.style.use('ggplot')
  
  if max_len is not None and max_len < len(np_data):
    data = np_data[-max_len:]
  else:
    data = np_data
    
  n_obs = len(data)

  if max_k is None:
    max_k = get_max_lag(data)
    
  
  acfs = []
  conf = []
  for k in range(max_k):
    acfs.append(acf(data, k, max_len=max_len))
    conf.append(acf_limit(acfs=acfs, series_len=n_obs, k=k))

  np_conf = np.array(conf)    
  np_acfs = np.array(acfs)
  np_season = np.abs(np_acfs) > np_conf
    
  if plot:
    np_k = np.arange(max_k)
    np_bad_k = np_k[~np_season]
    np_bad_acf = np_acfs[~np_season]
    np_good_k = np_k[np_season]
    np_good_acf = np_acfs[np_season]
    plt.figure(figsize=(13,8))
    plt.vlines(range(max_k),[0],acfs)
    plt.plot(np_bad_k, np_bad_acf, "bo")
    plt.plot(np_good_k, np_good_acf, "go")
    plt.fill_between(range(1, max_k), y1=np_conf[1:], y2=-np_conf[1:], alpha=0.25, label='low conf area')  
    _ymin = -1
    _ymax = 1
    plt.vlines(np_good_k.max(), ymin=_ymin, ymax=_ymax, color='g')
    plt.annotate(" Max lag={}".format(np_good_k.max()), (np_good_k.max(), _ymin), color='g')
    plt.title('Autocorrelation function with seasonality signals')
    plt.xlabel('Lag')
    plt.ylabel('Autocorrelation')
    plt.annotate(
      "local",
      xy=(1, -0.07), xytext=(0, 2),
      xycoords=('axes fraction'),
      textcoords='offset points',
      size=9, ha='right', va='top',   
      bbox=dict(boxstyle="round,pad=0.3", fc="lightgray", ec="lightgray", lw=1),
      )
    plt.show()
    
  if seas_as_lag:  
    return np_acfs[1:], np_conf[1:], (np.argwhere(np_season[1:]) + 1).ravel()
  else:
    return np_acfs[1:], np_conf[1:], np_season[1:]


def get_valid_lags(data, verbose=True, max_k=None, return_acf=False):
  np_acfs, np_confs, np_seas = calc_acfs(
    data=data, 
    max_k=max_k,
    )
  np_seas = np.arange(1, np_seas.shape[0]+1)[np_seas]
  if verbose:
    for _s in np_seas:
      print("  has seasonality={}".format(_s))
  if return_acf:
    return np_seas, np_acfs[np_seas]
  else:
    return np_seas


def rolling_mean(ts_init, window, center=True):
  ser = pd.Series(ts_init)
  return ser.rolling(window, center=center).mean()

def moving_averages(ts_init, window): 
  if len(ts_init) % 2 == 0:
      ts_ma = rolling_mean(ts_init, window, center=True)
      ts_ma = rolling_mean(ts_ma, 2, center=True)
      ts_ma = np.roll(ts_ma, -1)
  else:
      ts_ma = rolling_mean(ts_init, window, center=True)

  return ts_ma


def calc_trend_coefs(series, deg=1, last_steps=None):
  if len(series.shape) == 3 and series.shape[-1] ==1:
    series = series.reshape(series.shape[:-1])
  assert len(series.shape) == 2, "Series must be (nr_series, nr_steps,) but got {}".format(series.shape)
  if last_steps is not None:
    np_y = series[:,-last_steps:]
  else:
    np_y = series
  x = np.arange(np_y.shape[1])
  coefs = np.polyfit(x, np_y.T, deg=deg)  
  return coefs.T


def get_series_trends(series, last_steps=None, k=1):
  np_series = np.array(series)
  n_series = np_series.shape[0]
  nr_steps = np_series.shape[1] if last_steps is None else last_steps
  coefs = calc_trend_coefs(np_series, deg=k, last_steps=last_steps)
  np_ticks = np.arange(nr_steps) 
  np_trends = np.zeros((n_series, nr_steps))  
  for deg in range(k+1):
    np_trends += np_ticks ** (k-deg) * coefs[:,deg].reshape(-1,1)
  return np_trends

def get_trend_preds(series, nr_steps, prev_window=None, k=1):
  np_series = np.array(series)
  n_series = np_series.shape[0]
  assert len(np_series.shape) >= 2, "Series must be (N, S) or (N, S, 1)"
  if prev_window is not None:
    assert prev_window <= np_series.shape[1], "Auto-correlation window must be smaller than actual series size"
  coefs = calc_trend_coefs(np_series, deg=k, last_steps=prev_window)
  start_tick = prev_window if prev_window is not None else np_series.shape[1]
  np_ticks = np.arange(start_tick, start_tick + nr_steps)
  np_preds = np.zeros((n_series, nr_steps))  
  for deg in range(k+1):
    np_preds += np_ticks ** (k-deg) * coefs[:,deg].reshape(-1,1)
  return np_preds


def get_linear_preds(series, nr_steps, prev_window=None, k=1):
  return get_trend_preds(series, nr_steps, prev_window=prev_window, k=k)



def pd_model(X_data, value_tensor_idx, n_steps, 
             params=None, DEBUG=False, verbose=False,
             **kwargs):
  """
  Standard definition for PD models

  Parameters
  ----------
  X_data : TYPE
    DESCRIPTION.
  value_tensor_idx : TYPE
    DESCRIPTION.
  n_steps : TYPE
    DESCRIPTION.
  params : TYPE, optional
    DESCRIPTION. The default is None.
  DEBUG : TYPE, optional
    DESCRIPTION. The default is False.
  verbose : TYPE, optional
    DESCRIPTION. The default is False.
  **kwargs : TYPE
    DESCRIPTION.

  Returns
  -------
  None.

  """
  return None



def create_lm_mat_1(np_vect, np_lags, add_trend=True, dtype='float32'):
  x = np_vect.copy().reshape(-1,1)
  n_obs = x.shape[0]
  maxlag = np.max(np_lags)
  full_mat = np.zeros((maxlag + n_obs, maxlag + 1), dtype=dtype)
  for i in range(0, int(maxlag+1)):
    full_mat[maxlag-i:maxlag-i+n_obs, maxlag-i:maxlag-i+1] = x
  # delete zero-padded 
  full_mat = full_mat[maxlag:n_obs]
  x_data = full_mat[:,1:]
  y_data = full_mat[:,:1]
  # now slice only our lags
  x_data = x_data[:, np_lags - 1]
  if add_trend:
    np_trend = np.arange(maxlag+1, n_obs+1, dtype=dtype).reshape(-1,1)
    x_data = np.hstack((np_trend, x_data))
  return x_data, y_data

def create_lm_mat_2(np_vect, np_lags, add_trend=True, dtype='float32'):
  x = np_vect.copy()
  n_obs = x.shape[0]
  n_lags = len(np_lags)
  maxlag = np.max(np_lags)
  n_train = n_obs - maxlag
  full_mat = np.zeros((n_obs - maxlag, maxlag + 1), dtype=dtype)
  for i in range(n_train):
    start = i
    end = i + maxlag + 1
    full_mat[i] = x[start:end][::-1]
  x_data = full_mat[:,1:]
  if maxlag != n_lags:
    x_data = x_data[:, np_lags -1]
  y_data = full_mat[:,:1]
  if add_trend:
    np_trend = np.arange(maxlag+1, n_obs+1, dtype=dtype).reshape(-1,1)
    x_data = np.hstack((np_trend, x_data))    
  return x_data, y_data

def create_lm_mat(np_vect, np_lags, dtype='float32', add_trend=True):
  if len(np_vect.shape)>=2:
    if np_vect.shape[1] != 1:
      raise ValueError("np_vect must be (M,) or (M,1)")
    else:
      np_vect = np_vect.ravel()
    
  if np_lags.max() != len(np_lags):
    return create_lm_mat_2(
      np_vect,
      np_lags,
      add_trend,
      dtype=dtype,
      )
  else:
    return create_lm_mat_1(
      np_vect,
      np_lags,
      add_trend,
      dtype=dtype,
      )
  

STANDARD_FIRST_SALE_CONFIDENCES = {
      0.10 : {
        'interval' : [0,60], 
        'description' : 'Minimal confidence for sales that started max 60 days ago'
        },

      0.20 : {
        'interval' : [61,90],
        'description' : 'Very low confidence for sales that started max 60-90 days ago'
        },

      0.35 : {
        'interval' : [91,180],
        'description' : 'Low confidence for sales that started 90-180 days ago'
        },

      0.50 : {
        'interval' : [181,360],
        'description' : 'Average confidence for sales that started 181-360 days ago'
        },

      0.70 : {
        'interval' : [361,500],
        'description' : 'Superior confidence for sales that started 361-500 days ago'
        },

      0.90 : {
        'interval' : [500, np.inf],
        'description' : 'Max confidence for sales that started 500+ days ago'
        },

      }

STANDARD_LAST_SALE_CONFIDENCES = {
      0.10 : {
        'interval' : [180,np.inf], 
        'description' : ''
        },

      0.30 : {
        'interval' : [90,180],
        'description' : ''
        },

      0.40 : {
        'interval' : [60,90],
        'description' : ''
        },

      0.50 : {
        'interval' : [30,60],
        'description' : ''
        },

      0.70 : {
        'interval' : [10,30],
        'description' : ''
        },

      0.90 : {
        'interval' : [0, 10],
        'description' : ''
        },

      }
  
DEBUG_FIRST_SALE_CONFIDENCES = {
      0.10 : {
        'interval' : [0,5], 
        'description' : 'Minimal confidence for sales that started max 60 days ago'
        },

      0.20 : {
        'interval' : [6,9],
        'description' : 'Very low confidence for sales that started max 60-90 days ago'
        },

      0.35 : {
        'interval' : [10,15],
        'last_sale_before_today' : None,
        'description' : 'Low confidence for sales that started 90-180 days ago'
        },

      0.50 : {
        'interval' : [16,20],
        'description' : 'Average confidence for sales that started 181-360 days ago'
        },

      0.70 : {
        'interval' : [21,30],
        'description' : 'Superior confidence for sales that started 361-500 days ago'
        },

      0.90 : {
        'interval' : [31, np.inf],
        'description' : 'Max confidence for sales that started 500+ days ago'
        },

      }




###
### SERIES DATA UTILS
###


def get_random_series(nr_series=50000, nr_points=750, fake_preds=False, noise_max=None):
  from . import artificial_data

  print("\x1b[1;31m" + "WARNING: `get_random_series` is obsolete! Please use artificial_data.generate_artificial_series()" + "\x1b[0m")
  return artificial_data.get_basic_random_series(
    nr_series=nr_series,
    nr_points=nr_points,
    fake_preds=fake_preds,
    noise_max=noise_max,
    )


def get_daily_series_confidence(np_series, 
                                first_day_confidence_levels=None,
                                last_day_confidence_levels=None,
                                debug=False):
  """
  This method generates a confidence probability for a range o given timeseries
  based on the availablity of data within each series.
  

  Parameters
  ----------
  np_series : np.ndarray
    DESCRIPTION.
  fist_day_confidence_levels : dict, optional
    Confidence levels based on first day with sales in timeseries. The default is None.
  last_day_confidence_levels : dict, optional
    Confidence levels based on last day with sales in timeseries. The default is None.
  debug : bool, optional
    DESCRIPTION. The default is False.

  Returns
  -------
  np_conf_mod : np.ndarray
    (N_SERIES,) vector with confidences levels

  """
  
  assert type(np_series) == np.ndarray, "np_series must be ndarray"
  np_series = np.squeeze(np_series)
  if len(np_series.shape) == 1:
    np_series = np_series.reshape(1,-1)
  assert len(np_series.shape) == 2, "np_series must be [N_SERIES, N_HIST_STEPS]"
  
  if first_day_confidence_levels is None:
    if debug:
      first_day_confidence_levels = DEBUG_FIRST_SALE_CONFIDENCES
    else:
      first_day_confidence_levels = STANDARD_FIRST_SALE_CONFIDENCES
  
  if last_day_confidence_levels is None:
    if not debug:
      last_day_confidence_levels = STANDARD_LAST_SALE_CONFIDENCES
    
  np_non_zero = np_series != 0
  n_days = np_series.shape[1]
  n_series = np_series.shape[0]
  first_sale_before_today = n_days - np_non_zero.argmax(1)
  last_sale_before_today = np_non_zero[:,::-1].argmax(1)
  n_sales_start_end = first_sale_before_today - last_sale_before_today
  # sparsity is relative to the approx history size for each series
  sparsity = np_non_zero.sum(1) / n_sales_start_end
  first_day_confidences = np.zeros(n_series)
  last_day_confidences = np.ones(n_series)
  for ser in range(n_series):
    ser_first = first_sale_before_today[ser]
    ser_last = last_sale_before_today[ser]    
    for conf_first_day in first_day_confidence_levels:
      cnf_first = first_day_confidence_levels[conf_first_day]['interval']
      found = False
      if cnf_first[0] <= ser_first and cnf_first[1] >= ser_first:
        found = True
        break
    if found:
      first_day_confidences[ser] = conf_first_day
    # now for second day
    if last_day_confidence_levels is not None:
      for conf_last_day in last_day_confidence_levels:
        cnf_last = last_day_confidence_levels[conf_last_day]['interval']
        if cnf_last[0] <= ser_last and cnf_last[1] >= ser_last:
          found=True
          break
      if found:
        last_day_confidences[ser] = conf_last_day
        
  np_conf_mod = first_day_confidences * last_day_confidences * sparsity
  if debug:
    print("Non zero count:      {}".format(np_non_zero.sum(1)))
    print("Sarsity confidence:  {}".format(sparsity))
    print("First sale days ago: {}".format(first_sale_before_today))
    print("Confidence 1st day:  {}".format(first_day_confidences))
    print("Confidence last day: {}".format(last_day_confidences))
    print("Final confidence:    {}".format(np_conf_mod))
  return np_conf_mod



def get_past_periods_test_datasets(X, steps, freq):
  """
  generates tuple with test datasets
  """
  __RULES = {
    'M' : {
        'PERIODS' : [12, 24],
      },
    'W' : {
        'PERIODS' : [4, 52, 104],
      },
    'D' : {
        'PERIODS' : [30, 365, 730],
      },
    
    }
  TestData = namedtuple(
    'TestData', 
    ['X', 'y', 'look_back', 'freq']
    )
  freq = freq.upper()[0]
  if freq not in __RULES:
    raise ValueError("Past periods analysis for '{}' not implemented!".format(freq))
  periods = __RULES[freq]['PERIODS']
  if steps not in periods:
    periods = [steps] + periods
  result = []
  for p in periods:
    if p >= steps and X.shape[1] > p:
      start = -p
      end = (-p + steps) if steps < p else None
      np_per = X[:, start:end]
      x_data = X[:,:start]
      y_data = np_per
      # if x_data.shape[1] >= y_data.shape[1]:
      d = TestData(
        X=x_data,
        y=y_data,
        look_back=p,
        freq=freq,
        )
      result.append(d)        
  return result
  
          
        
        
  
if __name__ == '__main__':
  np.set_printoptions(
    suppress=True,
    # precision=3,
    # floatmode='fixed',
    threshold=1000,
    linewidth=1000,
    )
  #
  # test series confidence
  #
  # a = np.random.randint(0,5, size=(10,30))
  # a[0,:-4] = 0
  # a[1,:-8] = 0
  # a[2,:-15] = 0
  # a[3,::2] = 0
  # get_daily_series_confidence(
  #   np_series=a,
  #   first_day_confidence_levels=None,
  #   last_day_confidence_levels=None,
  #   debug=True,
  #   )
  #
  # test slicing
  #
  M = np.array([np.arange(0.01,0.13, step=0.01)] * 2)
  M = np.concatenate((M+1,M+2,M+3), axis=-1)[:,:-5]
  W = np.array([np.arange(0.01,0.53, step=0.01)] * 2)
  W = np.concatenate((W+1,W+2,W+3), axis=-1)[:,:-10]
  D = np.array([np.arange(0.001,0.366, step=0.001)] * 2)
  D = np.concatenate((D+1,D+2,D+3), axis=-1)[:,:-50]

  vm = get_past_periods_test_datasets(
    X=M,
    steps=12,
    freq='M'
    )

  
  vw = get_past_periods_test_datasets(
    X=W,
    steps=8,
    freq='W'
    )
  
  vd = get_past_periods_test_datasets(
    X=D,
    steps=15,
    freq='D'
    )
  
  
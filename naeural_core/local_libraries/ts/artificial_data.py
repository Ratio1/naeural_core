import numpy as np
import itertools
from collections import OrderedDict
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

__VER__ = '0.1.1.0'

def get_basic_random_series(nr_series=50000, nr_points=750, fake_preds=False, noise_max=None):
  """
  Random series generator

  Parameters
  ----------
  nr_series : int, optional
    number of series. The default is 50000.
  nr_points : int, optional
    number of steps in each serie. The default is 750.
  fake_preds : bool, optional
    return also dummy predictions. The default is False.
  noise_max : bool, optional
    put a predefined max noise. The default is None.

  Returns
  -------
  res : ndarray
    the generated series.

  """
  if nr_series is None:
    nr_series = 50000
  funcs = [np.cos, np.sin]  
  x_signals = []
  y_signals = []
  fakes = []
  if noise_max is None:
    fake_delta = 1
  else:
    fake_delta = noise_max
  outlier_prc = 0.25
  funnels = []
  print_every = 1000 if nr_series >= 10000 else None
  for i in range(nr_series):
    step = np.random.choice(np.arange(0.01, 0.4, step=0.01),size=1)
    func = funcs[np.random.randint(len(funcs))]
    start = np.random.randint(10)
    np_base = np.arange(start,(start+1000)*np.pi,step)[:nr_points]
    np_outl = np.random.rand(nr_points)
    np_outl = (np_outl > (1 - outlier_prc)).astype(int)
    np_outv = np.random.uniform(fake_delta, 2*fake_delta, size=nr_points)    
    np_outv *= np_outl
    np_base = func(np_base) + 2*fake_delta
    np_fake = np.zeros((nr_points, 2))
    np_fake += np_base.reshape((nr_points,1)) - fake_delta
    np_fake[:,1] += fake_delta*2
    if noise_max is None:
      noise_size = np.random.rand()
    else:
      noise_size = np.random.uniform(0, noise_max)
    np_signal = np_base + np.random.uniform(-noise_size,noise_size, size=np_base.shape) #np.random.rand(*np_base.shape)
    np_signal += np_outv
    y_signals.append(np_signal.reshape((-1,1)))  
    x_signals.append(np.array([0] + np_signal[:-1].tolist()).reshape((-1,1)))
    fakes.append(np_fake)
    funnels.append(np.repeat(i, nr_points))
    if print_every is not None and (i % print_every) == 0:
      print("\rGenerating series {:.1f}%\r".format((i+1)/nr_series * 100), flush=True, end="")
  print("\rGenerating ndarrays...", flush=True, end="")  
  yt = np.array(y_signals)  
  x_feat = np.array(x_signals)
  x_funnel = np.array(funnels)
  ypf = np.array(fakes)
  res = (x_feat, x_funnel, yt, ypf) if fake_preds else (x_feat, x_funnel, yt)
  print("\rDone generating pseudo-series...\r", flush=True, end="")  
  return res


def get_random_series_batch(periods, n_points, random_start=True):
  if type(periods) not in [list, np.ndarray]:
    raise ValueError("periods must be a vector or list(nr_series) with all series periods")
  np_periods = np.array(periods).reshape((-1,1))
  n_series = np_periods.shape[0]
  np_steps = (2 * np.pi) / np_periods
  rnd_offset = np.random.randint(low=0, high=np_periods.max(), size=(n_series,1)) if random_start else 0 
  np_x = np.array([np.arange(0, n_points) for _ in range(len(periods))])
  np_x += rnd_offset
  np_x = np_x * np_steps
  np_y = np.sin(np_x)
  np_y = np_y + np_y.max(axis=1).reshape((-1,1))
  return np_y

def compose_series(np_series):
  lst_series_components = []
  for nr_series_composed in range(2, np_series.shape[0]+1):
    lst_series_components += list(itertools.combinations(list(range(np_series.shape[0])), nr_series_composed))

  lst_composed_series = []
  for components in lst_series_components:
    np_complex_series = np.zeros((np_series.shape[-1]))  
    for component in components:
      np_complex_series += np_series[component,:]
    lst_composed_series.append(np_complex_series)
  np_composed_series = np.array(lst_composed_series)
  
  return np_composed_series, lst_series_components
  
def generate_artificial_series(n_series, 
                               n_steps, 
                               freq, 
                               max_sales, 
                               encode_date_time=True,
                               noise_size=2,
                               random_peaks=False,
                               ):
  """
  TODO:
    - add normalization option for 0-1 target
    - add (semi) non-linear patterns
  
  This function generates a batch of time-series simulated sales.

  Parameters
  ----------
  n_series : int
    number of series.
    
  n_steps : int
    number of history steps.
    
  freq : str
    type of series: 'D' means daily, 'W' means weekly, 'M' means monthly.
    
  max_sales: int or np.ndarray
    max sales for all series (will generate a max for each ts) or list/array of maxes
        
  encode_date_time: bool
    True to return date covariate as float value (int part is year, fractional part is step (M, W, D))
    
    
  random_peaks: bool, optional
    The series will gradually increase and peak at random points. Default is disabled
    
    

  Returns
  -------
    data dictionary containing:
      ['SIGNAL'] : ndarray[n_series, n_steps] with the time-seriess
      ['COVAR'] : ndarray[n_series, n_steps] with the date covariate
      ['PERIODS'] : ndarray[n_series, 1] with periodicity of each signal

  """
  np_result = None
  RULES = OrderedDict({
    # daily
    'D': {
      'PERIODS' : [7, 30, 91, 182, 365],
      'MULT' : 1, # multiply factor to determine how many days based on n_steps
      'PER_STEP' : 0.001, # for date encoding is not start and step
      'PER_MAX'  : 0.366, # for date encoding is max of year period
      'PER_SIZE' : 365,   # for date encoding to see how many years
      'TIME_DELTA' : timedelta(days=1),
      }, 
    
    # weekly
    'W': {
      'PERIODS' : [4, 13, 26, 52],
      'MULT' : 7,
      'PER_STEP' : 0.01,
      'PER_MAX'  : 0.53,
      'PER_SIZE' : 52,   # for date encoding to see how many years
      'TIME_DELTA' : timedelta(weeks=1),
      }, 
    
    # monthly
    'M': {
      'PERIODS' : [6, 12],
      'MULT' : 30.5,
      'PER_STEP' : 0.01,
      'PER_MAX'  : 0.13,
      'PER_SIZE' : 12,   # for date encoding to see how many years
      'TIME_DELTA' : relativedelta(months=1),
      }  
    })
  lst_imp = list(RULES.keys())
  if freq not in lst_imp:
    raise ValueError("Time series frequency {} is not implemented. Valid options are {}".format(
      freq, lst_imp))
  freq = freq.upper()
  rules = RULES[freq[0]]
  periods = rules['PERIODS']
  
  # Composed series variants (including single component)
  lst_series_components = []
  for nr_series_composed in range(1, len(periods)):
    lst_series_components += list(itertools.combinations(periods, nr_series_composed))

  # now we generated the splines including composed periods
  
  np_series = get_random_series_batch(
    periods=periods,
    n_points=n_steps,
    random_start=True,
    )

  composed_series, lst_series_components = compose_series(np_series)
  
  np_series = np.concatenate((np_series, composed_series), axis=0)

  composed_periods = [[periods[y] for y in x] for x in lst_series_components]
  if True:
    periods = composed_periods

  np_series_indexes = np.random.choice(list(range(np_series.shape[0])), size=(n_series))
  
  np_series = np_series[np_series_indexes, :]
  periods = [periods[x] for x in np_series_indexes]
  # now we rescale the splines
  if type(max_sales) == np.ndarray:
    if max_sales.shape[0] != n_series:
      raise ValueError('Max sales {} must have same number of vectors as number of series {}'.format(
        max_sales.shape, n_series))
    np_max_sales = max_sales.copy()
    if len(np_max_sales.shape) == 1 or len(np_max_sales.shape[1]) == 1:
      np_max_sales = np_max_sales.reshape((-1, 1))
  else:
    np_max_sales = np.random.randint(low=1, high=max_sales, size=(n_series, 1))
  
  if random_peaks:
    peak_point = np.random.randint(n_steps // 2, n_steps // 2 + n_steps // 3)
    min_per_serie = np_max_sales.max(1) / 2
    max_per_serie = np_max_sales.max(1) * 1.5
    if np_max_sales.shape[1] != n_steps:      
      np_max_sales = np.concatenate(
        (np_max_sales, np.zeros((n_series, n_steps - np_max_sales.shape[1]))),
        axis=1
        )
    for idx_ser in range(n_series):
      np_max_sales[idx_ser] = np.concatenate((
        np.linspace(
          min_per_serie[idx_ser], 
          max_per_serie[idx_ser], 
          num=peak_point
          ),
        np.linspace(
          max_per_serie[idx_ser], 
          min_per_serie[idx_ser], 
          num=n_steps - peak_point
          ),
        ))
      
    
  np_rescaled = np_series * np_max_sales
  
  # finally we add noise, round to integers and constraint lower bound zero
  np_noisy = np_rescaled + np.random.randint(
    low=-noise_size, 
    high=noise_size, 
    size=(n_series, n_steps)
    )
  np_result = np.floor(np.clip(a=np_noisy, a_min=0, a_max=None))
  
  # now lets prepare the covariates
  np_dates = None
  if encode_date_time:
    per_start = RULES[freq]['PER_STEP']
    per_max = RULES[freq]['PER_MAX']
    nr_years = int(np.ceil(n_steps / RULES[freq]['PER_SIZE']))
    years = []
    for y in range(1, nr_years + 1):
      np_cy = np.arange(start=per_start, stop=per_max, step=per_start)
      np_cy = np_cy + y
      years.append(np_cy)
    np_serie_dates = np.concatenate(years)
  else:    
    curr_date = datetime.now()
    step_size = RULES[freq]['MULT']
    back_days = n_steps * step_size + 1
    start_date = curr_date.now().date() - timedelta(days=back_days)    
    td = RULES[freq]['TIME_DELTA']
    dates = [start_date + td * j for j in range(1, n_steps + 1)]
    np_serie_dates = np.array(dates)
  
  np_dates = np.array([np_serie_dates] * n_series)[:,:n_steps]
    
  
  result = {
    'SIGNAL' : np_result,
    'PERIODS' : periods,
    'COVAR' : np_dates,
    }
  
  return result

if __name__ == '__main__':
  import matplotlib.pyplot as plt
  np.set_printoptions(
    suppress=True,
    precision=2,
    floatmode='fixed',
    threshold=1000,
    linewidth=1000,
    )
  
  # n_points = 52
  # period = 4 + 4 + 5
  # step = 2 * np.pi / period
  # x = np.arange(0, n_points)
  # xi = x * step
  # y = np.sin(xi)
  # y += y.max()
  # plt.plot(x, y, linestyle='--', marker='o')
  # plt.title('BASIC')
  # plt.show()
  
  # periods = [4, 13, 26, 52]
  # np_y = get_random_series_batch(
  #   periods=periods,
  #   n_points=n_points,
  #   random_start=True
  #   )
  
  # for i in range(np_y.shape[0]):
  #   plt.plot(np.arange(0, n_points), np_y[i], linestyle='--', marker='+')
  #   plt.title('Series {} with period {}'.format(i, periods[i]))
  #   plt.show()
  
  data = generate_artificial_series(
    n_series=1,
    n_steps=1000,
    freq='D', 
    max_sales=10,
    encode_date_time=False,
    )
  np.random.seed(2)

  np_ser = data['SIGNAL']
  np_per = data['PERIODS']
  np_dates = data['COVAR']
  
  plot_params_1 = {
    'linestyle' : 'None',
    'marker'    : '+'
    }
  plot_params_2 = {
    'linestyle'       : '-',
    'marker'          : '+',
    'markeredgecolor' : 'red',
    'markeredgewidth' : 2,
    }
  plot_params = plot_params_2
  
  for i in range(np_ser.shape[0]):
    np_serie = np_ser[i]
    n_steps = np_serie.shape[0]
    # x = np.arange(0, np_ser.shape[1])
    x_labels = ['{}'.format(j) for j in np_dates[i]]
    plt.figure(figsize=(14,9))
    plt.plot(np_serie, **plot_params)
    #plt.xticks(np.arange(0, n_steps), labels=x_labels, rotation='vertical')
    plt.title('Series {} with periods {}'.format(i, np_per[i]))
    plt.show()
    
    
  
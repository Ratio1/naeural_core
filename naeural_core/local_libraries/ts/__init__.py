# -*- coding: utf-8 -*-
"""
Created on Tue Jun 23 16:29:02 2020

@author: Andrei
"""


from .utils import get_random_series, moving_averages, get_series_trends, get_trend_preds, get_linear_preds
from .utils import acf, acf_limit, acf_plot, get_valid_lags, get_max_lag
from .utils import plot_autoreg
from .utils import get_daily_series_confidence
from .baselines import get_avail_baselines, get_preconfigured_baselines
from .benchmarker import TimeseriesBenchmarker
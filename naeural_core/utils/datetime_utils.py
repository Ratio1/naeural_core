from datetime import datetime as dt

def add_microseconds_to_str_timedelta(x):
  if '.' not in x:
    x = x + '.000000'
  return x


from time import time
import functools

def stopwatch_decorator(func):
  @functools.wraps(func)
  def wrapper(*arg, **kwargs):
    start = time()
    res = func(*arg, **kwargs)
    return res, time()-start

  return wrapper

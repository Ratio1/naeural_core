from time import time, sleep
import functools

def timer(func):
  @functools.wraps(func) # for debugging purposes; if not specified, foo.__name__ will be "wrapper" instead of "foo" (i.e. preserve original name, docstring, etc)
  def wrapper(*arg, **kwargs):
    start = time()
    res = func(*arg, **kwargs)
    return res, time()-start

  return wrapper

@timer
def foo(a,b,c=3):
  print(a,b,c)
  sleep(0.1)
  return

if __name__ == '__main__':

  x = foo("dfd","dfs")
  print("method foo returned '{}' (exec time: {:.2f}s)".format(x[0], x[1]))

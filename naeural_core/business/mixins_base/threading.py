from concurrent.futures import ThreadPoolExecutor
from threading import Thread
import os


class _ThreadingAPIMixin():
  def __init__(self):
    super(_ThreadingAPIMixin, self).__init__()
    return
  
  def __get_n_threads(self, n_threads):
    if n_threads is None:
      n_threads = 1
    if n_threads > os.cpu_count() // 4 and n_threads > 1:
      self.P("Warning: n_threads is too high, setting to 1/4 of available CPUs", color='y')
      n_threads = max(os.cpu_count() // 4, 1)
    return n_threads
  
  def __wrapper_func_run(self, func: callable, lst_results: list, thread_id: int, n_threads: int):
    lst_results[thread_id] = func(thread_id, n_threads)
    return

  def threadapi_map(self, func, lst_data, n_threads=1):
    """
    Run a function in parallel using ThreadPoolExecutor.map

    Parameters
    ----------
    func : callable
        The function to run in parallel
    lst_data : list
        The list of data to pass to the function
    n_threads : int, optional
        The number of threads to use, by default 1
        If this number is higher than 1/4 of available CPUs, it will be set to 1/4 of available CPUs

    Returns
    -------
    list
        The results of the function (similar to list(map(func, lst_data)))
    """
    n_threads = self.__get_n_threads(n_threads)
    with ThreadPoolExecutor(max_workers=n_threads) as executor:
      results = executor.map(func, lst_data)
    return list(results)

  def threadapi_base64_code_map(self, base64_code, lst_data, n_threads=1):
    """
    Run a custom code method in parallel using ThreadPoolExecutor.map

    Parameters
    ----------
    base64_code : str
        The base64 encoded custom code method
    lst_data : list
        The list of data to pass to the custom code method
    n_threads : int, optional
        The number of threads to use, by default 1
        If this number is higher than 1/4 of available CPUs, it will be set to 1/4 of available CPUs

    Returns
    -------
    list
        The results of the custom code method (similar to list(map(func, lst_data)))
    """
    base_custom_code_method, errors, warnings = self._get_method_from_custom_code(
      str_b64code=base64_code,
      self_var='plugin',
      method_arguments=['plugin', 'data']
    )

    custom_code_method = lambda data: base_custom_code_method(self, data)
    if errors is not None:
      errors_str = "\n".join([str(e) for e in errors])
      self.P(f"Errors found while getting custom code method:\n{errors_str}", color='r')
      return None
    
    warnings_str = "\n".join([str(w) for w in warnings])
    if len(warnings) > 0:
      self.P(f"Warnings found while getting custom code method:\n{warnings_str}", color='y')

    return self.threadapi_map(custom_code_method, lst_data, n_threads)
  
  def threadapi_run(self, func, n_threads):
    """
    Run a function in parallel using threads

    Parameters
    ----------
    func : callable
        The function to run in parallel
        This function must have the following signature: func(thread_id: int, n_threads: int)
    n_threads : int
        The number of threads to use, by default 1
        If this number is higher than 1/4 of available CPUs, it will be set to 1/4 of available CPUs

    Returns
    -------
    list
        A list of results from the function calls, similar to [func(0, n_threads), func(1, n_threads), ... func(n_threads-1, n_threads)]
    """
    # create n_threads threads that run func
    n_threads = self.__get_n_threads(n_threads)

    lst_results = [None] * n_threads

    lst_threads: list[Thread] = []

    for thread_id in range(n_threads):
      thread = Thread(target=self.__wrapper_func_run, args=(func, lst_results, thread_id, n_threads))
      lst_threads.append(thread)

    for thread in lst_threads:
      thread.start()
      
    for thread in lst_threads:
      thread.join()

    return lst_results

class PostponedRequest:
  def __init__(self, solver_method, method_kwargs={}):
    self.__solver_method = solver_method
    self.__method_kwargs = method_kwargs
    return

  def get_solver_method(self):
    return self.__solver_method

  def get_method_kwargs(self):
    return self.__method_kwargs

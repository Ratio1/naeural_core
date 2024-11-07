import uuid 

from threading import Lock

class Singleton:
  _lock: Lock = Lock()
  def __new__(cls, log, **kwargs):
    with cls._lock:
      if not hasattr(cls, '_instance'):
        instance = super(Singleton, cls).__new__(cls)
        instance.uuid = str(uuid.uuid4())[:4]
        instance.log = log
        instance.build(**kwargs)
        cls._instance = instance
      else:
        instance = cls._instance
    return instance
  
  def build(self, **kwargs):
    raise NotImplementedError('This method must be implemented by the subclass')
  

class Multitone:
  _lock: Lock = Lock()
  def __new__(cls, name, log, **kwargs):
    with cls._lock:
      if not hasattr(cls, '_instances'):
        cls._instances = {}
      if name not in cls._instances:
        instance = super(Multitone, cls).__new__(cls)
        instance.uuid = str(uuid.uuid4())[:8]
        instance.log = log
        instance.name = name
        instance.build(**kwargs)
        cls._instances[name] = instance
      else:
        instance = cls._instances[name]
    return instance
  
  def build(self, **kwargs):
    raise NotImplementedError('This method must be implemented by the subclass')
  
  def __del__(self):
    with self._lock:
      if hasattr(self, 'name') and self.name in self.__instances:
        del self.__instances[self.name]
    return
  
  
if __name__ == '__main__':
  log = "LOG"
  
  class TestSingleton(Singleton):
    def build(self, param1=None, param2=None):
      print('Building TestSingleton with params: {} {}'.format(param1, param2))
      return
    
  class TestMultitone(Multitone):
    def build(self, p1=1, p2=2, p3=3):
      print('Building TestMultitone {} with params: {} {}'.format(self.name, p1, p2, p3))
      return
    
    
  class TestContainerBase:
    def __init__(self, log=None):
      self.log = log
      self.startup()
      return
    
    def startup(self):
      raise NotImplementedError('This method must be implemented by the subclass')
  
  class TestContainer(TestContainerBase):
    def __init__(self, log=None):
      self.s1 = None
      super().__init__(log=log)
      return
    
    def startup(self):
      self.s1 = TestSingleton(log=self.log, param1='a', param2='b')
      return
    
    
    
  s1 = TestSingleton(log=log, param1='a', param2='b')
  s2 = TestSingleton(log=log, param1='c', param2='d')
  
  m1 = TestMultitone(name='m1', log=log, p1=1, p2=2, p3=3)
  m2 = TestMultitone(name='m2', log=log, p1=4, p2=5, p3=6)
  m3 = TestMultitone(name='m1', log=log, p1=7, p2=8, p3=9)
  m4 = TestMultitone(name='m2', log=log, p1=10, p2=11, p3=12)
  
  c1 = TestContainer(log=log)
  
  assert s1.uuid == s2.uuid
  assert m1.uuid == m3.uuid
  assert m2.uuid == m4.uuid
  
  assert s1.uuid == c1.s1.uuid
  
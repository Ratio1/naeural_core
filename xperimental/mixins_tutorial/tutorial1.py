"""


FinalClass.__mro__ = (<class '__main__.FinalClass'>, <class '__main__.BaseClass'>, <class '__main__._Mixin1'>, <class '__main__._Mixin2'>, <class '__main__._Mixin3'>, <class 'object'>)
FinalClass.1
BaseClass.1 
_Mixin1.1   
_Mixin2.1   
_Mixin3.1   
_Mixin3.2   
_Mixin2.2   
_Mixin1.2   
BaseClass.2 
FinalClass.2
  
"""
class _Mixin1:
  def __init__(self):      
    print("_Mixin1.1".format(self.__class__.__name__))
    super(_Mixin1, self).__init__()
    print("_Mixin1.2".format(self.__class__.__name__))
    return
  
class _Mixin2:
  def __init__(self):      
    print("_Mixin2.1".format(self.__class__.__name__))
    super(_Mixin2, self).__init__()
    print("_Mixin2.2".format(self.__class__.__name__))
    return
  
  
class _Mixin3:
  def __init__(self):      
    print("_Mixin3.1".format(self.__class__.__name__))
    super(_Mixin3, self).__init__()
    print("_Mixin3.2".format(self.__class__.__name__))
    return
  

class BaseClass:
  def __init__(self):
    print("BaseClass.1".format(self.__class__.__name__))
    super(BaseClass, self).__init__()
    print("BaseClass.2".format(self.__class__.__name__))
    return
  
class FinalClass(BaseClass, _Mixin1, _Mixin2, _Mixin3):
  def __init__(self):
    print("FinalClass.1".format(self.__class__.__name__))
    super(FinalClass, self).__init__()
    print("FinalClass.2".format(self.__class__.__name__))
    return    
  
  
if __name__ == "__main__":
  print("FinalClass.__mro__ = {}".format(FinalClass.__mro__))
  eng = FinalClass()
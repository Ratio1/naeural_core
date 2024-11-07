from naeural_core import DecentrAIObject


class _TestCommandsMixin:
  # handler definition must start with `cmd_handler_` followed by command name
  
  def cmd_handler_stop(self,):
    print('cmd_stop {}'.format(self.vv))

  def cmd_handler_notification(self,):
    print('cmd_notif')
    return 1000
    
  # end handler definition
  

class Test(DecentrAIObject, _TestCommandsMixin):

  def __init__(self, **kwargs):
    # self.COMMANDS ={
    # 'START' : self.cmd_handler_gigi,
    # } 
    self.vv = 100
    return
  
  def cmd_handler_gigi(self, test=None):
    print('cmd_start: test={}'.format(test))
  
    
  


  
if __name__ == '__main__':
  eng = Test()
  
  eng.run_cmd('gigi', test=10)
  eng.run_cmd('stop')
  eng.run_cmd('notif')
  rr = eng.run_cmd('notification')
  print(rr)
    
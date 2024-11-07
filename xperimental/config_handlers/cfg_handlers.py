from naeural_core.local_libraries import _ConfigHandlerMixin
from naeural_core import DecentrAIObject
from naeural_core import Logger


_CONFIG1 = {
  'P1' : 1,
  'TEST_PRC' : 1.0,
  
  'P_2_1' : 3,
  
  'P_A' : 't1',

  'D_A' : {},

  'L_A' : ['test'],
  
  'VALIDATION_RULES' : {
    'P1' : {
      'TYPE' : 'int',
      'MIN_VAL' : 1,
      'MAX_VAL' : 1,
      },
    
    'P_A' : {
      'TYPE' : 'str',
      'MIN_LEN' : 2,
      'EXCLUDED_LIST' : ['XXXX'],
      'ACCEPTED_VALUES' : ['t1','t2']
      },
    
    'TEST_PRC' :{
      'TYPE' : 'float',
      'MAX_VAL' : 1,
      'MIN_VAL' : 0,
      },
    'D_A' :{
      'TYPE' : 'dict',
      },
    'L_A' :{
      'TYPE' : 'list',
      },
    
    # 'TEST1' : { # this key cannot be found in validation
    #   'TYPE' : 'int'
    #   }
    }
  
}

class Test(DecentrAIObject, _ConfigHandlerMixin):
  CONFIG = _CONFIG1
  def __init__(self, config, **kwargs):
    self.config = config
    super().__init__(**kwargs)    
    return
    
  def startup(self):
    super().startup()    
    self.config = self._merge_prepare_config(
      default_config=self.config,
      
      debug=False
    )
    self.config_data = self.config
    self.create_config_handlers()
    self.validate()
    
      
  def update(self, cfg):
    self.config = self._merge_prepare_config(
      default_config=self.config,
      debug=False
    )      
    return

if __name__ == '__main__':
  l = Logger('CFG', base_folder='.', app_folder='_local_cache')
  
  
  eng = Test(config=_CONFIG1, log=l)
  res, msgs = eng.run_validation_rules(verbose=100)
  l.P("Result: {}, {}".format(res, msgs), color=None if res else 'r')
  

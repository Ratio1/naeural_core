from naeural_core.utils.config_utils import get_config_from_code

_CONFIG  = {
  "TEST" : 1,
  "TEST2" : {
     "1" : 2,
     "3" : [1,2,3],
     "5" : {"A": 1, "B": 0}
     
    }
  }




if __name__ == '__main__':

  print(get_config_from_code(__file__))
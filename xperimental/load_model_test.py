from naeural_core import Logger

if __name__ == '__main__':
  
  l = Logger('MLD', base_folder='.', app_folder='_cache')
  m = l.load_keras_model('xlpr_v201_004_e223_test_acc_73_361')
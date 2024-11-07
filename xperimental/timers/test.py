from naeural_core import Logger
from time import sleep

if __name__ == '__main__':
  log1 = Logger(
    lib_name='TST1',
    base_folder='.',
    app_folder='_cache',
    max_lines=1000, 
    TF_KERAS=False
  )

  for i in range(5):
    log1.start_timer('test1',section='test')
    sleep(0.01)
    log1.stop_timer('test1',section='test')
    log1.start_timer('test2',section='test')
    sleep(0.01)
    log1.stop_timer('test2',section='test')
    
  dct_t, dct_tg = log1.export_timers_section(section='test')
  log1.show_timers()  
    
  log = Logger(
    lib_name='TST',
    base_folder='.',
    app_folder='_cache',
    max_lines=1000, 
    TF_KERAS=False
  )
  
  for i in range(5):
    log.start_timer('test1',section='test')
    log.start_timer('test11',section='test')    
    sleep(0.01)
    log.stop_timer('test11',section='test')
    log.start_timer('test12',section='test')    
    sleep(0.01)
    log.stop_timer('test12',section='test')
    log.stop_timer('test1',section='test')
    log.start_timer('test2',section='test')
    sleep(0.01)
    log.stop_timer('test2',section='test')
  
  log.import_timers_section(
    dct_timers=dct_t, 
    dct_timers_graph=dct_tg, 
    section='test_imported'
  )
  
  for i in range(10):
    log.start_timer('main')
    if True:
      log.start_timer('op1')
      if i % 2 == 0:
        log.start_timer('op2')
        #concrete op
        sleep(0.001)
        log.stop_timer('op2')
      if i == 1 or i == 3:
        log.start_timer('op3')
        #concrete op
        sleep(0.001)
        # if i == 1:
        #   continue
        log.stop_timer('op3')        
      if i == 2:
        log.start_timer('op4')        
        #concrete op
        sleep(0.001)
        log.stop_timer('op4')
      log.stop_timer('op1')      
    log.stop_timer('main')
  
  log.show_timers()
  log.P("Overwriting ...", color='m')
  log.import_timers_section(
    dct_timers=dct_t, 
    dct_timers_graph=dct_tg, 
    section='test',
    overwrite=True,
  )
  log.show_timers()
  
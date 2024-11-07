from naeural_core import Logger

if __name__ == '__main__':
  d = {'STREAM_NAME': 'TS_REST_AND_OTHER',
   'STREAM_METADATA': {'actual_dps': -1,
    'cap_resolution': 1,
    'cap_max_queue_len': 1,
    'cap_queue_len': 0,
    'cap_time': '2022-10-14 07:26:30.483792',
    'cap_elapsed_time': 0.06400084495544434,
    'live_feed': True,
    'reconnectable': 'YES'},
   'INPUTS': [{'IMG': None,
     'STRUCT_DATA': {'DUMMY DATA': 0},
     'METADATA': {'current_interval': None, 'meta_dummy_count': 1},
     'INIT_DATA': None,
     'TYPE': 'STRUCT_DATA'}],
   'SERVING_PARAMS': {}}
  
  l = Logger('DCT', base_folder='.', app_folder='_local_cache')
  l.dict_show(d)

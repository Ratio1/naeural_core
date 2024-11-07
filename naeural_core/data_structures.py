import os.path
from copy import deepcopy

import pandas as pd
import numpy as np

from naeural_core import constants as ct
from naeural_core.utils.img_utils import maybe_prepare_img_payload

from naeural_core.utils.debug_save_img import save_images_and_payload_to_output

class MetadataObject:
  def __init__(self,  **kwargs):
    self.update(**kwargs)
    return

  def update(self, **kwargs):
    for _arg in kwargs:
      # this basically can be replaced with obj.var = val instead of obj.update(var=val)
      # although additional functionality can be inserted here
      vars(self)[_arg] = kwargs[_arg]
    return
  
GENERAL_PAYLOAD_INTERNAL = [
  'owner',
  
]

DEFAULT_STATUS_MESSAGE = "N/A"

class GeneralPayload:
  def __init__(self, owner, **kwargs):
    self.owner = owner
    self.debug_payload_saved = False    
    
    self._pre_process_object()
    
    for _arg in kwargs:
      vars(self)[_arg.upper()] = kwargs[_arg]

    self._handle_status()      
    return


  @staticmethod
  def _fmt_msg(lst_alerters, raised_or_lowered):
    if len(lst_alerters) == 1 and lst_alerters[0] == 'default':
      return "alert was {}".format(raised_or_lowered)
    return "{} alerts were {}: {}".format(len(lst_alerters), raised_or_lowered, lst_alerters)


  def _handle_status(self):
    if not hasattr(self, 'STATUS'):
      self.STATUS = DEFAULT_STATUS_MESSAGE
    #endif
    
    if self.STATUS == DEFAULT_STATUS_MESSAGE:
      if self.IS_NEW_RAISE:
        self.STATUS = "Alert raised at {}".format(self.TIMESTAMP_EXECUTION)
      elif self.IS_NEW_LOWER:
        self.STATUS = "Alert lowered at {}".format(self.TIMESTAMP_EXECUTION)
      #endif
    #endif
    return

  
  def _add_metadata_to_payload(
    self, 
    excluded_list=['original_image', 'temp_data'], 
    direct_keys=['payload_context']
    ):
    """
    This method added capture metadata as `_C_XXX` properties

    Parameters
    ----------
    excluded_list : list, optional
      Exclude images and other large objects from payloads. 
      The default is ['original_image'].


    Returns
    -------
    None.

    """
    for meta_key, meta_val in self.owner.dataapi_all_metadata().items():
      if meta_key.lower() not in excluded_list:
        if meta_key.lower() in direct_keys:
          vars(self)[meta_key.upper()] = meta_val
        else:
          vars(self)['_C_' + meta_key] = meta_val
    #end for all capture metadata
    
    # now process email heavy ops
    if self.owner.cfg_email_config is None:
      return

    # if the plugin instance contains "EMAIL_CONFIG" then we have to setup the
    # email alerting composing the subject & body and expecting downstream 
    # processing by the `SendMailHeavyOp`

    if vars(self).get('_H_SEND_EMAIL', False):
      # if payload already contains "SEND_EMAIL", then it means that the 
      # notification was already handled
      return
    
    device_id = self.owner._device_id
    stream_id = self.owner.get_stream_id()
    plugin_id = self.owner._signature
    instance_id = self.owner.cfg_instance_id
    
    vars(self)['_H_EMAIL_CONFIG'] = self.owner.cfg_email_config
    vars(self)['_H_EMAIL_SUBJECT'] = "Automatic alert in EE '{}': {}:{}:{}".format(
      device_id,
      stream_id,
      plugin_id,
      instance_id,
    )
    
    alerters = self.owner.alerters_names

    # Check changes in all alerters, and print the ones that raise or lower
    any_alerter_changed = any([self.owner.alerter_status_changed(al) for al in alerters])
    if any_alerter_changed:
      vars(self)['_H_SEND_EMAIL'] = True
      new_raise_alerters = [al for al in alerters if self.owner.alerter_is_new_raise(al)]
      new_lower_alerters = [al for al in alerters if self.owner.alerter_is_new_lower(al)]

      email_message = ""
      prefix = "On stream `{}`".format(self.owner.get_stream_id())
      if len(new_raise_alerters) > 0:
        s = self._fmt_msg(new_raise_alerters, 'raised')
        email_message = "{}, {}".format(prefix, s)
      #endif

      if len(new_lower_alerters) > 0:
        s = self._fmt_msg(new_lower_alerters, 'lowered')
        if len(email_message) == 0:
          email_message = "{}, {}".format(prefix, s)
        else:
          email_message += " and {}".format(s)
      #endif

      vars(self)['_H_EMAIL_MESSAGE'] = email_message
    #endif
    return
  
  
  def _pre_process_object(self):
    
    self.TIMESTAMP_EXECUTION = self.owner.log.now_str(nice_print=True, short=False)
    self.STREAM = self.owner._stream_id # backward compat
    self.PIPELINE = self.owner._stream_id


    # get default alerter status
    self.IS_ALERT     = self.owner.alerter_is_alert()
    self.IS_NEW_RAISE = self.owner.alerter_is_new_raise()       # leave dup for backwards compat
    self.IS_NEW_LOWER = self.owner.alerter_is_new_lower()       # leave dup for backwards compat
    self.IS_ALERT_NEW_RAISE = self.owner.alerter_is_new_raise()
    self.IS_ALERT_NEW_LOWER = self.owner.alerter_is_new_lower()
    # last alert duration will be non-NONE only if the has been lowered
    self.LAST_ALERT_DURATION = self.owner.alerter_get_last_alert_duration() 
    self.IS_ALERT_STATUS_CHANGED = self.owner.alerter_status_changed()
    # end get default alerter status

    self._P_DEBUG_SAVE_PAYLOAD = self.owner.cfg_debug_save_payload  
    
    vars(self)['_P_' + ct.ALIVE_TIME_MINS] = round(self.owner.time_alive / 60, 2)  # get plugin alive time
    vars(self)['_P_' + ct.PLUGIN_REAL_RESOLUTION] = self.owner.actual_plugin_resolution
    vars(self)['_P_' + ct.PLUGIN_LOOP_RESOLUTION] = self.owner.get_plugin_loop_resolution()
    vars(self)['_P_' + ct.ALERT_HELPER] = self.owner.get_alerter_status()
    
    self._add_metadata_to_payload()
    
    vars(self)[ct.PAYLOAD_DATA.STREAM_NAME] = self.owner._stream_id
    vars(self)[ct.PAYLOAD_DATA.SIGNATURE] = self.owner._signature
    vars(self)[ct.PAYLOAD_DATA.INSTANCE_ID] = self.owner.cfg_instance_id
    
    # add initiator (who created the pipeline) and modified by (who modified the pipeline)
    vars(self)[ct.PAYLOAD_DATA.INITIATOR_ID] = self.owner.initiator_id
    vars(self)[ct.PAYLOAD_DATA.INITIATOR_ADDR] = self.owner.initiator_addr
    vars(self)[ct.PAYLOAD_DATA.MODIFIED_BY_ID] = self.owner.modified_by_id
    vars(self)[ct.PAYLOAD_DATA.MODIFIED_BY_ADDR] = self.owner.modified_by_addr
    
    vars(self)[ct.PAYLOAD_DATA.SESSION_ID] = self.owner._session_id
    vars(self)[ct.PAYLOAD_DATA.TAGS] = self.owner.cfg_tags  # add TAGS if initially added to instance
    vars(self)[ct.PAYLOAD_DATA.ID_TAGS] = self.owner.cfg_id_tags  # add TAGS if initially added to instance
    
    self.COLLECTED = self.owner.cfg_collect_payloads_until_seconds_export
    ###
    
    vars(self)[ct.ID] = self.owner.current_process_iteration
    
    vars(self)['_P_' + ct.DEMO_MODE] = self.owner.cfg_demo_mode
    vars(self)['_P_' + ct.PROCESS_DELAY] = self.owner.cfg_process_delay
    vars(self)['_P_' + ct.GRAPH_TYPE] = self.owner._instance_config.get('AI_ENGINE', 'No model serving process')
    vars(self)['_P_' + ct.VERSION] = '{}'.format(self.owner.__version__)

    vars(self)["USE_LOCAL_COMMS_ONLY"] = vars(self).get("_C_USE_LOCAL_COMMS_ONLY", False) or self.owner.use_local_comms_only

    return
    
  
  
  def _process_result(self, dct_result):
    # this small section will populate with extra payload key-values then will
    # delete the dictionary from the instance cache
    dct_extra_vars = self.owner.get_default_plugin_vars() # get the default data from `plugin__default_payload_data`
    if len(dct_extra_vars) > 0:
      for k, v in dct_extra_vars.items():
        dct_result[k.upper()] = v
      self.owner.reset_default_plugin_vars()
      if False: 
        # show any manually added `default_plugin_vars`
        self.owner.P("Found extra payload, result: {}".format(dct_result))
    # end if we have data to add to the payload
    
    curr_img = dct_result.get('IMG')
    if curr_img is None:
      curr_img = vars(self.owner).get('default_image')
      if curr_img is not None:
        dct_result['IMG'] = curr_img
        # now reset default image
        self.owner.default_image = None 
    
    if hasattr(self.owner, 'cfg_debug_rest') and self.owner.cfg_debug_rest:
      self.owner.P(
        "GeneralPayload: IMG: {}".format((type(curr_img), len(curr_img))) 
        if curr_img is not None else 
        "GeneralPayload: IMG is None!"
      )
      
    if vars(self)['_P_DEBUG_SAVE_PAYLOAD'] and not self.debug_payload_saved:
      self.to_disk(dct_result)
      self.debug_payload_saved = True # this will be delivered to client and it is ok
      
    if self.owner.cfg_debug_save_img and curr_img is not None:      
      self.__debug_save_img(
        np_witness_img=curr_img, 
        np_orig_img=self.owner.dataapi_image()
      )
    return
  
  
  def __debug_save_img(self, np_witness_img, np_orig_img):
    save_images_and_payload_to_output(
      log=self.owner.log, 
      path=self.owner.save_path,
      relative_path=self.owner.instance_relative_path,
      np_witness_img=np_witness_img, 
      np_orig_img=np_orig_img,                      
      upload_nr_imgs=self.owner.cfg_debug_save_img_archive,                      

      last_archive_time=None,                   # no archive time as we use nr files
      archive_each_minutes=None,                # no archive time as we use nr files

      dct_payload=None,                         # no payload save for the moment
      file_system_manager=self.owner.global_shmem['file_system_manager'],                 # no upload for the moment
      perform_upload=True,                     # no upload for the moment
    )
    return
  
  
  def _check_for_blobs(self, dct_payload, numpy_keys=['IMG', 'IMG_ORIG']):
    self.owner.start_timer('_check_for_blobs')
    # numpy
    for k in numpy_keys:
      obj = dct_payload.get(k, [])
      if obj is not None and len(obj) > 0:
        if isinstance(obj, list):
          if isinstance(obj[0], np.ndarray):
            self.owner.P('Sending {}: {}'.format(k, [x.shape for x in obj]))
          else:
            self.owner.P('Sending {}: {}'.format(k, [len(x) for x in obj]))
        else:
          if isinstance(obj[0], np.ndarray):
            self.owner.P('Sending {}: {}'.format(k, obj.shape))
          else:
            self.owner.P('Sending {}: {}'.format(k, len(obj)))
    #end numpy
    
    sz = self.owner.log.get_obj_size(dct_payload)
    self.owner.P("Sending {:,.1f} KB".format(sz / 1024))
    self.owner.end_timer('_check_for_blobs')
    return
  
  
  def _post_process_result(self, dct_payload):
    # convert from numpy to base64 (incl compress) - error handling in `maybe_prepare_img_payload`
    img_orig = dct_payload.get('IMG_ORIG', [])
    already_has_original = img_orig is not None and len(img_orig) > 0
    if self.owner.config_data.get('ADD_ORIGINAL_IMAGE', False) and not already_has_original:
      # if required try to add original image beside the usual 'IMG' witness
      # this is a importat feature as the IMG is usually the processed image
      # TODO: check with ORIGINAL_FRAME maybe delete that old code
      dct_payload['IMG_ORIG'] = self.owner.dataapi_image()
    
    maybe_prepare_img_payload(
      sender=self.owner, 
      dct=dct_payload, 
      keys=['IMG', 'IMG_ORIG'],
      force_list=False, # set this to True in order to always encode the imgs in arrays
    )

    if self.owner.cfg_log_on_blob:
      self._check_for_blobs(dct_payload)  
    return
  
  
  def set_heavy_ops(self, key='_H_SAVE'):
    vars(self)[key] = True
    return
  
  
  def to_dict(self):
    """
    payload_to_dict = 0.0549s/q:0.0549s/nz:0.0549s, max: 0.0628s, lst: 0.0547s, c: 5623/L:15%
      filter = 0.0000s/q:0.0000s/nz:-1.0000s, max: 0.0000s, lst: 0.0000s, c: 5623/L:100%
      deepcopy = 0.0080s/q:0.0080s/nz:0.0080s, max: 0.0126s, lst: 0.0080s, c: 5623/L:3%
      process_result = 0.0000s/q:0.0000s/nz:-1.0000s, max: 0.0001s, lst: 0.0000s, c: 5623/L:100%    
    """
    self.owner.start_timer('payload_to_dict')
    
    self.owner.start_timer('payload_to_dict_filter')
    dct_result_self = {
      k:v for k,v in self.__dict__.items() 
      if k not in GENERAL_PAYLOAD_INTERNAL
    }
    self.owner.end_timer('payload_to_dict_filter')

    self.owner.start_timer('payload_to_dict_deepcopy')
    dct_result = deepcopy(dct_result_self)
    self.owner.end_timer('payload_to_dict_deepcopy')
    
    self.owner.start_timer('payload_to_dict_process_result')
    self._process_result(dct_result)
    self.owner.end_timer('payload_to_dict_process_result')

    self.owner.start_timer('payload_to_dict_post_proc')
    self._post_process_result(dct_result)
    self.owner.end_timer('payload_to_dict_post_proc')
    
    self.owner.end_timer('payload_to_dict')
    return dct_result
  

  def to_disk(self, dct_payload):
    """
    Saves current payload (excluding IMG) in local cache `_output/saved_payloads/[STREAM]__[PLUGIN]__[INSTANCE]`

    Parameters
    ----------
    dct_payload : dict
      the payload.

    Returns
    -------
    None.

    """
    file_name = '{}__{}__{}'.format(vars(self)[ct.STREAM], vars(self)[ct.SIGNATURE], vars(self)[ct.INSTANCE_ID])
    subfolder_path = 'saved_payloads'
    folder_path = self.owner.log.get_file_path(
      fn='',
      folder='output',
      subfolder_path=subfolder_path,
      force=True
    )
    if not os.path.exists(folder_path):
      os.makedirs(folder_path)
    dct_payload = {k:str(v) for k,v in dct_payload.items() if k != "IMG"}
    self.owner.log.update_dataframe_from_output(
      delta_df=pd.DataFrame([dct_payload]),
      fn=file_name,
      subfolder_path=subfolder_path,
      force_update=True,
      compress=False
    )
    


if __name__ == '__main__':
  class O:
    def __init__(self):
      self.x = 0
      
  o = O()         
  p = GeneralPayload(owner=o, a=1, b=2)

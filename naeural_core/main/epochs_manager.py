"""

TODO:

  - add a method for overall statistics:
    - for each node get from db the availability for each epoch
      - display overall availability prc
      - display number of epochs with non-zero availability

  - add a method for backlog recalculation:
    - given a database of hbs get all the timestamps for all nodes
    - for all dates in the timestamps see if the epoch is already registered in EM db
      - if not, update(FLAG) the epoch with all its and generate warning for missing epoch
      - if yes, check if the timestamps defined duration matches the expected db recorded duration
        - if not, generate warning for mismatched duration and update(FLAG)
  
  
"""
import uuid
import json

import numpy as np

from datetime import datetime, timedelta, timezone
from collections import defaultdict
from copy import deepcopy
from threading import Lock


from naeural_core import constants as ct
from naeural_core.utils import Singleton


EPOCH_MANAGER_VERSION = '0.2.2'

EPOCH_INTERVALS = 24
EPOCH_INTERVAL_SECONDS = 3600

EPOCH_MAX_VALUE = 255

FN_NAME = 'epochs_status.pkl'
FN_SUBFOLDER = 'network_monitor'
FN_FULL = FN_SUBFOLDER + '/' + FN_NAME

EPOCHMON_MUTEX = 'epochmon_mutex'

GENESYS_EPOCH_DATE = '2024-03-10 00:00:00'
INITIAL_SYNC_EPOCH = 0 # TODO: add initial sync epoch

NODE_ALERT_INTERVAL = EPOCH_INTERVAL_SECONDS

class EPCT:
  NAME = 'name'
  ID = 'id'
  EPOCHS = 'epochs'
  ALERTS = 'alerts'
  LAST_ALERT_TS = 'last_alert_ts'
  CURRENT_EPOCH = 'current_epoch'
  HB_TIMESTAMPS = 'hb_dates'
  HB_COUNT = 'hb_count'
  FIRST_SEEN = 'first_seen'
  LAST_SEEN = 'last_seen'
  SIGNATURES = 'signatures'

_NODE_TEMPLATE = {
  EPCT.NAME           : None,
  EPCT.EPOCHS         : defaultdict(int),
  EPCT.ALERTS         : 0,
  EPCT.LAST_ALERT_TS  : 0,
  EPCT.FIRST_SEEN     : None,    
  EPCT.LAST_SEEN      : None,  
  EPCT.SIGNATURES     : defaultdict(list),
  
  
  EPCT.CURRENT_EPOCH  : {
    EPCT.ID               : None,
    EPCT.HB_TIMESTAMPS   : set(),
  },
}

def _get_node_template(name):
  data = deepcopy(_NODE_TEMPLATE)
  data[EPCT.NAME] = name
  return data

class EpochsManager(Singleton):
  
  def build(self, owner, debug_date=None, debug=False):
    """
    self.__data = {
      node_addr: {
        current_epoch: {
          timestamps: set(datetime),
          id: int
        },
        epochs: list(int),
        name: str,
        first_seen: None | str datetime,
        last_seen: None | str datetime,
        signatures: list(list(dict))
      }
    }
    self.__full_data = {
      'NODES': self.__data,
      'LAST_SYNC_EPOCH': int,
    }
    """
    self.__genesis_date = self.log.str_to_date(GENESYS_EPOCH_DATE).replace(tzinfo=timezone.utc)

    self.owner = owner
    self.__current_epoch = None
    self.__data = {}
    self.__full_data = {}
    self.__debug = debug
    self._set_dbg_date(debug_date)

    self._load_status()

    self.P("Started EpochsManager v{}, epoch #{}, genesis on {} (debug={}, debug_date={})".format(
      EPOCH_MANAGER_VERSION, 
      self.get_current_epoch(),
      GENESYS_EPOCH_DATE,
      debug, debug_date
    ))
    return

  @property
  def data(self):
    return self.__data
  
  @property
  def genesis_date(self):
    return self.__genesis_date
  
  
  def _set_dbg_date(self, debug_date):
    if debug_date is not None:
      if isinstance(debug_date, str):
        debug_date = self.log.str_to_date(debug_date).replace(tzinfo=timezone.utc)
    self._debug_date = debug_date
    return


  def P(self, msg, **kwargs):
    self.log.P('[EPM] ' + msg, **kwargs)
    return


  def start_timer(self, name):
    self.log.start_timer(name, section='epoch')
    return
  
  def stop_timer(self, name):
    self.log.stop_timer(name, section='epoch')
    return
  
  def get_node_name(self, node_addr):
    """ 
    Given a node address, returns the name of the node.
    """
    return self.owner.network_node_eeid(node_addr)
  
  def __get_max_hb_per_epoch(self):
    max_hb = 0
    addr = self.owner.node_addr
    eeid = self.owner.node_name
    interval = self.owner.network_node_hb_interval(addr=addr)
    if interval is None:
      raise ValueError("Heartbeat interval not found for node: {} ({})".format(addr, eeid))
    nr_hb = 24 * 3600 // interval
    return nr_hb

      
  def _save_status(self):
    self.P("Saving epochs status...")
    
    with self.log.managed_lock_resource(EPOCHMON_MUTEX):
      _full_data_copy = deepcopy(self.__full_data)
    # endwith lock

    self.log.save_pickle_to_data(
      data=_full_data_copy, 
      fn=FN_NAME,
      subfolder_path=FN_SUBFOLDER,
    )
    return
  
  
  def _load_status(self):
    exists = self.log.get_data_file(FN_FULL) is not None
    if exists:
      self.P("Previous epochs state found. Loading epochs status...")
      epochs_status = self.log.load_pickle_from_data(
        fn=FN_NAME,
        subfolder_path=FN_SUBFOLDER
      )
      if epochs_status is not None:
        if 'NODES' not in epochs_status:
          # old format
          self.__data = epochs_status
          self.__full_data = {
            'NODES': self.__data,
            'LAST_SYNC_EPOCH': INITIAL_SYNC_EPOCH,
          }
        else:
          # new format
          self.__full_data = epochs_status
          self.__data = epochs_status['NODES']
        # end if using new format

        self.__add_empty_fields()
        self.P("Epochs status loaded with ....", boxed=True)
      else:
        self.P("Error loading epochs status.", color='r')
      return

  def __add_empty_fields(self):
    """
    Use this method to add missing fields to the loaded data structure.

    For now it adds the signatures field to the epochs data.
    """
    for node_addr in self.__data:
      if EPCT.SIGNATURES not in self.__data[node_addr]:
        self.__data[node_addr][EPCT.SIGNATURES] = defaultdict(list)
    return

  def get_epoch_id(self, date : any):
    """
    Given a date as string or datetime, returns the epoch id - ie the number of days since 
    the genesis epoch.

    Parameters
    ----------
    date : str or date
      The date as string that will be converted to epoch id.
    """
    if isinstance(date, str):
      # remove milliseconds from string
      date = date.split('.')[0]
      date = self.log.str_to_date(date)
      date = date.replace(tzinfo=timezone.utc) 
    elapsed = (date - self.__genesis_date).days
    return elapsed
  
  def epoch_to_date(self, epoch_id=None):
    """
    Given an epoch id, returns the date as string.

    Parameters
    ----------
    epoch_id : int
      the epoch id
    """
    if epoch_id is None:
      epoch_id = self.get_time_epoch()
    date = self.__genesis_date + timedelta(days=epoch_id)
    str_date = datetime.strftime(date, format="%Y-%m-%d")
    return str_date
  
  def date_to_str(self, date):
    """
    Converts a date to string.
    """
    return datetime.strftime(date, format=ct.HB.TIMESTAMP_FORMAT_SHORT)
    
  
  def get_current_date(self):
    if self._debug_date is not None:
      return self._debug_date
    else:
      return datetime.now(timezone.utc)
        
  def get_time_epoch(self):
    """
    Returns the current epoch id.
    """
    return self.get_epoch_id(self.get_current_date())
  
  def get_current_epoch(self):
    """
    Returns the current epoch id using `get_time_epoch`.
    """
    return self.get_time_epoch()
  
  
  def get_hb_utc(self, hb):
    """
    Generates a datetime object from a heartbeat and returns the UTC datetime.

    Parameters
    ----------
    hb : dict
      the hb object

    Returns
    -------
    datetime.datetime
    """
    ts = hb[ct.PAYLOAD_DATA.EE_TIMESTAMP]
    tz = hb.get(ct.PAYLOAD_DATA.EE_TIMEZONE, "UTC+0")        
    remote_datetime = datetime.strptime(ts, ct.HB.TIMESTAMP_FORMAT)
    offset_hours = int(tz.replace("UTC", ""))
    utc_datetime = remote_datetime - timedelta(hours=offset_hours)
    return utc_datetime.replace(tzinfo=timezone.utc)
  
  
  
  def __reset_timestamps(self, node_addr):
    """
    Resets the current epoch timestamps for a node.

    Parameters
    ----------
    node_addr : str
      The node address.
    """
    self.__data[node_addr][EPCT.CURRENT_EPOCH][EPCT.HB_TIMESTAMPS] = set()
    self.__data[node_addr][EPCT.CURRENT_EPOCH][EPCT.ID] = self.get_time_epoch()
    return


  def __reset_all_timestamps(self):
    for node_addr in self.__data:
      self.__reset_timestamps(node_addr)
    return
  
  # FIXME: this method does not work as expected
  def __calculate_avail_seconds(self, timestamps, time_between_heartbeats=10):
    """
    This method calculates the availability of a node in the current epoch based on the timestamps.

    Parameters
    ----------
    timestamps : set
      The set of timestamps for the current epoch.

    time_between_heartbeats: int
      Mandatory time between heartbeats in seconds.
    
    Returns
    -------
    int
      The availability seconds interval.
    """
    avail_seconds = 0
    nr_timestamps = len(timestamps)
    
    # need at least 2 hb timestamps to compute an interval 
    if nr_timestamps <= 1:
      return 0

    start_timestamp = timestamps[0]
    end_timestamp = timestamps[0]
    for i in range(1, nr_timestamps):
      delta = (timestamps[i] - timestamps[i - 1]).seconds
      # the delta between timestamps is bigger than the max heartbeat interval
      # or less than half the heartbeat interval (ignore same heartbeat)
      # TODO(AID): how can a heartbeat be sent more than once?
      # TODO: detect fraud mechanism (someone spams with heartbeats)
      if delta > (time_between_heartbeats + 5): # or delta < (time_between_heartbeats / 2)
        # the delta is too big. we compute the current interval length
        # then reset the interval
        avail_seconds += (end_timestamp - start_timestamp).seconds
        start_timestamp = timestamps[i]
      # endif delta

      # change the end of the current interval
      end_timestamp = timestamps[i]
    #endfor each hb timestamp

    # add the last interval length
    avail_seconds += (end_timestamp - start_timestamp).seconds
    return avail_seconds    
  
  def __calc_node_avail_seconds(self, node_addr, time_between_heartbeats=10, return_timestamps=False):
    if node_addr not in self.__data:
      self.__initialize_new_node(node_addr)
    # endif

    node_data = self.__data[node_addr]
    current_epoch_data = node_data[EPCT.CURRENT_EPOCH]
    timestamps = current_epoch_data[EPCT.HB_TIMESTAMPS]
    current_epoch = current_epoch_data[EPCT.ID]
    lst_timestamps = sorted(list(timestamps))
    avail_seconds = self.__calculate_avail_seconds(
      lst_timestamps, time_between_heartbeats=time_between_heartbeats
    )
    if return_timestamps:
      return avail_seconds, lst_timestamps, current_epoch
    return avail_seconds
  
  
  def get_current_epoch_availability(self, node_addr=None, time_between_heartbeats=10):
    if node_addr is None:
      node_addr = self.owner.node_addr
    # if node not seen yet, return None
    if node_addr not in self.__data:
      return None
    avail_seconds = self.__calc_node_avail_seconds(node_addr, time_between_heartbeats=time_between_heartbeats)
    # max is number of seconds from midnight to now    
    max_possible_from_midnight = (self.get_current_date() - self.get_current_date().replace(hour=0, minute=0, second=0)).seconds
    if max_possible_from_midnight == 0:
      prc_available = 0
    else:
      prc_available = round(avail_seconds / max_possible_from_midnight, 4)
    return prc_available


  def __recalculate_current_epoch_for_node(self, node_addr, time_between_heartbeats=10):
    """
    This method recalculates the current epoch availability for a node. 
    It should be used when the epoch changes just before resetting the timestamps.

    Parameters
    ----------
    node_addr : str
      The node address.
    """
    avail_seconds, lst_timestamps, current_epoch = self.__calc_node_avail_seconds(
      node_addr, time_between_heartbeats=time_between_heartbeats,
      return_timestamps=True
    )
    max_possible = EPOCH_INTERVALS * EPOCH_INTERVAL_SECONDS
    prc_available = round(avail_seconds / max_possible, 4)
    record_value = round(prc_available * EPOCH_MAX_VALUE)
    self.__data[node_addr][EPCT.EPOCHS][current_epoch] = record_value
    
    if self.__debug:
      try:
        node_name = self.__data[node_addr][EPCT.NAME]
        node_name = node_name[:8]
        start_date, end_date = None, None
        if len(lst_timestamps) >= 1:
          start_date = self.date_to_str(lst_timestamps[0])
          end_date = self.date_to_str(lst_timestamps[-1])
        str_node_addr = node_addr[:8] + '...' + node_addr[-3:]
        self.P("{:<8}<{}> avail in ep {}: {} ({:.2f}%) from {} to {}".format(
          node_name, str_node_addr, current_epoch, 
          record_value, prc_available * 100, start_date, end_date
        ))
      except Exception as e:
        self.P("Error calculating availability for node: {}".format(node_addr), color='r')
        self.P(str(e), color='r')
    return prc_available, current_epoch
    
  def recalculate_current_epoch_for_all(self):
    """
    This method recalculates the current epoch availability for all nodes using the recorded 
    timestamps.
    """
    with self.log.managed_lock_resource(EPOCHMON_MUTEX):
      self.P("Recalculating epoch {} availability for all nodes during epoch {}...".format(
        self.__current_epoch, self.get_time_epoch()
      ))

      # if current node was not 100% available, do not compute availability for other nodes
      self.start_timer('recalc_node_epoch')
      available_prc, current_epoch = self.__recalculate_current_epoch_for_node(self.owner.node_addr)
      self.stop_timer('recalc_node_epoch')
      record_value = self.__data[self.owner.node_addr][EPCT.EPOCHS][current_epoch]
      was_current_node_up_throughout_current_epoch = (int(record_value) == EPOCH_MAX_VALUE)

      if not was_current_node_up_throughout_current_epoch:
        msg = "Current node was {}%, not 100%, available in epoch {} and so cannot compute " \
              "availability scores for other nodes".format(available_prc, current_epoch)
        self.P(msg, color='r')
      else:
        self.start_timer('recalc_all_nodes_epoch')
        for node_addr in self.__data:
          self.start_timer('recalc_node_epoch')
          self.__recalculate_current_epoch_for_node(node_addr)
          self.stop_timer('recalc_node_epoch')
        self.stop_timer('recalc_all_nodes_epoch')
      # endif current node was not 100% available
    # endwith lock
    return


  def maybe_close_epoch(self):
    """
    This method checks if the current epoch has changed and if so, it closes the current epoch and 
    starts a new one. Closing the epoch implies recalculating the current epoch node availability 
    for all nodes and then resetting the timestamps.
    """
    result = 0 # assume no epoch change
    current_epoch = self.get_time_epoch()
    if self.__current_epoch is None:
      self.__current_epoch = current_epoch
      self.P("Starting epoch: {}".format(self.__current_epoch))
    elif current_epoch != self.__current_epoch:
      if current_epoch != (self.__current_epoch + 1):
        self.P("Epoch jump detected. Current epoch {} vs Last epoch {}".format(
          current_epoch, self.__current_epoch), color='r'
        )
      self.P("Closing epoch {} at start of epoch {}".format(self.__current_epoch, current_epoch))
      result = self.__current_epoch
      self.recalculate_current_epoch_for_all()
      self.P("Starting epoch: {}".format(current_epoch))
      self.__current_epoch = current_epoch      
      self.__reset_all_timestamps()
      self._save_status()
      #endif epoch is not the same as the current one
    #endif current epoch is not None
    return result
  
  def __initialize_new_node(self, node_addr):
    name = self.get_node_name(node_addr)
    name = name[:8]
    node_name = self.get_node_name(node_addr)
    self.__data[node_addr] = _get_node_template(node_name)
    self.__reset_timestamps(node_addr)
    self.P("New node {:<8} <{}> added to db".format(name, node_addr))
    return

  def register_data(self, node_addr, hb):
    """
    This method registers a heartbeat for a node in the current epoch.
    
    Parameters
    ----------
    node_addr : str
      The node address.
      
    hb : dict
      The heartbeat dict.
      
    """
    self.maybe_close_epoch()

    local_epoch = self.get_time_epoch()   
    # maybe first epoch for node_addr
    if node_addr not in self.__data:
      self.__initialize_new_node(node_addr)
    #endif node not in data
    dt_remote_utc = self.get_hb_utc(hb)
    str_date = self.date_to_str(dt_remote_utc)
    if self.__data[node_addr][EPCT.FIRST_SEEN] is None:
      self.__data[node_addr][EPCT.FIRST_SEEN] = str_date
    # check if the hb epoch is the same as the current one
    remote_epoch = self.get_epoch_id(dt_remote_utc)     
    if remote_epoch == local_epoch:
      # the remote epoch is the same as the local epoch so we can register the heartbeat
      with self.log.managed_lock_resource(EPOCHMON_MUTEX):
        # add the heartbeat timestamp for the current epoch
        self.__data[node_addr][EPCT.CURRENT_EPOCH][EPCT.HB_TIMESTAMPS].add(dt_remote_utc)
        self.__data[node_addr][EPCT.LAST_SEEN] = str_date
      # endwith lock
    else:
      self.P("Received invalid epoch {} from node {} on epoch {}".format(
        remote_epoch, node_addr, local_epoch
      ))
    #endif remote epoch is the same as the local epoch
    return
  
  
  def get_node_list(self):
    """
    Returns the list of nodes.
    """
    return list(self.data.keys())
  
  
  def get_node_state(self, node_addr):
    """
    Returns the state of a node in the current epoch.

    Parameters
    ----------
    node_addr : str
      The node address.
    """
    if node_addr not in self.__data:
      return None
    return self.__data[node_addr]
  
  
  def get_node_epochs(self, node_addr, autocomplete=False, as_list=False):
    """
    Returns the epochs availability for a node.

    Parameters
    ----------
    node_addr : str
      The node address.
      
    autocomplete : bool
      If True, the epochs are completed with 0 for missing epochs.
      
    as_list : bool
      If True, the epochs are returned as a list.
    """
    if node_addr not in self.__data:
      return None
    dct_state = self.get_node_state(node_addr)
    dct_epochs = dct_state[EPCT.EPOCHS]
    current_epoch = self.get_time_epoch()
    if autocomplete or as_list:
      for epoch in range(1, current_epoch):
        if epoch not in dct_epochs:
          dct_epochs[epoch] = 0    
    if as_list:
      result = [dct_epochs[x] for x in range(1, current_epoch)]
    else:
      result = dct_epochs
    return result
  
  
  def get_node_epoch(self, node_addr, epoch_id=None, as_percentage=False):
    """
    This method returns the percentage a node was alive in a given epoch.
    The data is returned from already calculated values.

    Parameters
    ----------
    node_addr : str
      The node address.
      
    epoch_id : int
      The epoch id. Defaults to the last epoch

    Returns
    -------
    float
      The value between 0 and 1 representing the percentage of the epoch the node was alive.
    """
    if node_addr not in self.__data:
      return 0
    if epoch_id is None:
      epoch_id = self.get_time_epoch() - 1
    if epoch_id < 1 or epoch_id >= self.get_time_epoch():
      raise ValueError("Invalid epoch requested: {}".format(epoch_id))
    # get the epochs data
    epochs = self.get_node_epochs(node_addr)
    if epochs is None:
      return 0    
    if as_percentage:
      return round(epochs[epoch_id] / 255, 4)
    return epochs[epoch_id]


  def get_node_previous_epoch(self, node_addr, as_percentage=False):
    """
    Returns the last epoch the node was alive.

    Parameters
    ----------
    node_addr : str
      The node address.
    """
    if node_addr not in self.__data:
      return 0
    last_epoch = self.get_time_epoch() - 1
    return self.get_node_epoch(node_addr, epoch_id=last_epoch, as_percentage=as_percentage)
  
  def get_node_last_epoch(self, node_addr, as_percentage=False):
    """
    Alias for get_node_previous_epoch.
    """
    return self.get_node_previous_epoch(node_addr, as_percentage=as_percentage)


  def get_node_first_epoch(self, node_addr):
    """
    Returns the first epoch the node was alive.

    Parameters
    ----------
    node_addr : str
      The node address.
    """
    if node_addr not in self.__data:
      return -1
    epochs = list(self.get_node_epochs(node_addr).keys())
    min_epoch = min(epochs)
    return min_epoch
  
  
  def get_stats(self, display=True):
    """
    Returns the overall statistics for all nodes.
    """
    stats = {}
    max_val = 0
    nr_eps = 0
    for node_addr in self.data:
      node_name = self.get_node_name(node_addr)
      epochs = self.get_node_epochs(node_addr, as_list=True, autocomplete=True)
      score = sum(epochs)
      current_val = EPOCH_MAX_VALUE * len(epochs)
      max_val = max(max_val, current_val)
      avail = round(score / current_val, 4)
      non_zero = len([x for x in epochs if x > 0])
      nr_eps = len(epochs)
      prev_epoch = self.get_time_epoch() - 1
      first_seen = self.data[node_addr][EPCT.FIRST_SEEN]
      last_seen = self.data[node_addr][EPCT.LAST_SEEN]
      if nr_eps != prev_epoch:
        raise ValueError("Epochs mismatch for node: {} - total {} vs prev {}".format(
          node_addr, nr_eps, prev_epoch
        ))
      stats[node_addr] = {
        'name' : node_name,
        'non_zero' : non_zero,
        'availability' : avail,
        'score' : score,
        'first_seen' : first_seen,
        'last_seen' : last_seen,
      }
    if display:
      str_stats = json.dumps(stats, indent=2)
      self.P("EpochManager stats (max_score: {}, nr_eps: {}):\n{}".format(
        max_val, nr_eps,
        str_stats
      ))
    return stats

  def get_last_sync_epoch(self):
    """
    Returns the last sync epoch.

    Returns
    -------
    int
      The last sync epoch.
    """
    return self.__full_data['LAST_SYNC_EPOCH']

  def get_epoch_availability(self, epoch):
    """
    Returns the availability table for a given epoch.

    Parameters
    ----------
    epoch : int
      The epoch id.

    Returns
    -------
    dict
      The availability table for the specified epoch.
    """

    availability_table = {}

    for node_addr in self.__data:
      epochs: defaultdict = self.get_node_epochs(node_addr)
      availability_table[node_addr] = {
        "VALUE" : epochs.get(epoch, 0),
        "SIGNATURES" : self.__data[node_addr][EPCT.SIGNATURES].get(epoch, [])
      }
    # end for each node

    return availability_table


  def update_epoch_availability(self, epoch, availability_table):
    """
    Updates the epoch availability for a given epoch.

    !! IMPORTANT !!
    ---------------
    Make sure the epoch is strictly greater than the last sync epoch.
    It is ideal that this method is called with `epoch == last_sync_epoch + 1`.

    Parameters
    ----------
    epoch : int
      The epoch id.

    availability_table : dict
      The availability table.
    """
    last_sync_epoch = self.get_last_sync_epoch()

    assert epoch > last_sync_epoch, \
      f"Epoch {epoch} is not greater than last sync epoch {last_sync_epoch}"

    for node_addr in availability_table:
      if node_addr not in self.__data:
        self.__initialize_new_node(node_addr)
      self.__data[node_addr][EPCT.EPOCHS][epoch] = availability_table[node_addr]["VALUE"]
      self.__data[node_addr][EPCT.SIGNATURES][epoch] = availability_table[node_addr]["SIGNATURES"]
    self.__full_data['LAST_SYNC_EPOCH'] = epoch

    return




if __name__ == '__main__':
  from naeural_core.core_logging import Logger
  from naeural_core.main.net_mon import NetworkMonitor
  
  FN_NETWORK = r"_local_cache\_data\network_monitor\db.pkl"
  
  l = Logger('EPOCH', base_folder='.', app_folder='_local_cache')
  
  DATES = [
    '2024-07-08 12:00:00',
    '2024-07-07 12:00:00',
    '2024-07-08 12:00:00',
    '2024-07-09 12:00:00',
    '2024-07-10 12:00:00',
  ]
  
  NODES = [
    '0xai_AkyWQ91tdk0QdJfH70nmRG6euFjxwYf1FSC7mBdtIbTh',
    '0xai_AgNxIxNN6RsDqBa0d5l2ZQpy7y-5bnbP55xej4OvcitO',
  ]
  
  # make sure you have a recent (today) save network status
  eng = EpochsManager(log=l, owner=1234, debug_date=DATES[0], debug=True)
  eng2 = EpochsManager(log=l, owner=None, debug_date=DATES[1])
  assert id(eng) == id(eng2)
    
  
  if True:
    netmon = NetworkMonitor(
      log=l, node_name='aid_hpc', node_addr='0xai_AgNxIxNN6RsDqBa0d5l2ZQpy7y-5bnbP55xej4OvcitO',
      epoch_manager=eng
    )
  else:
    netmon = NetworkMonitor(
      log=l, node_name='aid_hpc', node_addr='0xai_AgNxIxNN6RsDqBa0d5l2ZQpy7y-5bnbP55xej4OvcitO'
    )
    
  eng.owner = netmon
  
  assert id(eng) == id(netmon.epoch_manager)  

  has_data = netmon.network_load_status(FN_NETWORK)
  
  if has_data:    
    l.P("Current time epoch is: {} ({})".format(eng.get_time_epoch(), eng.epoch_to_date()))
    
    nodes = netmon.all_nodes
        
    dct_hb = {}
    
    # now check the nodes for some usable data
    _current_epoch = eng.get_time_epoch()
    for node_addr in nodes:
      hbs = netmon.get_box_heartbeats(node_addr)
      idx = -1
      done = False
      good_hbs = defaultdict(list)
      for hb in hbs:
        ep = eng.get_epoch_id(hb[ct.PAYLOAD_DATA.EE_TIMESTAMP])
        if ep >= _current_epoch:
          good_hbs[ep].append(hb)
      if len(good_hbs) > 0:
        dct_hb[node_addr] = good_hbs
    
    l.P("Data available for epochs:\n{}".format(
      "\n".join(["{}: {}".format(x, list(dct_hb[x].keys())) for x in dct_hb]) 
    ))
    
    
    for step in range(5):
      current_date = DATES[step]
      eng._set_dbg_date(current_date)
      epoch = eng.get_epoch_id(current_date)
      l.P("Running step {} - epoch {} / {}".format(
        step, epoch, current_date), color='b'
      )
      epoch_has_data = any([epoch in dct_hb[x] for x in dct_hb])
      if epoch_has_data:
        l.P("Starting registering data for epoch {}...".format(eng.get_current_epoch()), color='b')
      data_counter = 0
      for node_addr in dct_hb:
        for hb in dct_hb[node_addr][epoch]:
          eng.register_data(node_addr, hb)
          data_counter += 1
      if data_counter > 0:
        l.P("Data loaded ({}) for epoch {}.".format(
          data_counter, eng.get_current_epoch()), color='g'
        )
      else:
        l.P("No data registered for epoch {}.".format(eng.get_current_epoch()), color='r')
      #endif had data
    #endfor each step
    final_date = DATES[-1]
    l.P("Done all steps, setting final date: {}".format(final_date), color='b')
    eng._set_dbg_date(final_date)    
    eng.maybe_close_epoch()
    
    l.P('{}: {}'.format(
      eng.get_node_name(NODES[-2]), eng.get_node_epochs(NODES[-2], as_list=True))
    )
    l.P('{}: {}'.format(
      eng.get_node_name(NODES[-1]), eng.get_node_epochs(NODES[-1], as_list=True))
    )    
    
    inf = eng.get_stats()
    
    # l.show_timers()
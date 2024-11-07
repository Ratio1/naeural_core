MAX_LEN = 1000
IDS_DEQUE = 'IDS_DEQUE'
SEND_LOWERED = 'SEND_LOWERED'
IDS_SET = 'IDS_SET'


class _AlertTrackerMixin(object):
  """
  The purpose of this mixin is for us to determine if we should send
  a certain alert or not.
  To do that we will check if all the alertable entities in the current frame
  already created an alert before.
  If at least one entity E from the current frame was never in an alertable state
  when an alert was risen or if enough entities were seen between the last alert
  entity E was seen into and the current alert we will send the current event.
  The entity identification is based on a centroid object tracker.
  Also, 2 different Alerter objects will perform the above described analysis
  independently.
  """
  def __init__(self):
    """
    For each alerter we will keep track of its already alerted ids and whether
    or not we already sent a "raised alert" event and have to also send a "lowered
    alert" event.
    Each alerter will have its own dictionary with the following items:
      1) "IDS_DEQUE" - deque of the already alerted ids
      2) "IDS_SET" - set of already alerted ids (this is used strictly for efficiency reasons)
    """
    self.alerters_data = {}
    # self.alerted_ids = {}
    # self.send_lowered_alert = True
    # self.alerted_ids_set = {}
    super(_AlertTrackerMixin, self).__init__()
    return

  @property
  def cfg_alert_tracker_maxlen(self):
    return self._instance_config.get('ALERT_TRACKER_MAX_LEN', MAX_LEN)

  @property
  def cfg_send_all_alerts(self):
    return self._instance_config.get("SEND_ALL_ALERTS", False)

  def create_alerter_data(self, alerter='default'):
    self.alerters_data[alerter] = {
      IDS_DEQUE: self.deque(maxlen=self.cfg_alert_tracker_maxlen),
      IDS_SET: set(),
      SEND_LOWERED: True
    }
    return

  def full_deque(self, alerter='default'):
    ids_deque = self.alerters_data[alerter][IDS_DEQUE]
    return len(ids_deque) == ids_deque.maxlen

  def search_id(self, id, alerter='default'):
    if alerter not in self.alerters_data.keys():
      self.create_alerter_data(alerter=alerter)
    return id in self.alerters_data[alerter][IDS_SET]

  def add_track_id(self, id, alerter='default'):
    if not self.search_id(id=id, alerter=alerter):
      if self.full_deque(alerter=alerter):
        # in case the deque is full the first id in the deque will be removed from it
        # therefore, we have to remove it from the set too
        first_id, _ = self.alerters_data[IDS_DEQUE][0]
        self.alerters_data[alerter][IDS_SET].remove(first_id)
      now = self.time()
      self.alerters_data[alerter][IDS_DEQUE].append((id, now))
      self.alerters_data[alerter][IDS_SET].add(id)
      return True

    return False

  def add_track_ids(self, ids, alerter='default'):
    # returns True if at least one id is not alerted before
    # False otherwise
    if len(ids) > 0:
      return max(self.add_track_id(id=x, alerter=alerter) for x in ids)
    return False

  def check_event_sending(self, alert_ids, alert_state, alerter='default'):
    if self.cfg_send_all_alerts:
      return True

    if alerter not in self.alerters_data.keys():
      self.create_alerter_data(alerter=alerter)
    if alert_state:
      # raised alert
      # if the current alert is raised we check to see if we will send it
      self.alerters_data[alerter][SEND_LOWERED] = self.add_track_ids(ids=alert_ids, alerter=alerter)

    # in case the current alert is lowered we will send it only if we also sent the last raised alert
    return self.alerters_data[alerter][SEND_LOWERED]

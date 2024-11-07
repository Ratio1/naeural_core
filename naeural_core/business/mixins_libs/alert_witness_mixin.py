class _AlertWitnessMixin(object):
  def __init__(self):
    """
    self.last_witness - dictionary with the last alert-able and non alert-able
    witness image in each alerter
    self.last_witness[alerter][0] - data about last non alert-able witness image based on the specified alerter
    self.last_witness[alerter][1] - data about last alert-able witness image based on the specified alerter
    """
    self.__last_witness_kwargs = {}
    super(_AlertWitnessMixin, self).__init__()
    return

  """
  Update data for the current or any other state of the specified alerter
  """

  def alerter_get_current_frame_state(self, observation, alerter='default'):
    """
    This function returns the possible next alerter position based on the current alerter state and the current observation.

    If the current observation can change the alerter state from A to B, the function returns the position of the state B.
    (this ensures that an alertable observation will be saved to the alertable state, no matter the current alerter state)

    If the current observation cannot change the alerter state from A to B, the function returns the position of the state A.

    Parameters
    ----------
    observation : float
        The current observation

    alerter : str, optional
        The alerter name, by default 'default'

    Returns
    -------
    int
        The possible next alerter position
    """
    pos = None
    # we use this instead of self.cfg_alert_raise_value and self.cfg_alert_lower_value
    # because we may have multiple alerters with different configurations
    dct_alerter_config = self.get_alerter(alerter=alerter).get_setup_values()

    if observation is not None:
      # if the current observation can change the alerter state from A to B
      # we save the observation to state B
      # (we do not care about the current alerter state here)
      if observation > dct_alerter_config['raise_alert_value']:
        pos = 1
      elif observation < dct_alerter_config['lower_alert_value']:
        pos = 0

    if pos is None:
      # if the current observation cannot change the alerter status from A to B
      # we save the observation to state A
      # (the current alerter state)
      if self.alerter_is_alert():
        pos = 1
      else:
        pos = 0
    return pos

  def update_witness_kwargs(self, witness_args, pos, alerter='default'):
    if alerter not in self.__last_witness_kwargs.keys():
      # instantiating the values for the current alerter if no data
      # was previously provided
      self.__last_witness_kwargs[alerter] = [None, None]
    self.__last_witness_kwargs[alerter][pos] = witness_args

    return

  """
  Get data for the current or any other state of the specified alerter
  """

  def get_current_witness_kwargs(self, pos=None, alerter='default', demo_mode=False):
    if pos is None:
      if demo_mode:
        pos = self.alerter_get_current_frame_state(
          observation=self.alerter_get_last_value(alerter=alerter),
          alerter=alerter
        )
      else:
        pos = int(self.alerter_is_alert(alerter=alerter))
    witness_args = self.__last_witness_kwargs[alerter][pos]
    # if witness_args is None:
    #   return {}
    return witness_args

  """
  Get generated witness from data provided by get_current_witness_kwargs
  """

  def get_current_witness(self, pos=None, alerter='default'):
    witness_args = self.get_current_witness_kwargs(pos=pos, alerter=alerter)
    return self.get_witness_image(**witness_args) if witness_args is not None else None

class StateMachine(object):
  """
  state_machine_transitions = {
    'STATE': {
      'STATE_CALLBACK': callable,
      'TRANSITIONS': [
        {
          'NEXT_STATE': 'STATE',
          'TRANSITION_CONDITION': callable,
          'ON_TRANSITION_CALLBACK': callable
        },
        ...
      ]
    },
    ...
  } 
  """

  def __init__(self, owner, name, state_machine_transitions, state, on_step_callback, full_payloads=False):
    self.__owner = owner
    self.__name = name
    self.__state_machine_transitions = state_machine_transitions
    self.__possible_states = list(state_machine_transitions.keys())
    self.__state = state
    self.__on_step_callback = on_step_callback
    self.__full_payloads = full_payloads
    return
  
  def state_machine_step(self):
    state_callback = self.__state_machine_transitions[self.__state]['STATE_CALLBACK']
    lst_transitions = self.__state_machine_transitions[self.__state]['TRANSITIONS']

    # call the current state callback
    state_callback()

    # check that only one transition can be done in the current state as we need to have a deterministic behavior
    lst_possible_transitions = [transition for transition in lst_transitions if transition['TRANSITION_CONDITION']()]
    error_message = f"WARNING! Multiple transitions can be done in the current state {self.__state} (name {self.__name}). Transitions: {lst_possible_transitions}"
    assert len(lst_possible_transitions) <= 1, error_message

    # transition to the next state if possible
    if len(lst_possible_transitions) == 0:
      return
    
    transition = lst_possible_transitions[0]
    transition['ON_TRANSITION_CALLBACK']()
    
    next_state = transition['NEXT_STATE']
    assert next_state in self.__possible_states, f"Transition to unknown state! Target state: `{next_state}` Possible states: `{self.__possible_states}`"

    if self.__state == next_state:
      # no state change
      return

    msg = f"{self.__name} transition from state `{self.__state}` to state `{next_state}`"

    if self.__full_payloads:
      self.__owner._create_notification(msg=msg, from_state=self.__state, to_state=next_state)
    self.__owner.P(msg, color='y')
    self.__state = next_state

    if callable(self.__on_step_callback):
      self.__on_step_callback()
    return

  def get_current_state(self):
    return self.__state

class _StateMachineAPIMixin(object):
  def __init__(self):
    super(_StateMachineAPIMixin, self).__init__()
    self.__state_machines = {}
    return

  def state_machine_api_init(
    self, 
    name: str, 
    state_machine_transitions: dict, 
    initial_state: str, 
    on_successful_step_callback: callable, 
    full_payloads=False
  ):
    if name in self.__state_machines:
      raise Exception(f"State machine with name `{name}` already exists!")
    self.__state_machines[name] = StateMachine(
      owner=self, 
      name=name, 
      state_machine_transitions=state_machine_transitions, 
      state=initial_state, 
      on_step_callback=on_successful_step_callback, 
      full_payloads=full_payloads
    )
    return

  def state_machine_api_step(self, name: str):
    self.__state_machines[name].state_machine_step()
    return

  def state_machine_api_destroy(self, name: str):
    self.__state_machines.pop(name, None)
    return

  def state_machine_api_get_current_state(self, name: str):
    if name not in self.__state_machines:
      raise Exception(f"State machine with name `{name}` does not exist!")
    return self.__state_machines[name].get_current_state()

  def state_machine_api_callback_do_nothing(self):
    return

  def state_machine_api_callback_always_true(self):
    return True

  def state_machine_api_callback_always_false(self):
    return False

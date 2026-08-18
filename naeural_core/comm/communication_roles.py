"""Channel-derived communication receive roles.

Legacy instance names mix send and receive responsibilities. These helpers make
heartbeat and command consumers depend on the configured MQTT channel instead.
"""


CTRL_CHANNEL = "CTRL_CHANNEL"
CONFIG_CHANNEL = "CONFIG_CHANNEL"


def _normalize_channel(channel):
  if isinstance(channel, str):
    return channel.upper()
  return channel


def resolve_receive_roles(instances, require_heartbeat_receiver=True):
  """Resolve the unique heartbeat and command receive owners.

  Parameters
  ----------
  instances : dict
    Mapping of communicator instance name to its ``SEND_TO``/``RECV_FROM``
    configuration.
  require_heartbeat_receiver : bool, optional
    Set false after oracle-only policy deliberately removes the CTRL receiver.

  Returns
  -------
  dict
    ``heartbeat`` and ``command`` instance names.

  Raises
  ------
  ValueError
    If either required receive channel has zero or multiple owners.
  """
  owners = {
    CTRL_CHANNEL: [],
    CONFIG_CHANNEL: [],
  }
  for instance_name, paths in instances.items():
    if not isinstance(paths, dict):
      continue
    recv_channel = _normalize_channel(paths.get("RECV_FROM"))
    if recv_channel in owners:
      owners[recv_channel].append(instance_name)

  for channel, channel_owners in owners.items():
    required = channel != CTRL_CHANNEL or require_heartbeat_receiver
    if len(channel_owners) > 1 or (required and len(channel_owners) != 1):
      raise ValueError(
        "Expected {} receive owner(s) for {}, found {}: {}".format(
          1 if required else "zero or one",
          channel,
          len(channel_owners),
          channel_owners,
        )
      )

  return {
    "heartbeat": owners[CTRL_CHANNEL][0] if owners[CTRL_CHANNEL] else None,
    "command": owners[CONFIG_CHANNEL][0] if owners[CONFIG_CHANNEL] else None,
  }


def select_receive_communicators(communicators, channel_name):
  """Select runtime communicators by configured receive channel."""
  expected_channel = _normalize_channel(channel_name)
  selected = []
  for communicator in communicators.values():
    recv_channel = getattr(communicator, "recv_channel_name", None)
    if recv_channel is None:
      recv_channel = getattr(communicator, "_recv_channel_name", None)
    if _normalize_channel(recv_channel) == expected_channel:
      selected.append(communicator)
  return selected

# Loaded on first use (PEP 562): the uvicorn app and the IPC manager process of every FastAPI plugin import
# only naeural_core.utils.uvicorn_fast_api_ipc_manager and must not pull in the SDK, the Logger and torch.
def __getattr__(name):
  if name == 'DecentrAIObject':
    from .decentrai_object import DecentrAIObject as value
  elif name in ('Logger', 'SBLogger'):
    from . import core_logging
    value = getattr(core_logging, name)
  else:
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
  globals()[name] = value
  return value

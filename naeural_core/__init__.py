# Loaded on first use (PEP 562): the uvicorn app and the IPC manager process of every FastAPI plugin import
# only naeural_core.utils.uvicorn_fast_api_ipc_manager and must not pull in the SDK, the Logger and torch.
def __getattr__(name):
  """
  Import a package-root name on its first use instead of at package import (PEP 562).

  Python calls this module-level hook only when ``naeural_core.<name>`` is not already in the module
  globals. The package root used to import ``DecentrAIObject``, ``Logger`` and ``SBLogger`` eagerly, so
  any process importing any ``naeural_core`` submodule also loaded the SDK, the full Logger and torch.
  That included the uvicorn app and the IPC manager process of every FastAPI plugin, which only need
  ``naeural_core.utils.uvicorn_fast_api_ipc_manager``. Serving the three names from here keeps those
  processes light, while every in-tree ``from naeural_core import <name>`` keeps working unchanged.

  Parameters
  ----------
  name : str
    Attribute requested from the ``naeural_core`` package, e.g. ``'Logger'``.

  Returns
  -------
  type or functools.partial
    The requested object: the ``DecentrAIObject`` or ``Logger`` class, or ``SBLogger`` (a
    ``functools.partial`` of ``Logger``). It is also stored in the module globals, so later lookups find
    it directly and do not reach this hook again.

  Raises
  ------
  AttributeError
    For any other name, so ``hasattr`` returns False and ``from naeural_core import <submodule>`` falls
    through to the normal import system.

  Notes
  -----
  The eager imports cost about 300 MB RSS in each uvicorn process. A test imports the IPC manager module
  in a fresh interpreter and checks that torch, numpy, pandas, the SDK and the core Logger stay unloaded.

  Examples
  --------
  >>> import naeural_core.utils.uvicorn_fast_api_ipc_manager   # loads neither torch nor the core Logger
  >>> from naeural_core import Logger                          # core_logging is imported only now
  """
  if name == 'DecentrAIObject':
    # Imported only on first use: decentrai_object pulls in the SDK and the full Logger.
    from .decentrai_object import DecentrAIObject as value
  elif name in ('Logger', 'SBLogger'):
    # core_logging loads the full Logger stack (its mixins import torch); import it only when asked for.
    from . import core_logging
    # Pick the requested name (the Logger class or the SBLogger partial) from the module now loaded.
    value = getattr(core_logging, name)
  else:
    # Names this hook does not serve: AttributeError lets submodule imports and hasattr() behave as usual.
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
  # Cache it in the module globals: PEP 562 calls __getattr__ only for missing names, so later
  # lookups of this name are plain attribute hits that skip this hook.
  globals()[name] = value
  # Hand the object to `from naeural_core import <name>` or `naeural_core.<name>`.
  return value

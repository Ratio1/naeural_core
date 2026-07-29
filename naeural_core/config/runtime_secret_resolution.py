"""Pure helpers for building redacted and runtime pipeline configurations."""

import hashlib
import json
import os

from copy import deepcopy

from naeural_core import constants as ct
from ratio1.const.base import dAuth


DAUTH_SECRET_PLACEHOLDER = dAuth.DAUTH_SECRET_PLACEHOLDER
ENV_SECRET_PREFIX = "$EE_"
_MISSING = object()
_SECRET_STRUCTURE_MARKER = "__R1_SECRET_STRUCTURE_MARKER__"


class RuntimeSecretResolutionError(ValueError):
  """Raised when a redacted pipeline cannot be resolved without ambiguity."""


def resolve_environment_references(value, environment=None):
  """Return a copy with pipeline environment references resolved recursively.

  Parameters
  ----------
  value : Any
      JSON-like pipeline value to copy and resolve.
  environment : Mapping[str, str] or None, optional
      Environment source. ``os.environ`` is used when omitted.

  Returns
  -------
  Any
      A detached runtime value. Missing references resolve to ``None``, matching
      the historical stream-loading behavior.
  """
  environment = os.environ if environment is None else environment
  if isinstance(value, dict):
    return {
      key: resolve_environment_references(item, environment=environment)
      for key, item in value.items()
    }
  if isinstance(value, list):
    return [
      resolve_environment_references(item, environment=environment)
      for item in value
    ]
  if isinstance(value, str) and value.startswith(ENV_SECRET_PREFIX):
    return environment.get(value[1:])
  return deepcopy(value)


def contains_dauth_secret_placeholder(value):
  """Return whether a JSON-like value contains an exact dAuth placeholder."""
  if value == DAUTH_SECRET_PLACEHOLDER:
    return True
  if isinstance(value, dict):
    return any(contains_dauth_secret_placeholder(item) for item in value.values())
  if isinstance(value, list):
    return any(contains_dauth_secret_placeholder(item) for item in value)
  return False


def get_dauth_pipeline_identity(pipeline_name, pipeline_config):
  """Build the cache identity for a placeholder-bearing pipeline.

  Parameters
  ----------
  pipeline_name : str
      Name under which the pipeline is stored by the orchestrator.
  pipeline_config : dict
      Canonical redacted pipeline configuration.

  Returns
  -------
  tuple
      ``(pipeline_name, job_id, date_updated, redacted_hash)``.

  Raises
  ------
  RuntimeSecretResolutionError
      If ``DEEPLOY_SPECS`` or its version fields are missing or malformed.
  """
  specs = pipeline_config.get(ct.CONFIG_STREAM.DEEPLOY_SPECS)
  if not isinstance(specs, dict):
    raise RuntimeSecretResolutionError("DEEPLOY_SPECS must be a dictionary")

  job_id = specs.get("job_id", specs.get("JOB_ID"))
  date_updated = specs.get("date_updated", specs.get("DATE_UPDATED"))
  if job_id in [None, ""]:
    raise RuntimeSecretResolutionError("DEEPLOY_SPECS.job_id is required")
  if date_updated in [None, ""]:
    raise RuntimeSecretResolutionError("DEEPLOY_SPECS.date_updated is required")

  redacted_plugins = pipeline_config.get(ct.CONFIG_STREAM.K_PLUGINS)
  if not isinstance(redacted_plugins, list):
    raise RuntimeSecretResolutionError("PLUGINS must be a list")

  try:
    canonical = json.dumps(
      redacted_plugins,
      sort_keys=True,
      separators=(",", ":"),
      ensure_ascii=True,
    )
  except (TypeError, ValueError) as exc:
    raise RuntimeSecretResolutionError(
      "canonical PLUGINS configuration is not JSON serializable"
    ) from exc
  redacted_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
  return (str(pipeline_name), str(job_id), str(date_updated), redacted_hash)


def extract_dauth_plugins_secret_tree(response, expected_job_id):
  """Validate a dAuth client response and return its ``PLUGINS`` secret tree.

  Both the endpoint response shape and the already-unwrapped bundle shape are
  accepted so the worker remains compatible with the blockchain client API.
  """
  if not isinstance(response, dict):
    raise RuntimeSecretResolutionError("dAuth response must be a dictionary")
  bundle = response.get("secret_bundle", response)
  if not isinstance(bundle, dict):
    raise RuntimeSecretResolutionError("dAuth secret bundle must be a dictionary")

  bundle_job_id = bundle.get("job_id")
  if bundle_job_id in [None, ""] or str(bundle_job_id) != str(expected_job_id):
    raise RuntimeSecretResolutionError("dAuth secret bundle job_id mismatch")

  job_secrets = bundle.get("job_secrets")
  if not isinstance(job_secrets, dict):
    raise RuntimeSecretResolutionError("dAuth job_secrets must be a dictionary")
  plugins = job_secrets.get(ct.CONFIG_STREAM.K_PLUGINS, _MISSING)
  if plugins is _MISSING:
    raise RuntimeSecretResolutionError("dAuth job_secrets.PLUGINS is required")
  return deepcopy(plugins)


def resolve_dauth_plugin_secrets(redacted_plugins, runtime_plugins, secret_plugins):
  """Overlay a sparse dAuth secret tree onto runtime plugin configuration.

  Only exact ``__R1_DAUTH_SECRET__`` leaves are replaced. Secret-tree entries
  without a matching placeholder are ignored. Any missing entry or structural
  type mismatch on a path that contains a placeholder rejects the whole tree.
  """
  def resolve(redacted, runtime, secret, path):
    if redacted == DAUTH_SECRET_PLACEHOLDER:
      if secret is _MISSING or secret is None:
        raise RuntimeSecretResolutionError(
          "missing dAuth secret at {}".format(path)
        )
      if isinstance(secret, (dict, list)):
        raise RuntimeSecretResolutionError(
          "dAuth secret type mismatch at {}".format(path)
        )
      return deepcopy(secret)

    if not contains_dauth_secret_placeholder(redacted):
      return deepcopy(runtime)

    if isinstance(redacted, dict):
      if not isinstance(secret, dict):
        raise RuntimeSecretResolutionError(
          "dAuth secret structure mismatch at {}".format(path)
        )
      if not isinstance(runtime, dict):
        raise RuntimeSecretResolutionError(
          "runtime plugin structure mismatch at {}".format(path)
        )
      result = deepcopy(runtime)
      for key, item in redacted.items():
        if contains_dauth_secret_placeholder(item):
          result[key] = resolve(
            item,
            runtime.get(key, _MISSING),
            secret.get(key, _MISSING),
            "{}.{}".format(path, key),
          )
      return result

    if isinstance(redacted, list):
      if not isinstance(secret, list):
        raise RuntimeSecretResolutionError(
          "dAuth secret structure mismatch at {}".format(path)
        )
      if not isinstance(runtime, list) or len(runtime) != len(redacted):
        raise RuntimeSecretResolutionError(
          "runtime plugin structure mismatch at {}".format(path)
        )
      result = deepcopy(runtime)
      for idx, item in enumerate(redacted):
        if contains_dauth_secret_placeholder(item):
          item_secret = secret[idx] if idx < len(secret) else _MISSING
          result[idx] = resolve(
            item,
            runtime[idx],
            item_secret,
            "{}[{}]".format(path, idx),
          )
      return result

    raise RuntimeSecretResolutionError(
      "placeholder-bearing dAuth path has invalid structure at {}".format(path)
    )

  return resolve(redacted_plugins, runtime_plugins, secret_plugins, "PLUGINS")


def build_runtime_pipeline_config(canonical_pipeline, secret_plugins=None, environment=None):
  """Build a detached runtime pipeline from canonical redacted configuration.

  Parameters
  ----------
  canonical_pipeline : dict
      Canonical pipeline containing environment references and placeholders.
  secret_plugins : Any, optional
      Sparse dAuth ``PLUGINS`` tree. Required when placeholders are present.
  environment : Mapping[str, str] or None, optional
      Environment source used for ``$EE_*`` references.

  Returns
  -------
  dict
      Runtime-only pipeline configuration.
  """
  runtime_pipeline = resolve_environment_references(
    canonical_pipeline,
    environment=environment,
  )
  if not contains_dauth_secret_placeholder(canonical_pipeline):
    return runtime_pipeline

  redacted_plugins = canonical_pipeline.get(ct.CONFIG_STREAM.K_PLUGINS, _MISSING)
  runtime_plugins = runtime_pipeline.get(ct.CONFIG_STREAM.K_PLUGINS, _MISSING)
  if redacted_plugins is _MISSING or runtime_plugins is _MISSING:
    raise RuntimeSecretResolutionError(
      "dAuth placeholders must be contained in PLUGINS"
    )
  runtime_pipeline[ct.CONFIG_STREAM.K_PLUGINS] = resolve_dauth_plugin_secrets(
    redacted_plugins=redacted_plugins,
    runtime_plugins=runtime_plugins,
    secret_plugins=secret_plugins,
  )
  if contains_dauth_secret_placeholder(runtime_pipeline):
    raise RuntimeSecretResolutionError(
      "dAuth placeholders outside PLUGINS cannot be resolved"
    )
  return runtime_pipeline


def build_capture_pipeline_config(canonical_pipeline, environment=None):
  """Build a capture-safe pipeline without exposing plugin secret values.

  Capture configuration may contain local ``$EE_*`` references outside
  ``PLUGINS`` (for example, a capture URL). Those values are resolved for the
  capture subsystem, while the complete canonical plugin tree is restored so
  dAuth and local plugin secrets remain available only to BusinessManager.
  """
  plugins_key = ct.CONFIG_STREAM.K_PLUGINS
  canonical_plugins = canonical_pipeline.get(plugins_key, _MISSING)
  capture_pipeline = resolve_environment_references(
    canonical_pipeline,
    environment=environment,
  )
  if canonical_plugins is not _MISSING:
    capture_pipeline[plugins_key] = deepcopy(canonical_plugins)
  return capture_pipeline


def _contains_secret_reference(value):
  if isinstance(value, str):
    return value.startswith(ENV_SECRET_PREFIX) or value == DAUTH_SECRET_PLACEHOLDER
  if isinstance(value, dict):
    return any(_contains_secret_reference(item) for item in value.values())
  if isinstance(value, list):
    return any(_contains_secret_reference(item) for item in value)
  return False


def _index_list_by_identity(values, identity_key):
  indexed = {}
  for item in values:
    if not isinstance(item, dict) or identity_key not in item:
      return None
    identity = item[identity_key]
    if identity in indexed:
      raise RuntimeSecretResolutionError(
        "duplicate {} in persisted configuration".format(identity_key)
      )
    indexed[identity] = item
  return indexed


def _mask_protected_list_structure(proposed, canonical):
  """Mask only canonical secret leaves while preserving surrounding shape."""
  if (
    isinstance(canonical, str)
    and (
      canonical.startswith(ENV_SECRET_PREFIX)
      or canonical == DAUTH_SECRET_PLACEHOLDER
    )
  ):
    return _SECRET_STRUCTURE_MARKER
  if isinstance(canonical, dict) and isinstance(proposed, dict):
    return {
      key: _mask_protected_list_structure(proposed.get(key, _MISSING), value)
      for key, value in canonical.items()
    } | {
      key: deepcopy(value)
      for key, value in proposed.items()
      if key not in canonical
    }
  if isinstance(canonical, list) and isinstance(proposed, list):
    return [
      _mask_protected_list_structure(
        value,
        canonical[idx] if idx < len(canonical) else _MISSING,
      )
      for idx, value in enumerate(proposed)
    ]
  return deepcopy(proposed)


def overlay_canonical_secret_references(proposed, canonical):
  """Overlay canonical secret references onto a proposed persisted value.

  Only paths present in ``proposed`` are returned. This preserves delta-save
  semantics while replacing runtime plaintext wherever the canonical value is
  a ``$EE_*`` reference or exact dAuth placeholder.
  """
  if (
    isinstance(canonical, str)
    and (
      canonical.startswith(ENV_SECRET_PREFIX)
      or canonical == DAUTH_SECRET_PLACEHOLDER
    )
  ):
    return canonical

  if isinstance(proposed, dict) and isinstance(canonical, dict):
    return {
      key: overlay_canonical_secret_references(value, canonical.get(key, _MISSING))
      if key in canonical else deepcopy(value)
      for key, value in proposed.items()
    }
  if isinstance(proposed, list) and isinstance(canonical, list):
    if _contains_secret_reference(canonical):
      for identity_key in (ct.CONFIG_PLUGIN.K_SIGNATURE, ct.CONFIG_INSTANCE.K_INSTANCE_ID):
        canonical_by_identity = _index_list_by_identity(canonical, identity_key)
        if canonical_by_identity is None:
          continue
        proposed_by_identity = _index_list_by_identity(proposed, identity_key)
        if proposed_by_identity is None:
          raise RuntimeSecretResolutionError(
            "cannot safely persist reordered {} entries".format(identity_key)
          )
        if set(proposed_by_identity) != set(canonical_by_identity):
          raise RuntimeSecretResolutionError(
            "cannot safely persist changed {} entries".format(identity_key)
          )
        return [
          overlay_canonical_secret_references(
            value,
            canonical_by_identity.get(value[identity_key], _MISSING),
          )
          for value in proposed
        ]
      if (
        _mask_protected_list_structure(proposed, canonical)
        != _mask_protected_list_structure(canonical, canonical)
      ):
        raise RuntimeSecretResolutionError(
          "cannot safely persist structural changes to a protected list"
        )
    return [
      overlay_canonical_secret_references(
        value,
        canonical[idx] if idx < len(canonical) else _MISSING,
      )
      for idx, value in enumerate(proposed)
    ]
  return deepcopy(proposed)

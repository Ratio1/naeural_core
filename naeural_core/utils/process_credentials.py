"""Keep inherited credentials out of unrelated subprocesses and command logs."""

import os
import re
import subprocess


CREDENTIAL_ENV_NAMES = frozenset({
  "EE_CLOUDFLARE_TOKEN_DAUTH_MANAGER",
  "EE_CLOUDFLARE_TOKEN_DEEPLOY_MANAGER",
  "EE_CLOUDFLARE_TOKEN_EPOCH_MANAGER",
  "EE_CLOUDFLARE_TOKEN_LIVENESS_API",
  "EE_CLOUDFLARE_TOKEN_RELEASE_APP",
  "EE_CLOUDFLARE_TOKEN_TUNNELS_MANAGER",
  "EE_GITVER",
  "EE_IPFS_API_KEY_BASE64",
  "EE_MINIO_ACCESS_KEY",
  "EE_MINIO_SECRET_KEY",
  "EE_MQTT",
  "EE_NGROK_AUTH_TOKEN",
  "EE_ORACLE_R1FS_SECRET",
  "EE_SWARM_KEY_CONTENT_BASE64",
  "R1EN_SYNC_SECRET",
})
REDACTED = "[REDACTED]"
_KEYWORDS = ("TOKEN", "SECRET", "KEY", "PASS")
# Option suffixes cover application-specific prefixes while leaving options such as --token-file visible.
_SECRET_OPTION = r"(?:--(?:[a-z0-9]+[-_])*(?:token|password|pass|secret|(?:api|access|secret)[-_]key)|authtoken|add-authtoken)"
_SECRET_FLAG = re.compile(_SECRET_OPTION, re.IGNORECASE)
# A shell word can combine quoted and unquoted fragments. Also consume incomplete quoted values.
_SHELL_WORD = r"""(?:"(?:\\.|[^"\\])*(?:"|$)|'[^']*(?:'|$)|\\.|[^\s;&|"'\\])+"""
# A standalone secret flag or environment assignment keeps its name and loses its entire value.
_CREDENTIAL_ARGUMENT = re.compile(
  r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/\s@\"']+@"
  r"|(?<![\w-])(?P<option>" + _SECRET_OPTION +
  r")(?P<separator>=|\s+)(?P<argument>" + _SHELL_WORD + r")"
  r"|(?<![\w-])(?P<name>[a-z_][a-z0-9_]*)=(?P<assigned>" + _SHELL_WORD + r")",
  re.IGNORECASE,
)


def credential_env_names(environment):
  """Identify credential names in an environment mapping.

  Include established node names and find additional names by keyword.
  The result contains only names and can be used to select or report them.

  Parameters
  ----------
  environment : collections.abc.Iterable of str
    Environment names. A mapping is accepted because iteration yields its keys.

  Returns
  -------
  frozenset
    Names whose values must be treated as credentials.

  Examples
  --------
  >>> credential_env_names({"PATH": "/bin", "EE_MQTT": "example"})
  frozenset({'EE_MQTT'})

  Notes
  -----
  Match names without regard to case and retain their original spelling.
  Values need not be present for a name to be recognized.
  """
  # Match case-insensitively while retaining each original name for environment lookups.
  return frozenset(name for name in environment
                   if name.upper() in CREDENTIAL_ENV_NAMES or any(word in name.upper() for word in _KEYWORDS))


def child_environment(environment, extra_values=()):
  """Copy only environment settings safe for an unrelated child process.

  A setting containing the complete value of a named credential is private
  even when it embeds that value in a larger string.

  Parameters
  ----------
  environment : collections.abc.Mapping of str to str
    Parent environment.
  extra_values : iterable of str, optional
    Scoped credentials that must also be kept out of the inherited variables.

  Returns
  -------
  dict
    Ordinary inherited settings.

  Examples
  --------
  >>> child_environment({"PATH": "/bin", "EE_MQTT": "example"})
  {'PATH': '/bin'}

  Notes
  -----
  Named credential variables are removed even if empty. Empty credential
  values do not match unrelated variables. Nonempty values are compared as
  literal case-sensitive substrings, including inside other settings.
  """
  # Identify all named credentials once so ordinary keys need only a literal value check.
  names = credential_env_names(environment)
  # An empty credential is harmless and does not suppress unrelated empty settings.
  values = {environment[name] for name in names if environment[name]}
  # The explicitly scoped tunnel secret may occur under an otherwise ordinary name.
  values.update(value for value in extra_values if isinstance(value, str) and value)
  # Use exact, case-sensitive substrings. Patterns or partial credential matches could drop unrelated settings.
  return {name: value for name, value in environment.items()
          if name not in names and not any(secret in value for secret in values)}


def redact_command(command, environment, extra_values=()):
  """Render a command or diagnostic without its credential values.

  Keep command text as text and command vectors as lists.

  Parameters
  ----------
  command : str or sequence of str
    Text to redact or the command's individual arguments.
  environment : collections.abc.Mapping
    Credential values that may appear outside a named command option.
  extra_values : iterable of str, optional
    Scoped values from configuration that need the same redaction.

  Returns
  -------
  str or list
    Command retaining its executable, options and non-secret values.

  Examples
  --------
  >>> redact_command("tool --token 'two words' --mode normal", {})
  'tool --token [REDACTED] --mode normal'

  Notes
  -----
  Known literals are considered longest first. Credential options and
  assignment names are retained. An ordinary assignment loses its whole
  value if it contains a complete known credential.
  """
  # Match longer complete values first to avoid disclosing the remainder of overlapping credentials.
  secrets = {value for value in extra_values if isinstance(value, str)}
  # Include credentials declared by name in the calling process or app environment.
  secrets.update(environment[name] for name in credential_env_names(environment) if isinstance(environment[name], str))
  # Ignore empty and non-string values which cannot appear verbatim in command text.
  literals = sorted((secret for secret in secrets if isinstance(secret, str) and secret), key=len, reverse=True)
  # Replace literals and named arguments in one pass so inserted placeholders are never matched again.
  matcher = re.compile(_CREDENTIAL_ARGUMENT.pattern + (r"|(?P<literal>" + "|".join(map(re.escape, literals)) + ")"
                                                       if literals else ""), re.IGNORECASE)

  def replace(match):
    """Retain the matched option or assignment name while masking its value.

    Handle URL user information, credential options, assignments and known
    literals through the same regular expression pass.

    Parameters
    ----------
    match : re.Match
      A named option, an assignment, or a known credential value.

    Returns
    -------
    str
      The text safe for logging.

    Examples
    --------
    >>> replace(matcher.search("--token hidden"))
    '--token [REDACTED]'

    Notes
    -----
    Only the matched text is replaced. Other shell words stay available for
    diagnostics.
    """
    if match["scheme"]:
      # URL user information can contain credentials even without a named option.
      return match["scheme"] + REDACTED + "@"
    if match["option"]:
      # Preserve the option and original separator.
      return match["option"] + match["separator"] + REDACTED
    if match["name"]:
      # Preserve ordinary assignments, while masking an exact credential appearing in their value.
      return match["name"] + "=" + (
        REDACTED if credential_env_names((match["name"],)) else
        next((REDACTED for secret in literals if secret in match["assigned"]), match["assigned"])
      )
    return REDACTED

  if isinstance(command, str):
    return matcher.sub(replace, command)
  # An argv element can itself contain whitespace. Mask the whole next element of a credential flag.
  output, redact_next = [], False
  for arg in command:
    # Keep inline option values separate so an argument containing spaces is never partially exposed.
    flag, separator, _ = arg.partition("=")
    if redact_next:
      # Mask the entire argument, including any spaces or punctuation.
      output.append(REDACTED)
    elif separator and _SECRET_FLAG.fullmatch(flag):
      # Preserve the flag when its credential was passed with an equals sign.
      output.append(flag + "=" + REDACTED)
    elif separator and flag.isidentifier() and credential_env_names((flag,)):
      # A vector assignment's whole value belongs to its environment key.
      output.append(flag + "=" + REDACTED)
    else:
      # Apply ordinary assignments and known literal checks to each non-option argument.
      output.append(matcher.sub(replace, arg))
    # Consume the next value only for a standalone option that was itself kept.
    redact_next = not redact_next and _SECRET_FLAG.fullmatch(arg) is not None
  return output


def launch_with_tunnel_token(token, **popen_kwargs):
  """Start cloudflared with its configured credential on standard input.

  The command uses ``--token-file /dev/stdin``. The parent stages the token
  in a private anonymous pipe and closes both descriptors before returning.

  Parameters
  ----------
  token : str or None
    Token to read through standard input, or None for an ordinary launch.
  **popen_kwargs
    Subprocess options, including the token-free command in ``args``.
    Omit ``stdin`` when a token supplies that stream.

  Returns
  -------
  subprocess.Popen
    The launched command.

  Raises
  ------
  OSError
    Creating, writing or closing the pipe failed, or the subprocess could
    not be started. A nonblocking pipe write may raise ``BlockingIOError``.
  ValueError
    A write staged fewer bytes than the UTF-8 token contains, or a subprocess
    option was rejected. A token that cannot be encoded raises
    ``UnicodeEncodeError``.

  Examples
  --------
  >>> launch_with_tunnel_token(None, args=["cloudflared", "--version"])  # doctest: +SKIP

  Notes
  -----
  Launch only after the whole token is staged. Closing the write end gives
  cloudflared EOF. Pipe capacity is platform-dependent and measured in
  encoded bytes.
  """
  if token is None:
    return subprocess.Popen(**popen_kwargs)
  # The read descriptor is used only as the subprocess's stdin.
  read_fd, write_fd = os.pipe()
  try:
    try:
      # Stage the full token before launch without waiting on a child or a full pipe.
      os.set_blocking(write_fd, False)
      # A pipe consumes UTF-8 bytes, so compare the write against the encoded size.
      data = token.encode("utf-8")
      if os.write(write_fd, data) != len(data):
        # Abort before Popen so a partial write never launches with a truncated token.
        raise ValueError("Cloudflare token exceeds the standard-input pipe capacity.")
    finally:
      # EOF lets cloudflared finish reading the token file.
      os.close(write_fd)
    # Popen duplicates this descriptor onto stdin in the child.
    return subprocess.Popen(stdin=read_fd, **popen_kwargs)
  finally:
    # The parent cannot reuse the one-use input stream after this launch.
    os.close(read_fd)

class JsonUtil:
  @staticmethod
  def compare_json(left, right, keys_to_ignore=None) -> bool:
    """
    Compare two json strings
    Parameters
    ----------
    left
    right
    keys_to_ignore

    Returns
    -------
    bool
    """
    if keys_to_ignore is None:
      keys_to_ignore = []
    if left is None and right is None:
      return True
    elif left is None or right is None:
      return False
    elif isinstance(left, str) and isinstance(right, str):
      return left == right
    elif isinstance(left, list) and isinstance(right, list):
      return JsonUtil.__compare_json_list(left, right, keys_to_ignore)
    elif isinstance(left, dict) and isinstance(right, dict):
      return JsonUtil.__compare_json_dict(left, right, keys_to_ignore)
    else:
      return False

  @staticmethod
  def __compare_json_list(left, right, keys_to_ignore):
    """
    Compare two json lists
    Parameters
    ----------
    left
    right
    keys_to_ignore

    Returns
    -------
    bool
    """
    if len(left) != len(right):
      return False
    for i in range(len(left)):
      if not JsonUtil.compare_json(left[i], right[i], keys_to_ignore):
        return False
    return True

  @staticmethod
  def __compare_json_dict(left, right, keys_to_ignore):
    """
    Compare two json dicts
    Parameters
    ----------
    left
    right
    keys_to_ignore

    Returns
    -------
    bool
    """
    if len(left) != len(right):
      return False
    for key in left:
      if key not in right:
          return False
      if key in keys_to_ignore:
          continue
      if not left[key] == right[key]:
        return False
    return True

def hamming_distance(x, y):
  """Calculate the hamming distance (number of bits different) between the
  two integers given.
  >>> [hamming_distance(x, 15) for x in [0, 8, 10, 12, 14, 15]]
  [4, 3, 2, 2, 1, 0]
  """
  return bin(x ^ y).count('1')


def levenshtein_distance(s1, s2):
  if len(s1) < len(s2):
    return levenshtein_distance(s2, s1)

  # len(s1) >= len(s2)
  if len(s2) == 0:
    return len(s1)

  previous_row = range(len(s2) + 1)
  for i, c1 in enumerate(s1):
    current_row = [i + 1]
    for j, c2 in enumerate(s2):
      insertions = previous_row[j + 1] + 1  # j+1 instead of j since previous_row and current_row are one character longer
      deletions = current_row[j] + 1  # than s2
      substitutions = previous_row[j] + (c1 != c2)
      current_row.append(min(insertions, deletions, substitutions))
    previous_row = current_row

  return previous_row[-1]


def levenshtein_distance_normalised(s1, s2):
  return levenshtein_distance(s1, s2) / max(len(s1), len(s2))

def solution(S, K):
  DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat','Sun']
  assert S in DAYS  
  idx = DAYS.index(S)
  i = (idx + K) % len(DAYS)
  return DAYS[i]



print(solution('Wed', 2))
print(solution('Sat', 23))



def solution1(S):
  from itertools import groupby
  lst_case = [0 for _ in range(len(S))]
  for j,c in enumerate(S):
    if c.isupper():
      for i,_c in enumerate(S):
        if _c == c.lower():
          lst_case[i] = 2
          lst_case[j] = 2
    else:
      for i,_c in enumerate(S):
        if _c == c.upper():
          lst_case[i] = 2
          lst_case[j] = 2
  l = [list(group) for item, group in groupby(lst_case) if item == 2]
  if len(l) > 0:
    lst = max(l, key=len)
    if len(lst) > 1:
      return len(lst)
    else:
      return -1
  else:
    return -1
  
def solution2(s):
    """
    Given a string `s` of up to 200 characters, returns the longest balanced substring.
    A balanced substring is defined as a sequence of letters where each letter appears both in lower and upper case.

    Args:
        s (str): The input string.

    Returns:
        str: The longest balanced substring.
    """
    # Initialize a dictionary to keep track of the frequency of each letter
    letter_freq = {}
    # Initialize a variable to keep track of the longest balanced substring found so far
    longest_balanced = ""
    # Initialize two pointers, left and right, to keep track of the current substring being considered
    left, right = 0, 0
    # Iterate through the string
    while right < len(s):
        # Update the letter frequency dictionary with the current character
        char = s[right]
        if char.lower() not in letter_freq:
            letter_freq[char.lower()] = 1
        else:
            letter_freq[char.lower()] += 1
        # Move the right pointer to the next character
        right += 1
        # Check if the current substring is balanced
        is_balanced = all(letter_freq.get(c, 0) > 0 and letter_freq.get(c.upper(), 0) > 0 for c in letter_freq)
        if is_balanced:
            # If the current substring is balanced, check if it is longer than the longest found so far
            if len(longest_balanced) < right - left:
                longest_balanced = s[left:right]
        else:
            # If the current substring is not balanced, move the left pointer to the next character
            char = s[left]
            if letter_freq[char.lower()] == 1:
                del letter_freq[char.lower()]
            else:
                letter_freq[char.lower()] -= 1
            left += 1
    return longest_balanced
  
def solution(s):
    """
    Given a string `s` of up to 200 characters, returns the longest balanced substring.
    A balanced substring is defined as a sequence of letters where each letter appears both in lower and upper case.

    Args:
        s (str): The input string.

    Returns:
        str: The longest balanced substring.
    """
    # Initialize a variable to keep track of the longest balanced substring found so far
    longest_balanced = ""
    # Iterate through the string
    for i in range(len(s)):
        # Initialize two sets to keep track of the lowercase and uppercase letters seen so far
        lowercase_seen = set()
        uppercase_seen = set()
        for j in range(i, len(s)):
            if s[j].islower():
                lowercase_seen.add(s[j])
            elif s[j].isupper():
                uppercase_seen.add(s[j].lower())
            # Check if the current substring is balanced
            if lowercase_seen == uppercase_seen:
                # If the current substring is longer than the longest found so far, update it
                if len(longest_balanced) < len(s[i:j+1]):
                    longest_balanced = s[i:j+1]
    return len(longest_balanced) if len(longest_balanced)>0 else -1

print(solution('azABaabza'))
print(solution('TacoCat'))
print(solution('TestTES'))
print(solution('aaabcABccccc'))
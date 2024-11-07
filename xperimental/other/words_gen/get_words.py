import gensim
import gensim.downloader as api

from naeural_core import Logger

if __name__ == '__main__':
  l = Logger('WORDS', base_folder='.', app_folder='_local_cache')
  l.P("loading dict...")
  fn = 'test.pkl'
  if l.get_data_file('test.pkl') is not None:
    words = l.load_pickle_from_data(fn)
  else:
    wv = api.load('word2vec-google-news-300')
    words = wv.index_to_key
    l.save_pickle_to_data(data=words, fn=fn)
  
  WORD_SIZE = 5
  excluded = "eropadcnm"
  included = {
    'i' : 1,
    's' : 4,
    't' : {
      'not' : [0,2],
      },
    }

  checked = []
  l.p("processing...")
  for i, word in enumerate(words):
    word = word.lower()
    if len(word) != WORD_SIZE:
      continue
    if word in checked:
      continue
    checked.append(word)
    found = True
    for excl in excluded:
      if excl in word:
        found = False
        break
    if not found:
      continue
    for incl in included:
      if isinstance(included[incl], int):
        if word[included[incl]] != incl:
          found = False
          break
      else:
        for pos in included[incl]['not']:
          if word[pos] == incl:
            found = False
            break
      if not found:
        break
    if not found:
      continue
    print(word, flush=True, end=' ')

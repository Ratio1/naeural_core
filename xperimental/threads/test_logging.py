import random
import codecs

from naeural_core import Logger
from time import time


if __name__ == '__main__':
  l = Logger('THRP', base_folder='.', app_folder='_cache')
  
  TESTS = 4000

  phrases = ['test {} '.format(x) * 10 for x in range(100)]
  buffer = []
  
  def _p(s):
    buffer.append(s)
    print(s, flush=True)
    fn = '_local_cache/_logs/test.txt'
    if False:
      with codecs.open(fn, 'w', "utf-8") as f:
        for line in buffer:
          f.write("{}\n".format(line))
    else:
      with open(fn, 'w') as f:
        f.writelines(buffer)
    return

  start1=time()
  for i in range(TESTS):
    l.start_timer('_p')
    _p(random.choice(phrases))
    l.end_timer('_p')
  end1=time()

  start2=time()
  for i in range(TESTS):
    l.start_timer('.P')
    l.P(random.choice(phrases))
    l.end_timer('.P')
  end2=time()

  l.P("_p total time: {:.2f}s".format(end1-start1))
  l.P(".P total time: {:.2f}s".format(end2-start2))
  l.show_timers()
  
# N1_L05 ipython 18%
# [THRP][2022-05-28 23:10:52]  _p = 0.0014s, max: 0.0041s, last: 0.0021s, cnt: 999
# [THRP][2022-05-28 23:10:52]  .P = 0.0017s, max: 0.0110s, last: 0.0023s, cnt: 999

# N1_L05 ipython 16%
# [THRP][2022-05-29 10:13:28]  _p = 0.0037s, max: 0.0352s, last: 0.0048s, cnt: 3999
# [THRP][2022-05-29 10:13:28]  .P = 0.0044s, max: 0.0272s, last: 0.0063s, cnt: 3999

# N1_L05 cmd 10%
# [THRP][2022-05-29 10:14:48]  _p = 0.0034s, max: 0.0240s, last: 0.0067s, cnt: 3999
# [THRP][2022-05-29 10:14:48]  .P = 0.0038s, max: 0.0292s, last: 0.0069s, cnt: 3999

# N2_L80 cmd 8%
# N2_L80 ipython 10%


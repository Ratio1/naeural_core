import traceback
import os

from time import time


class _OrchestratorUtils(object):
  def __init__(self):
    super(_OrchestratorUtils, self).__init__()
    return
  
  
  def _save_object_tree(self, fn='obj_mem_tree.txt', save_top_only=True, top=100):   
    self.P("  Logging startup object tree in '{}' ...".format(fn), color='r')
    t3 = time()
    try:
      mem2, tree2, top2 = self.log.get_obj_size(
        obj=self, 
        top_consumers=top,
        return_tree=True,
      )
    except:
      mem2, tree2, top2  = 0, 0, 0
      self.P(" * * * * * * * * * * * * ERROR: get_obj_size FAILED !  * * * * * * * * * * * *\n{}".format(
        traceback.format_exc()
      ))
    #endtry    
    t4 = time()
    mem, tree, top = mem2, tree2, top2 
    self.P("  Done v2 logging object tree: {:,.1f} MB.".format(mem/1024**2), color='r')
    elapsed = t4 - t3
    if tree is not None:
      folder = self.log.get_output_subfolder('memory_logs', force_create=True)
      self.log.maybe_cleanup_timestamped_files(folder=folder, keep=8)
      if not save_top_only:
        _fn = self.log.now_str(short=True)[:12] + '_v2_' + fn
        with open(os.path.join(folder, _fn), 'wt') as fh:
          for line in tree:
            fh.write('{}\n'.format(line))
          
      _fn = self.log.now_str(short=True)[:12] + '_v2_top_' + fn
      with open(os.path.join(folder, _fn), 'wt') as fh:
        for line in top:
          fh.write('{}\n'.format(line))
          
      self.P("  Heavy duty op finished: logging startup object tree in {:.3f}s.".format(elapsed), color='r')      
      display = "\n".join(["(L{}) {}: {:.1f}".format(x['LEVEL'], x['NAME'], x['SIZE'] / 1024**2) for x in top])
      self.P("  Showing top {} memory consumers in MB:\n{}\n".format(len(top), display))
    else:
      self.P("  ERROR: Logging failed", color='r')
    #endif 
    return  
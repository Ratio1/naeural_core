from collections import OrderedDict

def get_packages(as_text=False, indent=0, as_dict=False, mandatory={}):
  """
  Will return the currently installed (and visible) packages
 
  Parameters
  ----------
  as_text : bool, optional
    If true return as text. The default is False.
    
  indent : int, optional
    If return text then return it with indent. The default is 0.
    
  mandatory : dict, optional
    Will raise error if any packages from the dict of key:ver are missing. The default is {}.
    
  as_dict: bool, optional
    Return as package_name:ver dict the result. Default False
 
  Returns
  -------
  packs : list/str/dict
    the list of packages as list of str, a full text to show or a dict of name:ver.
 
  """
  import pkg_resources
  def ver_to_int(version):
    comps = version.split('.')
    val = 0
    power = 3
    for i, comp in enumerate(comps):
      v = int(comp)
      v = v * 100**power
      power -= 1
      val += v
    return val    
  
  raw_packs = [x for x in pkg_resources.working_set]
  maxlen = max([len(x.key) for x in raw_packs]) + 1
  lst_pack_ver = [(x.key, x.version) for x in raw_packs]
  lst_pack_ver = sorted(lst_pack_ver, key=lambda x:x[0])
  dct_packs = OrderedDict(lst_pack_ver)
  
  if len(mandatory) > 0:      
    for mandatory_pack in mandatory:
      if mandatory_pack not in dct_packs:
        raise ValueError("Mandatory package `{}:{}` is missing. Please check your deployment!".format(
          mandatory_pack, mandatory[mandatory_pack]))
      if ver_to_int(mandatory[mandatory_pack]) > ver_to_int(dct_packs[mandatory_pack]):
        raise ValueError("Mandatory installed package `{}:{}` below required version `{}`. Please check your deployment!".format(
          mandatory_pack, dct_packs[mandatory_pack], mandatory[mandatory_pack]))        
  #endif check for packages and versions
  
  if as_dict:
    result = dct_packs
  else:
    result = []
    for x in lst_pack_ver:
      result.append("{}{}".format(x[0] + ' ' * (maxlen - len(x[0])), x[1] + ' ' * (14 - len(x[1]))))
      if x[0] in mandatory:
        result[-1] = result[-1] + ' ==> OK ({} > {})'.format(x[1], mandatory[x[0]])
    if as_text:
      fmt = "\n{}".format(' ' * indent)
      result = ' ' * indent + fmt.join(result)
  return result  
 
  
if __name__ == '__main__':
  print(get_packages(as_text=True, indent=4, mandatory={'torch':'2.1'}))
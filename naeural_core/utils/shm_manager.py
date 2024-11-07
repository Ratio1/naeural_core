from naeural_core import constants as ct


class SharedMemoryManager(object):
  """
  Key-value store shared memory manager is a simple facade to a thread-safe data strcuture such 
  as a dict for any kind of objects that acts/works as a plugin:
    - allows the creation of relationship between various external actors (instaces of SHM)
    - basic functionalities such as getters & setters
    
  
  The dict-like of dict-based memory is organized as follows:
    
  'DEFAULT' : {
      'PLUGIN_1' : {
          ('STREAM_1', 'INSTANCE_1') : {
              'LINKED_INSTANCES' : [('STREAM_1', 'INSTANCE_2')]
              'SOME_KEY_OF_P1_I1' : 'SOME_VALUE'
          }
          ('STREAM_1', 'INSTANCE_2') : {
              'LINKED_INSTANCES' : None
              'LINKED_SERVER' : ('STREAM_1', 'INSTANCE_1')
              'SOME_KEY_OF_P1_I2' : 20
          }
      }
      'PLUGIN_2' : {
          ('STREAM_1', 'INSTANCE_1') : {
              'LINKED_INSTANCES' : [('STREAM_1', 'INSTANCE_2')]
              'SOME_KEY_OF_P2_I1' : 100
          }
          ('STREAM_1', 'INSTANCE_2') : {
              'LINKED_INSTANCES' : None
              'LINKED_SERVER' : ('STREAM_1', 'INSTANCE_1')
              'SOME_KEY_OF_P2_I2' : 'OTHER_VALUE'
          }
      }
  }  
      
  In above example we have two plugins that work on same stream and each has two instances
  with a simple server-client relationship
  
  """
  def __init__(
    self, 
    dct_shared : dict, 
    stream : str,
    plugin : str, 
    instance : str, 
    log : any, 
    category : str='DEFAULT', 
    linked_instances : 
      list =[]
    ):
    """
    Initialize the shared memory manager for a particular plugin type (signature) via self.reset(linked)

    Parameters
    ----------
    dct_shared : dict
        the shared memory dict
    stream : str
        name of the pipeline
    plugin : str
        signature of the plugin
    instance : str
        instance id of the plugin
    log : any
        Logger object
    category : str, optional
        Can be used for multiple categories, by default 'DEFAULT'
    linked_instances : list, optional
        default linked instances, by default []
    """
    assert isinstance(dct_shared, dict)
    super(SharedMemoryManager, self).__init__()
    self._categ = category # TODO: finish adding root category !! 
    self._shm = dct_shared
    self._plugin = plugin
    self._instance_key = (stream, instance)
    self.log = log    
    linked = self._process_linked_keys(linked_instances) # just a helper function
    self.reset(linked)
    return

  # Basic functions that can be overwritten with other shm approaches
  
  def P(self, s, color=None):
    s = f"[SHM{self._instance_key}] " + s
    self.log.P(s, color=color)
    return
  
  
  def get_global_dict(self):
    """
    Returns the global dict of the shared memory for the current category including all types of plugins

    Returns
    -------
    _type_
        _description_
    """
    if self._categ not in self._shm:
      self._shm[self._categ] = {}
    return self._shm[self._categ]
  
  
  def _maybe_init_shm(self, instance_key=None):
    """
    Maybe initialize the shared mem associated with current instance or other instance

    Parameters
    ----------
    instance_key : TYPE, optional
      Other instance than self if not None. The default is None.

    Returns
    -------
    None.

    """
    # We do not work directly with the global dict but with the plugin dict
    # so first we need to get the category (global dict)
    shm = self.get_global_dict()
    # then we need to get the plugin dict
    if self._plugin not in shm:
      shm[self._plugin] = {}
    if instance_key is not None:
      if instance_key not in shm[self._plugin]:
        shm[self._plugin][instance_key] = {}
    return
  
  def get_plugin_dict(self, instance_key=None):
    """
    Returns the dict of the current plugin instance or of a specified instance

    Parameters
    ----------
    instance_key : TYPE, optional
      Other instance than self if not None. The default is None.

    Returns
    -------
    dict
      The key:value store of the instance.

    """    
    shm = self.get_global_dict()
    self._maybe_init_shm(instance_key)
    if instance_key is not None:
      assert len(instance_key) == 2
      instance_key = tuple(instance_key)
      return shm[self._plugin][instance_key]
    return shm[self._plugin] 
  
  
  def get_instance_dict(self, instance_key : str =None):
    """
    Alias for get_plugin_dict

    Parameters
    ----------
    instance_key : str, optional
        the instance key, by default None
    """
    return self.get_plugin_dict(instance_key=instance_key)
  
  
  def set_local_key(self, key : str, val, instance_key=None):
    """
    Setter for a particular key-value

    Parameters
    ----------
    key : str
      the key.
    val : any
      the value that will be asigned to the key.
    instance_key : str, optional
      Other instance than self if not None. The default is None.

    Returns
    -------
    None.

    """
    if instance_key is None:
      instance_key = self.get_local_instance_key()
    self.get_plugin_dict(instance_key)[key] = val
    return


  def get_local_value(self, key : str, instance_key=None, default=None):
    """
    Getter for a local key-value in a instance key-value store

    Parameters
    ----------
    key : str
      the key.
    instance_key : str, optional
      Other instance than self if not None. The default is None[str].
    default : any, optional
      default value is key does not exists. The default is None.

    Returns
    -------
    TYPE
      DESCRIPTION.

    """
    if instance_key is None:
      instance_key = self.get_local_instance_key()    
    return self.get_plugin_dict(instance_key).get(key, default)  
  

  def get_local_instance_key(self):
    """
    Gets the id of the current manager object

    Returns
    -------
    TYPE
      DESCRIPTION.

    """
    return self._instance_key

  
  def get_all_instances_keys(self):
    """
    Gets all the keys of the current manager instance key-value store 

    Returns
    -------
    list of keys

    """
    return list(self.get_plugin_dict().keys())  

  
  def _process_linked_keys(self, linked_instances):
    """
    Basically helper function that returns the list of linked instances is the input is not a 
    normal list of linked instances but a string such as "MAIN" or "server".
    
    This function is OBSOLETE and will be removed soon. Please use `get_linked_keys()` approach instead
    
    Parses a linked_instances parameter that can be "MAIN", "server" or a list of instances ids. 
    Basically it receive a list of ("pipeline", "instance") and will set the current instance as
    a server and all other instances as clients
    

    Parameters
    ----------
    linked_instances : list or str
      list of ("pipeline", "instance") as the `signature` is already known.

    Returns
    -------
    res : TYPE
      DESCRIPTION.

    """
    res = linked_instances
    set_main = False
    if linked_instances is not None:
      if isinstance(linked_instances, list) and len(linked_instances) > 0:
        # first check if we have normal links        
        if isinstance(linked_instances[0], str) and linked_instances[0].upper() in [ct.MAIN, ct.server]:
          set_main = True
          self.P('WARNING: using `"LINKED_INSTANCES=["server"]` is deprecated and will be removed soon. Please use multiple pairs of ["STREAM","INSTANCE"]')
        else:
          res = [tuple(x) for x in linked_instances]
      elif (
        isinstance(linked_instances, str)
        and linked_instances.upper() in [ct.MAIN, ct.server]
      ):
        set_main = True
    if set_main:
      # Obsolete and will be removed soon
      self.set_local_key(key=ct.MAIN, val=True)
      res = self.get_main_links()
    return res
  
  
  # Higher level functions that do not use basic data types

  
  def is_main(self, instance_key : tuple = None):
    """
    This function checks if the current instance is a "main" instance or not. 
    This feature is OBSOLETE and will be removed soon. Please use `get_linked_keys()` instead
    """
    instance_key = self._instance_key if instance_key is None else instance_key
    return self.get_local_value(key=ct.MAIN, instance_key=instance_key)
  
  
  def get_local_dict(self):
    return self.get_plugin_dict(instance_key=self._instance_key)
  
  
  def get_linked_keys(self, instance_key : tuple = None, default : list = []):
    """
    Returns the linked instances of a particular instance or of the current instance
    Will return:
     - None if the instance is a client (default will not be returned as None was a valid configured value)
     - [] if the instance is single (default)
     - list of instances if the instance is a server
     
     TODO: Check/refactor this function

    Parameters
    ----------
    instance_key : tuple, optional
        the tuple key, by default None
    default : list, optional
        list of linked instances, by default []

    Returns
    -------
    _type_
        _description_
    """
    if instance_key is None:
      if self.is_main():
        # recheck & return
        # Obsolete and will be removed soon
        return self.get_main_links()
      instance_key = self.get_local_instance_key()
    res = self.get_local_value(
      key=ct.LINKED_INSTANCES, 
      instance_key=instance_key, 
      default=default
      )
    return res
  
  def get_linked_instances(self, instance_key : tuple = None, default : list = []):
    """
    Alias for get_linked_keys
    """
    return self.get_linked_keys(instance_key=instance_key, default=default)


  def _init_instance(self, instance_key : tuple , linked_instances : list = None):
    """
    Initialize a particular instance. 
    This function is used in the `reset` function and should not be used directly

    Parameters
    ----------
    instance_key : tuple
        the tuple key
    linked_instances : list, optional
        the list of linked instances, by default None

    Returns
    -------
    _type_
        _description_

    Raises
    ------
    ValueError
        _description_
    """
    self._maybe_init_shm(instance_key)
    # get already existing linked instances
    existing_linked_instances = self.get_linked_keys(instance_key=instance_key, default=[])
    if linked_instances is not None and len(linked_instances) > 0:
      if existing_linked_instances is None:
        # if existing_linked_instances was none then this instance used to be a client!
        if len(linked_instances) > 0:
          msg = "client-becoming-a-server modification detected for {}: received link proposal {} althogh it has been previously set to `None`".format(
            instance_key, linked_instances)
          self.P(msg, color='r')
    else:
      if existing_linked_instances is not None and len(existing_linked_instances) > 0:
        msg = "server-becoming-a-client modification detected for {}: received link proposal {} althogh it has been previously set to `{}`".format(
          instance_key, linked_instances, existing_linked_instances)
        self.P(msg, color='r')
    #endif check various scenarios
    self.set_linked(instance_key=instance_key, linked_instances=linked_instances)
    return self.get_plugin_dict(instance_key=instance_key)
  

  @property
  def linked(self):
    return self.get_linked_keys()
  

  def reset(self, linked_instances):
    """
    The main instance reset function. Will reset the current instance and all linked instances.    
    

    Parameters
    ----------
    linked_instances : list
        list of linked instances

    Raises
    ------
    ValueError
        _description_
    """
    # init whole plugin & instance
    self._init_instance(
      instance_key=self._instance_key,
      linked_instances=linked_instances
      )    
    # reset linked if any  
    local_instance = self.get_local_instance_key()
    if linked_instances is not None and len(linked_instances) > 0:      
      for instance in linked_instances:
        self._init_instance(instance_key=instance)
        
        # check for the client being a server
        other_instance_links = self.get_linked_keys(instance)
        if other_instance_links is not None and len(other_instance_links) > 0:
          msg = "Cross linked instances error (server over server): {} proposes link with {} while {} is a server as already references {}!".format(
            local_instance, linked_instances,
            instance, other_instance_links,
          )
          raise ValueError(msg)
        #end referecing a server
        
        # now check maybe this client already has a server
        existing_server = self.get_linked_server(instance_key=instance)
        if existing_server != local_instance and existing_server is not None and len(existing_server) > 0: # check for None as well as for a valid key
          msg = "Cross linked instances error (server overwrite attempt): {} proposes link with {} while {} already references  {}!".format(
            local_instance, linked_instances,
            existing_server, instance,
          )
          raise ValueError(msg)
        #end referecing a already taken client
        self.set_linked(instance, None)
        self.set_local_key(
          key=ct.LINKED_SERVER,
          val=local_instance,
          instance_key=instance,
          )
    else:
      # now we check to see if there are instances pointing to us as it seems we have no links
      all_instances = self.get_all_instances_keys()
      for instance in all_instances:
        server = self.get_linked_server(instance)
        if server == local_instance:
          self.P("Resetting server for {} as it was pointing to us".format(instance))
          self.set_local_key(
            key=ct.LINKED_SERVER,
            val=None, # set value to None so it resembles non existing key
            instance_key=instance,
          )
        #end referecing a client
    return
      
  
  def get_other_instances_keys(self):
    return list(set(self.get_all_instances_keys()) - set([self.get_local_instance_key()])) 
  
  
  def get_main_links(self): 
    """
    This function is OBSOLETE and will be removed soon. Please use `get_linked_keys()` instead
    """
    if self.is_main():
      # this is a main. will reset all other instances links, check for errors
      # and return all links
      instances = self.get_other_instances_keys()
      for instance in instances:
        if self.is_main(instance_key=instance):
          raise ValueError('Detected two "server" instances: {} and {}'.format(
            self.get_local_instance_key(), instance))
        else:
          self.set_linked(instance, None)
          self.set_local_key(
            key=ct.LINKED_SERVER,
            val=self.get_local_instance_key(),
            instance_key=instance,
            )
      return instances
    return None
  
  
  def get_linked_server(self, instance_key : tuple = None):
    """
    Returns the server of a particular instance or of the current instance

    Parameters
    ----------
    instance_key : tuple, optional
        the instance to be cheked, by default None

    Returns
    -------
    instance key of the server or None if the instance is a server
    """
    return self.get_local_value(
      instance_key=instance_key,
      key=ct.LINKED_SERVER, 
    )


  def set_linked(self, instance_key, linked_instances):
    """
    Sets the linked instances of a particular instance and also checks for errors
    """
    
    self.set_local_key(
      key=ct.LINKED_INSTANCES,
      val=linked_instances,
      instance_key=instance_key,
      )
    return

    
  def get_linked_data(self):
    """
    This will return the data of all linked instances. Please note that even if a certain linked instance
    does not exists this will return a basic initialized dict.

    Returns
    -------
    dict
        instance_key : dict of key:value
    """
    if self.linked is not None:
      if len(self.linked) > 0:
        return {k : self.get_plugin_dict(instance_key=k) for k in self.linked}
      # else:
      #   return {k : v for k, v in self.get_plugin_dict().items() 
      #           if k != self.get_local_instance_key()}
    return None  
  
  def clean_instance(self, instance_key : tuple = None):
    """
    WARNING: distructive function. Will remove the instance from the shared memory
    Cleans an instance from the shared memory
    """
    instance_key = self.get_local_instance_key() if instance_key is None else instance_key
    self.P("Cleaning instance {} from shared memory".format(instance_key))
    if instance_key in self.get_all_instances_keys():
      # first check if we are a server
      linked_instances = self.get_linked_keys(instance_key=instance_key)
      if linked_instances is not None and len(linked_instances) > 0:
        # we are a server so we need to clean the clients
        self.P("  Resetting linked instances: {}".format(linked_instances))
        for instance in linked_instances:
          self.set_local_key(
            key=ct.LINKED_SERVER,
            val=None,
            instance_key=instance,
          )
      self.get_plugin_dict().pop(instance_key)
    else:
      self.P("Instance {} not found in shared memory".format(instance_key))
    return
    
      
    
if __name__ == '__main__':
  
  from naeural_core import Logger
  l = Logger('SHMT', base_folder='.', app_folder='_cache')
  
  TESTS = [
    {
      'stream'            : 'STREAM_1',
      'plugin'            : 'PLUGIN_1',
      'instance'          : 'INSTANCE_1',
      'linked_instances'  : [
        ['STREAM_1','INSTANCE_2'],
        ['STREAM_1','INSTANCE_3'],
      ],
    },
    {
      'stream'            : 'STREAM_1',
      'plugin'            : 'PLUGIN_1',
      'instance'          : 'INSTANCE_2',
    },
    {
      'stream'            : 'STREAM_1',
      'plugin'            : 'PLUGIN_1',
      'instance'          : 'INSTANCE_3',
    },

    {
      'stream'            : 'STREAM_1',
      'plugin'            : 'PLUGIN_1',
      'instance'          : 'INSTANCE_4',
      'linked_instances'  : [
        ['STREAM_1','INSTANCE_5'],
        ['STREAM_1','INSTANCE_6'],
        # ['STREAM_1','INSTANCE_3'],
      ],
    },
    {
      'stream'            : 'STREAM_1',
      'plugin'            : 'PLUGIN_1',
      'instance'          : 'INSTANCE_5',
    },
    {
      'stream'            : 'STREAM_1',
      'plugin'            : 'PLUGIN_1',
      'instance'          : 'INSTANCE_6',
    },
    {
      'stream'            : 'STREAM_X',
      'plugin'            : 'PLUGIN_1',
      'instance'          : 'INSTANCE_A',
    },
    {
      'stream'            : 'STREAM_Y',
      'plugin'            : 'PLUGIN_1',
      'instance'          : 'INSTANCE_A',
    },
    {
      'stream'            : 'STREAM_Z',
      'plugin'            : 'PLUGIN_1',
      'instance'          : 'INSTANCE_A',
    },

  ]
  
  K_NAME = 'KEY_1'
  dct_shm = {}
  objs = []
  for i, test in enumerate(TESTS):
    obj = SharedMemoryManager(
      dct_shared=dct_shm, 
      log=l,
      **test,
    )  
    obj.set_local_key(key='KEY_1', val=10**i)
    objs.append(obj)
    
  def visit_status():
    for i, obj in enumerate(objs):
      linked = obj.get_linked_keys()
      is_server = linked is not None and len(linked) > 0
      is_standalone = linked == []
      server = obj.get_linked_server()
      is_client = server is not None
      l.P("Instance {}: ".format(obj.get_local_instance_key()))
      l.P("  Is server:   {}".format(is_server))
      l.P("  Is client:   {} {}".format(is_client, f"({server})" if is_client else ''))
      l.P("  Is stndalne: {}".format(is_standalone))
      l.P("  {}:       {}".format(K_NAME, obj.get_local_value(K_NAME)))
      if is_server:
        my_val = obj.get_local_value(K_NAME)
        linked_data = obj.get_linked_data()
        agg = None
        for instance in linked_data:
          if K_NAME not in linked_data[instance]:
            agg = "N/A due to missing {}".format(instance)
        if agg is None:
          others = sum(v[K_NAME] for k,v in linked_data.items())
          agg = my_val + others
        l.P("  Aggregated:  {}".format(agg))
    


  l.dict_pretty_format(d=dct_shm, display=True)
  
  l.P("Visiting instances", color='y')
  visit_status()

  if False:
    # play with first 3 intances
    l.P("Updating instance 1 & 2", color='y')
    # clean up
    objs[0] = SharedMemoryManager(
        dct_shared=dct_shm, 
        log=l,
        linked_instances=[],
        **{k:v for k,v in TESTS[0].items() if k != 'linked_instances'}, 
    )
    # modify the master
    objs[1] = SharedMemoryManager(
        dct_shared=dct_shm, 
        log=l,
        linked_instances=[['STREAM_1','INSTANCE_3']],
        **TESTS[1], # old idx 1 is now 0 and becomes master
    )  
  
  
  instance_id = -3
  objs[instance_id] = SharedMemoryManager(
      dct_shared=dct_shm, 
      log=l,
      linked_instances=[
        ['STREAM_Y','INSTANCE_A'],
      ],
      **{k:v for k,v in TESTS[instance_id].items() if k != 'linked_instances'}, 
  )  
  l.P("Revisiting instances", color='y')
  visit_status()

  instance_id = -3
  objs[instance_id] = SharedMemoryManager(
      dct_shared=dct_shm, 
      log=l,
      linked_instances=[
        ['STREAM_Y','INSTANCE_A'],
        ['STREAM_Z','INSTANCE_A'],
      ],
      **{k:v for k,v in TESTS[instance_id].items() if k != 'linked_instances'}, 
  )  
  l.P("Revisiting instances", color='y')
  visit_status()

      
  # l.P("Deleting instance 1", color='y')
  # # clean up
  # objs[0].clean_instance()
  # objs = objs[1:] # delete the first instance
  # # modify the master
  # objs[0] = SharedMemoryManager(
  #     dct_shared=dct_shm, 
  #     log=l,
  #     linked_instances=[['STREAM_1','INSTANCE_3']],
  #     **TESTS[1], # old idx 1 is now 0 and becomes master
  # )  
  # l.P("Revisiting instances"), color='y')
  # visit_status()

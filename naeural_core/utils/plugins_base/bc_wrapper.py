from naeural_core.bc import DefaultBlockEngine
class BCWrapper:
  def __init__(self, blockchain_manager : DefaultBlockEngine):
    self.__bc : DefaultBlockEngine = blockchain_manager
    return
  
  @property
  def address(self):
    """
    Returns the address of the current node

    Returns
    -------
    str
        The address of the current node in the blockchain
    """
    return self.__bc.address
  
  def sign(
    self, 
    dct_data: dict, 
    add_data: bool = True, 
    use_digest: bool = True, 
    replace_nan: bool = True
  ) -> str:
    """
    Generates the signature for a dict object.
    Does not add the signature to the dict object

    Parameters
    ----------
    dct_data : dict
      the input message as a dict.
      
    add_data: bool, optional
      will add signature and address to the data dict (also digest if required). Default `True`
      
    use_digest: bool, optional  
      will compute data hash and sign only on hash
      
    replace_nan: bool, optional
      will replace `np.nan` and `np.inf` with `None` before signing. 

    Returns
    -------
      text signature

        
      IMPORTANT: 
        It is quite probable that the same sign(sk, hash) will generate different signatures
    """
    return self.__bc.sign(
      dct_data=dct_data,
      add_data=add_data,
      use_digest=use_digest,
      replace_nan=replace_nan
    )
  
  def verify(
    self, 
    dct_data: str, 
    str_signature: str = None, 
    sender_address: str = None,
    return_full_info : bool = True,
  ) -> bool:
    """
    Verifies a signature using the public key of the signer

    Parameters
    ----------
    dct_data : dict
        the data that was signed
        
    str_signature : str
        the base64 encoded signature. Default `None` will be taken from the data
        
    str_signer : str
        the signer's address (string) used as the public key for verification.
        Default `None` will be taken from the data
        
    return_full_info: bool, optional
        whether to return the full verification info. Default `True`      
    

    Returns
    -------
    bool
        True if the signature is valid, False otherwise
    """
    return self.__bc.verify(
      dct_data=dct_data, 
      signature=str_signature, 
      sender_address=sender_address,
      return_full_info=return_full_info,
    )

  def encrypt_str(self, str_data : str, str_recipient : str):
    """
    Encrypts a string using the public key of the recipient using asymmetric encryption

    Parameters
    ----------
    str_data : str
        the data to be encrypted (string)
        
    str_recipient : str
        the recipient's address (string) used as the public key
        
    OBSOLETE:
      compress: bool, optional
          whether to compress the data before encryption. Default `True`

    Returns
    -------
    str
       the base64 encoded encrypted data
    """
    encrypted_data = self.__bc.encrypt(
      plaintext=str_data, receiver_address=str_recipient,
    )
    return encrypted_data
  
  def decrypt_str(self, str_b64data : str, str_sender : str):
    """
    Decrypts a base64 encoded string using the private key of the sender using asymmetric encryption.
    This method is able to decrypt data that was was encrypted for multiple recipients as well.

    Parameters
    ----------
    str_b64data : str
        The base64 encoded encrypted data
        
    str_sender : str
        The sender's address (string) used as the public key for decryption
        
    OBSOLETE:
      embed_compressed: bool, optional
          whether the compression flag is embedded in the data. Default `True`. Modify this only for special cases.

    Returns
    -------
    str
       the decrypted data (string) that can be then decoded to the original data
    """
    decompressed_data = self.__bc.decrypt(
      encrypted_data_b64=str_b64data, sender_address=str_sender,
    )
    return decompressed_data
  
  
  def encrypt_multi(self, str_data : str, lst_recipients : list):
    """
    Encrypts a string using the public key of the recipient using asymmetric encryption

    Parameters
    ----------
    str_data : str
        the data to be encrypted (string)
        
    lst_recipients : list
        the list of recipient's addresses (string) used as the public keys

    Returns
    -------
    str
       the base64 encoded encrypted data
    """
    encrypted_data = self.__bc.encrypt_for_multi(
      plaintext=str_data, receiver_addresses=lst_recipients,
    )
    return encrypted_data


  def get_whitelist(self, with_prefix: bool = False):
    """
    Returns the list of nodes that are allowed to connect to the current node

    Returns
    -------
    list
        The list of addresses that are whitelisted
    """
    lst_allowed = self.__bc.whitelist
    if with_prefix:
      lst_allowed = [self.maybe_add_prefix(addr) for addr in lst_allowed]
    return lst_allowed


  def get_allowed_nodes(self, with_prefix: bool = False):
    """
    Returns the list of nodes that are allowed to connect to the current node. Alias of `get_whitelist`

    Returns
    -------
    list
        The list of addresses that are allowed to connect
    """
    return self.get_whitelist(with_prefix=with_prefix)


  def maybe_remove_addr_prefix(self, address: str):
    """
    Removes the address prefix from the current node's address
    
    Parameters
    ----------
    
    address: str
        The address to remove the prefix from

    Returns
    -------
    str
        The address of the current node without the prefix
    """
    return self.__bc.maybe_remove_prefix(address)
  
  
  def maybe_remove_prefix(self, address: str):
    """
    Removes the prefix from the address. Alias of `maybe_remove_addr_prefix`
    
    Parameters
    ----------
    
    address: str
        The address to remove the prefix from

    Returns
    -------
    str
        The address without the prefix
    """
    return self.maybe_remove_addr_prefix(address)
  
  
  def maybe_add_prefix(self, address: str):
    """
    Adds the prefix to the address
    
    Parameters
    ----------
    
    address: str
        The address to add the prefix to

    Returns
    -------
    str
        The address with the prefix
    """
    return self.__bc._add_prefix(address)
  


### EVM


  @property
  def eth_address(self):
    """
    Returns the EVM address of the current node. 

    Returns
    -------
    str
        The address of the current node in the blockchain
    """
    return self.__bc.eth_address
  

  def eth_sign_message(self, types: list, values: list):
    """
    Signs a message using the EVM account of the current node

    Parameters
    ----------
    types : list
        The types of the values to be signed
        
    values : list
        The values to be signed

    Returns
    -------
    dict
        A dictionary with the following keys
        - types: list
            The types of the values that were signed
        - values: list
            The values that were signed
        - signature: str
            The signature of the data
        - address: str
            The address of the EVM account used to sign the data
    """
    return self.__bc.eth_sign_message(types=types, values=values)  
  
  
  def eth_sign_node_epochs(self, node: str, epochs: list, epochs_vals: list, signature_only: bool = True):
    """
    Signs the epochs availabilities for a given node using the EVM account of the current node

    Parameters
    ----------
    node : str
        The address of the node
        
    epochs : list
        The epochs to be signed
        
    epochs_vals : list
        The values of the epochs to be signed
        
    signature_only : bool, optional
        Whether to return only the signature. Default `False`

    Returns
    -------
    dict or just str
        A dictionary with the following keys
        - node: str
            The address of the node
        - epochs_vals: list
            The values of the epochs that were signed
        - eth_signature: str
            The EVM signature of the data
        - eth_address: str
            The address of the EVM account used to sign the data
    """
    return self.__bc.eth_sign_node_epochs(
      node=node, epochs=epochs, epochs_vals=epochs_vals, signature_only=signature_only
    )
    
    
  def node_address_to_eth_address(self, node_address: str):
    """
    Converts a node address to an EVM address

    Parameters
    ----------
    node_address : str
        The address of the node

    Returns
    -------
    str
        The EVM address of the node
    """
    return self.__bc.node_address_to_eth_address(node_address)
  
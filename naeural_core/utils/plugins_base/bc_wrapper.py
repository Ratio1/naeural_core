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
  
  def verify(self, dct_data: str, str_signature: str, sender_address: str) -> bool:
    """
    Verifies a signature using the public key of the signer

    Parameters
    ----------
    dct_data : dict
        the data that was signed
    str_signature : str
        the base64 encoded signature
    str_signer : str
        the signer's address (string) used as the public key for verification

    Returns
    -------
    bool
        True if the signature is valid, False otherwise
    """
    return self.__bc.verify(dct_data=dct_data, signature=str_signature, sender_address=sender_address)

  def encrypt_str(self, str_data : str, str_recipient : str, compress: bool = True):
    """
    Encrypts a string using the public key of the recipient using asymmetric encryption

    Parameters
    ----------
    str_data : str
        the data to be encrypted (string)
        
    str_recipient : str
        the recipient's address (string) used as the public key
        
    compress: bool, optional
        whether to compress the data before encryption. Default `True`

    Returns
    -------
    str
       the base64 encoded encrypted data
    """
    encrypted_data = self.__bc.encrypt(
      plaintext=str_data, receiver_address=str_recipient,
      compressed=compress, embed_compressed=True,
    )
    return encrypted_data
  
  def decrypt_str(self, str_b64data : str, str_sender : str, embed_compressed: bool = True):
    """
    Decrypts a base64 encoded string using the private key of the sender using asymmetric encryption

    Parameters
    ----------
    str_b64data : str
        The base64 encoded encrypted data
        
    str_sender : str
        The sender's address (string) used as the public key for decryption
        
    embed_compressed: bool, optional
        whether the compression flag is embedded in the data. Default `True`. Modify this only for special cases.

    Returns
    -------
    str
       the decrypted data (string) that can be then decoded to the original data
    """
    decompressed_data = self.__bc.decrypt(
      encrypted_data_b64=str_b64data, sender_address=str_sender,
      embed_compressed=embed_compressed, 
    )
    return decompressed_data
  
  
  def get_whitelist(self):
    """
    Returns the whitelist of the current node

    Returns
    -------
    list
        The list of addresses that are whitelisted
    """
    return self.__bc.whitelist
  
  
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
  

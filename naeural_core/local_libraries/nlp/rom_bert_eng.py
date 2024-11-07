import numpy as np
import os

from naeural_client import BaseDecentrAIObject

from transformers import TFBertModel, BertTokenizer


__VER__ = '0.1.0.7'
__DEFAULT_MODEL__ = '_allan_data/_ro_bert/20200520'


class RomBERT(BaseDecentrAIObject):
  """
  Romanian BERT (or any BERT) wrapper
  """  
  def __init__(self, 
               max_sent=None,
               model_folder=None, 
               DEBUG=True,
               **kwargs):
    """
    
    Parameters
    ----------
    max_sent : int, optional
      If the downstream model needs a fixed len of seq here you must put it. Otherwise,
      the engine will pad at max len. The default is None.
    model_folder : str, optional
      Where to get the BERT weights from. The default is None.
    DEBUG : bool, optional
      if you need full debug. The default is True.
    **kwargs : TYPE
      other kwargs.

    Raises
    ------
    ValueError
      DESCRIPTION.

    Returns
    -------
    None.

    """    
    self.__version__ = __VER__
    super().__init__(DEBUG=DEBUG,**kwargs)    
    if model_folder is None:
      self._model_folder = os.path.join(
        self.log._get_cloud_base_folder('dropbox'),
        __DEFAULT_MODEL__
        )
    else:
      self.model_folder= model_folder
    if not os.path.isdir(self._model_folder):
      raise ValueError("{} does not exists".format(self._model_folder))
    self.max_sent = max_sent
    self._load_models()
    return


  def _load_models(self):
    self.log.start_timer('load_models')
    self.P("Loading tokenizer...")
    self.tokeng = BertTokenizer.from_pretrained(self._model_folder)
    self.P("Loading pretrained tf BERT...")
    self.embeng = TFBertModel.from_pretrained(self._model_folder)
    elapsed = self.log.stop_timer('load_models')
    self.P("Done loading models in {:.1f}s.".format(elapsed))
    self.P("Loaded model {}('{}') with {:,.2f} Mil params".format(
        self.embeng.__class__.__name__,
        self.embeng.base_model_prefix,
        self.embeng.num_parameters() / (1000**2),
        ),
      color='g'
      )
    return
  
  
  def text2embeds(self, sents, return_only_summary=False):
    """
    

    Parameters
    ----------
    sent : str or list[str]
      sentence or list of N sentences 
      
    return_only_summary: bool, default False
      return only the embedding "summary" consisting of the '[CLS]' token embedding

    Returns
    -------
    embeds : ndarray (N, E)
      returns the embeddings

    """
    self.D("Running {} text2embeds with return_only_summary={}".format(
      self.__class__.__name__,
      return_only_summary
      ))
    self.log.start_timer('text2embeds')
    self.np_last_ids, self.np_last_mask = self.text2ids(
      sents=sents,
      skip_pad=False,
      max_sent=self.max_sent
      )
    self.np_last_embeds, self.np_last_clf = self.embeng.predict([
      self.np_last_ids, 
      self.np_last_mask
      ])
    elapsed = self.log.stop_timer('text2embeds')
    self.D("  Done text2embeds in {:.1f}s.".format(elapsed))
    if not return_only_summary:
      return self.np_last_embeds
    else:
      return self.np_last_clf
  
  
  def text2tokens(self, sent, skip_pad=True):
    """
    

    Parameters
    ----------
    sent : str or list[str]
      sentence or list of N sentences 
      
    skip_pad:
      skip the padding

    Returns
    -------
    output : list
      list of word piece tokenization

    """
    self.log.start_timer('text2embeds')
    np_ids = self.text2ids(sent, return_mask=False, skip_pad=skip_pad)
    output = []
    for ids in np_ids:
      tokens = self.tokeng.convert_ids_to_tokens(ids)    
      output.append(tokens)
    self.log.stop_timer('text2embeds')
    return output
  
  def text2ids(self, sents, return_mask=True, skip_pad=False, max_sent=None):
    """
    

    Parameters
    ----------
    sents : str or list[str]
      sentence or list of N sentences 
    return_mask : bool, optional 
      If attention mask must be returned (derived from padding). The default is True.
    skip_pad : bool, optional 
      Skip padding if we just need the ids. The default is False.

    Returns
    -------
      the list or ndarray of ids

    """
    if type(sents) == str:
      sents = [sents]
    elif type(sents) == list:
      assert type(sents[0]) == str, "input must be `str` or `list[str]`"
    self.log.start_timer('text2ids')
    if max_sent or not skip_pad:
      data = self.tokeng.batch_encode_plus(
          sents,
          add_special_tokens=True,
          return_attention_mask=True,
          pad_to_max_length=True,
          max_length=max_sent,
          )
    else:
      data = self.tokeng.batch_encode_plus(
          sents,
          add_special_tokens=True,
          return_attention_mask=True,
          )
    np_ids = np.array(data['input_ids'])
    np_mask = np.array(data['attention_mask'])
    self.log.start_timer('text2ids')
    if return_mask:
      return np_ids, np_mask
    else:
      return np_ids

      
  
if __name__ == '__main__':
  from naeural_core import Logger

  l = Logger(lib_name='ALBERT', config_file='tagger/brain/configs/config_cv_test.txt')
  
  if 'eng' not in globals():
    eng = RomBERT(log=l)
  
  lines = ['ana are mere.','mara are multe pere!']
  one_sent = 'ana are mere fara punct'
  corpus = [
    'Totul este supercariflagiristicus', 
    'Ma duc sa imi iau un Samsung ca am luat leafa si sunt bogat!',
    'Iphone 8 husa rosie, trusa machiaj avon',
    'Scutece librese, camasa noapte, pantofi jack wolfskin',
    ]
  
  e1 = eng.text2embeds(lines)
  l.P("Generated {} embeds out of {}".format(e1.shape, lines))

  e2 = eng.text2embeds(lines, return_only_summary=True)
  l.P("Generated {} embeds out of {}".format(e2.shape, lines))  
  
  # l.P("e1[:,0] == e2 ({})".format(np.allclose(e1[:,0],e2)))

  l.P("Tokenizer analysis:")
  l.P('  {} = {}'.format(one_sent, eng.text2tokens(one_sent)))
  l.P('  {} = {}'.format(lines, eng.text2tokens(lines)))
  t = eng.text2tokens(corpus)
  l.P("  Full corpus tokens:")
  for _line in t:
    print(_line)
  

from naeural_core.serving.mixins_llm.llm_utils import LlmCT


class LlmTokenizerMixin(object):
  def __init__(self, *args, **kwargs):
    super(LlmTokenizerMixin, self).__init__(*args, **kwargs)
    return

  def _set_tokenizer_chat_template(self):
    """
    Update the chat template of the tokenizer for cases
    where transformers doesn't set the correct values.
    For now this covers mistral and llama-3.

    Parameters
    ----------
    None

    Returns
    -------
    None
    """
    if 'mistral' in self.cfg_model_name.lower():
      self.tokenizer.chat_template = LlmCT.MISTRAL_CHAT_TEMPLATE
    if 'llama-3' in self.cfg_model_name.lower():
      self.tokenizer.chat_template = LlmCT.LLAMA3_CHAT_TEMPLATE
    return

  def add_context_to_request(self, request, context):
    """
    Adds context to the request.

    Parameters
    ----------
    request : str
        the request
    context : str
        the context

    Returns
    -------
    str
        the request with context
    """
    return f'{request} - please answer while also considering the following: {context}'

  def _get_prompt_from_template(self, request, history, system_info, context=None):
    """
    Uses Jinja template to generate a prompt.

    Parameters
    ----------
    request : str
        the current request
    history : list[dict]
        the list of previous requests and responses in the same format as for `_get_prompt`
    system_info : str
        the system prompt
    context : str, optional
        the context for the prompt - CURRENTLY DISABLED

    Returns
    -------
    str
        full prompt

    Raises
    ------
    ValueError
        _description_
    """
    chat = []
    if system_info is not None:
      chat.append({LlmCT.ROLE_KEY: LlmCT.SYSTEM_ROLE, LlmCT.DATA_KEY: system_info})

    #endif create system info

    if history is not None and len(history) > 0:
      if not (isinstance(history, list) and isinstance(history[0], dict)):
        msg = "`history` must be a list of dicts. Received {}".format(type(history))
        raise ValueError(msg)
      # endif type check
      if self.cfg_history_limit is not None:
        limit = max(int(self.cfg_history_limit), 0)
        history = history[-limit:]
      # endif history limit
      for chat_round in history:
        round_request = chat_round.get(LlmCT.REQ, None)
        round_response = chat_round.get(LlmCT.RES, None)
        assert round_request is not None, "Each round in `history` must have a `request`"
        assert round_response is not None, "Each round in `history` must have a `response`"
        chat.append({LlmCT.ROLE_KEY: LlmCT.REQUEST_ROLE, LlmCT.DATA_KEY: round_request})
        chat.append({LlmCT.ROLE_KEY: LlmCT.REPLY_ROLE, LlmCT.DATA_KEY: round_response})
      #endfor chat rounds
    #endif history check

    assert isinstance(request, str), "`request` must be a string"
    if False:
      if context is not None and isinstance(context, str):
        request = self.add_context_to_request(request, context)
      # endif context provided
    # The context feature is disabled until further improvements are made.
    chat.append({LlmCT.ROLE_KEY: LlmCT.REQUEST_ROLE, LlmCT.DATA_KEY: request})
    from_template = self.tokenizer.apply_chat_template(
      chat, tokenize=False,
      add_generation_prompt=self.cfg_add_generation_prompt
    )
    return from_template

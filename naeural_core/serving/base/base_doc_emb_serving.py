from naeural_core.serving.base.base_llm_serving import BaseLlmServing as BaseServingProcess
from transformers import AutoTokenizer, AutoModel


"""
  TODO:
  - support multiple sets of context(maybe a dictionary of format {key: list[doc1, doc2, ...]})
  - add context to a single set
  - change context for a single set
  - reset all sets of context
  
"""


__VER__ = '0.1.0.0'

_CONFIG = {
  **BaseServingProcess.CONFIG,

  'MAX_BATCH_SIZE': 32,
  'MAX_EMB_SIZE': 512,

  'VALIDATION_RULES': {
    **BaseServingProcess.CONFIG['VALIDATION_RULES'],
  },

}


class DocEmbCt:
  REQUEST_TYPE = 'REQUEST_TYPE'
  REQUEST_ID = 'REQUEST_ID'
  REQUEST_DATA = 'REQUEST_DATA'
  BAD_REQUEST = 'BAD_REQUEST'
  QUERY = 'QUERY'
  CONTEXT_QUERY = 'CONTEXT_QUERY'
  CHANGE_CONTEXT = 'CHANGE_CONTEXT'
  ADD_CONTEXT = 'ADD_CONTEXT'
  DEFAULT_REQUEST_TYPE = QUERY
  REQUEST_TYPES = [QUERY, CHANGE_CONTEXT, ADD_CONTEXT, CONTEXT_QUERY]
# endclass


class BaseDocEmbServing(BaseServingProcess):
  CONFIG = _CONFIG
  def __init__(self, **kwargs):
    super(BaseDocEmbServing, self).__init__(**kwargs)
    # The embedded context
    self.__doc_embeddings = None
    # List of docs from the embedded context
    self.__docs = []
    # List of queries to be solved. Each query is a dict with the following keys
    # - REQUEST_ID : str - the request id
    # - TEXT : str - the query text
    self.__queries = []
    # List of docs to be embedded. These docs will be added to the context for the next queries.
    # This is done in order to avoid embedding the context if not needed.
    self.__docs_to_embed = []
    return

  def _setup_llm(self):
    # just override this method as the base class has a virtual method that raises an exception
    return

  def _get_device_map(self):
    return self.device

  def load_tokenizer(self, model_id, cache_dir, token):
    self.tokenizer = AutoTokenizer.from_pretrained(
      model_id,
      cache_dir=self.cache_dir,
      use_auth_token=self.hf_token
    )
    return

  def load_pretrained_model(self, model_id, **kwargs):
    return AutoModel.from_pretrained(model_id, **kwargs)

  def pooling(self, last_hidden_states, attention_mask):
    """
    Pool the last hidden states using the attention mask.
    Parameters
    ----------
    last_hidden_states : torch.Tensor (batch_size, seq_len, hidden_size) with the last hidden states
    attention_mask : torch.Tensor (batch_size, seq_len) with 0s for padding and 1s for real tokens

    Returns
    -------
    torch.Tensor (batch_size, hidden_size) with the pooled embeddings
    """
    return (self.th.sum(last_hidden_states * attention_mask.unsqueeze(-1), dim=1) /
            self.th.sum(attention_mask, dim=1, keepdim=True))

  def embed_texts(self, texts):
    if not isinstance(texts, list):
      texts = [texts]
    # endif texts is not a list
    if self.cfg_max_batch_size is not None and len(texts) > self.cfg_max_batch_size:
      batches = [texts[i:i + self.cfg_max_batch_size] for i in range(0, len(texts), self.cfg_max_batch_size)]
    else:
      batches = [texts]
    # endif more texts than max batch size
    embeddings = []
    for batch in batches:
      with self.th.no_grad():
        input_dict = self.tokenizer(
          batch, max_length=self.cfg_max_emb_size, padding=True, truncation=True, return_tensors='pt'
        )
        input_dict = {k: v.to(self.device) for k, v in input_dict.items()}
        outputs = self.model(**input_dict)
      # endwith no grad
      current_embeddings = self.pooling(outputs.last_hidden_state, input_dict['attention_mask'])
      current_embeddings = self.th.nn.functional.normalize(current_embeddings, p=2, dim=1)
      embeddings.append(current_embeddings.to('cpu'))
    # endfor each batch
    return self.th.cat(embeddings, dim=0)

  def _warmup(self):
    warmup_context = [
      "The Tesla Cybertruck is a battery electric pickup truck built by Tesla, Inc. since 2023.[6] Introduced as a "
      "concept vehicle in November 2019, it has a body design reminiscent of low-polygon modelling, consisting of flat "
      "stainless steel sheet panels.\nTesla initially planned to produce the vehicle in 2021, but it entered "
      "production in 2023 and was first delivered to customers in November. Three models are offered: a tri-motor "
      "all-wheel drive (AWD) \"Cyberbeast\", a dual-motor AWD model, and a rear-wheel drive (RWD) model, with EPA "
      "range estimates of 250–340 miles (400–550 km), varying by model.\nAs of December 2023, the Cybertruck is "
      "available only in North America.",

      "Am facut acest chec pufos cu cacao de atata ori si pentru atat de multe ocazii, incat cred ca-l pot face cu "
      "ochii inchisi. Checul este unul din deserturile clasice romanesti. Il faceau bunicile noastre, mamele noastre "
      "si acum este randul nostru sa ducem reteta mai departe. Este atat de iubit si de popular incat tuturor le "
      "place. Mama este una dintre marile iubitoarele acestui chec, la fel ca mine, de altfel. Alaturi de reteta de "
      "cozonac, checul este desertul pe care il facea cel mai des. Ni l-a facut toata copilaria si imi amintesc cu "
      "drag si nostalgie de feliile groase de chec presarate din abundenta cu zahar pudra. Era minunat pentru micul "
      "dejun, dar si ca gustare, alaturi de un pahar cu lapte sau de o cafea. Il manacam imediat si rar ne mai ramanea "
      "si a doua zi.\nReteta aceasta de chec pufos cu cacao este putin diferita de cea pe care o facea mama. Am "
      "modificat-o in asa fel incat sa fie usor de facut si sa reduc la minim riscul de a da gres. Cel mai important "
      "lucru atunci cand faceti aceasta reteta este sa bateti cat mai bine albusurile. Trebuie sa incorporati cat mai "
      "mult aer in ele. Pentru asta puteti folosi un stand-mixer sau pur si simplu un mixer manual. Puteti incerca si "
      "cu un tel, insa va dura considerabil mai mult timp. Aveti grija cand separati albusurile! Nicio picatura de "
      "galbenus nu trebuie sa ajunga in ele. La fel, nicio picatura de grasime, altfel nu se vor bate cum trebuie. Si "
      "bolul trebuie sa fie bine spalat si degresat cu putina zeama de lamaie sau otet.Evitati sa folositi boluri din "
      "plastic pentru ca nu se vor curata la fel de bine."
    ]
    warmup1 = warmup_context[:1]
    warmup4 = warmup_context + warmup_context
    self.P(f'Warming up with {len(warmup1)} texts')
    self.embed_texts(warmup_context[:1])
    self.P(f'Warming up with {len(warmup4)} texts')
    self.embed_texts(warmup_context + warmup_context)
    self.P(f'Warmup done')
    return

  def _pre_process(self, inputs):
    """
    Preprocess the inputs for the prediction.
    The inputs should have the following format:
    {
      'DATA': [
        {
          'REQUEST_ID': 'request_id_1',
          'REQUEST_TYPE': 'QUERY',
          'REQUEST_DATA': 'text1',
        },
        {
          'REQUEST_ID': 'request_id_2',
          'REQUEST_TYPE': 'QUERY',
          'REQUEST_DATA': 'text2',
        },
        ...
      ],
      'SERVING_PARAMS': [
        {
          'param1': 'value1',
          'param2': 'value2',
        },
        {},
        ...
      ]
    }, where SERVING_PARAMS is optional and contains additional parameters for the prediction.
    The requests can be of the following types:
    - QUERY:
      The request data is a text that needs to be embedded. After embedding, it will be compared
      with all the available documents and the most similar document will be returned along with
      the initial query.
    - CHANGE_CONTEXT:
      The request data is a text or a list of texts that will be used as the new context for the next queries.
    - ADD_CONTEXT:
      The request data is a text that will be added to the current context.
    - CONTEXT_QUERY:
      No embedding will take place. The request data becomes irrelevant and the entire available context
      will be returned as a dictionary.
    Parameters
    ----------
    inputs

    Returns
    -------

    """
    lst_inputs = inputs.get('DATA', [])
    serving_params = inputs.get('SERVING_PARAMS', [])

    processed_requests = []
    for i, inp in enumerate(lst_inputs):
      is_bad_request = False
      msg = ""
      if not isinstance(inp, dict):
        msg = f"Error! Input {i} must be a dict. Received {type(inp)}"
        self.P(msg)
        is_bad_request = True
      # endif input is a string
      predict_kwargs = serving_params[i] if i < len(serving_params) else {}
      request_id = inp.get(DocEmbCt.REQUEST_ID, None)
      if request_id is None:
        msg = f"Warning! Request {i} must have a request id"
        self.P(msg)
      request_type = inp.get(DocEmbCt.REQUEST_TYPE, DocEmbCt.DEFAULT_REQUEST_TYPE)
      if request_type not in DocEmbCt.REQUEST_TYPES:
        msg = f"Error! Request type must be one of {DocEmbCt.REQUEST_TYPES}. Received {request_type}"
        self.P(msg)
        is_bad_request = True
      # endif request type is not valid
      request_data = inp.get(DocEmbCt.REQUEST_DATA, '')
      if not isinstance(request_data, str) and not (isinstance(request_data, list) and all([isinstance(x, str) for x in request_data])):
        additional = "."
        if isinstance(request_data, list):
          non_str_types = [type(x) for x in request_data if not isinstance(x, str)]
          additional = f" containing non string types: {non_str_types}."
        msg = f"Error! Request data must be a string or a list of only strings. Received {type(request_data)}{additional}"
        self.P(msg)
        is_bad_request = True
      # endif request data is not a string or a list of strings
      processed_requests.append({
        DocEmbCt.REQUEST_ID: request_id,
        DocEmbCt.REQUEST_TYPE: request_type if not is_bad_request else DocEmbCt.BAD_REQUEST,
        DocEmbCt.REQUEST_DATA: request_data if not is_bad_request else msg,
        'PREDICT_KWARGS': predict_kwargs
      })
    # endfor each input
    return processed_requests

  def maybe_update_context(self):
    """
    Update the context if there are any additional documents.
    """
    if len(self.__docs_to_embed) == 0:
      return
    # endif no additional docs
    self.__docs.extend(self.__docs_to_embed)
    added_embeddings = self.embed_texts(self.__docs_to_embed)
    if self.__doc_embeddings is None:
      self.__doc_embeddings = added_embeddings
    else:
      self.__doc_embeddings = self.th.cat([self.__doc_embeddings, added_embeddings], dim=0)
    self.__docs_to_embed = []
    return

  def get_result_dict(self, request_id, doc=None, query=None, full_context=None, **kwargs):
    """
    Get the result dictionary.
    Parameters
    ----------
    request_id : str - the request id
    doc : str - the document
    query : str - the query
    full_context : dict - the full context
    kwargs : dict - additional parameters

    Returns
    -------
    dict - the result dictionary
    """
    uppercase_kwargs = {k.upper(): v for k, v in kwargs.items()}
    return {
      DocEmbCt.REQUEST_ID: request_id,
      'DOC': doc,
      DocEmbCt.QUERY: query,
      'FULL_CONTEXT': full_context,
      'MODEL_NAME': self.cfg_model_name,
      **uppercase_kwargs
    }

  def maybe_solve_queries(self):
    """
    Solve the queries with the current context if there are any.
    Returns
    -------
    bool - whether additional docs were added to the context
    list[dict] - the results
      - each dict must have the following keys
        - REQUEST_ID : str - the request id
        - DOC : str - the document
        - QUERY : str - the query
        - FULL_CONTEXT : dict - the full context
    """
    additional_docs = self.__docs_to_embed
    if len(self.__docs) + len(additional_docs) == 0:
      # No context so there is no need for any embedding
      results = [
        self.get_result_dict(
          request_id=query[DocEmbCt.REQUEST_ID],
          query=query['TEXT'],
        ) for query in self.__queries
      ]
      self.__queries = []
      return True, results
    # endif no context
    if len(self.__queries) == 0:
      return False, []
    # endif no queries
    self.maybe_update_context()

    query_texts = [query['TEXT'] for query in self.__queries]
    embedded_queries = self.embed_texts(query_texts)
    scores = (self.__doc_embeddings @ embedded_queries.T) * 100

    results = []
    for it, query in enumerate(self.__queries):
      query_text = query['TEXT']
      best_doc_idx = self.th.argmax(scores[:, it]).item()
      best_match = self.__docs[best_doc_idx]
      results.append(self.get_result_dict(
        request_id=query[DocEmbCt.REQUEST_ID],
        doc=best_match,
        query=query_text,
      ))
    # endfor each query
    self.__queries = []
    return True, results

  def _predict(self, preprocessed_requests):
    """
    Perform the prediction using the preprocessed requests.
    For details about the requests see the `_pre_process` method.
    Parameters
    ----------
    preprocessed_requests : list[dict] - the preprocessed requests
      - each dict must have the following keys:
        - REQUEST_ID : str - the request id
        - REQUEST_TYPE : str - the request type: QUERY, CHANGE_CONTEXT, ADD_CONTEXT, CONTEXT_QUERY
        - REQUEST_DATA : str or list - the request data: the text or list of texts(in case of context change)
      - each dict can have the following keys(they are optional):
        - PREDICT_KWARGS(not used for the moment) : dict - the prediction kwargs,
        additional parameters for the prediction

    Returns
    -------
    list[dict] - the predictions for each query or context query
      - each dict must have the following keys
        - REQUEST_ID : str - the request id
        - DOC : str - the request document, None if context query or no document available
        - QUERY : str - the query, None if context query
        - FULL_CONTEXT : dict - the full context, in case of context query, None otherwise
    """
    results = []
    for i, req in enumerate(preprocessed_requests):
      req_id = req[DocEmbCt.REQUEST_ID]
      req_type = req[DocEmbCt.REQUEST_TYPE]
      if req_type == DocEmbCt.ADD_CONTEXT:
        # Check if we need to solve the queries before adding the new context
        data = req[DocEmbCt.REQUEST_DATA]
        success = False
        if len(data) > 0:
          reset_temp_context, queries_results = self.maybe_solve_queries()
          if reset_temp_context:
            results.extend(queries_results)
          # endif reset context
          if isinstance(data, str):
            self.__docs_to_embed.append(data)
            success = True
          elif isinstance(data, list):
            self.__docs_to_embed.extend(data)
            success = True
        # endif data is not empty
        results.append(self.get_result_dict(request_id=req_id, success=success))
      # end ADD_CONTEXT
      elif req_type == DocEmbCt.BAD_REQUEST:
        # Add the error to the results
        results.append(self.get_result_dict(
          request_id=req[DocEmbCt.REQUEST_ID],
          error=req[DocEmbCt.REQUEST_DATA]
        ))
      # end BAD_REQUEST
      elif req_type == DocEmbCt.QUERY:
        # Add the query to the list of queries to be solved
        data = req[DocEmbCt.REQUEST_DATA]
        self.__queries.append({
          'TEXT': data,
          DocEmbCt.REQUEST_ID: req[DocEmbCt.REQUEST_ID]
        })
      # end QUERY
      elif req_type == DocEmbCt.CHANGE_CONTEXT:
        data = req[DocEmbCt.REQUEST_DATA]
        # Solve the queries,if any, and reset the context
        _, queries_results = self.maybe_solve_queries()
        results.extend(queries_results)
        self.__doc_embeddings = None
        self.__docs = []
        if isinstance(data, str):
          self.__docs_to_embed = [data]
        elif isinstance(data, list):
          self.__docs_to_embed = data
        # endif data is a string or a list
      # end CHANGE_CONTEXT
      elif req_type == DocEmbCt.CONTEXT_QUERY:
        # Return the full context
        current_docs = self.__docs + self.__docs_to_embed
        results.append(self.get_result_dict(
          request_id=req[DocEmbCt.REQUEST_ID],
          full_context={
            it: doc
            for it, doc in enumerate(current_docs)
          }
        ))
      # endif request type
    # endfor each request
    if len(self.__queries) > 0:
      _, queries_results = self.maybe_solve_queries()
      results.extend(queries_results)
    # endif there are queries left to solve
    return results

  def _post_process(self, preds_batch):
    return preds_batch



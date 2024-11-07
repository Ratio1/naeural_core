# SimpleFlaskModelServer

Simple engine that allows the quick'n'dirty operationalization of a model. _Ok,
maybe not that dirty ;)_

### Initialization:

`model` : tf/keras trained/loaded model or a class that has a `.predict` function

`fun_input`  : callback that receives a specific input json and transforms into a
             model input. Ideally the input json should also contain a `'client'` 
             key and value
            
`fun_output` : callback that receives model output and prepares output json

`log`  : mandatory Logger object

`host` : host default to local

`port` : port default 5000
    
### Example:

In below example we define the input and output preparation functions and construct a dummy sequence encoder-decoder model that we quickly operationalize.

    def inp_proc(data):
      if 'input_value' not in data.keys():
        print("ERROR: input json does not contain data")
        return None
      # get string
      s = data['input_value']     
      # create dict
      d = [chr(x) for x in range(5000)] 
      # tokenize
      t = [d.index(x) for x in s] 
      # batchfy
      np_t = np.array(t).reshape((1,-1))
      return np_t
    
    def out_proc(data):
      # create dict
      d = [chr(x) for x in range(5000)]
      # select first obs
      t = data[0]
      t = t.ravel().astype(int)
      # get string 
      c = [d[x] for x in t]    
      s = "".join(c)
      return {'output_value':str(s)}
    
    l = FakeLogger(lib_name='MSRVT', no_folders_no_save=True)
      
    # model gets a sequnce
    tf_inp = tf.keras.layers.Input((None,))
    # adds a value to each token in all sequences
    tf_x = tf.keras.layers.Lambda(lambda x: x+1)(tf_inp)
    # returns modified input
    m = tf.keras.models.Model(tf_inp, tf_x)
    
    eng = SimpleFlaskModelServer(model=m, 
                                 fun_input=inp_proc, 
                                 fun_output=out_proc,
                                 log=l,
                                 port=5001)
    eng.run()
    
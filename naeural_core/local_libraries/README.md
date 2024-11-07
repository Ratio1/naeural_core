# logger.py

## Logger : Jack-of-all-trades swiss-army-knife object

### history:

 - 2017-06-29 created
 - 2017-11-07 added Keras callback constructor connected to logger
 - 2017-11-08 added keras callback for tensorboard, switched to tf.keras
 - 2017-11-13 modified GetMachineName function, added config json loading
 - 2017-11-15 added timing between log entries (show_time = True)
 - 2017-11-27 added time dictionary features
 - 2017-12-05 added HTML output support
 - 2017-12-07 added keras version check
 - 2017-12-19 added keras-to-tf model prep for production deployment
 - 2017-12-20 added easy wrapper methods for working with graph/model files
 - 2018-02-07 added support for multi part logs (default max 1000 lines)
 - 2018-03-27 added new keras model support
 - 2018-06-07 added keras model history analysis
 - 2018-07-04 added GetKerasModelSummary
 - 2018-07-16 added SetNicePrints for Pandas and Numpy
 - 2018-07-27 modified SaveDataFrame method
 - 2018-07-28 added Keras recall, precision and f2 metrics
 - 2018-07-30 added Keras r2 metric; added TqdmEnumerate method which can iterate any generator whose number of yields in unknown
 - 2018-08-01 added GetEmbMap - embeddings map generation & plot with annotations
 - 2018-08-01 added GetConfigValue, UpdateConfig
 - 2018-08-03 updated UpdateConfig to accept dictionary, updated SaveDataFrame to return saved fn
 - 2018-08-08 added get_K_clf_metrics, get_K_clf_custom_dict, modified K metrics signatures for shorter logs (naming incosistency in the project for usability sake..)
 - 2018-08-12 modified Keras epoch-end callback to register/monitor best score use `GetTrainBestEpochStat()` to retrieve results.
 - 2018-08-19 added GridSearch method - Keras model grid-search based on model creation function and parameters dict (similar to sklearn)
 - 2018-08-24 fix empty str bug
 - 2018-08-30 added train eval to `GridSearch`
 - 2018-08-31 added post-epoch callback to get metrics against dev-set
 - 2018-09-09 added model saving on custom callback and watch prediction converter callback
 - 2018-09-12 more grid-search features
 - 2018-09-13 added grid-search functionality for contrastive loss training (convert from pred to probas, etc)
 - 2018-09-21 modified PlotHistogram functionality in order to support multiple distributions
 - 2018-09-22 added F1 score to GridSearch
 - 2018-09-25 slight modifications to GridSearch - validation_data can be (None,None)
 - 2018-09-30 added GridSearch helper function (can be used outside GridSearch)
 - 2018-11-08 added `get_tf_loss` & `get_tf_optimizer` methods
 - 2018-11-09 added SaveTFGraphCheckpoint and LoadTFGraphCheckpoint methods
 - 2018-11-13 added GetPathFromNode
 - 2018-11-14 added GetDropboxDrive
 - 2018-12-04 added ReadFromPath and WriteToPath
 - 2019-01-18 seaborn and matplot loaded only when required
 - 2019-01-21 added config_file_encoding for logger config file. needed to load portughese special chars
 - 2019-01-28 added extra GPU info to logger including gpu_mem list (prop)
 - 2019-01-28 added `SaveDataJSON(data, file)`, `SaveOutputJSON(data, file)`
 - 2019-03-06 added `SaveModelsJSON` and modified SaveGraphToModels to save known inputs/outputs of saved model
 - 2019-03-08 added `ShowTextHistogram`  - displays text only histogram
 - 2019-03-22 modified `ShowTextHistogram` with int-specialized distributions
 - 2019-04-11 modified `SaveImage` `OutputPyplotImage` to take 1st mandatory arg as plt object
 - 2019-04-18 GetObjectParams will return a short description of the oject parameters
 - 2019-05-08 AdvancedCallback supports multi-ouputs for time-series
 - 2019-05-08 SaveDataFrame(compress=True) compresses the df and LoadDataFrame suports automatic loading however for loading the file name must be ".zip"
 - 2019-05-14 added val_MAPE_loss that calculates the validation loss on last series points in validation data
 - 2019-05-17 Major cleanup of tf/keras code. Modified/corected R2 for TS with garabage spread constrol meaning a few BAD cases will not destroy the whole score (eg one -1e4 score will destroy 100 0.9 scores). added r2_score to logger
 - 2019-05-18 encapsulated AdvancedCallback in the logger function
 - 2019-05-28 added `PackageLoaded(package_name)` function - returns true if package is loaded
 - 2019-05-28 added `ResetSeeds` - resets al known random seeds for reproducible results
 - 2019-05-31 small modification to `r2_score`
 - 2019-07-03 added `PlotHistory` to plot-compare multiple series within dict of series
 - 2019-07-31 `ShowTextHistogram` is smarter now
 - 2019-07-31 added vectorized sliding_window over numpy array 
 - 2019-08-01 added weight initializers
 - 2019-08-08 added `prepare_input_tensors`
 - 2019-08-12 modified and documented `calc_timeseries_error`
 - 2019-08-12 modified `simple_autoregression_test` to accomodate seq2seq models via a  `input_full_tensors` list param that accepts the indices of fixed size input  tensors of the encoder part of the seq2seq model
 - 2019-08-29 major update with the inclusion of benchmarking process implementation for regression models on time-series - start the process with `start_autoregression_benchmark`, add models with `add_autoregression_benchmark` and finally see results with `get_auoregression_benchmark`
 - 2019-09-13 major update to autoregression benchmarking process. TF loading now is back to initial state.
 - 2019-10-15 major modules extracted from `Logger`
 - 2020-01-14 fixed tf2 tf1 compat random seed
 

# generic_obj

## BaseDecentrAIObject : generic class that must be inherited by any DecentrAI class
# AID


## Bugs

  - send - "loading model state" in HBs - no MLSTOP !


  - MOVE to UTC time in all logs and messages and stuff
    - remove time zone from container volume
    

  - sysmon: 
    - "Error generating FULL process tree for pid"
    - maybe stop process recursive analysis and generation

  - re-check MLSTOP on configs
  - check if MLSTOP is working properly and no HBs are sent


## BC & Admin



  - TEST COMMS env -> ENCRYPTED_COMMS default False

  - Epoch history:
    - netmon must track network state and verify hb validity
    - netmon must give "epoch" status of each node
    - status of hb-chain and epoch-status saved in BC
  
  - net-mon string consts in pye2
  - cleanup data & output

  - blockchain "mempool" implementation on all nodes
  - mempools aggregation on supervisor node
  - round-robin delegation of aggregator role among supervisor nodes
  - basic blockchain verification on each node
  - full blockchain verification on supervisor node


# Other

  - BATCH_ARCHIVE_PIPELINE: archive several given pipelines

  - large messages slow down the system!
  - `plugin` not working in list comprehension or other complex expressions
  - read-only mode when no writing on disk is allowed




## Refactor
  - _predict & other stuff in serving process -> public 
  - serving -> minimize amprent of tutorials
  - _run_data_aquisition_step & other stuff 
  - DCT minimize amprent of tutorials
  - add pipeline definitions as docstrings to tutorials

  - further break business plugin
  - MIGRATE cfg_variable to cfg.variable auto-generation using NestedDotDict

  - review "TODO:" in all files
  - big "TODO" remember to review all protected to private in base classes


https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-citation-files


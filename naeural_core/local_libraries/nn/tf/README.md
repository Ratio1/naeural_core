

# gated

	GatedDense : Dense that has a gating mechanism and includes also BatchNorm
		TODO: : InstanceNorm and LayerNorm

# t2e

	Time2Embedding : generates embeddings from a time scalar or vector
		TODO: testing, testing and more testing

# conv_receptive_field

	analyze_model(model) : method that analyzes a convolutional model and generates the receptive field information

	define_PatchGAN_discriminator : defines the classic PatchGAN conv graph discriminator


# utils

    multiple 'block-creator' functions

# attention_visualizer

- Class **AbstractAttentionVisualizer** is to be extended for any specific implementation:
	- **Input**:
		- **input\_sequence**: tensor with shape (batch\_size, input\_size, 1)
		- **output\_sequence**: tensor with shape (batch\_size, output\_size, 1)
		- **attention\_matrix**: tensor with shape (batch\_size, output\_size, input\_size)
	- **Notable members**:
		- **data\_id**: the currently selected series from the batch
		- **output\_item\_id**: the currently selected output_item to focus on in the attention plot
		- **select\_data(id)**: updates the **data_id** member with the given *id*; returns *True* if the update was successfull and *False* otherwise
		- **select\_output_item(id)**: updates the **output\_item\_id** with the given *id*; returns *True* if the update was successfull and *False* otherwise
		- **get\_<input/output>\_sequence()** and **get\_<input/output>\_ids()**: returns the currently selected sequences (given by **data_id**), stripped of the singleton; and their respective ids
		- **get_attention()**: returns the currently selected attention given by **data_id** and **output_item_id**
		- **get_full_attention()**: returns the currently selected attention given by **data_id** for the entire *output\_sequence*
		- **get\_most\_important\_input(window)**: retrieves the index of the element in the middle of the most important window

- Class **DashLensAttentionVisualizer** is a basic implementation in Dash for the LENS+ sales forecast, used to inspect the historical influence for each predicted sale for a selected series within a processed batch
	- **Mandatory Input**:
		- Base inputs from **AbstractAttentionVisualizer**
	- **Optional Input**:
		- **input\_name, output\_name, attention\_name**: identifiers for each plot
		- **input\_title, output\_title, attention\_title**: titles for each plot
		- **input\_<x/y>label, output\_<x/y>label, attention\_<x/y>label**: <x/y>-axis labels for each plot
		- **important\_attn\_text**: annotation text for most influential window in attention
		- **selected\_output\_text**: annotation text for selected day in forecasted plot
		- **colorscale**: colorscale option for the attention colorbar
	- **Notable members**:
		- **show\_plot()**: runs the dash app on the default server @ **localhost:8050**
		- **\_build\_<component>()**: methods that build the specific *<component>* within the app
		- **\_update\_<component>(\*args)**: callbacks used to update *<component>* when actions occur
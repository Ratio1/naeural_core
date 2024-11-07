import numpy as np

import plotly.graph_objects as go
import dash
import dash_core_components as dcc
import dash_html_components as html
from dash.dependencies import Input, Output

class AbstractAttentionVisualizer(object):
    '''
    Parameters:
        input_sequence: tensor with shape (batch_size, input_size, 1)
        output_sequence: tensor with shape (batch_size, output_size, 1)
        attention_matrix: tensor with shape (batch_size, output_size, input_size)
    '''
    def __init__(self, input_sequence, output_sequence, attention_matrix):
        self.input_sequence = input_sequence
        self.output_sequence = output_sequence
        self.attention_matrix = attention_matrix
        self.data_id = 0
        self.output_item_id = 0
        self._input_check()

    def _input_check(self):
        if self.input_sequence.shape[1] != self.attention_matrix.shape[2] or \
            self.output_sequence.shape[1] != self.attention_matrix.shape[1]:
            raise ValueError('Tensor args shape does not match!')

    def select_data(self, id):
        if id < 0 or id >= self.input_sequence.shape[0]:
            return False

        self.data_id = id
        return True

    def select_output_item(self, id):
        if id < 0 or id >= self.output_sequence.shape[1]:
            return False

        self.output_item_id = id
        return True

    def get_input_sequence(self):
        return self.input_sequence[self.data_id, :, 0]

    def get_output_sequence(self):
        return self.output_sequence[self.data_id, :, 0]

    def get_input_ids(self):
        return list(range(self.get_input_sequence().shape[0]))

    def get_output_ids(self):
        return list(range(self.get_output_sequence().shape[0]))

    def get_attention(self):
        return self.attention_matrix[self.data_id, self.output_item_id:self.output_item_id+1]

    def get_full_attention(self):
        return self.attention_matrix[self.data_id]

    def get_most_important_input(self, window=3):
        sequence = self.get_attention()[0]
        minval = min(sequence)
        maxval = max(sequence)
        threshold = minval + 0.75 * (maxval - minval)

        out_size = sequence.shape[-1]
        max_quants = 0
        max_total = 0
        id = 0
        for i in range(0, out_size-window):
            quants = sum(sequence[i:i+window] > threshold)
            total = sum(sequence[i:i+window])
            if quants > max_quants:
                max_quants, max_total = quants, total
                id = i + window // 2
            elif quants == max_quants and total > max_total:
                max_total = total
                id = i + window // 2

        return id

class DashLensAttentionVisualizer(AbstractAttentionVisualizer):
    '''
    Dash(Plotly) implementation for the Lens Attention Visualizer
    Parameters:
        input_sequence: tensor with shape (batch_size, input_size, 1)
        output_sequence: tensor with shape (batch_size, output_size, 1)
        attention_matrix: tensor with shape (batch_size, output_size, input_size)

        input_name (opt): historical figure component identifier
        output_name (opt): forecasted figure component identifier
        attention_name (opt): attention figure component identifier

        important_attn_text (opt): annotation text for most influential window in attention
        selected_output_text (opt): annotation text for selected forecasted day

        input_title (opt): figure title for historical plot
        input_xlabel (opt): x-axis label for historical plot
        input_ylabel (opt): y-axis label for historical plot

        output_title (opt): figure title for forecasted plot
        output_xlabel (opt): x-axis label for forecasted plot
        output_ylabel (opt): y-axis label for forecasted plot

        attention_title (opt): figure title for attention plot
        attention_xlabel (opt): x-axis label for attention plot
        attention_ylabel (opt): y-axis label for attention plot

        colorscale (opt): colorscale option for the attention colorbar
    '''
    def __init__(self, input_sequence, output_sequence, attention_matrix,
                input_name='Historical', output_name='Forecasted', attention_name='Attention',
                important_attn_text='Most important', selected_output_text='Selected day',
                input_title='Historical data', input_xlabel='Historical day', input_ylabel='Sold quantity',
                output_title='Forecasted data', output_xlabel='Forecasted day', output_ylabel='Expected sell quantity',
                attention_title='Historical influence', attention_xlabel='Historical day', attention_ylabel='Forecasted day',
                colorscale='Viridis'):
        super().__init__(input_sequence, output_sequence, attention_matrix)
        self.input_name = input_name
        self.output_name = output_name
        self.attention_name = attention_name

        self.important_attn_text = important_attn_text
        self.selected_output_text = selected_output_text

        self.input_title = input_title
        self.input_xlabel, self.input_ylabel = input_xlabel, input_ylabel

        self.output_title = output_title
        self.output_xlabel, self.output_ylabel = output_xlabel, output_ylabel

        self.attention_title = attention_title
        self.attention_xlabel, self.attention_ylabel = attention_xlabel, attention_ylabel

        self.colorscale = colorscale

        self.dropdown_id = "dropdown"

        self.app = self._build_app()

    def _build_historical(self):
        return go.Figure(
            data=[go.Scatter(
                x=self.get_input_ids(),
                y=self.get_input_sequence(),
                name=self.input_name
            )],
            layout=go.Layout(
                title=dict(
                    text=self.input_title
                ),
                xaxis=dict(
                    title=self.input_xlabel
                ),
                yaxis=dict(
                    title=self.input_ylabel
                )
            )
        )

    def _build_forecasted(self):
        return go.Figure(
            data=[go.Scatter(
                x=self.get_output_ids(),
                y=self.get_output_sequence(),
                name=self.output_name
            )],
            layout=go.Layout(
                title=dict(
                    text=self.output_title
                ),
                xaxis=dict(
                    title=self.output_xlabel
                ),
                yaxis=dict(
                    title=self.output_ylabel
                ),
                annotations=[
                    dict(
                        x=self.output_item_id,
                        y=self.get_output_sequence()[self.output_item_id],
                        xref='x',
                        yref='y',
                        ax=0,
                        ay=30,
                        text=self.selected_output_text,
                        font=dict(
                            color="black",
                            size=12
                        ),
                        arrowcolor="black",
                        arrowsize=2,
                        arrowwidth=1,
                        arrowhead=1
                    )
                ]
            )
        )

    def _build_attention(self):
        return go.Figure(
            data=[go.Heatmap(
                x=self.get_input_ids(),
                y=[self.get_output_ids()[self.output_item_id]],
                z=self.get_attention(),
                colorscale=self.colorscale,
                name=self.attention_name
            )],
            layout=go.Layout(
                title=dict(
                    text=self.attention_title
                ),
                xaxis=dict(
                    title=self.attention_xlabel
                ),
                yaxis=dict(
                    title=self.attention_ylabel,
                    nticks=1
                ),
                annotations=[
                    dict(
                        x=self.get_most_important_input()/self.input_sequence.shape[1]+ \
                            1/(2*self.input_sequence.shape[1]),
                        y=0,
                        xref='paper',
                        yref='paper',
                        ax=0,
                        ay=30,
                        text=self.important_attn_text,
                        font=dict(
                            color="black",
                            size=12
                        ),
                        arrowcolor="black",
                        arrowsize=2,
                        arrowwidth=1,
                        arrowhead=1
                    )
                ]
            )
        )

    def _update_historical(self, dropdown_value):
        if dropdown_value is not None:
            self.select_data(dropdown_value)

        return self._build_historical()

    def _update_forecasted(self, dropdown_value, forecast_clickData):
        if dropdown_value is not None:
            self.select_data(dropdown_value)

        if forecast_clickData is not None:
            self.select_output_item(forecast_clickData['points'][0]['pointIndex'])

        return self._build_forecasted()

    def _update_attention(self, dropdown_value, forecast_clickData):
        if dropdown_value is not None:
            self.select_data(dropdown_value)

        if forecast_clickData is not None:
            self.select_output_item(forecast_clickData['points'][0]['pointIndex'])

        return self._build_attention()

    def _build_app(self):
        app = dash.Dash(__name__)
        app.layout = html.Div(
            children=[
                dcc.Dropdown(
                    id=self.dropdown_id,
                    options=[{'label':f'Series {i}', 'value': i} for i in range(self.input_sequence.shape[0])],
                    value=self.data_id
                ),
                html.Div(
                    children=[
                        dcc.Graph(
                            id=self.input_name,
                            figure=self._build_historical(),
                            style={'width':'50%','display':'inline-block'}
                        ),
                        dcc.Graph(
                            id=self.output_name,
                            figure=self._build_forecasted(),
                            style={'width':'50%','display':'inline-block'}
                        )
                ]),
                html.Div(
                    children=[
                        dcc.Graph(
                            id=self.attention_name,
                            figure=self._build_attention(),
                            style={'width':'50%','display':'inline-block'}
                        )
                ])
            ]
        )

        app.callback(
            Output(self.input_name, 'figure'),
            [Input(self.dropdown_id, 'value')]
        )(self._update_historical)
        app.callback(
            Output(self.output_name, 'figure'),
            [Input(self.dropdown_id, 'value'),
            Input(self.output_name, 'clickData')]
        )(self._update_forecasted)
        app.callback(
            Output(self.attention_name, 'figure'),
            [Input(self.dropdown_id, 'value'),
            Input(self.output_name, 'clickData')]
        )(self._update_attention)

        return app

    def show_plot(self):
        self.app.run_server()

def generate_lens_data_example(batch_size=10, historical_size=180, forecasted_size=15,
                            historical_range=(5,10), forecasted_range=(5,10), attn_range=(0,10)):
    historical = historical_range[0] + np.random.rand(batch_size, historical_size, 1) * (historical_range[1] - historical_range[0])
    forecasted = forecasted_range[0] + np.random.rand(batch_size, forecasted_size, 1) * (forecasted_range[1] - forecasted_range[0])
    attention = attn_range[0] + np.random.rand(batch_size, forecasted_size, historical_size) * (attn_range[1] - attn_range[0])

    return historical, forecasted, attention

if __name__ == "__main__":
    h, f, a = generate_lens_data_example()
    app = DashLensAttentionVisualizer(h, f, a)
    app.show_plot()

import numpy as np

from naeural_core.utils.basic_anomaly_model import BasicAnomalyModel

ANOMALY_THRESHOLD = 0.02

if __name__ == '__main__':
    model = BasicAnomalyModel()
    x_train = np.random.randint(low=4, high=7, size=10000)
    x_train = x_train.reshape(-1, 1)
    model.fit(x_train=x_train, prc=ANOMALY_THRESHOLD)

    # model.predict(x_test=)

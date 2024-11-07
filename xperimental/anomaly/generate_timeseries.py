import pandas as pd
import datetime
import numpy as np

from naeural_core.core_logging import SBLogger


if __name__ == "__main__":
  log=SBLogger()

  NUMBER_OF_DATAPOINTS = 5000
  TIMEDELTA_MINUTES = 60
  ANOMALY_RATE = 0.01

  base = datetime.datetime.today()
  timestamps = [base - datetime.timedelta(minutes=TIMEDELTA_MINUTES * x) for x in range(NUMBER_OF_DATAPOINTS)]
  values = [
    np.random.normal(loc=0, scale=10) if np.random.uniform(0, 1) > ANOMALY_RATE
      else np.random.uniform(100,200)
    for _ in range(NUMBER_OF_DATAPOINTS)
  ]

  df = pd.DataFrame({'timestamps':timestamps, 'vals':values})
  log.save_dataframe(
    df=df,
    fn='tutorial_ts.csv'
  )


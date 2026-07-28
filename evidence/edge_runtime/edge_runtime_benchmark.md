# Edge Runtime Benchmark

- Status: **PASS**
- Model bundle: **2.4611 MB**
- Median four-horizon inference: **6.7911 ms**
- P95 four-horizon inference: **7.4665 ms**

The benchmark clears the prediction cache before every run and covers HGB, LSTM, Transformer and selected hybrid outputs for four horizons from one 49-interval facility history. It excludes API transport, browser rendering and full-campus plant response.

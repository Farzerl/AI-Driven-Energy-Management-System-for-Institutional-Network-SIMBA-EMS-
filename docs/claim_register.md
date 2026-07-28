# Demonstration Claim Register

| Claim | Status | Wording to use |
|---|---|---|
| The dataset covers 22 facilities | Verified | The authorised dataset contains 22 monitored facilities. |
| The model is trained and working | Verified | The packaged model was trained on UZ meter data and tested on April 2026 records not used for fitting. |
| The model predicts four horizons | Verified | The API produces 30-minute, 2-hour, 6-hour, and 24-hour forecasts. |
| The 30-minute test MAE is 2.2878 kVA | Verified | State the value with the chronological test period. |
| The forecasting model always beats every rule | Do not claim | Performance depends on horizon and operating conditions. |
| The system controls live campus circuits | Do not claim | The current release is advisory and software-in-the-loop. |
| The controller protects critical loads | Demonstrated in scenarios | Critical loads are excluded by rule and scenario violations are counted. |
| Scenario peak reduction is a realised saving | Do not claim | Scenario results are planning evidence until a measured pilot is commissioned. |
| The anomaly alert diagnoses a fault | Do not claim | It flags an unusual condition for investigation. |
| OpenDSS validates the present release | Do not claim | OpenDSS is planned after network parameters are verified. |
| The source workbooks are included | Do not claim | The release includes a trained model, metrics, and a limited historical replay. |

Use verified numbers only with their stated scope. Separate held-out model results, scenario results, and future pilot objectives.

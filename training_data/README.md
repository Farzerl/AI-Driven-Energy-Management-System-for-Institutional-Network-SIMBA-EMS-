# Training dataset input

Place exactly one authorised institutional meter dataset ZIP in this folder before running `INSTALL_AND_TRAIN_CHRONOS2.bat`.
The archive may contain XLSX or CSV meter exports. Existing SIMBA-EMS cleaning and alias rules are used.

The setup uses chronological splits. The latest month is kept as the final test period, and the preceding month is used for validation. Test records are never used for fitting or model selection.

The source ZIP is deleted only after all training, benchmark, evidence, routing, and verification steps complete successfully. Processed summaries and metrics remain in the repository; the source readings are not copied into the dashboard.

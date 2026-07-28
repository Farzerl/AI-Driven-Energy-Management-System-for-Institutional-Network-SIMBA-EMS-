# Proposed edge gateway plan

## Purpose

The proposed gateway connects approved institutional meter interfaces to the AI4I-EMS server while maintaining local buffering during network interruptions.

## Pilot functions

- collect timestamped kVA, kWh and power-factor readings;
- validate facility identity and data ranges;
- buffer readings locally when the server is unavailable;
- forward buffered readings after recovery;
- expose gateway health to the dashboard;
- run as a monitoring-only service during the initial pilot.

## Candidate platform

A Raspberry Pi or equivalent industrial edge computer is suitable for the first supervised pilot because the software requires modest CPU and storage resources. Final selection depends on environmental conditions, institutional maintenance requirements and the available meter interface.

## Installation boundary

This document is a pilot design, not evidence of a commissioned installation. Meter and communications wiring must be reviewed by qualified personnel before connection.

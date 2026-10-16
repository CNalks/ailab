# Data sources & licences

Data are not covered by the Apache-2.0 code licence. Verified 2026-10-16.

| source_id | Dataset | Access | Terms (as read on the source pages) |
|---|---|---|---|
| `cdc_fluview` (bundled fixture) | CDC FluView / ILINet, national weekly, 2010w40–2026w39, latest issue per week | `https://api.delphi.cmu.edu/epidata/fluview/` ([docs](https://cmu-delphi.github.io/delphi-epidata/api/fluview.html)) | Delphi docs: "Public Domain US Government". CDC agency-materials terms (https://www.cdc.gov/other/agencymaterials.html): attribute CDC, do not imply endorsement, do not alter the substance of the original material, say that it is available from CDC. Our forecasts/alerts are separate, labelled derivative analysis; original values are unchanged. |

| `ukhsa_dashboard` (showcase snapshot) | UKHSA data dashboard, England, influenza testing positivity by week (2017-07 → 2026-09) | `https://api.ukhsa-dashboard.data.gov.uk/` (snapshot in `site/data-src/`) | Dashboard footer (read 2026-10-16): "All content is available under the Open Government Licence v3.0, except where otherwise stated"; © Crown copyright. Attribution: "Contains public sector information published by the UK Health Security Agency (UKHSA) and licensed under the Open Government Licence v3.0." |
| `ecdc_erviss` (showcase snapshot) | ECDC ERVISS primary-care sentinel data, EU/EEA influenza positivity and Malta influenza detections (weekly, 2022-W25 → 2026-W41) | `https://github.com/EU-ECDC/Respiratory_viruses_weekly_data` (filtered snapshot in `site/data-src/`) | ECDC copyright page (read 2026-10-16): ECDC-owned content is CC BY 4.0 (credit ECDC, link the licence, say if modified); data from reporting countries may be reproduced if "Dataset provided by ECDC based on data provided by public health authorities, scientific institutes or health care providers in the relevant reporting countries and/or by WHO." is given. Weekly values are unchanged; forecasts, gate decisions and alerts are our own derived analysis, not ECDC products. |

The two snapshots only feed the static showcase site (`scripts/build_showcase_data.py`, see
[SHOWCASE_SITE.md](SHOWCASE_SITE.md)); the engine and the Docker image bundle only the FluView fixture.

Planned (not in this MVP): OpenDengue (CC BY-SA per the data paper, adaptations must be shared alike). WHO FluNet is
second tier (WHO terms restrict modification).

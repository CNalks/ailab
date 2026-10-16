# 5-minute API tutorial

Every command in a `bash verify` block below is executed against a running service by the
test suite (`tests/e2e/test_docs_samples.py`, part of `make e2e`), so the samples cannot rot.
Expected output is shown after each block (from a real run; timestamps/ids may differ).

## 0. Start a service

```bash
# a throw-away service with the demo data and write access (key >= 16 characters)
export AILAB_API_KEY=$(openssl rand -hex 24)
docker run --rm -p 127.0.0.1:8000:8000 -e AILAB_API_KEY ghcr.io/cnalks/ailab
```

Without `AILAB_API_KEY` the service is **read-only** (every write answers 403). All commands
below use two variables (the test-suite provides them; in your shell set them yourself):

```bash
export AILAB_URL=http://127.0.0.1:8000     # where the service listens
# export AILAB_API_KEY=...                 # the same key you started the service with
```

## 1. Is it up? What data does it have?

```bash verify
curl -fsS "$AILAB_URL/health" | jq -c '{status, version, writable}'
curl -fsS "$AILAB_URL/ready"  | jq -c .status
curl -fsS "$AILAB_URL/api/v1/series" | jq -r '.items[] | "\(.series_id)\t\(.indicator_type)\t\(.name)"'
```

```
{"status":"ok","version":"0.2.1","writable":true}
"ready"
us_ilinet_ili_visits	count	US national — ILI outpatient visits
us_ilinet_wili	proportion	US national — Weighted ILI (% of outpatient visits)
```

Interactive docs: `$AILAB_URL/docs` (Swagger UI), machine-readable: `$AILAB_URL/openapi.json`.

## 2. Forecast with a policy — and a reason when it refuses

```bash verify
curl -fsS "$AILAB_URL/api/v1/series/us_ilinet_wili/forecast?horizon=4" |
  jq -c '{policy, first_point: .forecast.points[0], backtest: (.backtest | {n_windows, seasons_covered, chosen_model})}'
```

```
{"policy":{"forecast_allowed":true,"reason":"beats_seasonal_naive_in_rolling_origin_backtest","display_mode":"forecast_band","model_family":"mstl_ets","horizon_weeks":4,"indicator_type":"proportion","warnings":[]},"first_point":{"h":1,"ds":"2026-10-04","point":1.89343,"lo80":1.72195,"hi80":2.08163,"lo95":1.67095,"hi95":2.14489},"backtest":{"n_windows":52,"seasons_covered":3.02,"chosen_model":"MSTL"}}
```

The first call computes a rolling-origin backtest (a few seconds); identical calls are cached.
`policy.forecast_allowed=false` means **no numbers are returned**, only the reason (see
[MODEL_POLICY.md](MODEL_POLICY.md)).

## 3. Add your own data (needs the key)

`indicator_type` is mandatory; `null` means *missing* (never zero); dates are week starts on one
weekly grid.

```bash verify
auth=(-H "X-API-Key: $AILAB_API_KEY" -H "Content-Type: application/json")
curl -fsS -X POST "$AILAB_URL/api/v1/series?overwrite=true" "${auth[@]}" -d '{
  "meta": {"series_id": "tut_positives", "name": "Positive tests", "indicator_type": "count"},
  "observations": [{"ds":"2025-01-05","y":12},{"ds":"2025-01-12","y":18},
                   {"ds":"2025-01-19","y":null},{"ds":"2025-01-26","y":35}]}'
echo
curl -fsS -X POST "$AILAB_URL/api/v1/series?overwrite=true" "${auth[@]}" -d '{
  "meta": {"series_id": "tut_tests", "name": "Tests performed", "indicator_type": "count"},
  "observations": [{"ds":"2025-01-05","y":200},{"ds":"2025-01-12","y":220},
                   {"ds":"2025-01-19","y":210},{"ds":"2025-01-26","y":240}]}'
echo
```

```
{"series_id":"tut_positives","observations":4,"replaced":false,"stale_dependents":[]}
{"series_id":"tut_tests","observations":4,"replaced":false,"stale_dependents":[]}
```

Sending the same request again with `overwrite=true` is idempotent; without it the second call is
`409 conflict`.

## 4. Derive a series: positivity = positives / tests

```bash verify
# dry run first (no key needed, nothing stored)
curl -fsS -X POST "$AILAB_URL/api/v1/derive/preview" -H "Content-Type: application/json" \
  -d '{"series_id":"tut_positivity","name":"Positivity","expr":"tut_positives / tut_tests"}' |
  jq -c '{stored, inferred_indicator_type, observations, n_missing}'
# create it
curl -fsS -X POST "$AILAB_URL/api/v1/derive" "${auth[@]}" \
  -d '{"series_id":"tut_positivity","name":"Positivity","expr":"tut_positives / tut_tests","overwrite":true}' |
  jq -c '{stored, inferred_indicator_type, sample: .sample[0:2]}'
curl -fsS "$AILAB_URL/api/v1/series/tut_positivity/lineage" | jq -c '{derived_from, kind: .lineage.kind_inference.inferred, missing: .lineage.alignment.n_missing_result}'
```

```
{"stored":false,"inferred_indicator_type":"proportion","observations":4,"n_missing":1}
{"stored":true,"inferred_indicator_type":"proportion","sample":[{"ds":"2025-01-05","y":0.06},{"ds":"2025-01-12","y":0.081818}]}
{"derived_from":["tut_positives","tut_tests"],"kind":"proportion","missing":1}
```

`count / count` is inferred to be a **proportion**; the week with a missing numerator stays missing.
Unsafe or unknown expressions are refused with `422 invalid_expression`:

```bash verify
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$AILAB_URL/api/v1/derive/preview" \
  -H "Content-Type: application/json" \
  -d '{"series_id":"tut_evil","name":"x","expr":"__import__(\"os\").system(\"id\")"}')
test "$code" = 422 && echo "rejected with $code"
```

## 5. Data quality and alert signals

```bash verify
curl -fsS "$AILAB_URL/api/v1/series/us_ilinet_wili/quality" | jq -c '{grade, n_weeks, reasons}'
curl -fsS "$AILAB_URL/api/v1/series/us_ilinet_ili_visits/alerts?method=ears_c2&baseline=7" |
  jq -c '{n_evaluated, n_signals, latest_is_signal, wording}'
# a stored rule, then evaluate all rules
curl -fsS -X PUT "$AILAB_URL/api/v1/alerts/rules" "${auth[@]}" \
  -d '{"rule_id":"tut_rule","series_id":"us_ilinet_ili_visits","method":"cusum","baseline":7}' | jq -c .
curl -fsS "$AILAB_URL/api/v1/alerts/evaluate" | jq -c '.items[] | select(.rule.rule_id=="tut_rule") | {rule: .rule.rule_id, n_signals}'
```

## 6. Download and export

```bash verify
curl -fsS "$AILAB_URL/api/v1/series/tut_positivity/export?format=csv"
curl -fsS "$AILAB_URL/api/v1/series/tut_positivity/export?format=parquet" -o /tmp/tut_positivity.parquet
head -c 4 /tmp/tut_positivity.parquet; echo
```

```
series_id,period_start,value
tut_positivity,2025-01-05,0.06
tut_positivity,2025-01-12,0.08181818181818182
tut_positivity,2025-01-19,
tut_positivity,2025-01-26,0.14583333333333334
PAR1
```

`POST /api/v1/export` writes the static site export (manifest + JSON + Parquet) to the server's
`AILAB_OUT` directory (see [EXPORT_FORMAT.md](EXPORT_FORMAT.md)); it analyses **every** series, so
it takes a few seconds per series.

## 7. Python

```python verify
import os
import httpx

base, key = os.environ["AILAB_URL"], os.environ["AILAB_API_KEY"]
body = {
    "meta": {"series_id": "tut_py", "name": "From Python", "indicator_type": "count"},
    "observations": [
        {"ds": "2025-01-05", "y": 3},
        {"ds": "2025-01-12", "y": None},
        {"ds": "2025-01-19", "y": 5},
    ],
}
with httpx.Client(base_url=base, timeout=120) as c:
    r = c.post(
        "/api/v1/series", params={"overwrite": "true"}, json=body, headers={"X-API-Key": key}
    )
    r.raise_for_status()
    items = c.get("/api/v1/series/tut_py/data").json()["items"]
    assert [o["y"] for o in items] == [3, None, 5]  # the missing week is None, not 0
    print([o["y"] for o in items])
    c.delete("/api/v1/series/tut_py", headers={"X-API-Key": key}).raise_for_status()
```

## 8. Load a whole synthetic test set

The package ships 55 deterministic test cases (seasonal, missing weeks, outbreaks, constant,
all-zero, huge, unicode, invalid values, …). Loading checks that valid cases are accepted (201)
and invalid ones rejected (422):

```bash verify
ailab synth load --url "$AILAB_URL" --key "$AILAB_API_KEY" | tail -2
```

```
ok   201 derive_positivity            accept
56/56 as expected
```

(In a container: `docker run --rm --network host ghcr.io/cnalks/ailab synth load --url ...`.)
The loaded series are named `ts_*`; remove them with `DELETE /api/v1/series/{id}?cascade=true`.

## 9. Clean up

```bash verify
for s in tut_positivity tut_positives tut_tests; do
  curl -fsS -X DELETE "$AILAB_URL/api/v1/series/$s?cascade=true" -H "X-API-Key: $AILAB_API_KEY" | jq -c .
done
curl -fsS -X DELETE "$AILAB_URL/api/v1/alerts/rules/tut_rule" -H "X-API-Key: $AILAB_API_KEY" | jq -c .
for s in $(curl -fsS "$AILAB_URL/api/v1/series?limit=200" | jq -r '.items[].series_id | select(startswith("ts_"))'); do
  # (a series may already be gone through an earlier cascade: ignore 404)
  curl -sS -X DELETE "$AILAB_URL/api/v1/series/$s?cascade=true" -H "X-API-Key: $AILAB_API_KEY" >/dev/null || true
done
```

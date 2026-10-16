"""Command line interface: ``ailab demo | export | list | check | forecast | alert | serve | doctor``."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from . import __version__

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="ailab — time-series engine for infectious-disease surveillance.",
)

DB_OPT = typer.Option(None, "--db", help="DuckDB file (default: in-memory, demo data loaded).")


def _store(db: Path | None, demo: bool = True, refresh: bool = False):
    from .demo import load_fluview_demo
    from .store import SeriesStore

    store = SeriesStore(db if db else ":memory:")
    if demo and (db is None or store.count_series() == 0 or refresh):
        info = load_fluview_demo(store, refresh=refresh)
        if info["stale"]:
            typer.secho(f"warning: {info['note']}", fg="yellow", err=True)
    return store


@app.command()
def demo(
    out: Path = typer.Option(Path("out"), help="Output directory for the static export."),
    refresh: bool = typer.Option(
        False, "--refresh", help="Fetch fresh data from Delphi (network)."
    ),
    db: Path | None = DB_OPT,
    horizon: int = typer.Option(8, min=1, max=26),
):
    """Run the whole pipeline on the CDC FluView demo series and write the static export."""
    from .export import export_site

    store = _store(db, refresh=refresh)
    manifest = export_site(store, out, horizon=horizon)
    typer.echo(f"ailab {__version__}: exported {len(manifest['series'])} series -> {out}/")
    for s in manifest["series"]:
        p = s["policy"]
        typer.echo(
            f"  {s['series_id']:<24} through {s['data_through']}  grade {s['quality_grade']}  "
            f"forecast_allowed={p['forecast_allowed']} ({p['reason']}) "
            f"model={p['model_family']} horizon={p['horizon_weeks']}w  "
            f"{s['gzip_bytes'] / 1024:.1f} KB gz"
        )
    typer.echo(f"manifest: {out}/manifest.json")


@app.command()
def export(
    out: Path = typer.Option(Path("out")),
    db: Path = typer.Option(..., "--db", help="DuckDB file to export from."),
    series: list[str] | None = typer.Option(None, "--series", help="Series id (repeatable)."),
    horizon: int = typer.Option(8, min=1, max=26),
):
    """Export stored series (analysis + policy + alerts) as static JSON/Parquet."""
    from .export import export_site

    store = _store(db, demo=False)
    manifest = export_site(store, out, series_ids=series or None, horizon=horizon)
    typer.echo(f"exported {len(manifest['series'])} series -> {out}/manifest.json")


@app.command("list")
def list_(db: Path | None = DB_OPT):
    """List stored series."""
    for m in _store(db).list_series():
        typer.echo(f"{m.series_id}\t{m.indicator_type}\t{m.name}")


@app.command()
def check(series_id: str, db: Path | None = DB_OPT):
    """Data-quality checklist for one series."""
    from . import quality

    store = _store(db)
    rep = quality.check_series(store.get_meta(series_id), store.get_observations(series_id))
    typer.echo(json.dumps(rep, indent=2))


@app.command()
def forecast(
    series_id: str,
    horizon: int = typer.Option(8, "--h", min=1, max=26),
    db: Path | None = DB_OPT,
):
    """Backtest, apply the forecast policy and (if allowed) forecast."""
    from .pipeline import analyse

    res = analyse(_store(db), series_id, horizon=horizon, include_observations=False)
    typer.echo(
        json.dumps(
            {k: res[k] for k in ("policy", "backtest", "forecast")}, indent=2, ensure_ascii=False
        )
    )


@app.command("gate")
def gate_cmd(
    series_id: str,
    horizon: int = typer.Option(8, "--h", min=1, max=26),
    lang: str = typer.Option("en", help="Message language: en | zh | fr"),
    as_of: str | None = typer.Option(
        None, "--as-of", help="YYYY-MM-DD or 'today': enables the stale-data check."
    ),
    quick: bool = typer.Option(False, "--quick", help="Data checks only (no backtest)."),
    set_: list[str] | None = typer.Option(
        None, "--set", help="Threshold override KEY=VALUE for this run (repeatable)."
    ),
    as_json: bool = typer.Option(False, "--json", help="Full machine-readable decision."),
    db: Path | None = DB_OPT,
):
    """Forecast quality gate: may this series be forecast? (allowed / limited / refused)"""
    import pandas as pd

    from . import gate as gate_mod
    from .pipeline import analyse_frame, gate_data_only

    if lang not in gate_mod.msg.LANGS:
        raise _fail(ValueError(f"--lang must be one of {gate_mod.msg.LANGS}"))
    store = _store(db)
    try:
        meta = store.get_meta(series_id)
        over = dict(meta.gate)
        for kv in set_ or []:
            k, sep, v = kv.partition("=")
            if not sep:
                raise ValueError(f"--set must look like KEY=VALUE, got {kv!r}")
            over[k] = v
        meta = meta.model_copy(update={"gate": gate_mod.validate_series_overrides(over)})
        today = (
            None
            if as_of is None
            else pd.Timestamp.now().normalize()
            if as_of == "today"
            else pd.Timestamp(as_of)
        )
        raw = store.get_observations(series_id)
        if quick:
            gd = gate_data_only(meta, raw, today).to_dict()
        else:
            gd = analyse_frame(
                meta, raw, [], horizon=horizon, include_observations=False, today=today
            )["gate"]
    except (KeyError, ValueError) as exc:
        raise _fail(exc) from None
    if as_json:
        typer.echo(json.dumps(gd, indent=2, ensure_ascii=False))
        return
    color = {"allowed": "green", "limited": "yellow", "refused": "red"}[gd["decision"]]
    typer.secho(
        f"{series_id}: {gd['decision'].upper()} (confidence {gd['confidence']}"
        f"{', hard to judge' if gd['hard_to_judge'] else ''}; stage {gd['stage']})",
        fg=color,
        bold=True,
    )
    for r in gd["reasons"]:
        typer.echo(f"  [{r['severity']:<5}] {r['code']}: {r['message'][lang]}")
    lim = {k: v for k, v in gd["limits"].items() if v not in (None, False, 0, 1.0)}
    if lim:
        typer.echo(f"  limits: {lim}")
    typer.echo(
        f"  action: {gd['recommended_action']['code']} - {gd['recommended_action']['message'][lang]}"
    )
    raise typer.Exit(0 if gd["decision"] != "refused" else 1)


@app.command()
def alert(
    series_id: str,
    method: str = typer.Option("ears_c2", help="ears_c1 | ears_c2 | ears_c3 | cusum"),
    baseline: int = typer.Option(7, min=3),
    db: Path | None = DB_OPT,
):
    """Statistical-signal scan (EARS / CUSUM) over a series."""
    from . import alerts, quality

    store = _store(db)
    g, _ = quality.model_input(store.get_observations(series_id))
    typer.echo(json.dumps(alerts.summarise(alerts.detect(g, method, baseline)), indent=2))


series_app = typer.Typer(no_args_is_help=True, help="Manage stored series (needs --db).")
derive_app = typer.Typer(no_args_is_help=True, help="Derived (composite) series.")
app.add_typer(series_app, name="series")
app.add_typer(derive_app, name="derive")
DB_REQ = typer.Option(..., "--db", envvar="AILAB_DB", help="DuckDB file.")


def _fail(exc: Exception) -> typer.Exit:
    typer.secho(f"error: {type(exc).__name__}: {exc}", fg="red", err=True)
    return typer.Exit(2)


def _db(db: Path):
    return _store(db, demo=False)


@series_app.command("add")
def series_add(
    series_id: str,
    file: Path = typer.Argument(..., exists=True, dir_okay=False, help="CSV with a date + value."),
    name: str = typer.Option(..., "--name"),
    kind: str = typer.Option(..., "--kind", help="count | proportion | rate | event"),
    db: Path = DB_REQ,
    ds_col: str = typer.Option("ds", help="Date column (week start)."),
    y_col: str = typer.Option("y", help="Value column; empty cells = missing."),
    value_scale: int = typer.Option(1, help="Proportions: 1 (fractions) or 100 (percent)."),
    season_length: int = typer.Option(52, min=2, max=60),
    disease: str = typer.Option(""),
    geography: str = typer.Option(""),
    unit: str = typer.Option(""),
    gate_set: list[str] | None = typer.Option(
        None,
        "--gate",
        help="Per-series gate threshold KEY=VALUE (repeatable), e.g. reporting_lag_weeks=2",
    ),
    mask: list[str] | None = typer.Option(
        None, "--mask", help="Disruption period START:END (YYYY-MM-DD, repeatable), e.g. COVID-19."
    ),
):
    """Import a base series from CSV (kind is required; values are validated against it)."""
    import pandas as pd

    from .schema import SeriesMeta

    try:
        df = pd.read_csv(file).rename(columns={ds_col: "ds", y_col: "y"})
        meta = SeriesMeta(
            series_id=series_id,
            name=name,
            indicator_type=kind,
            value_scale=value_scale,
            season_length=season_length,
            disease=disease,
            geography=geography,
            unit=unit,
            gate=dict(kv.split("=", 1) for kv in gate_set or []),
            mask_periods=[m.split(":", 1) for m in mask or []],
        )
        n = _db(db).upsert_series(meta, df[["ds", "y"]])
    except (ValueError, KeyError) as exc:
        raise _fail(exc) from None
    typer.echo(f"stored {series_id} ({kind}): {n} observations")


@series_app.command("list")
def series_list(
    db: Path = DB_REQ,
    limit: int = typer.Option(50, min=1, max=200),
    offset: int = typer.Option(0, min=0),
    kind: str | None = typer.Option(None, help="Filter by indicator_type."),
    derived: bool | None = typer.Option(None, "--derived/--base"),
):
    """List series with kind and lineage flag."""
    items, total = _db(db).page_series(limit, offset, kind, derived)
    for m in items:
        typer.echo(
            f"{m.series_id}\t{m.indicator_type}\t{'derived' if m.is_derived else 'base'}\t{m.name}"
        )
    typer.echo(f"# {len(items)} of {total}", err=True)


@series_app.command("show")
def series_show(series_id: str, db: Path = DB_REQ):
    """Metadata and lineage as JSON."""
    try:
        typer.echo(_db(db).get_meta(series_id).model_dump_json(indent=2))
    except KeyError as exc:
        raise _fail(exc) from None


@series_app.command("data")
def series_data(
    series_id: str,
    db: Path = DB_REQ,
    limit: int = typer.Option(20, min=1, max=5000),
    offset: int = typer.Option(0, min=0),
    csv_out: Path | None = typer.Option(None, "--csv", help="Write the whole series to CSV."),
):
    """Print observations (missing shown as empty) or write them all to CSV."""
    store = _db(db)
    try:
        df = store.get_observations(series_id, limit=None if csv_out else limit, offset=offset)
    except KeyError as exc:
        raise _fail(exc) from None
    if csv_out:
        df.rename(columns={"ds": "period_start", "y": "value"}).to_csv(csv_out, index=False)
        typer.echo(f"wrote {len(df)} rows -> {csv_out}")
    else:
        typer.echo(df.to_csv(index=False, date_format="%Y-%m-%d"))


@series_app.command("delete")
def series_delete(
    series_id: str,
    db: Path = DB_REQ,
    cascade: bool = typer.Option(False, help="Also delete derived series built from it."),
    yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation."),
):
    """Delete a series (refuses if derived series depend on it, unless --cascade)."""
    from .store import DependencyError

    if not yes:
        typer.confirm(f"Delete {series_id}?", abort=True)
    try:
        ids = _db(db).delete_series(series_id, cascade=cascade)
    except (KeyError, DependencyError) as exc:
        raise _fail(exc) from None
    typer.echo("deleted: " + ", ".join(ids))


def _bindings(bind: list[str] | None) -> dict[str, str]:
    out = {}
    for b in bind or []:
        a, sep, sid = b.partition("=")
        if not sep:
            raise _fail(ValueError(f"--bind must look like ALIAS=series_id, got {b!r}"))
        out[a] = sid
    return out


@derive_app.command("preview")
def derive_preview(
    expr: str,
    db: Path = DB_REQ,
    bind: list[str] | None = typer.Option(None, "--bind", help="ALIAS=series_id (repeatable)."),
    align: str = typer.Option("intersection", help="intersection | union"),
):
    """Dry run: inferred kind, alignment report and warnings (nothing is stored)."""
    from . import derive_service

    try:
        d = derive_service.plan(_db(db), "preview", "preview", expr, _bindings(bind), align)
    except (ValueError, KeyError) as exc:
        raise _fail(exc) from None
    typer.echo(
        json.dumps(
            {
                "indicator_type": d.meta.indicator_type,
                "value_scale": d.meta.value_scale,
                "observations": len(d.data),
                "warnings": d.warnings,
                "lineage": d.meta.lineage,
            },
            indent=2,
        )
    )


@derive_app.command("create")
def derive_create(
    series_id: str,
    expr: str,
    db: Path = DB_REQ,
    name: str = typer.Option(..., "--name"),
    bind: list[str] | None = typer.Option(None, "--bind", help="ALIAS=series_id (repeatable)."),
    align: str = typer.Option("intersection", help="intersection | union"),
    kind: str | None = typer.Option(None, help="Assert the inferred kind (optional)."),
    overwrite: bool = typer.Option(False),
):
    """Create a derived series, e.g.  ailab derive create pos_rate "pos / tests" --name ..."""
    from . import derive_service

    try:
        d = derive_service.create_derived(
            _db(db), series_id, name, expr, _bindings(bind), align, kind, overwrite
        )
    except (ValueError, KeyError) as exc:
        raise _fail(exc) from None
    typer.echo(
        f"stored {series_id}: kind={d.meta.indicator_type} from {d.meta.derived_from} "
        f"({len(d.data)} weeks)"
    )
    for w in d.warnings:
        typer.secho(f"warning: {w}", fg="yellow", err=True)


@derive_app.command("recompute")
def derive_recompute(series_id: str, db: Path = DB_REQ):
    """Re-evaluate a derived series from the current data of its sources."""
    from . import derive_service

    try:
        d = derive_service.recompute(_db(db), series_id)
    except (ValueError, KeyError) as exc:
        raise _fail(exc) from None
    typer.echo(f"recomputed {series_id}: {len(d.data)} weeks, kind={d.meta.indicator_type}")


synth_app = typer.Typer(
    invoke_without_command=True,
    help="Deterministic synthetic data. `ailab synth --kind count ...` writes one CSV; "
    "`ailab synth testset` writes the API test-data set; `ailab synth load` loads it into a "
    "running service.",
)
app.add_typer(synth_app, name="synth")


@synth_app.callback()
def synth(
    ctx: typer.Context,
    out: Path = typer.Option(Path("synthetic.csv"), help="CSV output (ds,y)."),
    kind: str = typer.Option("count", help="count | proportion"),
    weeks: int = typer.Option(312, min=10, max=5200),
    seed: int = typer.Option(0),
    season_length: int = typer.Option(52, min=2, max=60),
    missing_frac: float = typer.Option(0.0, min=0.0, max=0.5),
):
    """Write a deterministic synthetic weekly series (dev/testing; seeded)."""
    if ctx.invoked_subcommand is not None:
        return
    from .synth import synth_series

    try:
        df = synth_series(weeks, kind, seed, season_length=season_length, missing_frac=missing_frac)
    except ValueError as exc:
        raise _fail(exc) from None
    df.to_csv(out, index=False)
    typer.echo(f"wrote {len(df)} weeks ({kind}, seed={seed}) -> {out}")


@synth_app.command("testset")
def synth_testset(
    out: Path = typer.Option(Path("tests/data/synthetic"), help="Output directory."),
):
    """Write the reusable API test-data set (one JSON body per case + manifest.json)."""
    from .testset import build_cases, write_testset

    cases = build_cases()
    write_testset(out, cases)
    n_ok = sum(c.expect == "accept" for c in cases)
    typer.echo(f"wrote {len(cases)} cases ({n_ok} accepted, {len(cases) - n_ok} invalid) -> {out}/")


@synth_app.command("load")
def synth_load(
    url: str = typer.Option("http://127.0.0.1:8000", help="Base URL of a running service."),
    key: str = typer.Option(..., envvar="AILAB_API_KEY", help="Service write key."),
    directory: Path | None = typer.Option(
        None, "--dir", help="Load from a directory written by `synth testset` (default: generate)."
    ),
    derive: bool = typer.Option(True, help="Also create ts_pair_positivity = positives / tests."),
):
    """Load the test-data set through the REST API and check each case's expected outcome."""
    from .testset import build_cases, load_into_service, read_testset

    cases = read_testset(directory) if directory else build_cases()
    try:
        results = load_into_service(url, key, cases, derive=derive)
    except Exception as exc:
        raise _fail(exc) from None
    bad = [r for r in results if not r["ok"]]
    for r in results:
        typer.echo(
            f"{'ok  ' if r['ok'] else 'FAIL'} {r['status']} {r['case_id']:<28} {r['expect']}"
        )
    typer.echo(f"{len(results) - len(bad)}/{len(results)} as expected")
    raise typer.Exit(1 if bad else 0)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Use 0.0.0.0 inside containers."),
    port: int = typer.Option(8000),
    db: Path | None = typer.Option(None, "--db", envvar="AILAB_DB"),
    preload_demo: bool = typer.Option(False, "--preload-demo", envvar="AILAB_PRELOAD_DEMO"),
):
    """Run the service API (FastAPI): /health, /docs, /api/v1/..."""
    import logging
    import os

    import uvicorn

    from .api.app import create_app

    logging.basicConfig(
        level=os.environ.get("AILAB_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from .api.settings import ConfigError

    try:
        application = create_app(db_path=db, preload_demo=preload_demo or None)
    except ConfigError as exc:  # clean one-line message instead of a rich traceback
        raise _fail(exc) from None
    uvicorn.run(
        application,
        host=host,
        port=port,
        log_level="info",
        server_header=False,
        access_log=False,  # ailab.api logs one line per request (path only, no query string)
    )


@app.command()
def doctor():
    """Environment self-check (versions, DuckDB, statsforecast import). Exit code 1 on failure."""
    import importlib

    ok = True
    for mod in ("duckdb", "pandas", "numpy", "statsforecast", "fastapi", "pyarrow", "simpleeval"):
        try:
            m = importlib.import_module(mod)
            typer.echo(f"ok   {mod} {getattr(m, '__version__', '?')}")
        except Exception as exc:
            ok = False
            typer.echo(f"FAIL {mod}: {exc}")
    typer.echo(f"ailab {__version__}")
    raise typer.Exit(0 if ok else 1)


if __name__ == "__main__":
    app()

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


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Use 0.0.0.0 inside containers."),
    port: int = typer.Option(8000),
    db: Path | None = typer.Option(None, "--db", envvar="AILAB_DB"),
    preload_demo: bool = typer.Option(False, "--preload-demo", envvar="AILAB_PRELOAD_DEMO"),
):
    """Run the service API (FastAPI): /health, /docs, /api/v1/..."""
    import uvicorn

    from .api.app import create_app

    uvicorn.run(
        create_app(db_path=db, preload_demo=preload_demo), host=host, port=port, log_level="info"
    )


@app.command()
def doctor():
    """Environment self-check (versions, DuckDB, statsforecast import). Exit code 1 on failure."""
    import importlib

    ok = True
    for mod in ("duckdb", "pandas", "numpy", "statsforecast", "fastapi", "pyarrow"):
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

"""Create / preview / recompute derived series against a :class:`SeriesStore`.

The result of a derivation is *materialised* (observations stored in DuckDB) together with its
lineage (expression, alias bindings, source fingerprints, alignment report, kind-inference
trail). ``recompute`` re-runs it from the current source data.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pandas as pd

from . import __version__, derive
from .derive import TS, AlignmentError, ExpressionError
from .schema import SeriesMeta
from .store import SeriesStore

MAX_ALIAS_COUNT = derive.MAX_SOURCES


class DerivationConflict(ValueError):
    """Target id exists (and may not be overwritten) or would create a dependency cycle."""


@dataclass
class Derivation:
    meta: SeriesMeta
    data: pd.DataFrame  # ds, y (NaN = missing)
    warnings: list[str]


def resolve_bindings(expr: str, bindings: dict[str, str] | None) -> dict[str, str]:
    """name used in the expression -> series id (an unbound name is taken as a series id)."""
    bindings = dict(bindings or {})
    for alias, sid in bindings.items():
        if not derive.ALIAS_RE.match(alias) or alias in derive.FUNCTIONS:
            raise ExpressionError(f"invalid alias {alias!r}")
        if not isinstance(sid, str):
            raise ExpressionError("binding values must be series ids")
    names = derive.referenced_names(expr)
    if not names:
        raise ExpressionError("expression must reference at least one series")
    if len(names) > derive.MAX_SOURCES:
        raise ExpressionError(f"at most {derive.MAX_SOURCES} source series")
    unused = set(bindings) - set(names)
    if unused:
        raise ExpressionError(f"bindings not used in the expression: {sorted(unused)}")
    return {n: bindings.get(n, n) for n in names}


def _default_unit(res: TS) -> str:
    if res.kind == "proportion":
        return "percent" if res.scale == 100 else "proportion"
    return ""


def _fingerprint(df: pd.DataFrame) -> str:
    return hashlib.sha256(df.to_csv(index=False, date_format="%Y-%m-%d").encode()).hexdigest()[:16]


def plan(
    store: SeriesStore,
    series_id: str,
    name: str,
    expr: str,
    bindings: dict[str, str] | None = None,
    align: str = "intersection",
    indicator_type: str | None = None,
    description_meta: dict | None = None,
) -> Derivation:
    """Evaluate without storing anything."""
    mapping = resolve_bindings(expr, bindings)
    sources: dict[str, SeriesMeta] = {}
    frames: dict[str, pd.DataFrame] = {}
    for nm, sid in mapping.items():
        try:
            sources[nm] = store.get_meta(sid)
        except KeyError:
            raise ExpressionError(f"unknown series {sid!r} (name {nm!r})") from None
        frames[nm] = store.get_observations(sid)
    if series_id in mapping.values():
        raise DerivationConflict("a derived series cannot use itself as a source")
    # a series cannot be derived from something that is itself derived from it (cycle)
    cyc = set(store.dependents(series_id)) & set(mapping.values())
    if cyc:
        raise DerivationConflict(f"dependency cycle through {sorted(cyc)}")
    seasons = {m.season_length for m in sources.values()}
    freqs = {m.frequency for m in sources.values()}
    if len(seasons) > 1 or len(freqs) > 1:
        raise AlignmentError(
            f"sources differ in frequency/season_length ({sorted(freqs)}, {sorted(seasons)})"
        )
    aligned, report = derive.align(frames, align)
    inputs = {
        nm: TS(aligned[nm], sources[nm].indicator_type, sources[nm].value_scale) for nm in mapping
    }
    res = derive.evaluate(expr, inputs)
    if indicator_type is not None and indicator_type != res.kind:
        raise ExpressionError(
            f"requested indicator_type {indicator_type!r} but the expression infers "
            f"{res.kind!r} ({'; '.join(res.notes[-2:])}); omit indicator_type to accept inference"
        )
    y = res.s
    valid = y.dropna()
    if valid.empty:
        raise ExpressionError("the derived series has no valid values (all missing)")
    trimmed_lead = int(y.index.get_loc(valid.index[0]))
    trimmed_tail = int(len(y) - 1 - y.index.get_loc(valid.index[-1]))
    y = y.loc[valid.index[0] : valid.index[-1]]
    report.update(
        n_missing_result=int(y.isna().sum()),
        n_result=int(len(y)),
        trimmed_leading_periods=trimmed_lead,
        trimmed_trailing_periods=trimmed_tail,
    )
    warnings = []
    if report["n_missing_result"]:
        warnings.append(
            f"{report['n_missing_result']} of {len(y)} weeks are missing in the result "
            "(missing inputs/undefined ratios stay missing, never 0)"
        )
    if res.kind == "rate" and any("downgraded" in n for n in res.notes):
        warnings.append("kind downgraded to 'rate': " + "; ".join(res.notes[-1:]))
    uniq = lambda xs: sorted({x for x in xs if x})  # noqa: E731
    metas = list(sources.values())
    ids = sorted(set(mapping.values()))
    src_ids = uniq(m.source_id for m in metas)
    lineage = {
        "ailab_version": __version__,
        "expr": expr,
        "bindings": mapping,
        "sources": [
            {
                "series_id": mapping[nm],
                "alias": nm,
                "indicator_type": sources[nm].indicator_type,
                "value_scale": sources[nm].value_scale,
                "source_id": sources[nm].source_id,
                "sha256_16": _fingerprint(frames[nm]),
            }
            for nm in sorted(mapping)
        ],
        "alignment": report,
        "kind_inference": {"inferred": res.kind, "steps": res.notes},
    }
    extra = description_meta or {}
    meta = SeriesMeta(
        series_id=series_id,
        name=name,
        disease=extra.get("disease") or "; ".join(uniq(m.disease for m in metas)),
        geography=extra.get("geography") or "; ".join(uniq(m.geography for m in metas)),
        unit=extra.get("unit") or _default_unit(res),
        indicator_type=res.kind,
        value_scale=res.scale if res.kind == "proportion" else 1,
        season_length=metas[0].season_length,
        source_id=src_ids[0] if len(src_ids) == 1 else "",
        licence="; ".join(uniq(m.licence for m in metas)),
        attribution=" | ".join(uniq(m.attribution for m in metas)),
        notice=" | ".join(uniq(m.notice for m in metas))
        + (
            " This series is derived by ailab from the sources listed in its lineage."
            if ids
            else ""
        ),
        derived_expr=expr,
        derived_from=ids,
        bindings={a: s for a, s in mapping.items() if a != s},
        lineage=lineage,
    )
    return Derivation(meta, y.rename("y").rename_axis("ds").reset_index(), warnings)


def create_derived(
    store: SeriesStore,
    series_id: str,
    name: str,
    expr: str,
    bindings: dict[str, str] | None = None,
    align: str = "intersection",
    indicator_type: str | None = None,
    overwrite: bool = False,
    description_meta: dict | None = None,
) -> Derivation:
    try:
        existing = store.get_meta(series_id)
    except KeyError:
        existing = None
    if existing is not None and not (overwrite and existing.is_derived):
        raise DerivationConflict(
            f"series {series_id!r} already exists"
            + ("" if existing.is_derived else " and is not a derived series")
        )
    d = plan(store, series_id, name, expr, bindings, align, indicator_type, description_meta)
    store.upsert_series(d.meta, d.data)
    return d


def recompute(store: SeriesStore, series_id: str) -> Derivation:
    """Re-evaluate a derived series from the current data of its sources."""
    meta = store.get_meta(series_id)
    if not meta.is_derived or not meta.lineage:
        raise DerivationConflict(f"{series_id!r} is not a derived series")
    mode = meta.lineage["alignment"]["mode"]
    d = plan(
        store,
        series_id,
        meta.name,
        meta.derived_expr,
        meta.bindings,
        mode,
        None,
        {"unit": meta.unit},
    )
    d.meta = d.meta.model_copy(update={"created_at": meta.created_at})
    d.meta.lineage["recomputed_at"] = pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")
    store.upsert_series(d.meta, d.data)
    return d

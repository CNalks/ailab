"""Source registry: every dataset the engine knows about carries its licence and attribution."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SourceInfo:
    source_id: str
    name: str
    publisher: str
    url: str
    docs_url: str
    licence: str
    licence_url: str
    attribution: str
    notice: str  # mandatory wording that must accompany derived output

    def to_dict(self) -> dict:
        return asdict(self)


_SOURCES: dict[str, SourceInfo] = {
    "cdc_fluview": SourceInfo(
        source_id="cdc_fluview",
        name="CDC FluView / ILINet (national, weekly), via Delphi Epidata",
        publisher="US Centers for Disease Control and Prevention (CDC); API by Carnegie Mellon "
        "University Delphi Research Group",
        url="https://api.delphi.cmu.edu/epidata/fluview/",
        docs_url="https://cmu-delphi.github.io/delphi-epidata/api/fluview.html",
        licence="Public domain (US Government work); Delphi API documentation states "
        "'Public Domain US Government'",
        licence_url="https://www.cdc.gov/other/agencymaterials.html",
        attribution="Source: US CDC FluView (ILINet), retrieved through the Delphi Epidata API "
        "(Carnegie Mellon University).",
        notice="Forecasts and alerts shown here are our own statistical analysis of public data. "
        "They are not CDC/HHS products, are not endorsed by CDC/HHS, and are not public-health "
        "advice. The original data are available free of charge from CDC.",
    ),
}


def get_source(source_id: str) -> SourceInfo:
    try:
        return _SOURCES[source_id]
    except KeyError as exc:
        raise KeyError(f"unknown source_id {source_id!r}") from exc


def list_sources() -> list[SourceInfo]:
    return list(_SOURCES.values())

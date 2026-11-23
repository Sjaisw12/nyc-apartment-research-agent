"""NYC Building Footprints (5zhs-2jue): where a BIN physically stands, and what is on a lot."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import SourceResponseError
from ..http_client import SourceHttpClient
from ..identifiers import normalize_bbl, normalize_bin

DATASET = "5zhs-2jue"


@dataclass
class FootprintsCheck:
    lots_for_bin: list[str]  # base_bbl / mappluto_bbl values of rows with this BIN
    bins_on_lot: list[str]  # BINs whose base_bbl or mappluto_bbl is the lot
    url: str
    fetched_at: str


def check_bin_and_lot(client: SourceHttpClient, bbl: str, bin_value: str | None) -> FootprintsCheck:
    """One request answering both questions. Identifiers are validated before use."""
    bbl = normalize_bbl(bbl)
    where = f"base_bbl='{bbl}' OR mappluto_bbl='{bbl}'"
    if bin_value is not None:
        where += f" OR bin='{normalize_bin(bin_value)}'"
    resp = client.get_json(
        f"/resource/{DATASET}.json",
        params={
            "$select": "bin,base_bbl,mappluto_bbl",
            "$where": where,
            "$order": "bin",
            "$limit": 500,
        },
    )
    if not isinstance(resp.data, list):
        raise SourceResponseError(resp.source, resp.url, "expected a JSON array")
    lots_for_bin: set[str] = set()
    bins_on_lot: set[str] = set()
    for row in resp.data:
        lots = {row.get("base_bbl"), row.get("mappluto_bbl")} - {None}
        if bin_value is not None and row.get("bin") == bin_value:
            lots_for_bin |= lots
        if bbl in lots and row.get("bin"):
            bins_on_lot.add(row["bin"])
    return FootprintsCheck(
        lots_for_bin=sorted(lots_for_bin),
        bins_on_lot=sorted(bins_on_lot),
        url=resp.url,
        fetched_at=resp.fetched_at,
    )

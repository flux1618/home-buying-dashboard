"""FastAPI wrapper around the analyzer.

Design constraints, and why each one is there:

**No new domain logic.** Every endpoint here is a translation layer. It parses a request,
calls `analyzer.pipeline.run` or `analyzer.batch`, and shapes the reply. If a rule about
what a house is worth ever appears in this file it is in the wrong place, because the
CLI and the batch runner would not get it. The endpoints are thin on purpose.

**Degradation is a 200, not a 503.** A missing broadband key or a dead county server does
not mean the request failed — it means the answer has a hole in it, and the answer says
where. Returning an error would throw away the tax, flood, and commute work that did
succeed. The only 4xx/5xx cases are a request that cannot be parsed and an address that
cannot be geocoded, because without coordinates there is nothing to ask anyone about.

**Blocking work in a threadpool.** The pipeline is synchronous `urllib` and each station
waits on a public server. Declaring the handlers `def` rather than `async def` lets
Starlette run them in its worker threadpool, so one slow county request does not stall
the whole event loop. Making them `async def` without an async HTTP client would be the
worst of both worlds — the syntax of concurrency with none of the behaviour.

**Stateless.** No database, no saved shortlists. Persisted state is a separate decision
with its own privacy consequences (see docs/THREAT_MODEL.md), and this service is the
container Bao points at locally, not a hosted multi-user app.
"""

from __future__ import annotations

import io
import time
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Body, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from analyzer import batch
from analyzer.core.profile import BuyerProfile, load_profile
from analyzer.core.analyze import ENGINE_VERSION
from analyzer.core.cost import solve_max_price
from analyzer.pipeline import PipelineAborted, run

# The profile is read once at startup rather than per request. It is a config file, not
# user input, and re-reading it on every call would let a half-saved edit produce two
# properties scored against two different rulebooks in the same batch.
_PROFILE: BuyerProfile | None = None


def profile() -> BuyerProfile:
    global _PROFILE
    if _PROFILE is None:
        _PROFILE = load_profile()
    return _PROFILE


# =============================================================================
# Request and response models
# =============================================================================


class PropertyRequest(BaseModel):
    """Only address and price are required.

    Everything else is a fact no public source will tell you — HOA dues, component
    ages, garage bay count. Omitting them is safe and explicitly supported: `None`
    means unknown, and the engine treats unknown differently from bad. Sending `0`
    for an unknown roof age would claim the roof is brand new, which is why these
    are nullable rather than defaulted to zero.
    """

    model_config = {"extra": "forbid"}

    address: str = Field(min_length=3, max_length=200)
    price: float = Field(gt=0, le=100_000_000)
    hoa_monthly: float = Field(default=0.0, ge=0, le=10_000)
    roof_age_years: int | None = Field(default=None, ge=0, le=200)
    hvac_age_years: int | None = Field(default=None, ge=0, le=200)
    garage_spaces: int | None = Field(default=None, ge=0, le=10)

    @field_validator("address")
    @classmethod
    def strip_address(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("address cannot be blank")
        return cleaned


class MaxPriceRequest(BaseModel):
    """Nothing about a specific house is required, because this is the pre-shopping question.

    Every field is optional. `sqft` and `year_built` only sharpen the maintenance reserve,
    and leaving them out is honest rather than broken -- the response says which band it
    fell back to.
    """

    model_config = {"extra": "forbid"}

    # Percent, not a fraction. `dti=22` is unambiguous over HTTP in a way that 0.22 is
    # not: a caller who means 22 and sends 22 should not silently get a 2200% ceiling.
    dti_pct: float | None = Field(default=None, gt=0, le=100)
    sqft: float | None = Field(default=None, gt=0, le=50_000)
    year_built: int | None = Field(default=None, ge=1700, le=2100)
    hoa_monthly: float = Field(default=0.0, ge=0, le=10_000)
    owner_occupied: bool = True


class AnalysisResponse(BaseModel):
    """The full document plus what went wrong getting it.

    `degraded_sources` is promoted to the top level rather than buried in the document
    because it is the field a caller needs to branch on. A client rendering this should
    be able to decide whether to show a warning banner without walking the provenance
    tree.
    """

    document: dict[str, Any]
    degraded_sources: list[str]
    complete: bool
    stations_run: list[str]
    elapsed_seconds: float
    engine_version: str


# =============================================================================
# App
# =============================================================================


def create_app() -> FastAPI:
    app = FastAPI(
        title="Spartanburg Home Buying Analyzer",
        version=ENGINE_VERSION,
        description=(
            "Deterministic property scoring over public data sources. "
            "Same engine as the CLI and the batch runner — this is only an HTTP door."
        ),
    )

    # -- errors ---------------------------------------------------------------

    @app.exception_handler(PipelineAborted)
    def geocoding_failed(request: Request, exc: PipelineAborted) -> JSONResponse:
        """422, not 500. The service worked; the address did not resolve.

        A 500 would tell a caller to retry, and retrying an address that does not
        exist will fail identically every time. 422 tells them to fix the input.
        """
        return JSONResponse(
            status_code=422,
            content={
                "error": "could_not_locate_address",
                "detail": str(exc),
                "hint": (
                    "Include the street number, city, and ZIP. The Census geocoder "
                    "matches addresses, not place names or intersections."
                ),
            },
        )

    # -- operational ----------------------------------------------------------

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, Any]:
        """Liveness only. Deliberately does not call any external source.

        A health check that reaches out to the county GIS server would report the
        container as unhealthy whenever the county has a bad afternoon, and a
        restarting container fixes nothing about someone else's server. External
        source status belongs in /sources, which is a diagnostic, not a probe.
        """
        return {"status": "ok", "engine_version": ENGINE_VERSION}

    @app.get("/profile", tags=["ops"])
    def read_profile() -> dict[str, Any]:
        """The rulebook every score in this service was produced against.

        Exposed because a score is meaningless without it. "52 out of 100" only means
        something once you can see that a 2-car garage was wanted and 74 is the TAKE
        floor. This is also what makes the tool re-usable for a different buyer.
        """
        p = profile()
        return {
            "name": p.name,
            "verdict_bands": {
                "take_min": p.verdict_take_min,
                "watch_min": p.verdict_watch_min,
                "unevaluated_score": p.unevaluated_score,
            },
            "preferences": {
                "min_beds": p.min_beds,
                "min_baths": p.min_baths,
                "min_sqft": p.min_sqft,
                "min_garage_spaces": p.min_garage_spaces,
                "max_hoa_monthly": p.max_hoa_monthly,
                "require_fiber": p.require_fiber,
            },
            "penalties": p.penalties,
            "engine_version": ENGINE_VERSION,
        }

    @app.get("/sources", tags=["ops"])
    def sources() -> dict[str, Any]:
        """Which stations exist, what each one provides, and which are fatal.

        This is the honest-limitations endpoint. Two of the seven sources do not work as
        originally designed — the authoritative county server has a broken certificate
        chain and the FCC map needs a key — and a caller deserves to see that before
        trusting a number, not after.
        """
        from analyzer.pipeline import build_stations

        return {
            "mnemonic": (
                "GAFRCBS — Geocode, Assess, FEMA flood, Risk index, Commute, "
                "Broadband, Score"
            ),
            "stations": [
                {"name": s.name, "provides": list(s.provides), "fatal": s.fatal}
                for s in build_stations(profile(), {})
            ],
            "known_limitations": "see docs/KNOWN_LIMITATIONS.md",
        }

    # -- analysis -------------------------------------------------------------

    @app.post("/analyze", response_model=AnalysisResponse, tags=["analysis"])
    def analyze_property(
        payload: Annotated[PropertyRequest, Body()],
    ) -> AnalysisResponse:
        """Score one property. The vertical slice, over HTTP.

        Synchronous `def` on purpose — see the module docstring.
        """
        started = time.monotonic()
        result = run(
            payload.address,
            payload.price,
            profile=profile(),
            hoa_monthly=payload.hoa_monthly,
            roof_age_years=payload.roof_age_years,
            hvac_age_years=payload.hvac_age_years,
            garage_spaces=payload.garage_spaces,
        )
        return AnalysisResponse(
            document=result.document,
            degraded_sources=result.degraded_stations,
            complete=result.complete,
            stations_run=result.stations_run,
            elapsed_seconds=round(time.monotonic() - started, 2),
            engine_version=ENGINE_VERSION,
        )

    @app.post("/max-price", tags=["analysis"])
    def max_price(payload: Annotated[MaxPriceRequest, Body()]) -> dict[str, Any]:
        """Invert the affordability question: how much house does a DTI ceiling buy.

        No address, no network, no stations. This is pure arithmetic over the profile, so
        it answers instantly and works with every source offline -- which is exactly when
        somebody browsing listings wants it.
        """
        solution = solve_max_price(
            profile(),
            dti_ceiling=None if payload.dti_pct is None else payload.dti_pct / 100.0,
            sqft=payload.sqft,
            year_built=payload.year_built,
            hoa_monthly=payload.hoa_monthly,
            current_year=datetime.now().year,
            owner_occupied=payload.owner_occupied,
        )
        return {
            "max_price": solution.to_dict(),
            "engine_version": ENGINE_VERSION,
            "assumptions": {
                "down_payment": profile().down_payment,
                "mortgage_rate": profile().mortgage_rate,
                "loan_term_months": profile().loan_term_months,
                "annual_insurance": profile().annual_insurance,
                "gross_annual_income": profile().gross_annual_income,
            },
            # Spelled out because the down payment is fixed while the price moves, which
            # means a higher solved price is a lower down-payment percentage.
            "note": (
                "The down payment is held fixed from the profile, so a higher solved "
                "price means a smaller percentage down. Check the notes for a mortgage "
                "insurance warning."
            ),
        }

    @app.post("/shortlist", tags=["analysis"])
    def analyze_shortlist(
        file: UploadFile,
        dry_run: Annotated[
            bool,
            Query(description="Validate the CSV and return what would be analysed, no requests made."),
        ] = False,
        fmt: Annotated[Literal["json", "csv", "markdown"], Query(alias="format")] = "json",
    ) -> Any:
        """Score a whole shortlist from an uploaded CSV.

        `dry_run` matters more over HTTP than it does on the command line. A ten-row
        shortlist is around a minute of sequential requests to free public servers, and
        discovering a misnamed column on row nine of that is a waste of someone else's
        bandwidth as well as your time. Validation is offline and instant.
        """
        raw = file.file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="uploaded file is empty")
        if len(raw) > 2_000_000:
            # A house shortlist is kilobytes. Anything this large is a mistake or an
            # attempt to make the container do a lot of unpaid work.
            raise HTTPException(status_code=413, detail="file too large for a shortlist")

        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400, detail="file must be UTF-8 encoded CSV"
            ) from None

        try:
            rows, rejected, unknown_headers = batch.parse_shortlist_text(
                text, source=file.filename or "uploaded file"
            )
        except ValueError as exc:
            # A missing required column is the caller's mistake, not a server fault.
            raise HTTPException(status_code=422, detail=str(exc)) from None

        if dry_run:
            return {
                "dry_run": True,
                "would_analyse": [
                    {"line": r.line, "address": r.address, "price": r.price} for r in rows
                ],
                "rejected": [
                    {"line": r.line, "problem": r.problem} for r in rejected
                ],
                "unknown_headers": unknown_headers,
            }

        if not rows:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"no usable rows: {len(rejected)} rejected. "
                    f"Call with ?dry_run=true to see why."
                ),
            )

        entries = batch.analyse_shortlist(rows, profile=profile())
        result = batch.BatchResult(
            entries=entries,
            rejected=rejected,
            unknown_headers=unknown_headers,
            profile_name=profile().name,
        )

        if fmt == "csv":
            buffer = io.StringIO()
            batch.write_summary_stream(result, buffer)
            return PlainTextResponse(buffer.getvalue(), media_type="text/csv")
        if fmt == "markdown":
            return PlainTextResponse(
                batch.render_markdown(result), media_type="text/markdown"
            )

        return {
            "profile": result.profile_name,
            "engine_version": ENGINE_VERSION,
            "counts": {
                "scored": len(result.scored),
                "errors": len(result.entries) - len(result.scored),
                "rejected": len(result.rejected),
                "take": len(result.by_verdict("TAKE")),
                "watch": len(result.by_verdict("WATCH")),
                "pass": len(result.by_verdict("PASS")),
            },
            "unknown_headers": result.unknown_headers,
            "rejected": [{"line": r.line, "problem": r.problem} for r in result.rejected],
            "ranked": batch.summary_rows(result),
            "documents": [
                {"address": e.row.address, "document": e.document}
                for e in result.ranked
                if e.ok
            ],
        }

    return app


app = create_app()

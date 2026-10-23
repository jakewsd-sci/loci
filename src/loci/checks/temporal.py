"""Temporal coverage: gaps, uneven sampling and repeated timestamps.

Steps are judged against the median step, so the check works for daily, 5-day or monthly series
without being told the expected interval.
"""

from typing import Any

import numpy as np
import pandas as pd

from loci.checks.core import Layer, result
from loci.models import CheckResult
from loci.summary.common import counted, iso, sig

GAP_FACTOR = 1.5
SHORT_FACTOR = 0.5
MAX_GAPS_LISTED = 3


def check_temporal_coverage(layer: Layer, times: Any) -> CheckResult:
    """warn on gaps (steps > 1.5x the median), short steps (< 0.5x) and repeated timestamps."""
    stamps = pd.DatetimeIndex(pd.to_datetime(np.asarray(times))).dropna().sort_values()
    if len(stamps) == 0:
        return result(
            "temporal_coverage",
            "warn",
            [layer],
            "No timestamps to assess.",
            hint="Check the time values.",
        )
    repeated = int(stamps.duplicated().sum())
    unique = stamps.unique()
    if len(unique) == 1:
        return result(
            "temporal_coverage",
            "pass" if not repeated else "warn",
            [layer],
            f"All timestamps fall on {iso(unique[0])}; there is no sampling to assess.",
            details={"steps": 1, "duplicates": repeated},
            hint="Check whether several dates were expected.",
        )

    # Via timedelta64[s]: pandas 3 may hold timestamps in us or ns, so raw integers mislead.
    days = np.diff(unique.to_numpy()).astype("timedelta64[s]").astype(float) / 86_400
    median = float(np.median(days))
    gap_steps = np.flatnonzero(days > GAP_FACTOR * median)
    short = int((days < SHORT_FACTOR * median).sum())
    largest_first = sorted(gap_steps, key=lambda i: -days[i])
    # Dates read better than timestamps once steps are a day or more apart.
    stamp = iso if median < 1 else (lambda t: pd.Timestamp(t).date().isoformat())
    gaps = [
        {"from": stamp(unique[i]), "to": stamp(unique[i + 1]), "days": sig(days[i])}
        for i in largest_first[:MAX_GAPS_LISTED]
    ]
    details = {
        "steps": len(unique),
        "start": iso(unique[0]),
        "end": iso(unique[-1]),
        "median_step_days": sig(median),
        "gaps": gaps,
        "short_steps": short,
        "duplicates": repeated,
    }

    findings = []
    if gaps:
        top = gaps[0]
        findings.append(
            f"{counted(len(gap_steps), 'gap', 'gaps')} longer than {GAP_FACTOR:g}x the median "
            f"step of {median:.3g} days (largest: {top['from']} to {top['to']}, "
            f"{days[largest_first[0]]:.3g} days)."
        )
    if short:
        findings.append(f"{counted(short, 'step is', 'steps are')} under half the median step.")
    if repeated:
        findings.append(f"{counted(repeated, 'timestamp is', 'timestamps are')} repeated.")
    if findings:
        return result(
            "temporal_coverage",
            "warn",
            [layer],
            " ".join(findings),
            details=details,
            hint="Gaps and uneven sampling bias composites and trends. Say how they are handled, "
            "for example by masking, interpolating or reporting the gap.",
        )
    return result(
        "temporal_coverage",
        "pass",
        [layer],
        f"{len(unique)} time steps from {stamp(unique[0])} to {stamp(unique[-1])}, evenly "
        f"spaced (median step {median:.3g} days).",
        details=details,
    )

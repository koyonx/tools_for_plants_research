#!/usr/bin/env python3
"""Compare basic_measurement output against the legacy `measure_results.xlsx`.

Reads the spreadsheet, looks up each filename in Supabase by
`original_filename`, kicks off the backend's `POST /images/{id}/analyze`
basic-measurement endpoint, then computes per-row deltas and overall
stats.  Writes a Markdown report to `outputs/validation_report.md`.

Usage
-----
    # 1. start the stack
    docker compose up -d

    # 2. upload the images that appear in the xlsx via the web UI

    # 3. run the validator (host-side; reads .env for Supabase keys)
    python scripts/validate_against_xlsx.py \
        --xlsx ../measure_results.xlsx \
        --reference-um 100 \
        --user-email you@example.com

The script needs a logged-in user's access token to reach RLS-gated
endpoints.  Easiest path: the script signs in via password grant against
the locally-running GoTrue using the email/password you'd use through
the UI.  For pure CI / unattended runs, point `--service-role` at the
service_role key instead — it bypasses RLS but still uses the same
endpoints.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from openpyxl import load_workbook

# Heuristic mapping from the xlsx `メモ` column to backend metric keys.
METRIC_MAP = {
    "厚さ": "leaf_mean_thickness_um",        # full leaf thickness
    "葉肉の厚さ": "leaf_median_thickness_um",  # mesophyll-only ≈ central thickness
}
AREA_METRIC = "leaf_area_um2"
AREA_NOTE = "維管束面積"  # not directly available in basic_measurement


def _parse_value(raw: str | float | int | None) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, int | float):
        return float(raw)
    text = str(raw).strip().replace(",", "")
    for suffix in (" um2", " um²", " µm²", " um", " µm"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    try:
        return float(text)
    except ValueError:
        return None


def _read_groundtruth(xlsx_path: Path) -> list[dict[str, Any]]:
    wb = load_workbook(xlsx_path, data_only=True)
    ws = wb["All_Data"] if "All_Data" in wb.sheetnames else wb.active
    rows: list[dict[str, Any]] = []
    headers = None
    for raw in ws.iter_rows(values_only=True):
        if headers is None:
            headers = [str(h) if h is not None else "" for h in raw]
            continue
        record = dict(zip(headers, raw, strict=False))
        rows.append(record)
    return rows


def _signin_password(supabase_url: str, anon_key: str, email: str, password: str) -> str:
    resp = httpx.post(
        f"{supabase_url}/auth/v1/token?grant_type=password",
        headers={"apikey": anon_key, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=30,
    )
    resp.raise_for_status()
    return str(resp.json()["access_token"])


def _find_image(supabase_url: str, anon_key: str, jwt: str, filename: str) -> dict[str, Any] | None:
    resp = httpx.get(
        f"{supabase_url}/rest/v1/images",
        params={"original_filename": f"eq.{filename}", "select": "*", "limit": "1"},
        headers={"apikey": anon_key, "Authorization": f"Bearer {jwt}"},
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


def _run_basic_measurement(
    backend_url: str, jwt: str, image_id: str, reference_um: float
) -> dict[str, Any]:
    resp = httpx.post(
        f"{backend_url}/images/{image_id}/analyze",
        headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
        json={"reference_um": reference_um},
        timeout=180,
    )
    resp.raise_for_status()
    body: dict[str, Any] = resp.json()
    return body


def _format_delta(measured: float | None, expected: float | None) -> str:
    if measured is None or expected is None:
        return "—"
    delta = measured - expected
    pct = 100.0 * delta / expected if expected else 0.0
    return f"{delta:+.2f} ({pct:+.1f}%)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--xlsx", required=True, type=Path, help="path to measure_results.xlsx")
    parser.add_argument("--reference-um", type=float, default=100.0, help="scale-bar length in µm")
    parser.add_argument("--out", type=Path, default=Path("outputs/validation_report.md"))
    parser.add_argument(
        "--supabase-url",
        default=os.environ.get("SUPABASE_PUBLIC_URL") or "http://localhost:8000",
        help="Supabase Kong URL (defaults to SUPABASE_PUBLIC_URL or localhost:8000)",
    )
    parser.add_argument(
        "--anon-key",
        default=os.environ.get("ANON_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY") or "",
        help="Supabase anon JWT (defaults to env)",
    )
    parser.add_argument(
        "--backend-url",
        default=os.environ.get("NEXT_PUBLIC_BACKEND_URL") or "http://localhost:8001",
        help="FastAPI backend URL",
    )
    auth_group = parser.add_mutually_exclusive_group(required=True)
    auth_group.add_argument(
        "--user-email",
        help="Email to sign in via password grant (prompts for password)",
    )
    auth_group.add_argument(
        "--service-role",
        action="store_true",
        help="Use SERVICE_ROLE_KEY env var instead of a user grant (bypasses RLS)",
    )
    args = parser.parse_args()

    if not args.anon_key:
        parser.error("anon key not provided (set ANON_KEY env or pass --anon-key)")

    if args.service_role:
        jwt = os.environ.get("SERVICE_ROLE_KEY", "")
        if not jwt:
            parser.error("--service-role requires SERVICE_ROLE_KEY in env")
    else:
        import getpass

        password = getpass.getpass(f"password for {args.user_email}: ")
        jwt = _signin_password(args.supabase_url, args.anon_key, args.user_email, password)

    rows = _read_groundtruth(args.xlsx)
    print(f"loaded {len(rows)} ground-truth rows from {args.xlsx}", file=sys.stderr)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        fname = r.get("ファイル名")
        if not fname:
            continue
        grouped.setdefault(str(fname), []).append(r)

    output_lines: list[str] = []
    output_lines.append("# Validation report\n")
    output_lines.append(f"- xlsx: `{args.xlsx}`")
    output_lines.append(f"- reference_um: {args.reference_um}")
    output_lines.append(f"- generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    summary_rows: list[tuple[str, str, float | None, float | None, str]] = []

    for filename, gt_rows in grouped.items():
        output_lines.append(f"## {filename}\n")
        try:
            image = _find_image(args.supabase_url, args.anon_key, jwt, filename)
        except httpx.HTTPError as e:
            output_lines.append(f"- ERROR: image lookup failed: {e}\n")
            continue
        if image is None:
            output_lines.append(f"- ERROR: image `{filename}` not found in `images` table; upload it first\n")
            continue

        try:
            analysis = _run_basic_measurement(
                args.backend_url, jwt, image["id"], args.reference_um
            )
        except httpx.HTTPError as e:
            output_lines.append(f"- ERROR: basic_measurement failed: {e}\n")
            continue

        result = analysis.get("result") or {}
        measurement = result.get("measurement") or {}
        scale = result.get("scale") or {}
        output_lines.append(f"- µm/px detected: `{scale.get('um_per_px', 'n/a')}`")
        output_lines.append(f"- analysis_id: `{analysis.get('id')}`\n")

        output_lines.append("| メモ | 期待値 (µm/µm²) | 実測 (µm/µm²) | Δ (差) |")
        output_lines.append("| --- | ---: | ---: | ---: |")
        for gt in gt_rows:
            note = str(gt.get("メモ", "")).strip()
            mtype = str(gt.get("測定タイプ", "")).strip()
            expected = _parse_value(gt.get("測定値"))
            measured: float | None = None
            if note in METRIC_MAP:
                measured = measurement.get(METRIC_MAP[note])
            elif note == AREA_NOTE and mtype == "Area":
                # Vascular bundle area isn't measured by basic_measurement;
                # would need SegFormer + xylem polygon area instead.  Mark
                # as N/A for now so users see the gap.
                measured = None
            elif mtype == "Area":
                measured = measurement.get(AREA_METRIC)
            output_lines.append(
                f"| {note} | {expected if expected is not None else '—'} "
                f"| {f'{measured:.2f}' if measured is not None else '—'} "
                f"| {_format_delta(measured, expected)} |"
            )
            summary_rows.append((filename, note, measured, expected, _format_delta(measured, expected)))
        output_lines.append("")

    # Aggregate stats
    output_lines.append("## Aggregate\n")
    parsed = [
        (m, e) for _, _, m, e, _ in summary_rows if m is not None and e is not None and e != 0
    ]
    if not parsed:
        output_lines.append("- no rows with both expected + measured values\n")
    else:
        rel_errors = [abs(m - e) / e for m, e in parsed]
        mae = sum(abs(m - e) for m, e in parsed) / len(parsed)
        output_lines.append(f"- compared rows: {len(parsed)}")
        output_lines.append(f"- mean absolute error: {mae:.2f}")
        output_lines.append(f"- mean relative error: {100 * sum(rel_errors) / len(parsed):.1f} %")
        output_lines.append(f"- max relative error: {100 * max(rel_errors):.1f} %\n")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(output_lines), encoding="utf-8")
    # Also dump the raw structured data so downstream tooling can re-process.
    args.out.with_suffix(".json").write_text(
        json.dumps(
            [
                {"filename": f, "note": n, "measured": m, "expected": e}
                for f, n, m, e, _ in summary_rows
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {args.out}", file=sys.stderr)
    print(f"wrote {args.out.with_suffix('.json')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

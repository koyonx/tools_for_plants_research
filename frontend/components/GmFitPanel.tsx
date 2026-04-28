"use client";

import { ValidationBadge } from "@/components/ValidationBadge";
import { errorMessage } from "@/lib/error-message";
import { createClient } from "@/lib/supabase/client";
import type {
  GasExchangePointRow,
  GmFitResult,
  GmFitRow,
  GmMethodResult,
} from "@/lib/supabase/types";
import { useCallback, useEffect, useMemo, useState } from "react";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8001";

type Props = {
  sessionId: string;
  points: GasExchangePointRow[];
  canRun: boolean;
};

function fmtSci(n: number | null, digits = 3): string {
  if (n === null || !Number.isFinite(n)) return "—";
  return n.toExponential(digits);
}

function fmt(n: number | null, digits = 2): string {
  if (n === null || !Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

const METHOD_LABELS: Record<GmMethodResult["method"], string> = {
  harley_variable_j: "Harley variable-J",
  ethier_livingston: "Ethier-Livingston",
  nonlinear_slope: "非線形全曲線フィット",
};

export function GmFitPanel({ sessionId, points, canRun }: Props) {
  const supabase = useMemo(() => createClient(), []);
  const [latestFit, setLatestFit] = useState<GmFitRow | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tleafInput, setTleafInput] = useState<string>("");
  const [rdInput, setRdInput] = useState<string>("");

  const loadLatest = useCallback(async () => {
    try {
      const { data } = await supabase
        .from("gm_fits")
        .select("*")
        .eq("session_id", sessionId)
        .order("created_at", { ascending: false })
        .limit(1)
        .maybeSingle<GmFitRow>();
      setLatestFit(data ?? null);
    } catch {
      // fall through; UI still shows the run button
    }
  }, [supabase, sessionId]);

  useEffect(() => {
    void loadLatest();
  }, [loadLatest]);

  const onRun = async () => {
    setRunning(true);
    setError(null);
    try {
      const { data: sess } = await supabase.auth.getSession();
      if (!sess.session) throw new Error("セッションが切れました");
      const body: Record<string, number | null> = {
        bootstrap_iters: 500,
        o2_mmol_mol: 210,
      };
      if (tleafInput.trim()) body.tleaf_c = Number(tleafInput);
      if (rdInput.trim()) body.rd = Number(rdInput);
      const resp = await fetch(`${BACKEND_URL}/gas-exchange/sessions/${sessionId}/gm-fit`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${sess.session.access_token}`,
        },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const detail = await resp.text().catch(() => "");
        throw new Error(`${resp.status}: ${detail.slice(0, 320)}`);
      }
      await loadLatest();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setRunning(false);
    }
  };

  const fit: GmFitResult | null = latestFit?.result ?? null;

  return (
    <section className="space-y-3 rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-base font-semibold">g_m フィット (Farquhar A-Cc)</h3>
        {canRun && (
          <button
            type="button"
            onClick={onRun}
            disabled={running || points.length < 4}
            className="rounded bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900"
          >
            {running ? "計算中…" : "3 手法で g_m を推定"}
          </button>
        )}
        <span className="text-xs text-neutral-500">
          Harley (variable-J) · Ethier-Livingston · 非線形全曲線フィット
        </span>
      </div>

      {canRun && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block text-xs text-neutral-500">
            <span className="mb-1 block">Tleaf (℃) — 空欄で LI-COR 値の中央値を使用</span>
            <input
              type="text"
              value={tleafInput}
              onChange={(e) => setTleafInput(e.target.value)}
              placeholder="例: 25.0"
              className="w-full rounded border border-neutral-300 bg-white px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900"
            />
          </label>
          <label className="block text-xs text-neutral-500">
            <span className="mb-1 block">Rd (µmol/m²/s) — 空欄で自動フィット</span>
            <input
              type="text"
              value={rdInput}
              onChange={(e) => setRdInput(e.target.value)}
              placeholder="例: 1.5"
              className="w-full rounded border border-neutral-300 bg-white px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900"
            />
          </label>
        </div>
      )}

      {error && (
        <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </p>
      )}

      {fit && (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-xs text-neutral-500">
              Tleaf: {fmt(fit.tleaf_c, 1)} ℃ · 使用点数: {fit.input_point_count} · O₂:{" "}
              {fmt(fit.o2_mmol_mol, 0)} mmol/mol
            </p>
            <ValidationBadge target={{ kind: "session", sessionId }} refreshKey={latestFit?.id} />
          </div>
          {fit.notes.length > 0 && (
            <ul className="list-disc space-y-1 pl-5 text-xs text-neutral-500">
              {fit.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs">
              <thead className="bg-neutral-100 text-neutral-500 dark:bg-neutral-900">
                <tr>
                  <th className="px-2 py-1 text-left">手法</th>
                  <th className="px-2 py-1 text-right">g_m</th>
                  <th className="px-2 py-1 text-right">95% CI</th>
                  <th className="px-2 py-1 text-right">Vcmax</th>
                  <th className="px-2 py-1 text-right">J_max</th>
                  <th className="px-2 py-1 text-right">Rd</th>
                  <th className="px-2 py-1 text-right">RMSE</th>
                  <th className="px-2 py-1 text-right">n</th>
                  <th className="px-2 py-1 text-left">備考</th>
                </tr>
              </thead>
              <tbody>
                {fit.methods.map((m) => (
                  <tr
                    key={m.method}
                    className="border-b border-neutral-200 dark:border-neutral-800"
                  >
                    <td className="px-2 py-1">{METHOD_LABELS[m.method]}</td>
                    <td className="px-2 py-1 text-right font-mono">{fmtSci(m.g_m, 3)}</td>
                    <td className="px-2 py-1 text-right font-mono">
                      {m.g_m_ci_low !== null && m.g_m_ci_high !== null
                        ? `[${m.g_m_ci_low.toExponential(2)}, ${m.g_m_ci_high.toExponential(2)}]`
                        : "—"}
                    </td>
                    <td className="px-2 py-1 text-right font-mono">{fmt(m.vcmax, 1)}</td>
                    <td className="px-2 py-1 text-right font-mono">{fmt(m.j_max, 1)}</td>
                    <td className="px-2 py-1 text-right font-mono">{fmt(m.rd, 2)}</td>
                    <td className="px-2 py-1 text-right font-mono">{fmt(m.rmse, 2)}</td>
                    <td className="px-2 py-1 text-right font-mono">{m.n_points_used}</td>
                    <td className="px-2 py-1 text-xs text-neutral-500">{m.notes.join("; ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ACiCurveWithFit points={points} fit={fit} />
        </>
      )}

      {!fit && !error && (
        <p className="text-xs text-neutral-500">
          このセッションではまだ g_m フィットを実行していません。
        </p>
      )}
    </section>
  );
}

function ACiCurveWithFit({
  points,
  fit,
}: {
  points: GasExchangePointRow[];
  fit: GmFitResult;
}) {
  // Valid measured (Ci, A) pairs.
  const pts = points
    .filter(
      (p) =>
        p.ci_ppm !== null &&
        p.photo_a !== null &&
        Number.isFinite(p.ci_ppm) &&
        Number.isFinite(p.photo_a),
    )
    .map((p) => ({ ci: p.ci_ppm as number, a: p.photo_a as number }));
  if (pts.length === 0) return null;

  const xs = pts.map((p) => p.ci);
  const ys = pts.map((p) => p.a);
  const xmin = Math.min(...xs);
  const xmax = Math.max(...xs);
  const ymin = Math.min(...ys, 0);
  const ymax = Math.max(...ys);
  const w = 520;
  const h = 280;
  const pad = 36;
  const sx = (x: number) => pad + ((x - xmin) / Math.max(xmax - xmin, 1e-6)) * (w - 2 * pad);
  const sy = (y: number) => h - pad - ((y - ymin) / Math.max(ymax - ymin, 1e-6)) * (h - 2 * pad);

  // Build one fitted curve per method that successfully fit Vcmax / J.
  // g_m alone isn't enough to draw a curve — we need all Farquhar
  // parameters.  Ethier gives Vcmax (no J); nonlinear gives all.
  const curves: { label: string; color: string; pts: { ci: number; a: number }[] }[] = [];
  const tleaf = fit.tleaf_c;
  const o2 = fit.o2_mmol_mol;
  const ciRange = Array.from({ length: 40 }, (_, i) => xmin + (i * (xmax - xmin)) / 39);
  for (const m of fit.methods) {
    if (m.g_m === null || m.vcmax === null) continue;
    const j = m.j_max ?? 1e6;
    const rd = m.rd ?? 1.5;
    const color =
      m.method === "harley_variable_j"
        ? "rgba(34, 197, 94, 0.85)"
        : m.method === "ethier_livingston"
          ? "rgba(234, 179, 8, 0.85)"
          : "rgba(56, 189, 248, 0.85)";
    // Evaluate Farquhar A(Ci) on the client with iterative Cc solve.
    const kin = bernacchiKinetics(tleaf);
    const curvePts: { ci: number; a: number }[] = [];
    for (const ci of ciRange) {
      let cc = ci;
      let aNet = 0;
      for (let iter = 0; iter < 20; iter++) {
        const ac = (m.vcmax * (cc - kin.gammaStar)) / (cc + kin.kc * (1 + o2 / kin.ko));
        const aj = (j * (cc - kin.gammaStar)) / (4 * cc + 8 * kin.gammaStar);
        const candidate = Math.min(ac, aj) - rd;
        const ccNew = Math.max(kin.gammaStar * 0.5, Math.min(ci, ci - candidate / m.g_m));
        if (Math.abs(candidate - aNet) < 1e-4) break;
        aNet = candidate;
        cc = ccNew;
      }
      curvePts.push({ ci, a: aNet });
    }
    curves.push({ label: METHOD_LABELS[m.method], color, pts: curvePts });
  }

  return (
    <div className="overflow-x-auto">
      <svg
        width={w}
        height={h}
        className="rounded border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-950"
        aria-label="A-Ci curve with Farquhar fit"
      >
        <title>A-Ci ({pts.length} measured points) + Farquhar fitted curves per method</title>
        <text x={pad} y={16} className="fill-neutral-500" fontSize="11">
          A (µmol m⁻² s⁻¹)
        </text>
        <text x={w - pad} y={h - 8} textAnchor="end" className="fill-neutral-500" fontSize="11">
          Ci (µmol mol⁻¹)
        </text>
        <line x1={pad} y1={h - pad} x2={w - pad} y2={h - pad} stroke="#888" strokeWidth={1} />
        <line x1={pad} y1={pad} x2={pad} y2={h - pad} stroke="#888" strokeWidth={1} />
        {/* Fitted curves */}
        {curves.map((c) => (
          <polyline
            key={c.label}
            points={c.pts.map((p) => `${sx(p.ci)},${sy(p.a)}`).join(" ")}
            fill="none"
            stroke={c.color}
            strokeWidth={1.5}
          />
        ))}
        {/* Measured points */}
        {pts.map((p) => (
          <circle
            key={`${p.ci}-${p.a}`}
            cx={sx(p.ci)}
            cy={sy(p.a)}
            r={3}
            fill="rgba(236, 72, 153, 0.85)"
          />
        ))}
        {/* Legend */}
        {curves.map((c, i) => (
          <g key={`legend-${c.label}`} transform={`translate(${w - pad - 180}, ${28 + i * 16})`}>
            <line x1={0} y1={0} x2={20} y2={0} stroke={c.color} strokeWidth={2} />
            <text x={24} y={4} className="fill-neutral-700 dark:fill-neutral-300" fontSize="11">
              {c.label}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

/**
 * Bernacchi 2001 kinetic constants at the requested Tleaf (C).
 * Mirrored from the backend's `kinetics_at` so the client can draw
 * the fitted curve without another round-trip to the server.
 */
function bernacchiKinetics(tleafC: number): {
  kc: number;
  ko: number;
  gammaStar: number;
} {
  const R = 8.314462618;
  const Tref = 298.15;
  const T = tleafC + 273.15;
  const arr = (k25: number, ea: number) => k25 * Math.exp((ea / R) * (1 / Tref - 1 / T));
  return {
    kc: arr(404.9, 79430),
    ko: arr(278.4, 36380),
    gammaStar: arr(42.75, 37830),
  };
}

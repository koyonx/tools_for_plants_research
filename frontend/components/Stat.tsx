// One label/value pair for analysis result panels.  Wrapping each pair in
// a single grid cell (instead of letting `<dt>`/`<dd>` occupy adjacent
// cells of a multi-column grid) keeps the label and value visually paired
// when the row holds 2 or 3 metrics.
export function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs uppercase tracking-wide text-neutral-500">{label}</dt>
      <dd className="font-mono text-sm">{value}</dd>
    </div>
  );
}

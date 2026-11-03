import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import type { components } from "../api/schema";

type ChangeStats = components["schemas"]["ChangeStats"];

const fmt = (n: number) => n.toLocaleString("en-GB", { maximumFractionDigits: 2, minimumFractionDigits: 2 });

type SortDir = "ascending" | "descending";
interface Column<R> {
  key: string;
  header: string;
  numeric?: boolean;
  value: (r: R) => string | number;
  render?: (r: R) => string;
}

function SortableTable<R>({
  caption,
  columns,
  rows,
  rowKey,
  initial,
}: {
  caption: string;
  columns: Column<R>[];
  rows: R[];
  rowKey: (r: R) => string;
  initial: { key: string; dir: SortDir };
}) {
  const [sort, setSort] = useState(initial);
  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === sort.key) ?? columns[0];
    const sign = sort.dir === "ascending" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const x = col.value(a);
      const y = col.value(b);
      const r = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
      return r * sign;
    });
  }, [rows, columns, sort]);

  const toggle = (key: string) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "ascending" ? "descending" : "ascending" } : { key, dir: "ascending" }));

  return (
    <table>
      <caption>{caption}</caption>
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c.key} scope="col" aria-sort={sort.key === c.key ? sort.dir : "none"} style={{ textAlign: c.numeric ? "right" : "left" }}>
              <button type="button" onClick={() => toggle(c.key)}>
                {c.header}
                <span aria-hidden="true">{sort.key === c.key ? (sort.dir === "ascending" ? " ▲" : " ▼") : ""}</span>
              </button>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sorted.map((r) => (
          <tr key={rowKey(r)}>
            {columns.map((c) => (
              <td key={c.key} style={{ textAlign: c.numeric ? "right" : "left" }}>
                {c.render ? c.render(r) : String(c.value(r))}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

type Transition = ChangeStats["transitions"][number];
type ClassArea = ChangeStats["from_class_areas"][number];

const transitionLabel = (t: Transition) => `${t.from_class.label} to ${t.to_class.label}`;

const transitionColumns: Column<Transition>[] = [
  { key: "from", header: "From class", value: (t) => t.from_class.label },
  { key: "to", header: "To class", value: (t) => t.to_class.label },
  { key: "ha", header: "Area (ha)", numeric: true, value: (t) => t.area_ha, render: (t) => fmt(t.area_ha) },
  { key: "pct", header: "Percent of site", numeric: true, value: (t) => t.percent, render: (t) => `${fmt(t.percent)}%` },
];

const classColumns = (header: string): Column<ClassArea>[] => [
  { key: "class", header, value: (a) => a.class.label },
  { key: "ha", header: "Area (ha)", numeric: true, value: (a) => a.area_ha, render: (a) => fmt(a.area_ha) },
  { key: "pct", header: "Percent of site", numeric: true, value: (a) => a.percent, render: (a) => `${fmt(a.percent)}%` },
];

const fromColumns = classColumns("Class (from)");
const toColumns = classColumns("Class (to)");

/** Statistics for one change: tables, bar chart and CSV download. */
export function StatsPanel({ stats }: { stats: ChangeStats }) {
  const [csvError, setCsvError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const changeId = stats.change.id;

  const downloadCsv = async () => {
    setCsvError(null);
    setBusy(true);
    try {
      const r = await api.GET("/changes/{change_id}/stats", {
        params: { path: { change_id: changeId } },
        headers: { Accept: "text/csv" },
        parseAs: "text",
      });
      const text = r.data as unknown;
      if (typeof text !== "string" || !r.response.ok) {
        setCsvError(r.error?.message ?? "Could not download the CSV.");
        return;
      }
      const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `peatwatch-change-${changeId}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      setCsvError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  };

  const hasData = stats.transitions.length > 0 && stats.total_ha > 0;
  const changed = stats.transitions.filter((t) => t.from_class.id !== t.to_class.id);
  const chartData = [...stats.transitions]
    .sort((a, b) => b.area_ha - a.area_ha)
    .map((t) => ({ name: transitionLabel(t), ha: t.area_ha, hex: t.to_class.hex }));

  return (
    <section aria-label="Statistics">
      <h3>Statistics</h3>
      {!hasData ? (
        <p role="status">No change statistics: no valid area was compared.</p>
      ) : (
        <>
          <p>
            {fmt(stats.total_ha)} ha compared.{" "}
            {changed.length === 0
              ? "No change detected: every area stayed in the same class."
              : `${changed.length} class change${changed.length === 1 ? "" : "s"} detected.`}
          </p>
          <p>
            <button type="button" onClick={downloadCsv} disabled={busy}>
              Download CSV
            </button>
          </p>
          {csvError && <p role="alert">{csvError}</p>}

          <figure style={{ margin: 0 }}>
            <figcaption id="stats-chart-caption">Area by transition (hectares). The transitions table below has the same values.</figcaption>
            <div role="img" aria-labelledby="stats-chart-caption" style={{ width: "100%", height: Math.max(220, chartData.length * 36 + 60) }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} layout="vertical" margin={{ left: 16, right: 16 }}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis type="number" unit=" ha" />
                  <YAxis type="category" dataKey="name" width={200} />
                  <Tooltip formatter={(v) => `${fmt(Number(v))} ha`} />
                  <Bar dataKey="ha" name="Area (ha)" isAnimationActive={false}>
                    {chartData.map((d) => (
                      <Cell key={d.name} fill={d.hex} stroke="#000" strokeWidth={1} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </figure>

          <SortableTable
            caption="Area by transition"
            columns={transitionColumns}
            rows={stats.transitions}
            rowKey={(t) => `${t.from_class.id}-${t.to_class.id}`}
            initial={{ key: "ha", dir: "descending" }}
          />
          <SortableTable
            caption="Area by class, from date"
            columns={fromColumns}
            rows={stats.from_class_areas}
            rowKey={(a) => String(a.class.id)}
            initial={{ key: "ha", dir: "descending" }}
          />
          <SortableTable
            caption="Area by class, to date"
            columns={toColumns}
            rows={stats.to_class_areas}
            rowKey={(a) => String(a.class.id)}
            initial={{ key: "ha", dir: "descending" }}
          />
        </>
      )}
    </section>
  );
}

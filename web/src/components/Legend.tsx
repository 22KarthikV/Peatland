import { CLASSES } from "../lib/classes";

/** Always-visible legend. Colours are the Okabe-Ito classes from contracts/classes.yaml. */
export function Legend() {
  return (
    <section aria-label="Legend" className="legend">
      <h3>Legend</h3>
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {CLASSES.map((c) => (
          <li key={c.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span
              aria-hidden="true"
              data-testid={`swatch-${c.name}`}
              style={{ width: 16, height: 16, background: c.hex, border: "1px solid #000", display: "inline-block" }}
            />
            {c.label}
          </li>
        ))}
      </ul>
    </section>
  );
}

import { useId } from "react";

export interface DateOption {
  id: string;
  label: string;
}

interface Props {
  options: DateOption[];
  value: string | null;
  onChange: (id: string) => void;
}

export function DateSelect({ options, value, onChange }: Props) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id}>Date </label>
      <select id={id} value={value ?? ""} onChange={(e) => onChange(e.target.value)} disabled={options.length === 0}>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

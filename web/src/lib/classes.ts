/**
 * Copy of contracts/classes.yaml (version 1). Okabe-Ito colour-blind-safe palette.
 * Keep in sync by hand when the contract changes.
 */
export interface LandClass {
  id: number;
  name: string;
  label: string;
  hex: string;
}

export const NODATA = 255;

export const CLASSES: readonly LandClass[] = [
  { id: 0, name: "other", label: "Other", hex: "#999999" },
  { id: 1, name: "bog", label: "Bog (peatland)", hex: "#0072B2" },
  { id: 2, name: "woodland", label: "Woodland", hex: "#009E73" },
  { id: 3, name: "felled_or_burnt_bare", label: "Felled or burnt-bare", hex: "#D55E00" },
];

export const classById = (id: number): LandClass | undefined => CLASSES.find((c) => c.id === id);

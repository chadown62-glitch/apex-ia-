import type { Measurement } from "../../api/types";
import { StatusBadge } from "./StatusBadge";

/**
 * Affiche UNE mesure {value, status} — jamais `value` seul. Si `status`
 * n'est pas "real", `value` est ignoré à l'affichage (il vaut `null` côté
 * backend de toute façon) et remplacé par "—", pour qu'un 0 réel ne soit
 * jamais confondu avec une absence de mesure.
 */
export function MeasurementValue({
  measurement,
  format,
  unit,
}: {
  measurement: Measurement<number | string>;
  format?: (value: number | string) => string;
  unit?: string;
}) {
  const display =
    measurement.status === "real" && measurement.value !== null
      ? `${format ? format(measurement.value) : measurement.value}${unit ?? ""}`
      : "—";
  return (
    <span className="measurement-value">
      <span className="measurement-value__number mono">{display}</span>
      <StatusBadge status={measurement.status} />
    </span>
  );
}

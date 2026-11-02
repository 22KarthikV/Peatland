import { useParams } from "react-router-dom";

export function RunStatus() {
  const { runId } = useParams();
  return <h2>Run status {runId}</h2>;
}

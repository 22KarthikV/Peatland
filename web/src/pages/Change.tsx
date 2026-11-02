import { useParams } from "react-router-dom";

export function Change() {
  const { siteId } = useParams();
  return <h2>Change {siteId}</h2>;
}

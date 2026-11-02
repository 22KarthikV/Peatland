import { useParams } from "react-router-dom";

export function SiteMap() {
  const { siteId } = useParams();
  return <h2>Site map {siteId}</h2>;
}

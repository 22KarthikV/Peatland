import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { Change } from "./pages/Change";
import { NewSite } from "./pages/NewSite";
import { RunStatus } from "./pages/RunStatus";
import { SiteMap } from "./pages/SiteMap";
import { Sites } from "./pages/Sites";

export function App() {
  return (
    <>
      <header>
        <h1>PeatWatch</h1>
        <nav aria-label="Main">
          <NavLink to="/sites">Sites</NavLink> <NavLink to="/sites/new">New site</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/sites" replace />} />
          <Route path="/sites" element={<Sites />} />
          <Route path="/sites/new" element={<NewSite />} />
          <Route path="/sites/:siteId" element={<SiteMap />} />
          <Route path="/sites/:siteId/change" element={<Change />} />
          <Route path="/runs/:runId" element={<RunStatus />} />
          <Route path="*" element={<p>Page not found.</p>} />
        </Routes>
      </main>
    </>
  );
}

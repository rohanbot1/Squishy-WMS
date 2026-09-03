import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import WallBuilder from "./pages/WallBuilder";
import PackerScan from "./pages/PackerScan";
import Shipments from "./pages/Shipments";
import Financials from "./pages/Financials";
import FinancialDetail from "./pages/FinancialDetail";
import Login from "./pages/Login";
import { checkAuth, logout } from "./api";

export default function App() {
  const [isAdmin, setIsAdmin] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    checkAuth().then(setIsAdmin);
  }, []);

  async function handleLogout() {
    await logout();
    setIsAdmin(false);
    navigate("/");
  }

  return (
    <div className="app-shell">
      <nav className="top-nav">
        <span className="brand">Squishy WMS</span>
        <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
          Wall Builder
        </NavLink>
        <NavLink to="/scan" className={({ isActive }) => (isActive ? "active" : "")}>
          Packer Scan
        </NavLink>
        <NavLink to="/shipments" className={({ isActive }) => (isActive ? "active" : "")}>
          Shipments
        </NavLink>
        {isAdmin && (
          <NavLink to="/financials" className={({ isActive }) => (isActive ? "active" : "")}>
            Financials
          </NavLink>
        )}
        {isAdmin ? (
          <span className="row" style={{ gap: "0.5rem" }}>
            <span className="muted">Logged in</span>
            <button type="button" className="secondary" onClick={handleLogout}>
              Logout
            </button>
          </span>
        ) : (
          <NavLink to="/login" className={({ isActive }) => (isActive ? "active" : "")}>
            Log in
          </NavLink>
        )}
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<WallBuilder />} />
          <Route path="/scan" element={<PackerScan />} />
          <Route path="/shipments" element={<Shipments />} />
          <Route path="/financials" element={<Financials />} />
          <Route path="/financials/:wallSetId" element={<FinancialDetail />} />
          <Route path="/login" element={<Login onLoggedIn={() => setIsAdmin(true)} />} />
        </Routes>
      </main>
    </div>
  );
}

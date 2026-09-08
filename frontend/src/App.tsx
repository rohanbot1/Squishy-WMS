import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import WallBuilder from "./pages/WallBuilder";
import PackerScan from "./pages/PackerScan";
import Shipments from "./pages/Shipments";
import Financials from "./pages/Financials";
import FinancialDetail from "./pages/FinancialDetail";
import Login from "./pages/Login";
import FloorLogin from "./pages/FloorLogin";
import { checkAuth, checkFloorAccess, logout } from "./api";
import { LanguageProvider, LanguageSwitcher } from "./i18n";

// Wall Builder / Packer Scan / Shipments make PIN-gated calls throughout
// their whole lifetime (every scan, every upload), not just one on mount,
// so this checks once up front and never renders the page at all while
// locked, rather than trying to catch a 401 at every one of those call
// sites. While the mount-time check is still in flight, render nothing --
// otherwise the real page would flash briefly before redirecting.
function RequireFloor({
  unlocked, checking, children,
}: {
  unlocked: boolean;
  checking: boolean;
  children: JSX.Element;
}) {
  if (checking) return null;
  if (!unlocked) return <Navigate to="/floor-login" replace />;
  return children;
}

export default function App() {
  const [isAdmin, setIsAdmin] = useState(false);
  const [isFloorUnlocked, setIsFloorUnlocked] = useState(false);
  const [isCheckingFloor, setIsCheckingFloor] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    checkAuth().then(setIsAdmin);
    checkFloorAccess().then((ok) => {
      setIsFloorUnlocked(ok);
      setIsCheckingFloor(false);
    });
  }, []);

  async function handleLogout() {
    await logout();
    setIsAdmin(false);
    navigate("/");
  }

  return (
    <LanguageProvider>
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
          <LanguageSwitcher />
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
            <Route
              path="/"
              element={
                <RequireFloor unlocked={isFloorUnlocked} checking={isCheckingFloor}>
                  <WallBuilder onAuthError={() => setIsFloorUnlocked(false)} />
                </RequireFloor>
              }
            />
            <Route
              path="/scan"
              element={
                <RequireFloor unlocked={isFloorUnlocked} checking={isCheckingFloor}>
                  <PackerScan onAuthError={() => setIsFloorUnlocked(false)} />
                </RequireFloor>
              }
            />
            <Route
              path="/shipments"
              element={
                <RequireFloor unlocked={isFloorUnlocked} checking={isCheckingFloor}>
                  <Shipments onAuthError={() => setIsFloorUnlocked(false)} />
                </RequireFloor>
              }
            />
            <Route path="/financials" element={<Financials onAuthError={() => setIsAdmin(false)} />} />
            <Route
              path="/financials/:wallSetId"
              element={<FinancialDetail onAuthError={() => setIsAdmin(false)} />}
            />
            <Route path="/login" element={<Login onLoggedIn={() => setIsAdmin(true)} />} />
            <Route path="/floor-login" element={<FloorLogin onUnlocked={() => setIsFloorUnlocked(true)} />} />
          </Routes>
        </main>
      </div>
    </LanguageProvider>
  );
}

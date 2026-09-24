import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, login } from "../api";

interface LoginProps {
  onLoggedIn: () => void;
}

export default function Login({ onLoggedIn }: LoginProps) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!password) return;
    setError(null);
    setSubmitting(true);
    try {
      await login(password);
      onLoggedIn();
      navigate("/");
    } catch (e) {
      // Same distinctions as FloorLogin: a server crash or missing config
      // must never read as a mistyped password.
      if (e instanceof ApiError && e.status < 500) {
        setError("Incorrect password.");
      } else if (
        e instanceof ApiError &&
        typeof e.detail === "string" &&
        e.detail.startsWith("ADMIN_PASSWORD_HASH is not set")
      ) {
        setError("Admin password isn't configured on this server yet -- see scripts/set_admin_password.py.");
      } else if (e instanceof ApiError) {
        setError(`Something went wrong on the server (error ${e.status}). Try again, and check the server log if it keeps happening.`);
      } else {
        setError("Couldn't reach the server. Check the connection and try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="auth-screen">
      <h1>Admin login</h1>
      <section>
        <form onSubmit={handleSubmit}>
          <label htmlFor="admin-password">Password</label>
          <input
            id="admin-password"
            type="password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <button type="submit" disabled={!password || submitting}>
            {submitting ? "Logging in..." : "Log in"}
          </button>
        </form>
        {error && <p className="error-text">{error}</p>}
      </section>
    </div>
  );
}

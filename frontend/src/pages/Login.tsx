import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { login } from "../api";

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
    } catch {
      setError("Incorrect password.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <h1>Admin login</h1>
      <section className="panel" style={{ maxWidth: "20rem" }}>
        <form onSubmit={handleSubmit}>
          <label htmlFor="admin-password">Password</label>
          <input
            id="admin-password"
            type="password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={{ width: "100%" }}
          />
          <button type="submit" disabled={!password || submitting} style={{ marginTop: "0.75rem" }}>
            {submitting ? "Logging in..." : "Log in"}
          </button>
        </form>
        {error && <p className="error-text">{error}</p>}
      </section>
    </div>
  );
}

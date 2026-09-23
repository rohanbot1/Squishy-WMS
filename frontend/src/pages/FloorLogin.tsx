import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { floorLogin } from "../api";

interface FloorLoginProps {
  onUnlocked: () => void;
}

export default function FloorLogin({ onUnlocked }: FloorLoginProps) {
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!pin) return;
    setError(null);
    setSubmitting(true);
    try {
      await floorLogin(pin);
      onUnlocked();
      navigate("/");
    } catch (e) {
      // A real server misconfiguration (FLOOR_PIN_HASH never set) must
      // say so, not masquerade as a wrong PIN -- otherwise a floor
      // worker has no way to tell "I mistyped" from "this deployment was
      // never finished being set up" apart. Wrong PIN vs. rejected-by-
      // backoff still share one message deliberately -- see
      // verify_floor_pin in app/auth.py.
      if (e instanceof Error && e.message.startsWith("500")) {
        setError("Floor PIN isn't configured on this server yet -- contact your admin.");
      } else {
        setError("Incorrect PIN.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="auth-screen">
      <h1>Enter floor PIN</h1>
      <section>
        <form onSubmit={handleSubmit}>
          <label htmlFor="floor-pin">PIN</label>
          <input
            id="floor-pin"
            className="pin-input"
            type="password"
            inputMode="numeric"
            pattern="[0-9]*"
            maxLength={4}
            autoFocus
            value={pin}
            onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 4))}
          />
          <button type="submit" disabled={pin.length !== 4 || submitting}>
            {submitting ? "Checking..." : "Unlock"}
          </button>
        </form>
        {error && <p className="error-text">{error}</p>}
      </section>
    </div>
  );
}

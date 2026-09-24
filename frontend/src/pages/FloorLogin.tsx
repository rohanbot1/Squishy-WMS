import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, floorLogin } from "../api";

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
      // Four different situations, four different messages -- a floor
      // worker has to be able to tell "I mistyped" from "this deployment
      // isn't set up" from "the server crashed" from "no connection".
      // "Not configured" is only claimed when the server explicitly says
      // so (get_floor_pin_hash in app/auth.py); any other 500 is a real
      // crash, which was once shown as a setup mistake and sent everyone
      // looking in the wrong place. Wrong PIN vs. rejected-by-backoff
      // still share one message deliberately -- see verify_floor_pin.
      if (e instanceof ApiError && e.status < 500) {
        setError("Incorrect PIN.");
      } else if (
        e instanceof ApiError &&
        typeof e.detail === "string" &&
        e.detail.startsWith("FLOOR_PIN_HASH is not set")
      ) {
        setError("Floor PIN isn't configured on this server yet -- contact your admin.");
      } else if (e instanceof ApiError) {
        setError(`Something went wrong on the server (error ${e.status}). Try again, and tell your admin if it keeps happening.`);
      } else {
        setError("Couldn't reach the server. Check the connection and try again.");
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

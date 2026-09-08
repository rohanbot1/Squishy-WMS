import { useNavigate } from "react-router-dom";
import { isUnauthorizedError } from "./api";

// Wall Builder, Packer Scan, and Shipments each make PIN-gated calls from
// several different places (every scan, every upload, every catalog
// edit) -- this is the one place that logic lives, instead of repeating
// the same "was this a real 401, or just some other error" check at
// every one of those call sites.
export function useAuthErrorHandler(onAuthError: () => void, redirectTo: string) {
  const navigate = useNavigate();
  return function handleAuthAwareError(e: unknown, setError: (message: string) => void) {
    if (isUnauthorizedError(e)) {
      onAuthError();
      navigate(redirectTo);
      return;
    }
    setError(String(e));
  };
}

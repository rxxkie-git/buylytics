/**
 * Dependency status, polled once for the whole app.
 *
 * `/health/ready` is unauthenticated and cheap, and it is the only honest answer to
 * "why is the dashboard empty" when the cause is infrastructure rather than data.
 * Polling it in one provider rather than per component means the status chip, the
 * settings page and any page that wants to explain itself all read one snapshot.
 *
 * The interpretation lives in `readiness.js`, which has no React dependency and is
 * checked directly.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { fetchReadiness } from "../api/system.js";
import { summariseReadiness } from "./readiness.js";

const REFRESH_MS = 30_000;

const SystemStatusContext = createContext(null);

export const SystemStatusProvider = ({ children }) => {
  const [readiness, setReadiness] = useState(null);
  const [error, setError] = useState(null);
  const [isChecking, setIsChecking] = useState(true);

  const controllerRef = useRef(null);
  const mountedRef = useRef(true);

  const check = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    try {
      const next = await fetchReadiness({ signal: controller.signal });
      if (!mountedRef.current) return;
      setReadiness(next);
      setError(null);
    } catch (caught) {
      if (caught?.isCancelled || !mountedRef.current) return;
      // The previous snapshot is deliberately kept. One failed poll during a
      // backend restart should not blank out a status that was fine a moment ago.
      setError(caught);
    } finally {
      if (mountedRef.current) setIsChecking(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    check();

    const timer = setInterval(check, REFRESH_MS);

    return () => {
      mountedRef.current = false;
      clearInterval(timer);
      controllerRef.current?.abort();
    };
  }, [check]);

  const value = useMemo(
    () => ({
      readiness,
      error,
      isChecking,
      refresh: check,
      summary: summariseReadiness(readiness, error),
    }),
    [readiness, error, isChecking, check]
  );

  return <SystemStatusContext.Provider value={value}>{children}</SystemStatusContext.Provider>;
};

/**
 * Safe to call outside the provider — returns a neutral snapshot rather than
 * throwing, so a component can mention system status without forcing every
 * wrapper that renders it to install the provider.
 */
export const useSystemStatus = () =>
  useContext(SystemStatusContext) ?? {
    readiness: null,
    error: null,
    isChecking: false,
    refresh: () => {},
    summary: { tone: "default", label: "Unknown", detail: null },
  };

export default SystemStatusContext;

/**
 * Poll an analytics job until it finishes.
 *
 * Two things make this more than a `setInterval`.
 *
 * **It gives up.** With `KAFKA_ENABLED=false` and no inline handler registered, a
 * submitted job is written to Postgres and never picked up by anything. It stays
 * `queued` forever, and a naive poller would spin against the API for the life of
 * the tab. After `stallAfterMs` in a non-terminal state the hook stops and reports
 * `stalled`, so the page can say "no worker is consuming the job queue" — which is
 * the actual problem — instead of showing a spinner that never resolves.
 *
 * **It backs off.** A job that takes two minutes should not be asked about 120
 * times. The interval grows from `initialIntervalMs` toward `maxIntervalMs`, which
 * keeps the first few seconds responsive without hammering a long run.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchJob, isTerminalStatus } from "../api/analytics";

const DEFAULTS = {
  initialIntervalMs: 1_000,
  maxIntervalMs: 8_000,
  backoffFactor: 1.5,
  /** Long enough for a real RFM run over a large order table, short enough to
   *  notice a queue with nothing behind it. */
  stallAfterMs: 90_000,
};

const useJobPolling = (jobId, options = {}) => {
  const { initialIntervalMs, maxIntervalMs, backoffFactor, stallAfterMs } = {
    ...DEFAULTS,
    ...options,
  };

  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [isPolling, setIsPolling] = useState(false);
  const [isStalled, setIsStalled] = useState(false);

  const timerRef = useRef(null);
  const controllerRef = useRef(null);
  const stoppedRef = useRef(false);

  const stop = useCallback(() => {
    stoppedRef.current = true;
    clearTimeout(timerRef.current);
    controllerRef.current?.abort();
    setIsPolling(false);
  }, []);

  useEffect(() => {
    if (!jobId) {
      setJob(null);
      setError(null);
      setIsStalled(false);
      setIsPolling(false);
      return undefined;
    }

    stoppedRef.current = false;
    setIsPolling(true);
    setIsStalled(false);
    setError(null);

    const startedAt = Date.now();
    let interval = initialIntervalMs;

    const tick = async () => {
      if (stoppedRef.current) return;

      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;

      try {
        const next = await fetchJob(jobId, { signal: controller.signal });

        if (stoppedRef.current) return;

        setJob(next);

        if (isTerminalStatus(next?.status)) {
          setIsPolling(false);
          return;
        }

        if (Date.now() - startedAt > stallAfterMs) {
          setIsStalled(true);
          setIsPolling(false);
          return;
        }
      } catch (caught) {
        if (caught?.isCancelled || stoppedRef.current) return;

        // A single failed poll is not a failed job — a proxy hiccup or a 503 from
        // a restarting API should not discard a job that is still running. The
        // error is surfaced but polling continues until the stall deadline.
        setError(caught);

        if (caught?.isNotFound || caught?.isAuthError) {
          setIsPolling(false);
          return;
        }

        if (Date.now() - startedAt > stallAfterMs) {
          setIsStalled(true);
          setIsPolling(false);
          return;
        }
      }

      interval = Math.min(interval * backoffFactor, maxIntervalMs);
      timerRef.current = setTimeout(tick, interval);
    };

    tick();

    return () => {
      stoppedRef.current = true;
      clearTimeout(timerRef.current);
      controllerRef.current?.abort();
    };
  }, [jobId, initialIntervalMs, maxIntervalMs, backoffFactor, stallAfterMs]);

  return { job, error, isPolling, isStalled, stop };
};

export default useJobPolling;

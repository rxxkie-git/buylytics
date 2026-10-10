/**
 * Data fetching for API-backed pages.
 *
 * Improves on the Phase 1 `useApiData` in three ways that matter once pages start
 * taking parameters:
 *
 * 1. **Aborts on unmount and on re-fetch.** The old hook set state after the
 *    component had gone, which React warns about, and it left dead requests in
 *    flight.
 * 2. **Ignores stale responses.** With a dependency like a date range, a slow
 *    request for 30 days can land after a fast one for 7 and overwrite it, so the
 *    chart shows a window the controls say is not selected. Each run carries a
 *    sequence number and only the newest may write.
 * 3. **Keeps the last good data while refreshing.** Blanking the page to a
 *    spinner on every poll makes a dashboard flicker; `isRefreshing` lets the UI
 *    show a subtle indicator over real data instead.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";

const useApiResource = (fetcher, { deps = [], initialData = null, enabled = true } = {}) => {
  const [data, setData] = useState(initialData);
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(Boolean(enabled));
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState(null);

  // A ref, not state: the fetcher is usually an inline arrow function, so it has
  // a new identity every render. Reading it from a ref keeps `load` stable and
  // stops the effect from firing on every render.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const controllerRef = useRef(null);
  const runIdRef = useRef(0);
  const hasDataRef = useRef(initialData !== null);
  const mountedRef = useRef(true);

  useEffect(
    () => () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
    },
    []
  );

  const load = useCallback(async ({ silent = false } = {}) => {
    controllerRef.current?.abort();

    const controller = new AbortController();
    controllerRef.current = controller;

    const runId = runIdRef.current + 1;
    runIdRef.current = runId;

    // Only the newest run may write. An aborted request usually rejects, but not
    // always — one that already resolved in the microtask queue will still land.
    const isCurrent = () => mountedRef.current && runIdRef.current === runId;

    if (silent && hasDataRef.current) {
      setIsRefreshing(true);
    } else {
      setIsLoading(true);
    }

    try {
      const result = await fetcherRef.current({ signal: controller.signal });

      if (!isCurrent()) return null;

      setData(result);
      setError(null);
      setLastUpdatedAt(new Date());
      hasDataRef.current = true;
      return result;
    } catch (caught) {
      // A cancelled request is the expected outcome of navigating away or
      // changing a filter. Showing it as an error would be wrong.
      if (caught?.isCancelled || caught?.name === "CanceledError" || !isCurrent()) {
        return null;
      }

      setError(caught instanceof ApiError ? caught : new ApiError({ message: String(caught) }));
      return null;
    } finally {
      if (isCurrent()) {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      setIsLoading(false);
      return;
    }

    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps]);

  return {
    data,
    error,
    isLoading,
    isRefreshing,
    lastUpdatedAt,
    /** Re-fetch showing a spinner. */
    reload: load,
    /** Re-fetch keeping the current data on screen. */
    refresh: useCallback(() => load({ silent: true }), [load]),
    setData,
  };
};

export default useApiResource;

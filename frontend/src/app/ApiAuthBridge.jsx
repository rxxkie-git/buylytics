/**
 * Connects Clerk's session to the API client.
 *
 * Renders nothing. Its whole job is to hand `getToken` to the client's module-level
 * slot so the request interceptor — registered once, at import time — can mint a
 * fresh JWT per request.
 *
 * Why a bridge rather than calling `useAuth()` inside the client: the client is a
 * plain module, not a component, so it cannot use hooks; and Clerk's tokens are
 * short-lived, so capturing one at sign-in and reusing it would start failing with
 * 401s after a few minutes. `getToken()` returns a cached token until it is close to
 * expiry and refreshes it transparently, which is exactly the behaviour wanted — but
 * only if it is called per request rather than once.
 *
 * The token itself is never stored here, logged, or put in application state.
 */

import { useEffect } from "react";
import { useAuth } from "@clerk/clerk-react";
import { setTokenProvider } from "../api/client.js";

const ApiAuthBridge = ({ children = null }) => {
  const { getToken, isLoaded } = useAuth();

  useEffect(() => {
    if (!isLoaded) return undefined;

    // Passing a thunk rather than `getToken` itself keeps the client's contract
    // narrow — it needs "give me a token", not Clerk's option bag — and means a
    // Clerk upgrade that changes the signature is handled in one place.
    setTokenProvider(() => getToken());

    return () => {
      // Cleared on unmount so a signed-out app does not keep a live handle to a
      // session that no longer exists.
      setTokenProvider(null);
    };
  }, [getToken, isLoaded]);

  return children;
};

export default ApiAuthBridge;

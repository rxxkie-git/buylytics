/**
 * Loading, error and empty states.
 *
 * One component because pages consistently get the *ordering* of these three
 * wrong: check error before empty, or you show "no customers yet — import a file"
 * when the truth is the request 500'd and there may be ten thousand customers.
 * `<AsyncBoundary>` enforces the order once.
 *
 * The error card reads the `ApiError` fields rather than printing a message, so a
 * 401 offers a sign-in, an unreachable API says to check the backend, a timeout
 * says the opposite (the API answered the connection and then went quiet), and a
 * server error shows the correlation id — which is the only thing that lets
 * someone find the matching log line.
 *
 * One state deliberately does *not* live here: "this feature is switched off in
 * the deployment's configuration". That is a successful response, not an error,
 * and rendering it through this component would tell the operator their backend
 * was broken when it is merely unconfigured. Pages own that distinction — see the
 * webhook section of `SettingsPage.jsx`.
 */

import CloudOffRoundedIcon from "@mui/icons-material/CloudOffRounded";
import ErrorOutlineRoundedIcon from "@mui/icons-material/ErrorOutlineRounded";
import HourglassEmptyRoundedIcon from "@mui/icons-material/HourglassEmptyRounded";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  CircularProgress,
  Paper,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";

const Centered = ({ children, minHeight = 260 }) => (
  <Paper variant="outlined" sx={{ borderRadius: 3, p: 4, minHeight, display: "grid", placeItems: "center" }}>
    <Box sx={{ textAlign: "center", maxWidth: 460 }}>{children}</Box>
  </Paper>
);

export const LoadingBlock = ({ label = "Loading…", variant = "spinner", rows = 4 }) => {
  if (variant === "skeleton") {
    return (
      <Stack spacing={1.5}>
        {Array.from({ length: rows }).map((_, index) => (
          <Skeleton key={index} variant="rounded" height={index === 0 ? 56 : 44} />
        ))}
      </Stack>
    );
  }

  return (
    <Centered>
      <CircularProgress size={38} />
      <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
        {label}
      </Typography>
    </Centered>
  );
};

const errorPresentation = (error) => {
  if (error?.isAuthError) {
    return {
      icon: <LockOutlinedIcon color="warning" sx={{ fontSize: 44 }} />,
      title: "You need to sign in again",
      hint: "Your session expired or does not have access to this business.",
    };
  }

  // Checked *before* the status === 0 branch below, because a timeout also has no
  // response and so also carries status 0.
  //
  // Conflating the two is what produced the misleading "Cannot reach the API /
  // The request timed out." on the settings page: two statements that contradict
  // each other, the first of which sent operators off to check whether the
  // backend was running and whether VITE_API_BASE_URL was right. Neither was the
  // problem. The request reached the server; the server took too long to answer,
  // because its event loop was blocked (see app/core/concurrency.py). A timeout
  // is evidence the API *is* there, so it must not be reported as absence.
  if (error?.code === "timeout") {
    return {
      icon: <HourglassEmptyRoundedIcon color="warning" sx={{ fontSize: 44 }} />,
      title: "The API did not answer in time",
      hint:
        "The request reached the server but no response arrived within the timeout — " +
        "so the API is running, and something it is doing is slow. Retry; if it keeps " +
        "happening, check the backend logs for the matching request id.",
    };
  }

  if (error?.status === 0) {
    return {
      icon: <CloudOffRoundedIcon color="warning" sx={{ fontSize: 44 }} />,
      title: "Cannot reach the API",
      hint: "Start the backend, then retry. If it is running, check VITE_API_BASE_URL.",
    };
  }

  if (error?.status === 504) {
    return {
      icon: <HourglassEmptyRoundedIcon color="warning" sx={{ fontSize: 44 }} />,
      title: "That took too long to finish",
      hint: "The server released the request before it completed. A smaller file or a narrower range usually succeeds.",
    };
  }

  if (error?.isDependencyDown) {
    return {
      icon: <CloudOffRoundedIcon color="warning" sx={{ fontSize: 44 }} />,
      title: "A dependency is unavailable",
      hint: "The API is up but something it needs is not. Check /health/ready.",
    };
  }

  return {
    icon: <ErrorOutlineRoundedIcon color="error" sx={{ fontSize: 44 }} />,
    title: "That request failed",
    hint: null,
  };
};

export const ErrorBlock = ({ error, onRetry }) => {
  const { icon, title, hint } = errorPresentation(error);

  return (
    <Centered>
      {icon}
      <Typography variant="h6" sx={{ mt: 1.5 }}>
        {title}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
        {error?.message}
      </Typography>
      {hint ? (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1.5 }}>
          {hint}
        </Typography>
      ) : null}

      {error?.fields?.length ? (
        <Alert severity="warning" sx={{ mt: 2, textAlign: "left" }}>
          <AlertTitle sx={{ fontSize: 13 }}>Invalid input</AlertTitle>
          {error.fields.map((field) => (
            <Typography key={field.field} variant="caption" sx={{ display: "block" }}>
              {field.field || "request"}: {field.reason}
            </Typography>
          ))}
        </Alert>
      ) : null}

      {onRetry && error?.isRetryable !== false ? (
        <Button variant="contained" startIcon={<RefreshRoundedIcon />} onClick={onRetry} sx={{ mt: 2.5 }}>
          Try again
        </Button>
      ) : null}

      {error?.requestId ? (
        <Typography
          variant="caption"
          sx={{ display: "block", mt: 2, color: "text.disabled", fontFamily: "monospace" }}
        >
          request {error.requestId}
        </Typography>
      ) : null}
    </Centered>
  );
};

export const EmptyBlock = ({ icon, title, description, action }) => (
  <Centered>
    {icon}
    <Typography variant="h6" sx={{ mt: icon ? 1.5 : 0 }}>
      {title}
    </Typography>
    {description ? (
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
        {description}
      </Typography>
    ) : null}
    {action ? <Box sx={{ mt: 2.5 }}>{action}</Box> : null}
  </Centered>
);

/**
 * Renders exactly one of loading, error, empty, or children — in that order.
 *
 * `isEmpty` is only consulted once the request has succeeded, which is the whole
 * point: an empty result and a failed request are different facts and must not
 * produce the same screen.
 *
 * **Pass a function when the children read the data.** JSX children are ordinary
 * arguments, so `<AsyncBoundary isLoading><b>{data.total}</b></AsyncBoundary>`
 * evaluates `data.total` *before* this component runs and throws while the
 * request is still in flight — the early return never gets the chance to protect
 * it. This is not a hypothetical: it is what blanked the dashboard. A function
 * child is only called on the path that actually renders it, which makes the
 * guard real rather than decorative.
 */
const AsyncBoundary = ({
  isLoading,
  error,
  isEmpty = false,
  onRetry,
  loadingLabel,
  loadingVariant,
  empty = null,
  children,
}) => {
  if (isLoading) return <LoadingBlock label={loadingLabel} variant={loadingVariant} />;
  if (error) return <ErrorBlock error={error} onRetry={onRetry} />;
  if (isEmpty && empty) return empty;
  return typeof children === "function" ? children() : children;
};

export default AsyncBoundary;

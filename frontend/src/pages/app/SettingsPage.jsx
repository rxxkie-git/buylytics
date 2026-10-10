/**
 * Settings.
 *
 * An operator page, not a preferences page: what this deployment is pointed at, which
 * dependencies are up, and how to send it a webhook. There is nothing here to save,
 * because everything shown is configured server-side — a form that appeared to change
 * `KAFKA_ENABLED` from a browser would be lying.
 *
 * Every value rendered comes from an endpoint that was built not to leak. `/health/ready`
 * returns fixed status vocabulary and a host:port target only when
 * `EXPOSE_HEALTH_TARGETS` is on, with credentials stripped server-side.
 * `/api/v1/webhooks/config` returns header names, the replay tolerance and delivery
 * counts, and never the signing secret. So this page can display what it receives
 * without filtering — but it deliberately does not invent an input for the secret
 * either: it is shared out of band and typing it into a browser is how it ends up in
 * a log.
 */

import { useMemo } from "react";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import Grid from "@mui/material/Grid";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { useUser } from "@clerk/clerk-react";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";

import { dependencyRows } from "../../app/readiness.js";
import { useSystemStatus } from "../../app/SystemStatusProvider.jsx";
import { getApiBaseUrl, systemApi } from "../../api/index.js";
import { environmentSummary } from "../../config/env.js";
import useApiResource from "../../hooks/useApiResource.js";
import AsyncBoundary from "../../components/ui/AsyncBoundary.jsx";
import RecordTable from "../../components/ui/RecordTable.jsx";
import SectionCard from "../../components/ui/SectionCard.jsx";
import {
  DEPENDENCY_COLOR,
  formatCount,
  formatDateTime,
  formatDuration,
  formatRelative,
  shortId,
} from "../../utils/format.js";

const DELIVERY_COLOR = {
  processed: "success",
  received: "info",
  duplicate: "default",
  failed: "error",
};

/** A label/value pair. Used enough on this page to be worth naming. */
const Field = ({ label, value, mono = false, hint = null }) => (
  <Box sx={{ minWidth: 0 }}>
    <Stack direction="row" spacing={0.5} alignItems="center">
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      {hint ? (
        <Tooltip title={hint}>
          <Typography variant="caption" color="text.disabled" sx={{ cursor: "help" }}>
            ⓘ
          </Typography>
        </Tooltip>
      ) : null}
    </Stack>
    <Typography
      variant="body2"
      fontWeight={600}
      sx={{ mt: 0.25, wordBreak: "break-all", fontFamily: mono ? "monospace" : undefined }}
    >
      {value === null || value === undefined || value === "" ? "—" : String(value)}
    </Typography>
  </Box>
);

const SettingsPage = () => {
  const { user } = useUser();
  const { readiness, error: readinessError, isChecking, refresh, summary } = useSystemStatus();

  const webhooks = useApiResource((options) => systemApi.fetchWebhookConfig(options));
  const deliveries = useApiResource((options) => systemApi.fetchWebhookDeliveries({ limit: 50, ...options }));

  const rows = useMemo(() => dependencyRows(readiness), [readiness]);
  const environment = useMemo(() => environmentSummary(), []);
  const config = webhooks.data;

  // "Nothing has arrived yet" and "nothing can arrive yet" read the same in a
  // table with no rows, so say which one it is. Only claimed when the config call
  // actually succeeded — if it failed we do not know, and guessing would be the
  // same category of mistake as reporting a timeout as an unreachable backend.
  const emptyDeliveriesMessage =
    config && config.configured === false && config.signature_required
      ? "No deliveries can be recorded yet: signatures are required and no WEBHOOK_SECRET is set, so every delivery is rejected before it is stored."
      : "No webhook deliveries recorded yet.";

  const deliveryColumns = useMemo(
    () => [
      {
        id: "received_at",
        label: "Received",
        render: (row) => (
          <Tooltip title={formatDateTime(row.received_at)}>
            <span>{formatRelative(row.received_at)}</span>
          </Tooltip>
        ),
      },
      { id: "source", label: "Source" },
      { id: "event_type", label: "Event" },
      {
        id: "status",
        label: "Status",
        render: (row) => (
          <Chip size="small" label={row.status} color={DELIVERY_COLOR[row.status] ?? "default"} variant="outlined" />
        ),
      },
      {
        id: "signature_verified",
        label: "Signature",
        render: (row) =>
          row.signature_verified ? (
            <Chip size="small" color="success" variant="outlined" label="verified" />
          ) : (
            <Tooltip title="Accepted without a verified signature — only possible when no webhook secret is configured.">
              <Chip size="small" color="warning" variant="outlined" label="unverified" />
            </Tooltip>
          ),
      },
      {
        id: "external_event_id",
        label: "Event ID",
        render: (row) =>
          row.external_event_id ? (
            <Typography variant="caption" sx={{ fontFamily: "monospace" }} title={row.external_event_id}>
              {shortId(row.external_event_id)}
            </Typography>
          ) : (
            "—"
          ),
      },
      {
        id: "error_message",
        label: "Error",
        render: (row) =>
          row.error_message ? (
            <Tooltip title={row.error_message}>
              <Typography variant="caption" color="error.main" noWrap sx={{ display: "block", maxWidth: 220 }}>
                {row.error_message}
              </Typography>
            </Tooltip>
          ) : (
            "—"
          ),
      },
    ],
    []
  );

  return (
    <Stack spacing={3}>
      <SectionCard
        title="This deployment"
        subtitle="Read from the bundle and the authenticated session. Nothing here is editable from the browser."
      >
        <Grid container spacing={2.5}>
          <Grid item xs={12} sm={6} md={4}>
            <Field
              label="API base URL"
              value={getApiBaseUrl()}
              mono
              hint="Empty means same-origin: requests go through whatever serves this page. There is deliberately no localhost fallback."
            />
          </Grid>
          <Grid item xs={12} sm={6} md={4}>
            <Field label="Build mode" value={environment.mode} />
          </Grid>
          <Grid item xs={12} sm={6} md={4}>
            <Field label="Currency / locale" value={`${environment.currency} · ${environment.locale}`} />
          </Grid>
          <Grid item xs={12} sm={6} md={4}>
            <Field
              label="Authentication"
              value={environment.clerkConfigured ? "Clerk (publishable key present)" : "Clerk key missing"}
            />
          </Grid>
          <Grid item xs={12} sm={6} md={4}>
            <Field label="Signed in as" value={user?.primaryEmailAddress?.emailAddress ?? user?.id} mono />
          </Grid>
          <Grid item xs={12} sm={6} md={4}>
            <Field
              label="Business"
              value={config?.business_id}
              mono
              hint="Derived server-side from your Clerk user. The frontend never sends a business id — it could not be trusted if it did."
            />
          </Grid>
        </Grid>
      </SectionCard>

      <SectionCard
        title="Dependency status"
        subtitle={summary.detail ?? "Polled every 30 seconds."}
        isRefreshing={isChecking}
        action={
          <Stack direction="row" spacing={1} alignItems="center">
            <Chip
              size="small"
              label={summary.label}
              color={summary.tone === "default" ? "default" : summary.tone}
              variant={summary.tone === "success" ? "filled" : "outlined"}
            />
            <Button size="small" startIcon={<RefreshRoundedIcon />} onClick={refresh} disabled={isChecking}>
              Check now
            </Button>
          </Stack>
        }
      >
        {readinessError ? (
          <Alert severity="warning" sx={{ mb: 2 }}>
            <AlertTitle>Could not reach the health endpoint</AlertTitle>
            {readinessError.message}. The rows below, if any, are from the last successful check.
          </Alert>
        ) : null}

        {rows.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No readiness snapshot yet.
          </Typography>
        ) : (
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Dependency</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Detail</TableCell>
                <TableCell>Transport</TableCell>
                <TableCell align="right">Latency</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.key}>
                  <TableCell sx={{ textTransform: "capitalize", fontWeight: 600 }}>
                    {row.label}
                    {row.target ? (
                      <Typography variant="caption" color="text.secondary" sx={{ display: "block", fontFamily: "monospace" }}>
                        {row.target}
                      </Typography>
                    ) : null}
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      label={row.status}
                      color={DEPENDENCY_COLOR[row.status] ?? "default"}
                      variant="outlined"
                    />
                  </TableCell>
                  <TableCell sx={{ color: "text.secondary" }}>{row.detail ?? "—"}</TableCell>
                  <TableCell sx={{ color: "text.secondary" }}>{row.transport ?? "—"}</TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                    {/* Zero is a real measurement and renders as "0 ms". Only a
                        missing value shows a dash. */}
                    {row.latencyMs === null || row.latencyMs === undefined
                      ? "—"
                      : formatDuration(row.latencyMs)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 2 }}>
          The cache and event bus are optional by design. When either is disabled or down, reads fall
          back to PostgreSQL and publishes become no-ops — the API stays ready, and only asynchronous
          job dispatch is affected.
        </Typography>
      </SectionCard>

      <SectionCard
        title="Webhook ingestion"
        subtitle="How to send this business events. The signing secret is shared out of band and is never returned by the API."
        isRefreshing={webhooks.isRefreshing}
      >
        <AsyncBoundary
          isLoading={webhooks.isLoading}
          error={webhooks.error}
          onRetry={webhooks.reload}
          loadingVariant="skeleton"
          loadingLabel="Loading webhook configuration…"
        >
          {config ? (
            <Stack spacing={2.5}>
              {config.configured === false ? (
                // Not an error card. This is a 200 response saying the deployment
                // has no WEBHOOK_SECRET, which is the same fact `/api/v1/config`
                // reports as `webhooks_configured: false`. Routing it through
                // <AsyncBoundary> — or leaving the section blank — is what made an
                // unconfigured feature look like an unreachable backend. The
                // integration details below stay on screen because they are what
                // you need in order to finish the configuration.
                <Alert severity={config.signature_required ? "warning" : "info"}>
                  <AlertTitle>
                    {config.signature_required
                      ? "Webhook ingestion is configured to require signatures, but no secret is set"
                      : "Webhook ingestion is enabled without a signing secret"}
                  </AlertTitle>
                  <Typography variant="body2" component="div">
                    {config.signature_required
                      ? "Every delivery will be rejected with 401 until WEBHOOK_SECRET is set on the API. The endpoint itself is reachable — this is a configuration gap, not an outage."
                      : "Deliveries are accepted unsigned. That is fine for local development and not for anything reachable from the internet: without a secret, anyone who knows the URL can post events for this business."}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
                    Set <code>WEBHOOK_SECRET</code> in the API environment and restart the backend, then
                    use <code>Check now</code> above. This is the same flag <code>/api/v1/config</code>{" "}
                    reports as <code>webhooks_configured: false</code>.
                  </Typography>
                </Alert>
              ) : null}

              <Grid container spacing={2.5}>
                <Grid item xs={12} md={6}>
                  <Field label="Endpoint" value={config.endpoint} mono />
                </Grid>
                <Grid item xs={12} md={6}>
                  <Field label="Signature scheme" value={config.signature_scheme} mono />
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Field label="Signature header" value={config.signature_header} mono />
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Field label="Timestamp header" value={config.timestamp_header} mono />
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Field label="Business header" value={config.business_header} mono />
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Field label="Event ID header" value={config.event_id_header} mono />
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Field
                    label="Replay tolerance"
                    value={`${formatCount(config.tolerance_seconds)} seconds`}
                    hint="A signed timestamp older than this is rejected, which is what stops a captured request being replayed later."
                  />
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Field
                    label="Signature required"
                    value={config.signature_required ? "yes" : "no"}
                    hint="Derived server-side from whether a secret is configured and the enforcement setting."
                  />
                </Grid>
                <Grid item xs={12} sm={6} md={6}>
                  <Field label="Deliveries recorded" value={formatCount(config.total)} />
                </Grid>
              </Grid>

              <Divider />

              <Box>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.75 }}>
                  Accepted sources
                </Typography>
                <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                  {(config.supported_sources ?? []).map((source) => (
                    <Chip key={source} size="small" label={source} variant="outlined" sx={{ fontFamily: "monospace" }} />
                  ))}
                </Stack>
              </Box>

              <Box>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.75 }}>
                  Accepted event types
                </Typography>
                <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                  {(config.supported_event_types ?? []).map((type) => (
                    <Chip
                      key={type}
                      size="small"
                      label={type}
                      variant="outlined"
                      // A type that is accepted but not persisted is worth
                      // distinguishing: it will return 200 and write nothing.
                      color={(config.persisted_event_types ?? []).includes(type) ? "primary" : "default"}
                      sx={{ fontFamily: "monospace" }}
                    />
                  ))}
                </Stack>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.75 }}>
                  Highlighted types are written to your tables. The rest are accepted, recorded as a
                  delivery and published to the event bus, but change no records.
                </Typography>
              </Box>

              {config.by_status && Object.keys(config.by_status).length ? (
                <Box>
                  <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.75 }}>
                    Deliveries by status
                  </Typography>
                  <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
                    {Object.entries(config.by_status).map(([key, value]) => (
                      <Chip
                        key={key}
                        size="small"
                        label={`${key}: ${formatCount(value)}`}
                        color={DELIVERY_COLOR[key] ?? "default"}
                        variant="outlined"
                      />
                    ))}
                  </Stack>
                </Box>
              ) : null}
            </Stack>
          ) : null}
        </AsyncBoundary>
      </SectionCard>

      <SectionCard
        title="Recent deliveries"
        subtitle="Your business only. Payloads are not echoed back — the data is already visible on the customers and orders pages."
        isRefreshing={deliveries.isRefreshing}
        action={
          <Button size="small" startIcon={<RefreshRoundedIcon />} onClick={() => deliveries.refresh()}>
            Refresh
          </Button>
        }
      >
        <AsyncBoundary
          isLoading={deliveries.isLoading}
          error={deliveries.error}
          onRetry={deliveries.reload}
          loadingVariant="skeleton"
        >
          <RecordTable
            columns={deliveryColumns}
            rows={deliveries.data?.deliveries ?? []}
            getRowId={(row) => row.id}
            initialSortBy="received_at"
            initialSortDirection="desc"
            searchable
            searchPlaceholder="Search source, event type or status"
            searchKeys={["source", "event_type", "status", "external_event_id"]}
            emptyMessage={emptyDeliveriesMessage}
          />
        </AsyncBoundary>
      </SectionCard>
    </Stack>
  );
};

export default SettingsPage;

/**
 * Analytics jobs.
 *
 * The one screen in the app that has to be honest about asynchronous work, because
 * the interesting failure here is not an error — it is silence. With
 * `KAFKA_ENABLED=false` and `EVENTS_INLINE_FALLBACK=false` a submitted job is
 * written to Postgres and no worker ever reads it: the API answers 200 instead of
 * 202, sets `dispatched: false`, and returns a note saying which switch to flip. A
 * UI that showed a spinner for that would be describing a queue as a delay.
 *
 * So three states are rendered distinctly:
 *
 * - **dispatched and running** — polled with backoff until terminal.
 * - **queued but not dispatched** — a warning, with the reason, and no poller.
 * - **stalled** — dispatched, still non-terminal after 90 seconds, which means the
 *   transport accepted it but nothing is consuming. Polling stops and says so.
 *
 * `reference_date` is exposed because it is what makes a run reproducible: it fixes
 * the instant recency is measured from, so re-running last month's numbers gives
 * last month's answer rather than today's.
 */

import { useCallback, useMemo, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Collapse from "@mui/material/Collapse";
import Divider from "@mui/material/Divider";
import FormControlLabel from "@mui/material/FormControlLabel";
import Grid from "@mui/material/Grid";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import PlayArrowRoundedIcon from "@mui/icons-material/PlayArrowRounded";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";

import { ROUTES } from "../../app/navigation.js";
import { analyticsApi } from "../../api/index.js";
import useApiResource from "../../hooks/useApiResource.js";
import useJobPolling from "../../hooks/useJobPolling.js";
import AsyncBoundary from "../../components/ui/AsyncBoundary.jsx";
import RecordTable from "../../components/ui/RecordTable.jsx";
import SectionCard from "../../components/ui/SectionCard.jsx";
import {
  formatCount,
  formatDateTime,
  formatDuration,
  formatRelative,
  JOB_STATUS_COLOR,
  shortId,
} from "../../utils/format.js";

/** Fallback only. The live list is `supported_job_types` from the API. */
const FALLBACK_JOB_TYPES = ["full_refresh", "rfm", "segmentation"];

const JOB_TYPE_LABEL = {
  rfm: "RFM",
  segmentation: "Segmentation",
  full_refresh: "Full refresh",
};

const JOB_TYPE_DESCRIPTION = {
  rfm: "Aggregates orders into recency, frequency and monetary scores.",
  segmentation: "Clusters customers from the stored RFM snapshot. Needs RFM to have run.",
  full_refresh: "RFM followed by segmentation, in one run. Start here.",
};

const STATUS_FILTERS = ["all", "queued", "running", "completed", "failed"];

/** Elapsed time between two ISO timestamps, or null when either is missing. */
const elapsed = (from, to) => {
  if (!from || !to) return null;

  const start = new Date(from).getTime();
  const end = new Date(to).getTime();

  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return Math.max(0, end - start);
};

/** Renders a job's `result` metadata without assuming which keys it holds. */
const ResultDetail = ({ result }) => {
  const entries = Object.entries(result ?? {});
  if (!entries.length) return null;

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)", md: "repeat(3, 1fr)" },
        gap: 1.25,
        mt: 1.5,
      }}
    >
      {entries.map(([key, value]) => (
        <Box key={key} sx={{ minWidth: 0 }}>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
            {key.replace(/_/g, " ")}
          </Typography>
          <Typography variant="body2" sx={{ wordBreak: "break-word" }} fontWeight={600}>
            {typeof value === "object" && value !== null ? JSON.stringify(value) : String(value ?? "—")}
          </Typography>
        </Box>
      ))}
    </Box>
  );
};

const JobsPage = () => {
  const [jobType, setJobType] = useState("full_refresh");
  const [useReferenceDate, setUseReferenceDate] = useState(false);
  const [referenceDate, setReferenceDate] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  /** The response from the last submit: carries `dispatched`, `transport`, `note`. */
  const [submitted, setSubmitted] = useState(null);
  const [expandedJobId, setExpandedJobId] = useState(null);

  const jobs = useApiResource(
    (options) =>
      analyticsApi.fetchJobs({
        status: statusFilter === "all" ? undefined : statusFilter,
        limit: 100,
        ...options,
      }),
    { deps: [statusFilter] }
  );

  // Only poll a job that a transport actually accepted. Polling an undispatched
  // job would ask the API 30 times about a row that cannot change.
  const pollableJobId = submitted?.dispatched ? submitted.job_id : null;
  const polled = useJobPolling(pollableJobId);

  const jobTypes = jobs.data?.supported_job_types ?? FALLBACK_JOB_TYPES;
  const rows = jobs.data?.results ?? [];

  // `jobs` is a fresh object every render, so depending on it would rebuild
  // `submit` each time. `refresh` is memoised inside the hook and is stable.
  const refreshJobs = jobs.refresh;

  const counts = useMemo(() => {
    const tally = { queued: 0, running: 0, completed: 0, failed: 0 };
    for (const row of rows) {
      if (row.status in tally) tally[row.status] += 1;
    }
    return tally;
  }, [rows]);

  const submit = useCallback(async () => {
    setSubmitting(true);
    setSubmitError(null);
    setSubmitted(null);

    try {
      const payload = await analyticsApi.submitJob({
        jobType,
        // `datetime-local` gives "2026-08-15T10:30" with no zone. `new Date(...)`
        // reads that as local time, and `toISOString` converts to UTC — which is
        // what the backend's `datetime` field expects.
        referenceDate: useReferenceDate && referenceDate ? new Date(referenceDate) : null,
      });

      setSubmitted(payload);
      refreshJobs();
    } catch (caught) {
      setSubmitError(caught);
    } finally {
      setSubmitting(false);
    }
  }, [jobType, useReferenceDate, referenceDate, refreshJobs]);

  // The polled job is fresher than the list, so it wins where both have the row.
  const mergedRows = useMemo(() => {
    if (!polled.job) return rows;
    return rows.map((row) => (row.job_id === polled.job.job_id ? { ...row, ...polled.job } : row));
  }, [rows, polled.job]);

  const columns = useMemo(
    () => [
      {
        id: "job_id",
        label: "Job",
        render: (row) => (
          <Stack direction="row" spacing={0.5} alignItems="center">
            <Typography variant="body2" sx={{ fontFamily: "monospace" }} title={row.job_id}>
              {shortId(row.job_id)}
            </Typography>
            {row.result || row.error_message ? (
              <Button
                size="small"
                onClick={(event) => {
                  event.stopPropagation();
                  setExpandedJobId((current) => (current === row.job_id ? null : row.job_id));
                }}
                sx={{ minWidth: 0, px: 0.5 }}
              >
                <ExpandMoreRoundedIcon
                  fontSize="small"
                  sx={{
                    transition: "transform 150ms",
                    transform: expandedJobId === row.job_id ? "rotate(180deg)" : "none",
                  }}
                />
              </Button>
            ) : null}
          </Stack>
        ),
      },
      {
        id: "job_type",
        label: "Type",
        render: (row) => JOB_TYPE_LABEL[row.job_type] ?? row.job_type,
      },
      {
        id: "status",
        label: "Status",
        render: (row) => (
          <Stack direction="row" spacing={0.75} alignItems="center">
            <Chip
              size="small"
              label={row.status}
              color={JOB_STATUS_COLOR[row.status] ?? "default"}
              variant={row.status === "queued" ? "outlined" : "filled"}
            />
            {row.job_id === pollableJobId && polled.isPolling ? <CircularProgress size={13} /> : null}
          </Stack>
        ),
      },
      {
        id: "created_at",
        label: "Queued",
        render: (row) => (
          <Tooltip title={formatDateTime(row.created_at)}>
            <span>{formatRelative(row.created_at)}</span>
          </Tooltip>
        ),
      },
      {
        id: "duration",
        label: "Duration",
        numeric: true,
        sortValue: (row) => elapsed(row.started_at, row.completed_at),
        render: (row) => {
          const ms = elapsed(row.started_at, row.completed_at);
          if (ms !== null) return formatDuration(ms);
          // A running job has a start but no end; a queued one has neither. Those
          // are different answers and a single dash would conflate them.
          return row.started_at ? "running" : "—";
        },
      },
      {
        id: "import_id",
        label: "Import",
        render: (row) =>
          row.import_id ? (
            <Typography variant="caption" sx={{ fontFamily: "monospace" }} title={row.import_id}>
              {shortId(row.import_id)}
            </Typography>
          ) : (
            "—"
          ),
      },
      {
        id: "error_message",
        label: "Outcome",
        render: (row) => {
          if (row.error_message) {
            return (
              <Tooltip title={row.error_message}>
                <Typography variant="caption" color="error.main" noWrap sx={{ maxWidth: 220, display: "block" }}>
                  {row.error_message}
                </Typography>
              </Tooltip>
            );
          }

          const scored = row.result?.customers_scored ?? row.result?.rows_written;
          if (scored !== undefined && scored !== null) {
            return (
              <Typography variant="caption" color="text.secondary">
                {formatCount(scored)} rows
              </Typography>
            );
          }

          return "—";
        },
      },
      {
        id: "actions",
        label: "",
        sortable: false,
        render: (row) =>
          row.status === "running" || row.status === "queued" ? (
            <Button
              size="small"
              color="error"
              variant="outlined"
              onClick={async (event) => {
                event.stopPropagation();
                try {
                  await analyticsApi.cancelJob(row.job_id);
                  refreshJobs();
                } catch (caught) {
                  console.error("Failed to cancel job", caught);
                }
              }}
              sx={{ minWidth: 0, px: 1, py: 0.25, fontSize: "0.75rem" }}
            >
              Cancel
            </Button>
          ) : null,
      },
    ],
    [expandedJobId, pollableJobId, polled.isPolling, refreshJobs]
  );

  const expandedJob = mergedRows.find((row) => row.job_id === expandedJobId) ?? null;

  return (
    <Stack spacing={3}>
      <SectionCard
        title="Queue a job"
        subtitle="Analysis runs asynchronously. Submitting returns a job id, not a result."
      >
        <Grid container spacing={2} alignItems="flex-start">
          <Grid item xs={12} md={4}>
            <TextField
              select
              fullWidth
              size="small"
              label="Job type"
              value={jobType}
              onChange={(event) => setJobType(event.target.value)}
              helperText={JOB_TYPE_DESCRIPTION[jobType] ?? " "}
            >
              {jobTypes.map((type) => (
                <MenuItem key={type} value={type}>
                  {JOB_TYPE_LABEL[type] ?? type}
                </MenuItem>
              ))}
            </TextField>
          </Grid>

          <Grid item xs={12} md={5}>
            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={useReferenceDate}
                  onChange={(event) => setUseReferenceDate(event.target.checked)}
                />
              }
              label={
                <Typography variant="body2">
                  Measure recency from a fixed date
                </Typography>
              }
            />
            <Collapse in={useReferenceDate}>
              <TextField
                fullWidth
                size="small"
                type="datetime-local"
                label="Reference date"
                value={referenceDate}
                onChange={(event) => setReferenceDate(event.target.value)}
                InputLabelProps={{ shrink: true }}
                helperText="Leave off for “now”. Setting it makes the run reproducible."
                sx={{ mt: 1 }}
              />
            </Collapse>
          </Grid>

          <Grid item xs={12} md={3}>
            <Button
              fullWidth
              variant="contained"
              startIcon={submitting ? <CircularProgress size={16} color="inherit" /> : <PlayArrowRoundedIcon />}
              onClick={submit}
              disabled={submitting || (useReferenceDate && !referenceDate)}
              sx={{ mt: { md: 0.5 } }}
            >
              {submitting ? "Submitting" : "Run analysis"}
            </Button>
          </Grid>
        </Grid>

        {submitError ? (
          <Alert severity="error" sx={{ mt: 2 }} onClose={() => setSubmitError(null)}>
            <AlertTitle>Could not queue the job</AlertTitle>
            {submitError.message}
            {submitError.fields?.length
              ? submitError.fields.map((field) => (
                  <Typography key={field.field} variant="caption" sx={{ display: "block" }}>
                    {field.field}: {field.reason}
                  </Typography>
                ))
              : null}
          </Alert>
        ) : null}

        {submitted && !submitted.dispatched ? (
          <Alert severity="warning" sx={{ mt: 2 }}>
            <AlertTitle>Queued, but nothing will pick it up</AlertTitle>
            {submitted.note ??
              "No event transport is active, so this job was recorded but not dispatched."}
            <Typography variant="caption" sx={{ display: "block", mt: 1 }}>
              Job {submitted.job_id} is stored with status “{submitted.status}”. It will run as soon
              as a worker is consuming the queue.
            </Typography>
          </Alert>
        ) : null}

        {submitted?.dispatched ? (
          <Alert
            severity={
              polled.job?.status === "failed"
                ? "error"
                : polled.job?.status === "completed"
                  ? "success"
                  : "info"
            }
            sx={{ mt: 2 }}
            icon={polled.isPolling ? <CircularProgress size={18} /> : undefined}
          >
            <AlertTitle>
              {polled.job?.status === "completed"
                ? "Analysis complete"
                : polled.job?.status === "failed"
                  ? "Analysis failed"
                  : `Job ${polled.job?.status ?? submitted.status}`}
            </AlertTitle>

            <Typography variant="body2">
              Job {shortId(submitted.job_id)} dispatched over {submitted.transport ?? "the event bus"}.
            </Typography>

            {polled.job?.error_message ? (
              <Typography variant="caption" color="error.main" sx={{ display: "block", mt: 0.5 }}>
                {polled.job.error_message}
              </Typography>
            ) : null}

            {polled.job?.result ? <ResultDetail result={polled.job.result} /> : null}

            {polled.isStalled ? (
              <Typography variant="caption" sx={{ display: "block", mt: 1 }}>
                Stopped polling after 90 seconds — the job is still not finished, which usually means
                the event was published but no worker is consuming that topic. Check the worker
                process and the event bus status in Settings.
              </Typography>
            ) : null}

            {polled.error && !polled.isStalled ? (
              <Typography variant="caption" sx={{ display: "block", mt: 1 }}>
                A status check failed ({polled.error.message}). Still polling — one failed request is
                not a failed job.
              </Typography>
            ) : null}
          </Alert>
        ) : null}
      </SectionCard>

      <Grid container spacing={2.5}>
        {STATUS_FILTERS.filter((key) => key !== "all").map((key) => (
          <Grid item xs={6} md={3} key={key}>
            <Box
              onClick={() => setStatusFilter(statusFilter === key ? "all" : key)}
              sx={{
                cursor: "pointer",
                borderRadius: 3,
                border: "1px solid",
                borderColor: statusFilter === key ? "primary.main" : "divider",
                p: 2,
              }}
            >
              <Typography variant="caption" color="text.secondary" textTransform="capitalize">
                {key}
              </Typography>
              <Typography variant="h5" sx={{ fontVariantNumeric: "tabular-nums" }}>
                {formatCount(counts[key])}
              </Typography>
            </Box>
          </Grid>
        ))}
      </Grid>

      <SectionCard
        title="Recent jobs"
        subtitle={
          statusFilter === "all"
            ? `${formatCount(rows.length)} most recent`
            : `Filtered to ${statusFilter}`
        }
        isRefreshing={jobs.isRefreshing}
        action={
          <Stack direction="row" spacing={1} alignItems="center">
            <ToggleButtonGroup
              size="small"
              exclusive
              value={statusFilter}
              onChange={(_, next) => next && setStatusFilter(next)}
            >
              {STATUS_FILTERS.map((key) => (
                <ToggleButton key={key} value={key} sx={{ textTransform: "capitalize" }}>
                  {key}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              onClick={async () => {
                try {
                  await analyticsApi.reapStaleJobs();
                  refreshJobs();
                } catch (caught) {
                  console.error("Failed to clean stuck jobs", caught);
                }
              }}
            >
              Clean stuck jobs
            </Button>
            <Button size="small" startIcon={<RefreshRoundedIcon />} onClick={() => jobs.refresh()}>
              Refresh
            </Button>
          </Stack>
        }
      >
        <AsyncBoundary
          isLoading={jobs.isLoading}
          error={jobs.error}
          onRetry={jobs.reload}
          loadingVariant="skeleton"
        >
          <Stack spacing={2}>
            <RecordTable
              columns={columns}
              rows={mergedRows}
              getRowId={(row) => row.job_id}
              initialSortBy="created_at"
              initialSortDirection="desc"
              searchable
              searchPlaceholder="Search job ID or type"
              searchKeys={["job_id", "job_type", "status"]}
              emptyMessage={
                statusFilter === "all"
                  ? "No jobs yet. Queue a full refresh above."
                  : `No ${statusFilter} jobs.`
              }
            />

            <Collapse in={Boolean(expandedJob)} unmountOnExit>
              {expandedJob ? (
                <Box sx={{ p: 2, borderRadius: 2, bgcolor: "action.hover" }}>
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                    <Typography variant="subtitle2">Job {expandedJob.job_id}</Typography>
                    <Chip
                      size="small"
                      label={expandedJob.status}
                      color={JOB_STATUS_COLOR[expandedJob.status] ?? "default"}
                    />
                  </Stack>

                  <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                    queued {formatDateTime(expandedJob.created_at)}
                    {expandedJob.started_at ? ` · started ${formatDateTime(expandedJob.started_at)}` : ""}
                    {expandedJob.completed_at
                      ? ` · finished ${formatDateTime(expandedJob.completed_at)}`
                      : ""}
                    {expandedJob.requested_by ? ` · requested by ${expandedJob.requested_by}` : ""}
                  </Typography>

                  {expandedJob.error_message ? (
                    <Alert severity="error" sx={{ mt: 1.5 }}>
                      {expandedJob.error_message}
                    </Alert>
                  ) : null}

                  {expandedJob.result ? (
                    <>
                      <Divider sx={{ mt: 1.5 }} />
                      <ResultDetail result={expandedJob.result} />
                    </>
                  ) : null}
                </Box>
              ) : null}
            </Collapse>

            <Typography variant="caption" color="text.secondary">
              Results land on the{" "}
              <Button component={RouterLink} to={ROUTES.RFM} size="small" sx={{ minWidth: 0, p: 0 }}>
                RFM
              </Button>{" "}
              and{" "}
              <Button component={RouterLink} to={ROUTES.SEGMENTS} size="small" sx={{ minWidth: 0, p: 0 }}>
                Segments
              </Button>{" "}
              pages once a job completes.
            </Typography>
          </Stack>
        </AsyncBoundary>
      </SectionCard>
    </Stack>
  );
};

export default JobsPage;

/**
 * Dependency status as a single chip, with the detail behind a click.
 *
 * A status light in the top bar is only worth having if it distinguishes "the
 * database is down" from "you chose not to run Redis". Both leave parts of the UI
 * empty, but only one is a problem, and an operator who cannot tell them apart
 * learns to ignore the light entirely.
 *
 * The tone/label decision is made by `summariseReadiness` in `app/readiness.js`, not
 * here, so it can be asserted without rendering anything.
 */

import { useState } from "react";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import IconButton from "@mui/material/IconButton";
import Popover from "@mui/material/Popover";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import CircleRoundedIcon from "@mui/icons-material/CircleRounded";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";

import { useSystemStatus } from "../../app/SystemStatusProvider.jsx";
import { dependencyRows } from "../../app/readiness.js";
import { DEPENDENCY_COLOR, formatDecimal } from "../../utils/format.js";

const TONE_COLOR = {
  success: "success",
  warning: "warning",
  error: "error",
  info: "info",
  default: "default",
};

const SystemStatusChip = () => {
  const { readiness, summary, isChecking, refresh } = useSystemStatus();
  const [anchor, setAnchor] = useState(null);

  const rows = dependencyRows(readiness);

  return (
    <>
      <Tooltip title={summary.detail ?? "Dependency status"}>
        <Chip
          size="small"
          variant="outlined"
          color={TONE_COLOR[summary.tone] ?? "default"}
          onClick={(event) => setAnchor(event.currentTarget)}
          icon={
            isChecking && !readiness ? (
              <CircularProgress size={12} sx={{ ml: 1 }} />
            ) : (
              <CircleRoundedIcon sx={{ fontSize: 10 }} />
            )
          }
          label={summary.label}
          sx={{ cursor: "pointer" }}
        />
      </Tooltip>

      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { p: 2, minWidth: 300, borderRadius: 3 } } }}
      >
        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
          <Typography variant="subtitle2" fontWeight={800}>
            System status
          </Typography>
          <IconButton size="small" onClick={refresh} aria-label="Re-check dependencies">
            <RefreshRoundedIcon fontSize="small" />
          </IconButton>
        </Stack>

        {summary.detail ? (
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
            {summary.detail}
          </Typography>
        ) : null}

        <Divider sx={{ mb: 1.5 }} />

        {rows.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No dependency information yet.
          </Typography>
        ) : (
          <Stack spacing={1.25}>
            {rows.map((row) => (
              <Stack key={row.key} direction="row" alignItems="center" justifyContent="space-between" spacing={2}>
                <Box>
                  <Typography variant="body2" fontWeight={700} textTransform="capitalize">
                    {row.label}
                  </Typography>
                  {/* `transport` distinguishes an inline event bus from a real
                      broker, which is the difference between a job that runs
                      synchronously and one that runs on a consumer. */}
                  {row.transport ? (
                    <Typography variant="caption" color="text.secondary">
                      {row.transport}
                      {row.target ? ` · ${row.target}` : ""}
                    </Typography>
                  ) : null}
                </Box>
                <Stack direction="row" alignItems="center" spacing={1}>
                  {/* Zero is a real latency; only a genuine absence is hidden. */}
                  {row.latencyMs !== null && row.latencyMs !== undefined ? (
                    <Typography variant="caption" color="text.secondary">
                      {formatDecimal(row.latencyMs)} ms
                    </Typography>
                  ) : null}
                  <Chip size="small" label={row.status} color={DEPENDENCY_COLOR[row.status] ?? "default"} />
                </Stack>
              </Stack>
            ))}
          </Stack>
        )}
      </Popover>
    </>
  );
};

export default SystemStatusChip;

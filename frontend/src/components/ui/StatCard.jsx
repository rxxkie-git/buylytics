/**
 * A single headline figure.
 *
 * Deliberately has no trend indicator. The Phase 1 `KpiCard` defaulted its delta
 * to `"+0.0%"` when none was supplied and rendered it with an upward arrow in
 * green, so every card claimed a measured period-over-period change that nothing
 * had computed. A dashboard that invents a number is worse than one that omits it,
 * because the invented one gets acted on. When the API grows a real comparison
 * figure this component can take a `delta` prop — until then there is nothing to
 * show.
 *
 * Formatting is passed in rather than guessed. The old card decided a value was
 * currency by testing whether its label contained "revenue" or "value", which
 * silently rendered "Average order value" correctly and "Customer lifetime value
 * count" as money.
 *
 * `format` takes either a key from `FORMATS` or a function. The key form exists so
 * that every currency figure in the app goes through the same formatter — a caller
 * writing `format="currency"` cannot accidentally pass a percent formatter — while
 * the function form stays available for a one-off.
 */

import { alpha, Box, Paper, Skeleton, Stack, Tooltip, Typography } from "@mui/material";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";

import {
  formatCompact,
  formatCount,
  formatCurrency,
  formatDecimal,
  formatPercent,
  formatShare,
} from "../../utils/format.js";

const TONES = {
  primary: "#0F766E",
  info: "#2563EB",
  success: "#16A34A",
  warning: "#EA580C",
  danger: "#DC2626",
  neutral: "#64748B",
};

const FORMATS = {
  currency: formatCurrency,
  count: formatCount,
  decimal: formatDecimal,
  compact: formatCompact,
  share: formatShare,
  percent: formatPercent,
  text: (raw) => raw,
};

const resolveFormat = (format) => {
  if (typeof format === "function") return format;
  return FORMATS[format] ?? FORMATS.text;
};

const StatCard = ({
  label,
  value,
  format = "text",
  caption,
  hint,
  icon,
  tone = "primary",
  isLoading = false,
  /** Tighter padding and a smaller figure, for a card inside another card. */
  compact = false,
}) => {
  const accent = TONES[tone] ?? TONES.primary;
  const formatValue = resolveFormat(format);

  // `0` is a real answer and must render as "0"; only a genuine absence shows a
  // dash. `value || "—"` would turn zero revenue into "no data".
  const isMissing = value === null || value === undefined;

  return (
    <Paper variant="outlined" sx={{ p: compact ? 1.75 : 2.5, height: "100%", borderRadius: 3 }}>
      <Stack direction="row" spacing={2} justifyContent="space-between" alignItems="flex-start">
        <Box sx={{ minWidth: 0 }}>
          <Stack direction="row" spacing={0.75} alignItems="center">
            <Typography variant="body2" color="text.secondary" noWrap>
              {label}
            </Typography>
            {hint ? (
              <Tooltip title={hint} arrow>
                <InfoOutlinedIcon sx={{ fontSize: 15, color: "text.disabled" }} />
              </Tooltip>
            ) : null}
          </Stack>

          {isLoading ? (
            <Skeleton width={96} height={compact ? 32 : 44} sx={{ mt: 0.5 }} />
          ) : (
            <Typography
              variant={compact ? "h6" : "h4"}
              sx={{ mt: 0.5, fontVariantNumeric: "tabular-nums" }}
              color={tone === "primary" ? "text.primary" : accent}
            >
              {isMissing ? "—" : formatValue(value)}
            </Typography>
          )}

          {caption ? (
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.75 }}>
              {caption}
            </Typography>
          ) : null}
        </Box>

        {icon ? (
          <Box
            sx={{
              flexShrink: 0,
              width: 44,
              height: 44,
              borderRadius: 2,
              display: "grid",
              placeItems: "center",
              color: accent,
              backgroundColor: alpha(accent, 0.12),
            }}
          >
            {icon}
          </Box>
        ) : null}
      </Stack>
    </Paper>
  );
};

export default StatCard;

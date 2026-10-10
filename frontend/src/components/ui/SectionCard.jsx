/**
 * A titled card for a chart or table.
 *
 * Exists so every section on every page has the heading in the same place, and so
 * the "refreshing" indicator is consistent — a subtle bar rather than replacing the
 * content with a spinner, because a dashboard that blanks itself every time it polls
 * is unusable.
 */

import Box from "@mui/material/Box";
import LinearProgress from "@mui/material/LinearProgress";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

const SectionCard = ({ title, subtitle, action, isRefreshing = false, children, sx = {} }) => (
  <Paper variant="outlined" sx={{ borderRadius: 3, overflow: "hidden", height: "100%", ...sx }}>
    {isRefreshing ? <LinearProgress sx={{ height: 2 }} /> : <Box sx={{ height: 2 }} />}

    <Box sx={{ p: { xs: 2, md: 2.5 } }}>
      {title || action ? (
        <Stack
          direction="row"
          alignItems="flex-start"
          justifyContent="space-between"
          spacing={2}
          sx={{ mb: subtitle ? 0.5 : 2 }}
        >
          <Box sx={{ minWidth: 0 }}>
            {title ? (
              <Typography variant="subtitle1" fontWeight={800}>
                {title}
              </Typography>
            ) : null}
            {subtitle ? (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                {subtitle}
              </Typography>
            ) : null}
          </Box>
          {action ? <Box sx={{ flexShrink: 0 }}>{action}</Box> : null}
        </Stack>
      ) : null}

      <Box sx={{ mt: title || action ? 2 : 0 }}>{children}</Box>
    </Box>
  </Paper>
);

export default SectionCard;

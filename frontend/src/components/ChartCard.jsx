import { Box, Paper, Stack, Typography } from "@mui/material";

// Standardized wrapper for Recharts visualizations and section-level charts.
const ChartCard = ({ title, subtitle, action, height = 320, children }) => (
  <Paper sx={{ p: 2.5, height: "100%" }}>
    <Stack direction="row" justifyContent="space-between" spacing={1.5} alignItems="flex-start">
      <Box>
        <Typography variant="h6">{title}</Typography>
        {subtitle ? (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.6 }}>
            {subtitle}
          </Typography>
        ) : null}
      </Box>
      {action}
    </Stack>

    <Box sx={{ mt: 2.5, height }}>{children}</Box>
  </Paper>
);

export default ChartCard;

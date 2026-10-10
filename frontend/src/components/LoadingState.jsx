import { Box, CircularProgress, Paper, Typography } from "@mui/material";

// Shared loading placeholder so async screens feel consistent.
const LoadingState = ({ title = "Loading analytics data...", description = "Preparing your dashboard view." }) => (
  <Paper
    sx={{
      minHeight: 280,
      display: "grid",
      placeItems: "center",
      p: 3,
      textAlign: "center",
    }}
  >
    <Box>
      <CircularProgress size={46} />
      <Typography variant="h6" sx={{ mt: 2 }}>
        {title}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
        {description}
      </Typography>
    </Box>
  </Paper>
);

export default LoadingState;

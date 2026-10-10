import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";
import WarningAmberRoundedIcon from "@mui/icons-material/WarningAmberRounded";
import { Box, Button, Paper, Typography } from "@mui/material";

// Shared error card for API-backed views.
const ErrorState = ({ message, onRetry }) => (
  <Paper
    sx={{
      minHeight: 280,
      display: "grid",
      placeItems: "center",
      p: 3,
      textAlign: "center",
    }}
  >
    <Box sx={{ maxWidth: 480 }}>
      <WarningAmberRoundedIcon color="warning" sx={{ fontSize: 52 }} />
      <Typography variant="h6" sx={{ mt: 1.5 }}>
        Something needs attention
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
        {message}
      </Typography>
      <Button variant="contained" startIcon={<RefreshRoundedIcon />} sx={{ mt: 2.5 }} onClick={onRetry}>
        Retry
      </Button>
    </Box>
  </Paper>
);

export default ErrorState;

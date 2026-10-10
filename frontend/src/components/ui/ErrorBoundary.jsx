/**
 * Catches a render error in one page instead of losing the whole application.
 *
 * Without a boundary anywhere in the tree, React 18 unmounts everything when a
 * render throws, and the user sees a blank white page with the real error only in
 * the console. That is exactly how the dashboard failed: it dereferenced
 * `summary.data.revenue` while the request was still in flight, threw a
 * TypeError, and took the entire app down with it.
 *
 * The underlying dashboard bug is fixed at its source in `DashboardPage`. This
 * component exists so that the *class* of bug degrades into one broken panel with
 * a Try again button, rather than a white screen — a page that renders nothing is
 * indistinguishable from a dead server, and someone will spend an hour on the
 * wrong problem.
 *
 * Placed around the shell's `<Outlet />`, so the sidebar and top bar survive and
 * the user can navigate away from the page that failed.
 *
 * Must be a class component: `componentDidCatch` and `getDerivedStateFromError`
 * have no hook equivalent.
 */

import { Component } from "react";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";

class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // The console is where a developer will look, and the stack is the only
    // thing that identifies which component threw.
    console.error("Unhandled render error", error, info?.componentStack);
  }

  render() {
    const { error } = this.state;

    if (!error) return this.props.children;

    return (
      <Paper variant="outlined" sx={{ borderRadius: 3, p: 4 }}>
        <Stack spacing={2} alignItems="flex-start">
          <Alert severity="error" sx={{ width: "100%" }}>
            <AlertTitle>This page could not be displayed</AlertTitle>
            Something went wrong while rendering it. Your data is unaffected — nothing on this
            page writes anything.
          </Alert>

          <Box>
            <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }}>
              {error.message || String(error)}
            </Typography>
          </Box>

          <Button
            variant="contained"
            startIcon={<RefreshRoundedIcon />}
            onClick={() => this.setState({ error: null })}
          >
            Try again
          </Button>
        </Stack>
      </Paper>
    );
  }
}

export default ErrorBoundary;

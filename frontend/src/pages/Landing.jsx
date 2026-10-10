import ArrowOutwardRoundedIcon from "@mui/icons-material/ArrowOutwardRounded";
import AutoGraphRoundedIcon from "@mui/icons-material/AutoGraphRounded";
import BubbleChartRoundedIcon from "@mui/icons-material/BubbleChartRounded";
import CloudUploadRoundedIcon from "@mui/icons-material/CloudUploadRounded";
import InsightsRoundedIcon from "@mui/icons-material/InsightsRounded";
import InventoryRoundedIcon from "@mui/icons-material/InventoryRounded";
import {
  alpha,
  AppBar,
  Box,
  Button,
  Chip,
  Container,
  Grid,
  Paper,
  Stack,
  Toolbar,
  Typography,
  useTheme,
} from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import { SignInButton, useAuth } from "@clerk/clerk-react";
import heroGraphic from "../assets/analytics-hero.svg";
import { landingQuickLinks } from "../utils/navigation";

const highlights = [
  {
    title: "RFM-Driven Profiling",
    description:
      "Recency, frequency, and monetary behavior are translated into clean, comparable customer signals.",
    icon: AutoGraphRoundedIcon,
  },
  {
    title: "K-Means Clustering",
    description:
      "Unsupervised learning groups customers into meaningful cohorts that support strategy and targeting.",
    icon: BubbleChartRoundedIcon,
  },
  {
    title: "Actionable Recommendations",
    description:
      "The interface connects model output to marketing, retention, and growth opportunities.",
    icon: InsightsRoundedIcon,
  },
];

const STAT_ACCENTS = [
  { color: "#14B8A6", glow: "rgba(20,184,166,0.25)" },
  { color: "#2563EB", glow: "rgba(37,99,235,0.25)" },
  { color: "#7C3AED", glow: "rgba(124,58,237,0.25)" },
];

const landingStats = [
  { value: "4", label: "Customer clusters surfaced" },
  { value: "0.68", label: "Silhouette score in mock results" },
  { value: "48", label: "Customer records in demo dataset" },
];

// Public-facing landing page introduces the project before the analytics shell takes over.
const Landing = () => {
  const theme = useTheme();
  const { isSignedIn } = useAuth();

  return (
    <Box
      sx={{
        minHeight: "100vh",
        background:
          "radial-gradient(circle at top left, rgba(20,184,166,0.16), transparent 30%), radial-gradient(circle at top right, rgba(37,99,235,0.16), transparent 30%), linear-gradient(180deg, #07111F 0%, #081321 100%)",
      }}
    >
      <Box sx={{ pb: { xs: 8, md: 12 } }}>
        <AppBar
          position="static"
          elevation={0}
          sx={{ backgroundColor: "transparent", boxShadow: "none" }}
        >
          <Toolbar sx={{ px: { xs: 2, md: 4 } }}>
            <Stack
              direction="row"
              spacing={1.4}
              alignItems="center"
              sx={{ flexGrow: 1 }}
            >
              <Box
                sx={{
                  width: 42,
                  height: 42,
                  borderRadius: "14px",
                  display: "grid",
                  placeItems: "center",
                  background: "linear-gradient(135deg, #14B8A6, #2563EB)",
                  color: "#fff",
                }}
              >
                <AutoGraphRoundedIcon />
              </Box>
              <Box>
                <Typography variant="h6" sx={{ color: "#E7EEF8" }}>
                  Customer Buying Pattern Analysis
                </Typography>
                <Typography variant="body2" sx={{ color: "rgba(231,238,248,0.68)" }}>
                  Analytics Dashboard
                </Typography>
              </Box>
            </Stack>

            <Stack direction="row" spacing={1.25}>
              <Button
                onClick={() => {
                  document.getElementById("features-section")?.scrollIntoView({ behavior: "smooth" });
                }}
                sx={{ color: "#E7EEF8", display: { xs: "none", md: "inline-flex" } }}
              >
                About
              </Button>
              {isSignedIn ? (
                <Button component={RouterLink} to="/dashboard" variant="contained">
                  Open Dashboard
                </Button>
              ) : (
                <SignInButton mode="modal">
                  <Button variant="contained">Sign In</Button>
                </SignInButton>
              )}
            </Stack>
          </Toolbar>
        </AppBar>

        <Container maxWidth="xl">
          <Grid
            container
            spacing={4}
            alignItems="center"
            sx={{ pt: { xs: 4, md: 8 } }}
          >
            <Grid item xs={12} md={6}>
              <Chip
                label="React + Material UI + Recharts"
                sx={{
                  color: "#E7EEF8",
                  backgroundColor: "rgba(255,255,255,0.08)",
                  border: "1px solid rgba(148, 163, 184, 0.14)",
                }}
              />

              <Typography
                variant="h1"
                sx={{
                  mt: 2.5,
                  color: "#F8FBFF",
                  fontSize: { xs: "2.6rem", md: "4.1rem" },
                  lineHeight: 1.04,
                  maxWidth: 640,
                }}
              >
                Discover buying patterns with a modern ML analytics workspace.
              </Typography>

              <Typography
                variant="h6"
                sx={{
                  mt: 2.5,
                  color: "rgba(231,238,248,0.78)",
                  fontWeight: 500,
                  maxWidth: 640,
                  lineHeight: 1.7,
                }}
              >
                This frontend presents RFM analysis, K-Means clustering, customer segmentation,
                and decision-ready insights in a clean dashboard experience.
              </Typography>

              <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} sx={{ mt: 3.5 }}>
                {isSignedIn ? (
                  <Button
                    component={RouterLink}
                    to="/dashboard"
                    variant="contained"
                    size="large"
                    endIcon={<ArrowOutwardRoundedIcon />}
                  >
                    Explore Analytics
                  </Button>
                ) : (
                  <SignInButton mode="modal">
                    <Button
                      variant="contained"
                      size="large"
                      endIcon={<ArrowOutwardRoundedIcon />}
                    >
                      Explore Analytics
                    </Button>
                  </SignInButton>
                )}
                <Button
                  component={RouterLink}
                  to="/inventory"
                  variant="contained"
                  size="large"
                  startIcon={<InventoryRoundedIcon />}
                  sx={{
                    background: "linear-gradient(135deg, #10B981 0%, #059669 100%)",
                    "&:hover": {
                      background: "linear-gradient(135deg, #059669 0%, #047857 100%)",
                    },
                  }}
                >
                  Manage Inventory
                </Button>
                <Button
                  component={RouterLink}
                  to="/upload"
                  variant="outlined"
                  size="large"
                  startIcon={<CloudUploadRoundedIcon />}
                  sx={{ color: "#E7EEF8", borderColor: "rgba(231,238,248,0.28)" }}
                >
                  Upload Dataset
                </Button>
              </Stack>

              <Stack
                direction="row"
                spacing={1.25}
                flexWrap="wrap"
                useFlexGap
                sx={{ mt: 3.5 }}
              >
                {landingStats.map((stat, i) => {
                  const accent = STAT_ACCENTS[i];
                  return (
                    <Paper
                      key={stat.label}
                      sx={{
                        minWidth: 158,
                        px: 2.2,
                        py: 2,
                        borderRadius: "16px",
                        color: "#F8FBFF",
                        background: `linear-gradient(145deg, rgba(0,0,0,0.72), rgba(0,0,0,0.55))`,
                        border: `1px solid ${accent.color}44`,
                        borderTop: `3px solid ${accent.color}`,
                        boxShadow: `0 4px 24px ${accent.glow}`,
                        backdropFilter: "blur(12px)",
                        transition: "transform 0.22s ease, box-shadow 0.22s ease",
                        "&:hover": {
                          transform: "translateY(-5px)",
                          boxShadow: `0 12px 36px ${accent.glow}, 0 0 0 1px ${accent.color}66`,
                        },
                      }}
                    >
                      <Typography
                        variant="h5"
                        sx={{ fontWeight: 800, color: accent.color }}
                      >
                        {stat.value}
                      </Typography>
                      <Typography
                        variant="body2"
                        sx={{ color: "rgba(231,238,248,0.68)", mt: 0.5, lineHeight: 1.5 }}
                      >
                        {stat.label}
                      </Typography>
                    </Paper>
                  );
                })}
              </Stack>
            </Grid>

            <Grid item xs={12} md={6}>
              <Box
                component="img"
                src={heroGraphic}
                alt="Analytics dashboard illustration"
                className="float-card"
                sx={{
                  width: "100%",
                  maxWidth: 700,
                  display: "block",
                  mx: "auto",
                  filter: "drop-shadow(0 34px 60px rgba(8,17,29,0.32))",
                }}
              />
            </Grid>
          </Grid>
          <Grid id="features-section" container spacing={3} sx={{ mt: { xs: 4, md: 6 } }}>
            {highlights.map((item) => {
              const Icon = item.icon;

              return (
                <Grid item xs={12} md={4} key={item.title}>
                  <Paper
                    className="fade-up"
                    sx={{
                      p: 3,
                      height: "100%",
                      background: "#000000",
                      border: "1px solid rgba(148,163,184,0.12)",
                      cursor: "default",
                      transition: "transform 0.25s ease, box-shadow 0.25s ease, background 0.25s ease, border-color 0.25s ease",
                      "&:hover": {
                        transform: "translateY(-6px)",
                        background: "linear-gradient(145deg, rgba(20,184,166,0.12), rgba(37,99,235,0.10))",
                        borderColor: "rgba(20,184,166,0.45)",
                        boxShadow: "0 16px 48px rgba(0,0,0,0.55), 0 0 0 1px rgba(20,184,166,0.18), 0 0 24px rgba(20,184,166,0.12)",
                      },
                    }}
                  >
                    <Box
                      sx={{
                        width: 54,
                        height: 54,
                        borderRadius: "18px",
                        display: "grid",
                        placeItems: "center",
                        color: "primary.main",
                        backgroundColor: alpha(theme.palette.primary.main, 0.12),
                      }}
                    >
                      <Icon />
                    </Box>
                    <Typography variant="h6" sx={{ mt: 2, color: "#F8FBFF" }}>
                      {item.title}
                    </Typography>
                    <Typography
                      variant="body2"
                      sx={{ mt: 1.1, lineHeight: 1.8, color: "rgba(231,238,248,0.65)" }}
                    >
                      {item.description}
                    </Typography>
                  </Paper>
                </Grid>
              );
            })}
          </Grid>
        </Container>
      </Box>

      <Container maxWidth="xl" sx={{ mt: { xs: 6, md: 8 }, pb: { xs: 6, md: 10 } }}>
        <Typography
          className="fade-up"
          variant="overline"
          sx={{ color: "#14B8A6", fontWeight: 800, letterSpacing: 1.5 }}
        >
          QUICK NAVIGATION
        </Typography>
        <Typography variant="h4" sx={{ mt: 1, color: "#F8FAFC" }}>
          Move from upload to insights without friction
        </Typography>
        <Typography
          variant="body1"
          sx={{ mt: 1.2, maxWidth: 720, color: "rgba(248, 250, 252, 0.65)" }}
        >
          Each page is already wired like a real analytics tool, including dataset preview,
          clustering outputs, segment drill-down, and recommendation panels.
        </Typography>

        <Grid container spacing={2.2} sx={{ mt: 3 }}>
          {landingQuickLinks.slice(1).map((item) => {
            const Icon = item.icon;

            return (
              <Grid item xs={12} sm={6} md={4} key={item.path}>
                <Box
                  component={RouterLink}
                  to={item.path}
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 2,
                    p: 2.5,
                    borderRadius: "16px",
                    backgroundColor: "#0B1829",
                    border: "1px solid rgba(255,255,255,0.07)",
                    textDecoration: "none",
                    cursor: "pointer",
                    transition: "all 0.22s ease",
                    "&:hover": {
                      transform: "translateY(-4px)",
                      backgroundColor: "#0F2040",
                      borderColor: "rgba(20,184,166,0.4)",
                      boxShadow: "0 12px 40px rgba(0,0,0,0.45), 0 0 0 1px rgba(20,184,166,0.15)",
                    },
                  }}
                >
                  <Box
                    sx={{
                      width: 48,
                      height: 48,
                      borderRadius: "14px",
                      display: "grid",
                      placeItems: "center",
                      flexShrink: 0,
                      color: "#14B8A6",
                      backgroundColor: "rgba(20, 184, 166, 0.12)",
                      border: "1px solid rgba(20,184,166,0.2)",
                    }}
                  >
                    <Icon sx={{ fontSize: 22 }} />
                  </Box>
                  <Box>
                    <Typography sx={{ fontWeight: 700, fontSize: "0.95rem", color: "#F8FAFC", lineHeight: 1.3 }}>
                      {item.label}
                    </Typography>
                    <Typography sx={{ fontSize: "0.8rem", color: "rgba(248,250,252,0.55)", mt: 0.3 }}>
                      {item.description}
                    </Typography>
                  </Box>
                </Box>
              </Grid>
            );
          })}
        </Grid>
      </Container>
    </Box>
  );
};

export default Landing;

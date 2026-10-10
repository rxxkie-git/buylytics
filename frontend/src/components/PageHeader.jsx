import ArrowForwardRoundedIcon from "@mui/icons-material/ArrowForwardRounded";
import { alpha, Box, Button, Chip, Paper, Stack, Typography, useTheme } from "@mui/material";

// Reusable hero-like section header for every page.
const PageHeader = ({ eyebrow, title, subtitle, chipLabel, actionLabel, onActionClick }) => {
  const theme = useTheme();

  return (
    <Paper
      sx={{
        py: { xs: 3, md: 4 },
        px: { xs: 5, md: 7.5 },
        mx: { xs: -2, md: -3.5 },
        mb: 4,
        overflow: "hidden",
        position: "relative",
        borderRadius: 0,
        borderLeft: "none",
        borderRight: "none",
        background:
          theme.palette.mode === "light"
            ? `linear-gradient(135deg, #115E59 0%, #0F766E 100%)`
            : `linear-gradient(135deg, #042F2E 0%, #115E59 100%)`,
        color: "#FFFFFF",
        boxShadow: 
          theme.palette.mode === "light" 
            ? "0 10px 30px rgba(15, 118, 110, 0.15)" 
            : "0 10px 30px rgba(0, 0, 0, 0.3)",
        borderBottom: "1px solid rgba(255, 255, 255, 0.05)",
        borderTop: "1px solid rgba(255, 255, 255, 0.05)",
      }}
    >
      {/* Decorative Glow Elements */}
      <Box
        sx={{
          position: "absolute",
          width: 300,
          height: 300,
          borderRadius: "50%",
          right: -50,
          top: -100,
          background: `radial-gradient(circle, ${alpha("#34D399", 0.25)} 0%, transparent 70%)`,
          zIndex: 0,
        }}
      />
      <Box
        sx={{
          position: "absolute",
          width: 250,
          height: 250,
          borderRadius: "50%",
          left: -50,
          bottom: -80,
          background: `radial-gradient(circle, ${alpha("#2563EB", 0.2)} 0%, transparent 70%)`,
          zIndex: 0,
        }}
      />

      <Stack
        direction={{ xs: "column", md: "row" }}
        spacing={3}
        alignItems={{ xs: "flex-start", md: "center" }}
        justifyContent="space-between"
        sx={{ position: "relative", zIndex: 1 }}
      >
        <Box>
          <Typography
            variant="overline"
            sx={{ color: "#6EE7B7", fontWeight: 800, letterSpacing: 2 }}
          >
            {eyebrow}
          </Typography>
          <Typography variant="h4" sx={{ mt: 1, mb: 1.5, fontWeight: 800, color: "#FFFFFF" }}>
            {title}
          </Typography>
          <Typography variant="body1" sx={{ color: "rgba(255, 255, 255, 0.85)", maxWidth: 760, fontSize: "1.05rem", lineHeight: 1.6 }}>
            {subtitle}
          </Typography>
        </Box>

        <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
          {chipLabel ? (
            <Chip
              label={chipLabel}
              sx={{
                backgroundColor: "rgba(255, 255, 255, 0.1)",
                backdropFilter: "blur(10px)",
                color: "#FFFFFF",
                fontWeight: 600,
                border: "1px solid rgba(255, 255, 255, 0.2)",
                px: 1,
              }}
            />
          ) : null}

          {actionLabel ? (
            <Button
              variant="contained"
              endIcon={<ArrowForwardRoundedIcon />}
              onClick={onActionClick}
              sx={{
                backgroundColor: "#FFFFFF",
                color: "#0F766E",
                fontWeight: 700,
                borderRadius: 2,
                px: 3,
                '&:hover': {
                  backgroundColor: "#F0FDF4",
                  transform: "translateY(-1px)",
                  boxShadow: "0 8px 16px rgba(0,0,0,0.1)",
                },
                transition: "all 0.2s ease-in-out",
              }}
            >
              {actionLabel}
            </Button>
          ) : null}
        </Stack>
      </Stack>
    </Paper>
  );
};

export default PageHeader;

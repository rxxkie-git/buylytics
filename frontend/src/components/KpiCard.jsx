import NorthEastRoundedIcon from "@mui/icons-material/NorthEastRounded";
import SouthRoundedIcon from "@mui/icons-material/SouthRounded";
import { alpha, Box, Chip, Paper, Stack, Typography, useTheme } from "@mui/material";
import { formatCurrency, formatNumber } from "../utils/formatters";

const toneMap = {
  success: "#16A34A",
  primary: "#0F766E",
  secondary: "#2563EB",
  warning: "#F97316",
};

// KPI card used on dashboard and summary sections.
const KpiCard = ({ label, value, description, delta, tone = "primary", icon }) => {
  const theme = useTheme();
  const accent = toneMap[tone] || toneMap.primary;
  const isCurrency = label.toLowerCase().includes("revenue") || label.toLowerCase().includes("value");
  const parsedDelta = `${delta || "+0.0%"}`;
  const isPositive = !parsedDelta.startsWith("-");

  return (
    <Paper
      sx={{
        p: 2.4,
        height: "100%",
        background:
          theme.palette.mode === "light"
            ? "linear-gradient(180deg, rgba(255,255,255,0.9), rgba(248,251,255,0.96))"
            : "linear-gradient(180deg, rgba(14,26,47,0.94), rgba(10,18,35,0.98))",
      }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Box>
          <Typography variant="body2" color="text.secondary">
            {label}
          </Typography>
          <Typography variant="h4" sx={{ mt: 1 }}>
            {isCurrency ? formatCurrency(value) : formatNumber(value)}
          </Typography>
        </Box>

        <Box
          sx={{
            width: 54,
            height: 54,
            borderRadius: "18px",
            display: "grid",
            placeItems: "center",
            color: accent,
            backgroundColor: alpha(accent, 0.14),
          }}
        >
          {icon}
        </Box>
      </Stack>

      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mt: 2 }}>
        <Chip
          icon={isPositive ? <NorthEastRoundedIcon /> : <SouthRoundedIcon />}
          label={parsedDelta}
          sx={{
            backgroundColor: alpha(isPositive ? "#16A34A" : "#DC2626", 0.12),
            color: isPositive ? "#15803D" : "#B91C1C",
          }}
        />
        <Typography variant="caption" color="text.secondary" sx={{ maxWidth: 200, textAlign: "right" }}>
          {description}
        </Typography>
      </Stack>
    </Paper>
  );
};

export default KpiCard;

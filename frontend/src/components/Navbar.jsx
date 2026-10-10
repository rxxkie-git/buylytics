import DarkModeRoundedIcon from "@mui/icons-material/DarkModeRounded";
import LightModeRoundedIcon from "@mui/icons-material/LightModeRounded";
import MenuRoundedIcon from "@mui/icons-material/MenuRounded";
import RocketLaunchRoundedIcon from "@mui/icons-material/RocketLaunchRounded";
import TuneRoundedIcon from "@mui/icons-material/TuneRounded";
import {
  alpha,
  AppBar,
  Box,
  Button,
  Chip,
  IconButton,
  Stack,
  Toolbar,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { Link as RouterLink, useLocation } from "react-router-dom";
import { UserButton } from "@clerk/clerk-react";
import { getDateLabel } from "../utils/formatters";
import { getRouteDetails } from "../utils/navigation";

// Top navigation keeps context visible and exposes the theme toggle on every page.
const Navbar = ({ drawerWidth, mode, onMenuClick, onToggleTheme }) => {
  const theme = useTheme();
  const location = useLocation();
  const routeDetails = getRouteDetails(location.pathname);

  return (
    <AppBar
      position="fixed"
      color="transparent"
      elevation={0}
      sx={{
        width: { sm: `calc(100% - ${drawerWidth}px)` },
        ml: { sm: `${drawerWidth}px` },
        backgroundColor: alpha(theme.palette.background.default, 0.88),
        backdropFilter: "blur(18px)",
        borderBottom: "1px solid",
        borderColor: "divider",
        color: "text.primary", // Explicitly set text color just in case
      }}
    >
      <Toolbar
        sx={{
          minHeight: { xs: "84px", md: "96px" },
          px: { xs: 2, md: 3.5 },
          gap: 2,
        }}
      >
        <IconButton
          color="inherit"
          onClick={onMenuClick}
          edge="start"
          sx={{ display: { sm: "none" } }}
        >
          <MenuRoundedIcon />
        </IconButton>

        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="h5" sx={{ fontSize: { xs: "1.2rem", md: "1.45rem" } }}>
            {routeDetails.title}
          </Typography>
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ mt: 0.5, maxWidth: 720, display: { xs: "none", md: "block" } }}
          >
            {routeDetails.subtitle}
          </Typography>
        </Box>

        <Stack direction="row" spacing={1.25} alignItems="center">
          <Chip
            icon={<TuneRoundedIcon />}
            label={`Mock API - ${getDateLabel()}`}
            sx={{
              display: { xs: "none", lg: "inline-flex" },
              backgroundColor: alpha(theme.palette.primary.main, 0.12),
            }}
          />

          <Button
            component={RouterLink}
            to="/upload"
            variant="contained"
            startIcon={<RocketLaunchRoundedIcon />}
            sx={{ display: { xs: "none", md: "inline-flex" } }}
          >
            New Upload
          </Button>

          <Tooltip title={mode === "light" ? "Switch to dark mode" : "Switch to light mode"}>
            <IconButton
              color="inherit"
              onClick={onToggleTheme}
              sx={{
                backgroundColor: alpha(theme.palette.common.white, mode === "light" ? 0.78 : 0.04),
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              {mode === "light" ? <DarkModeRoundedIcon /> : <LightModeRoundedIcon />}
            </IconButton>
          </Tooltip>
          <UserButton afterSignOutUrl="/" />
        </Stack>
      </Toolbar>
    </AppBar>
  );
};

export default Navbar;



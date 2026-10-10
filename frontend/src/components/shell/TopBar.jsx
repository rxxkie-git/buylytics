/**
 * Top bar: page title, system status, theme toggle, account menu.
 *
 * The title comes from the route rather than being passed down by each page, so a
 * new page cannot ship with the previous page's heading — a small thing, but it was
 * a real inconsistency in Phase 1 where three pages hardcoded "Dashboard".
 */

import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import AppBar from "@mui/material/AppBar";
import Box from "@mui/material/Box";
import Divider from "@mui/material/Divider";
import IconButton from "@mui/material/IconButton";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Toolbar from "@mui/material/Toolbar";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import Avatar from "@mui/material/Avatar";
import ListItemIcon from "@mui/material/ListItemIcon";
import DarkModeRoundedIcon from "@mui/icons-material/DarkModeRounded";
import LightModeRoundedIcon from "@mui/icons-material/LightModeRounded";
import LogoutRoundedIcon from "@mui/icons-material/LogoutRounded";
import MenuRoundedIcon from "@mui/icons-material/MenuRounded";
import SettingsRoundedIcon from "@mui/icons-material/SettingsRounded";
import { useClerk, useUser } from "@clerk/clerk-react";

import { ROUTES, getPageMeta } from "../../app/navigation.js";
import SystemStatusChip from "./SystemStatusChip.jsx";

const TopBar = ({ onOpenNav, mode, onToggleMode }) => {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { user } = useUser();
  const { signOut } = useClerk();
  const [anchor, setAnchor] = useState(null);

  const meta = getPageMeta(pathname);
  const initial = (user?.firstName || user?.primaryEmailAddress?.emailAddress || "?")
    .charAt(0)
    .toUpperCase();

  return (
    <AppBar
      position="sticky"
      elevation={0}
      color="transparent"
      sx={{
        backdropFilter: "blur(12px)",
        bgcolor: (theme) =>
          theme.palette.mode === "light" ? "rgba(255,255,255,0.78)" : "rgba(7,17,31,0.78)",
        borderBottom: (theme) => `1px solid ${theme.palette.divider}`,
      }}
    >
      <Toolbar sx={{ gap: 1.5, minHeight: { xs: 64, md: 72 } }}>
        <IconButton
          edge="start"
          onClick={onOpenNav}
          sx={{ display: { md: "none" } }}
          aria-label="Open navigation"
        >
          <MenuRoundedIcon />
        </IconButton>

        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography variant="h6" noWrap fontWeight={800}>
            {meta.title}
          </Typography>
          {meta.subtitle ? (
            <Typography variant="caption" color="text.secondary" noWrap sx={{ display: { xs: "none", sm: "block" } }}>
              {meta.subtitle}
            </Typography>
          ) : null}
        </Box>

        <Stack direction="row" spacing={1} alignItems="center">
          <Box sx={{ display: { xs: "none", sm: "block" } }}>
            <SystemStatusChip />
          </Box>

          <Tooltip title={mode === "light" ? "Switch to dark" : "Switch to light"}>
            <IconButton onClick={onToggleMode} aria-label="Toggle colour mode">
              {mode === "light" ? <DarkModeRoundedIcon /> : <LightModeRoundedIcon />}
            </IconButton>
          </Tooltip>

          <Tooltip title={user?.primaryEmailAddress?.emailAddress ?? "Account"}>
            <IconButton onClick={(event) => setAnchor(event.currentTarget)} aria-label="Account menu">
              <Avatar
                src={user?.imageUrl}
                sx={{ width: 32, height: 32, fontSize: 14, fontWeight: 800 }}
              >
                {initial}
              </Avatar>
            </IconButton>
          </Tooltip>
        </Stack>

        <Menu
          anchorEl={anchor}
          open={Boolean(anchor)}
          onClose={() => setAnchor(null)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
          slotProps={{ paper: { sx: { minWidth: 240, borderRadius: 3, mt: 0.5 } } }}
        >
          <Box sx={{ px: 2, py: 1.25 }}>
            <Typography variant="body2" fontWeight={700} noWrap>
              {user?.fullName || "Signed in"}
            </Typography>
            <Typography variant="caption" color="text.secondary" noWrap>
              {user?.primaryEmailAddress?.emailAddress ?? ""}
            </Typography>
          </Box>

          <Divider />

          <MenuItem
            onClick={() => {
              setAnchor(null);
              navigate(ROUTES.SETTINGS);
            }}
          >
            <ListItemIcon>
              <SettingsRoundedIcon fontSize="small" />
            </ListItemIcon>
            Settings
          </MenuItem>

          <MenuItem
            onClick={() => {
              setAnchor(null);
              // Clerk clears its own session; the API client's token provider is
              // torn down by ApiAuthBridge's cleanup when the tree unmounts.
              signOut({ redirectUrl: "/" });
            }}
          >
            <ListItemIcon>
              <LogoutRoundedIcon fontSize="small" />
            </ListItemIcon>
            Sign out
          </MenuItem>
        </Menu>
      </Toolbar>
    </AppBar>
  );
};

export default TopBar;

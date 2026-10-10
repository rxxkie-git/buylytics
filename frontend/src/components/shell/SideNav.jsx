/**
 * Sidebar navigation.
 *
 * Two behaviours the Phase 1 sidebar got wrong and this one does not.
 *
 * **Active matching is exact for the dashboard.** `/app` is a prefix of every other
 * route, so a `startsWith` test highlights Dashboard on every page. React Router's
 * `NavLink` with `end` handles this properly, which is why routes are declared here
 * as links rather than as buttons with an `onClick`.
 *
 * **Nothing is hidden behind a hover.** The Phase 1 nav collapsed to icons only and
 * relied on tooltips, which meant the labels were unavailable on touch devices. Here
 * the drawer is either open with labels or closed entirely.
 */

import { NavLink } from "react-router-dom";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import InsightsRoundedIcon from "@mui/icons-material/InsightsRounded";
import { alpha } from "@mui/material/styles";

import { ROUTES, navigationGroups } from "../../app/navigation.js";

export const SIDENAV_WIDTH = 272;

const SideNav = ({ onNavigate }) => (
  <Box sx={{ width: SIDENAV_WIDTH, height: "100%", display: "flex", flexDirection: "column" }}>
    <Stack direction="row" spacing={1.5} alignItems="center" sx={{ px: 2.5, py: 2.5 }}>
      <Box
        sx={{
          width: 38,
          height: 38,
          borderRadius: 2.5,
          display: "grid",
          placeItems: "center",
          bgcolor: "primary.main",
          color: "primary.contrastText",
        }}
      >
        <InsightsRoundedIcon fontSize="small" />
      </Box>
      <Box>
        <Typography variant="subtitle1" fontWeight={800} lineHeight={1.2}>
          Retail Analytics
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Customer &amp; buying patterns
        </Typography>
      </Box>
    </Stack>

    <Divider />

    <Box sx={{ flex: 1, overflowY: "auto", px: 1.5, py: 1.5 }}>
      {navigationGroups.map((group) => (
        <Box key={group.heading} sx={{ mb: 1.5 }}>
          <Typography
            variant="overline"
            sx={{ px: 1.5, color: "text.secondary", fontWeight: 800, letterSpacing: 1 }}
          >
            {group.heading}
          </Typography>

          <List dense disablePadding sx={{ mt: 0.5 }}>
            {group.items.map((item) => {
              const Icon = item.icon;

              return (
                <ListItemButton
                  key={item.path}
                  component={NavLink}
                  to={item.path}
                  // Only the dashboard needs exact matching; every other route is a
                  // leaf, and `end` on a leaf would break future nested routes.
                  end={item.path === ROUTES.DASHBOARD}
                  onClick={onNavigate}
                  sx={{
                    borderRadius: 2.5,
                    mb: 0.25,
                    color: "text.secondary",
                    "&.active": {
                      color: "primary.main",
                      bgcolor: (theme) => alpha(theme.palette.primary.main, 0.1),
                      "& .MuiListItemIcon-root": { color: "primary.main" },
                      "& .MuiListItemText-primary": { fontWeight: 800 },
                    },
                  }}
                >
                  <ListItemIcon sx={{ minWidth: 38, color: "inherit" }}>
                    <Icon fontSize="small" />
                  </ListItemIcon>
                  <ListItemText
                    primary={item.label}
                    primaryTypographyProps={{ variant: "body2", fontWeight: 600 }}
                  />
                  {/* Marked so it is clear which pages still read a Phase 1 route
                      rather than a tenant-scoped v1 one. */}
                  {item.legacy ? (
                    <Chip label="v0" size="small" variant="outlined" sx={{ height: 18, fontSize: 10 }} />
                  ) : null}
                </ListItemButton>
              );
            })}
          </List>
        </Box>
      ))}
    </Box>

    <Divider />

    <Box sx={{ px: 2.5, py: 1.75 }}>
      <Typography variant="caption" color="text.secondary">
        Every figure on these pages is computed from your imported data. Nothing is
        sample data.
      </Typography>
    </Box>
  </Box>
);

export default SideNav;

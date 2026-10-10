/**
 * The application frame: permanent sidebar on desktop, temporary drawer on mobile,
 * top bar, and an `<Outlet />` for the page.
 *
 * `<Outlet />` rather than `children` means the shell mounts once and stays mounted
 * across navigations. The Phase 1 layout wrapped each page element individually, so
 * moving between pages remounted the sidebar, the theme provider subtree and — after
 * Phase 2 added it — would have remounted the readiness poller, firing a health
 * request on every click.
 */

import { Suspense, useState } from "react";
import { Outlet } from "react-router-dom";
import Box from "@mui/material/Box";
import Container from "@mui/material/Container";
import Drawer from "@mui/material/Drawer";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";

import { LoadingBlock } from "../ui/AsyncBoundary.jsx";
import ErrorBoundary from "../ui/ErrorBoundary.jsx";
import SideNav, { SIDENAV_WIDTH } from "./SideNav.jsx";
import TopBar from "./TopBar.jsx";

const AppShell = ({ mode, onToggleMode }) => {
  const theme = useTheme();
  const isDesktop = useMediaQuery(theme.breakpoints.up("md"));
  const [navOpen, setNavOpen] = useState(false);

  return (
    <Box sx={{ display: "flex", minHeight: "100vh", bgcolor: "background.default" }}>
      {isDesktop ? (
        <Drawer
          variant="permanent"
          open
          sx={{
            width: SIDENAV_WIDTH,
            flexShrink: 0,
            "& .MuiDrawer-paper": {
              width: SIDENAV_WIDTH,
              boxSizing: "border-box",
              borderRight: `1px solid ${theme.palette.divider}`,
              bgcolor: "background.paper",
            },
          }}
        >
          <SideNav />
        </Drawer>
      ) : (
        <Drawer
          variant="temporary"
          open={navOpen}
          onClose={() => setNavOpen(false)}
          // Keeps the drawer in the DOM between opens; on mobile the alternative
          // re-runs every icon import on each toggle, which is visibly slow.
          ModalProps={{ keepMounted: true }}
          sx={{ "& .MuiDrawer-paper": { width: SIDENAV_WIDTH, bgcolor: "background.paper" } }}
        >
          <SideNav onNavigate={() => setNavOpen(false)} />
        </Drawer>
      )}

      <Box sx={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <TopBar onOpenNav={() => setNavOpen(true)} mode={mode} onToggleMode={onToggleMode} />

        <Container maxWidth="xl" sx={{ py: { xs: 2.5, md: 4 }, flex: 1 }}>
          {/* One boundary for every lazy page. Placed inside the container rather
              than around the whole shell so a chunk arriving does not unmount and
              remount the sidebar, which would close the mobile drawer mid-tap. */}
          {/* The error boundary is inside the shell for the same reason: a page
              that throws leaves the sidebar and top bar on screen, so the user can
              navigate away instead of staring at a blank window. */}
          <ErrorBoundary>
            <Suspense fallback={<LoadingBlock label="Loading page…" />}>
              <Outlet />
            </Suspense>
          </ErrorBoundary>
        </Container>
      </Box>
    </Box>
  );
};

export default AppShell;

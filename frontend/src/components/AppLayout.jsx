import { Box, Toolbar } from "@mui/material";
import { useState } from "react";
import { Outlet } from "react-router-dom";
import Navbar from "./Navbar";
import Sidebar from "./Sidebar";

export const drawerWidth = 292;

// Shared application shell used by all analytics pages.
const AppLayout = ({ mode, onToggleTheme }) => {
  const [mobileOpen, setMobileOpen] = useState(false);

  const handleDrawerToggle = () => {
    setMobileOpen((currentValue) => !currentValue);
  };

  const handleDrawerClose = () => {
    setMobileOpen(false);
  };

  return (
    <Box sx={{ display: "flex", minHeight: "100vh" }}>
      <Navbar
        drawerWidth={drawerWidth}
        mode={mode}
        onMenuClick={handleDrawerToggle}
        onToggleTheme={onToggleTheme}
      />
      <Sidebar
        drawerWidth={drawerWidth}
        mobileOpen={mobileOpen}
        onDrawerClose={handleDrawerClose}
      />
      <Box
        component="main"
        sx={{
          flexGrow: 1,
          width: { sm: `calc(100% - ${drawerWidth}px)` },
          px: { xs: 2, md: 3.5 },
          pb: { xs: 3, md: 4 },
        }}
      >
        <Toolbar sx={{ minHeight: { xs: 84, md: 96 } }} />
        <Outlet />
      </Box>
    </Box>
  );
};

export default AppLayout;

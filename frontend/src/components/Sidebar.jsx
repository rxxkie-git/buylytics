import AutoGraphRoundedIcon from "@mui/icons-material/AutoGraphRounded";
import ChevronRightRoundedIcon from "@mui/icons-material/ChevronRightRounded";
import HomeRoundedIcon from "@mui/icons-material/HomeRounded";
import {
  alpha,
  Box,
  Button,
  Divider,
  Drawer,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import { Link as RouterLink, useLocation } from "react-router-dom";
import { navigationItems } from "../utils/navigation";

const SidebarContent = ({ onNavigate }) => {
  const theme = useTheme();
  const location = useLocation();

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <Box sx={{ p: 3 }}>
        <Stack direction="row" spacing={1.5} alignItems="center">
          <Box
            sx={{
              width: 46,
              height: 46,
              borderRadius: "16px",
              display: "grid",
              placeItems: "center",
              background:
                "linear-gradient(135deg, rgba(20,184,166,0.95), rgba(37,99,235,0.95))",
              boxShadow: "0 14px 36px rgba(37, 99, 235, 0.3)",
            }}
          >
            <AutoGraphRoundedIcon />
          </Box>

          <Box>
            <Typography variant="h6">CBPA Studio</Typography>
            <Typography variant="body2" sx={{ color: "rgba(231,238,248,0.7)" }}>
              RFM + K-Means Intelligence
            </Typography>
          </Box>
        </Stack>
      </Box>

      <Box sx={{ px: 3, pb: 2 }}>
        <Button
          component={RouterLink}
          to="/"
          fullWidth
          onClick={onNavigate}
          startIcon={<HomeRoundedIcon />}
          sx={{
            justifyContent: "flex-start",
            color: "#E7EEF8",
            border: "1px solid rgba(148, 163, 184, 0.18)",
            backgroundColor: "rgba(255,255,255,0.04)",
            "&:hover": {
              backgroundColor: "rgba(255,255,255,0.08)",
            },
          }}
        >
          Back to Landing
        </Button>
      </Box>

      <Divider sx={{ borderColor: "rgba(148, 163, 184, 0.12)" }} />

      <Box sx={{ px: 2, pt: 2 }}>
        <Typography
          variant="caption"
          sx={{ px: 1.5, color: "rgba(231,238,248,0.58)", letterSpacing: 1.1 }}
        >
          ANALYTICS WORKSPACE
        </Typography>
      </Box>

      <List
        sx={{
          px: 2,
          py: 1.5,
          flexGrow: 1,
          overflowY: "auto",
          "&::-webkit-scrollbar": {
            width: "6px",
          },
          "&::-webkit-scrollbar-track": {
            background: "transparent",
          },
          "&::-webkit-scrollbar-thumb": {
            background: "rgba(255, 255, 255, 0.12)",
            borderRadius: "999px",
          },
          "&::-webkit-scrollbar-thumb:hover": {
            background: "rgba(255, 255, 255, 0.24)",
          },
        }}
      >
        {navigationItems.map((item) => {
          const Icon = item.icon;
          const active = location.pathname === item.path;

          return (
            <ListItemButton
              key={item.path}
              component={RouterLink}
              to={item.path}
              onClick={onNavigate}
              sx={{
                mb: 1,
                borderRadius: "12px",
                alignItems: "flex-start",
                py: 1.4,
                background: active 
                  ? "linear-gradient(135deg, rgba(37,99,235,0.15) 0%, rgba(20,184,166,0.15) 100%)" 
                  : "transparent",
                border: active
                  ? "1px solid rgba(94, 234, 212, 0.3)"
                  : "1px solid transparent",
                boxShadow: active ? "0 4px 20px rgba(20, 184, 166, 0.15)" : "none",
                position: "relative",
                "&:hover": {
                  background: active 
                    ? "linear-gradient(135deg, rgba(37,99,235,0.22) 0%, rgba(20,184,166,0.22) 100%)" 
                    : "rgba(255,255,255,0.06)",
                  borderColor: active 
                    ? "rgba(94, 234, 212, 0.5)" 
                    : "rgba(255, 255, 255, 0.1)",
                },
                "&::before": active ? {
                  content: '""',
                  position: "absolute",
                  left: -1,
                  top: "15%",
                  height: "70%",
                  width: "3px",
                  backgroundColor: "#5EEAD4",
                  borderRadius: "0 4px 4px 0",
                  boxShadow: "0 0 8px rgba(94, 234, 212, 0.6)",
                } : {},
              }}
            >
              <ListItemIcon sx={{ minWidth: 42, color: active ? "#5EEAD4" : "#CBD5E1" }}>
                <Icon />
              </ListItemIcon>
              <ListItemText
                primary={item.label}
                secondary={item.description}
                primaryTypographyProps={{ fontWeight: 700, color: "#E7EEF8" }}
                secondaryTypographyProps={{ color: "rgba(231,238,248,0.64)", sx: { mt: 0.3 } }}
              />
              <ChevronRightRoundedIcon sx={{ color: "rgba(231,238,248,0.42)" }} />
            </ListItemButton>
          );
        })}
      </List>

      <Box sx={{ p: 2.5, mt: "auto" }}>
        <Box
          className="shimmer"
          sx={{
            borderRadius: "14px",
            p: 2.25,
            background:
              "linear-gradient(145deg, rgba(15,118,110,0.22), rgba(37,99,235,0.18), rgba(249,115,22,0.14))",
            border: "1px solid rgba(94, 234, 212, 0.12)",
          }}
        >
          <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
            Model Health
          </Typography>
          <Typography variant="body2" sx={{ mt: 0.8, color: "rgba(231,238,248,0.72)" }}>
            Clustering confidence is stable with silhouette score above the benchmark threshold.
          </Typography>
        </Box>
      </Box>
    </Box>
  );
};

// Responsive sidebar switches between a mobile drawer and desktop rail.
const Sidebar = ({ drawerWidth, mobileOpen, onDrawerClose }) => {
  const theme = useTheme();

  return (
    <>
      <Drawer
        variant="temporary"
        open={mobileOpen}
        onClose={onDrawerClose}
        ModalProps={{ keepMounted: true }}
        sx={{
          display: { xs: "block", sm: "none" },
          "& .MuiDrawer-paper": {
            width: drawerWidth,
            background:
              theme.palette.mode === "light"
                ? "linear-gradient(180deg, rgba(7,17,31,0.96) 0%, rgba(9,25,47,0.98) 100%)"
                : "linear-gradient(180deg, rgba(2,6,23,0.98) 0%, rgba(9,20,38,0.98) 100%)",
            color: "#E7EEF8",
          },
        }}
      >
        <SidebarContent onNavigate={onDrawerClose} />
      </Drawer>

      <Drawer
        variant="permanent"
        open
        sx={{
          display: { xs: "none", sm: "block" },
          width: drawerWidth,
          flexShrink: 0,
          "& .MuiDrawer-paper": {
            width: drawerWidth,
            background:
              theme.palette.mode === "light"
                ? "linear-gradient(180deg, rgba(7,17,31,0.96) 0%, rgba(9,25,47,0.98) 100%)"
                : "linear-gradient(180deg, rgba(2,6,23,0.98) 0%, rgba(9,20,38,0.98) 100%)",
            color: "#E7EEF8",
          },
        }}
      >
        <SidebarContent />
      </Drawer>
    </>
  );
};

export default Sidebar;

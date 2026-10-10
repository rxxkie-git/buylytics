import { alpha, createTheme } from "@mui/material/styles";

// Build a themed design system so the dashboard feels consistent across pages.
const getTheme = (mode = "light") =>
  createTheme({
    palette: {
      mode,
      primary: {
        main: mode === "light" ? "#0F766E" : "#34D399",
      },
      secondary: {
        main: "#2563EB",
      },
      success: {
        main: "#16A34A",
      },
      warning: {
        main: "#EA580C",
      },
      error: {
        main: "#DC2626",
      },
      background: {
        default: mode === "light" ? "#EFF5FB" : "#07111F",
        paper: mode === "light" ? "#FFFFFF" : "#0E1A2F",
      },
      text: {
        primary: mode === "light" ? "#102033" : "#E7EEF8",
        secondary: mode === "light" ? "#3E546D" : "#B5C8DD",
      },
      divider:
        mode === "light" ? "rgba(148, 163, 184, 0.18)" : "rgba(148, 163, 184, 0.16)",
    },
    shape: {
      borderRadius: 18,
    },
    typography: {
      fontFamily: '"Manrope", sans-serif',
      h1: {
        fontFamily: '"Space Grotesk", sans-serif',
        fontWeight: 700,
      },
      h2: {
        fontFamily: '"Space Grotesk", sans-serif',
        fontWeight: 700,
      },
      h3: {
        fontFamily: '"Space Grotesk", sans-serif',
        fontWeight: 700,
      },
      h4: {
        fontFamily: '"Space Grotesk", sans-serif',
        fontWeight: 700,
      },
      h5: {
        fontFamily: '"Space Grotesk", sans-serif',
        fontWeight: 700,
      },
      h6: {
        fontFamily: '"Space Grotesk", sans-serif',
        fontWeight: 700,
      },
      button: {
        fontWeight: 700,
        textTransform: "none",
      },
    },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: {
            color: mode === "light" ? "#102033" : "#E7EEF8",
          },
        },
      },
      MuiPaper: {
        styleOverrides: {
          root: {
            backgroundImage: "none",
            border: `1px solid ${
              mode === "light"
                ? "rgba(255, 255, 255, 0.72)"
                : "rgba(148, 163, 184, 0.1)"
            }`,
            boxShadow:
              mode === "light"
                ? "0 18px 42px rgba(15, 23, 42, 0.08)"
                : "0 18px 42px rgba(2, 6, 23, 0.34)",
          },
        },
      },
      MuiDrawer: {
        styleOverrides: {
          paper: {
            borderRight: "none",
            backdropFilter: "blur(14px)",
          },
        },
      },
      MuiButton: {
        defaultProps: {
          disableElevation: true,
        },
        styleOverrides: {
          root: {
            borderRadius: 14,
            paddingInline: 18,
          },
        },
      },
      MuiChip: {
        styleOverrides: {
          root: {
            fontWeight: 700,
            borderRadius: 12,
            color: mode === "light" ? "#102033" : "#E7EEF8",
          },
          outlined: {
            borderColor:
              mode === "light"
                ? "rgba(30, 41, 59, 0.28)"
                : "rgba(148, 163, 184, 0.38)",
            backgroundColor:
              mode === "light"
                ? "rgba(255, 255, 255, 0.72)"
                : "rgba(15, 23, 42, 0.45)",
          },
        },
      },
      MuiTableCell: {
        styleOverrides: {
          head: {
            fontWeight: 800,
          },
        },
      },
      MuiTooltip: {
        styleOverrides: {
          tooltip: {
            backgroundColor:
              mode === "light"
                ? alpha("#0F172A", 0.92)
                : alpha("#020617", 0.95),
            color: "#F8FAFC",
            border: `1px solid ${
              mode === "light"
                ? "rgba(148, 163, 184, 0.42)"
                : "rgba(148, 163, 184, 0.28)"
            }`,
            borderRadius: 12,
          },
        },
      },
    },
  });

export default getTheme;

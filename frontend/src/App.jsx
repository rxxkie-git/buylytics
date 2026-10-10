import { useEffect } from "react";
import { useAuth } from "./context/AuthContext";
import { CssBaseline, ThemeProvider } from "@mui/material";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./components/AppLayout";
import ProtectedRoute from "./components/ProtectedRoute";
import { setupApiInterceptors } from "./services/api";
import useThemeMode from "./hooks/useThemeMode";
import About from "./pages/About";
import Dashboard from "./pages/Dashboard";
import DataHub from "./pages/DataHub";
import Insights from "./pages/Insights";
import Landing from "./pages/Landing";
import Orders from "./pages/Orders";
import Preview from "./pages/Preview";
import ProductAnalysis from "./pages/ProductAnalysis";
import Results from "./pages/Results";
import Segments from "./pages/Segments";
import Inventory from "./pages/Inventory";
import getTheme from "./theme";

const ApiInterceptorSetup = () => {
  const { getToken } = useAuth();
  useEffect(() => {
    setupApiInterceptors(getToken);
  }, [getToken]);
  return null;
};

// Route setup keeps the landing page separate from the dashboard shell.
const App = () => {
  const { mode, toggleMode } = useThemeMode();
  const theme = getTheme(mode);

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <ApiInterceptorSetup />
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Landing />} />

          <Route element={<ProtectedRoute />}>
            <Route element={<AppLayout mode={mode} onToggleTheme={toggleMode} />}>
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/upload" element={<DataHub />} />
              <Route path="/orders" element={<Orders />} />
              <Route path="/product-analysis" element={<ProductAnalysis />} />
              <Route path="/preview" element={<Preview />} />
              <Route path="/results" element={<Results />} />
              <Route path="/segments" element={<Segments />} />
              <Route path="/insights" element={<Insights />} />
              <Route path="/inventory" element={<Inventory />} />
              <Route path="/about" element={<About />} />
            </Route>
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  );
};

export default App;

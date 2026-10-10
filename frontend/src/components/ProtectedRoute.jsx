import { useAuth } from "@clerk/clerk-react";
import { Box, CircularProgress } from "@mui/material";
import { Navigate, Outlet } from "react-router-dom";

const ProtectedRoute = () => {
  const { isLoaded, isSignedIn } = useAuth();

  if (!isLoaded) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100vh" }}>
        <CircularProgress />
      </Box>
    );
  }

  if (!isSignedIn) {
    return <Navigate to="/" replace />;
  }

  return <Outlet />;
};

export default ProtectedRoute;

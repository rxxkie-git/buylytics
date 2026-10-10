import React, { createContext, useContext, useState } from "react";
import {
  ClerkProvider as RealClerkProvider,
  useAuth as useClerkAuth,
  UserButton as RealUserButton,
  SignInButton as RealSignInButton,
} from "@clerk/clerk-react";
import { Avatar, Tooltip, IconButton, Menu, MenuItem, ListItemIcon, ListItemText } from "@mui/material";
import PersonRoundedIcon from "@mui/icons-material/PersonRounded";
import LogoutRoundedIcon from "@mui/icons-material/LogoutRounded";

const rawKey = (import.meta.env.VITE_CLERK_PUBLISHABLE_KEY || "").trim();
export const isClerkConfigured =
  Boolean(rawKey) &&
  rawKey !== "your_clerk_publishable_key" &&
  (rawKey.startsWith("pk_test_") || rawKey.startsWith("pk_live_"));

const DevAuthContext = createContext({
  isLoaded: true,
  isSignedIn: true,
  userId: "dev_user",
  user: { firstName: "Demo", lastName: "User", primaryEmailAddress: { emailAddress: "demo@example.com" } },
  getToken: async () => "dev-token",
  signOut: () => {},
  signIn: () => {},
});

export const AuthProvider = ({ children }) => {
  const [isSignedIn, setIsSignedIn] = useState(true);

  if (isClerkConfigured) {
    return (
      <RealClerkProvider publishableKey={rawKey} afterSignOutUrl="/">
        {children}
      </RealClerkProvider>
    );
  }

  const value = {
    isLoaded: true,
    isSignedIn,
    userId: "dev_user",
    user: { firstName: "Demo", lastName: "User", primaryEmailAddress: { emailAddress: "demo@example.com" } },
    getToken: async () => "dev-token",
    signOut: () => setIsSignedIn(false),
    signIn: () => setIsSignedIn(true),
  };

  return <DevAuthContext.Provider value={value}>{children}</DevAuthContext.Provider>;
};

export const useAuth = () => {
  if (isClerkConfigured) {
    return useClerkAuth();
  }
  return useContext(DevAuthContext);
};

export const SignInButton = ({ children, mode, ...props }) => {
  const { signIn } = useAuth();
  if (isClerkConfigured) {
    return <RealSignInButton mode={mode} {...props}>{children}</RealSignInButton>;
  }
  return (
    <span onClick={signIn} style={{ cursor: "pointer", display: "inline-flex" }}>
      {children}
    </span>
  );
};

export const UserButton = ({ afterSignOutUrl, ...props }) => {
  const [anchorEl, setAnchorEl] = useState(null);
  const auth = useAuth();

  if (isClerkConfigured) {
    return <RealUserButton afterSignOutUrl={afterSignOutUrl} {...props} />;
  }

  const handleClick = (event) => {
    setAnchorEl(event.currentTarget);
  };

  const handleClose = () => {
    setAnchorEl(null);
  };

  return (
    <>
      <Tooltip title="Demo User">
        <IconButton onClick={handleClick} size="small" sx={{ p: 0.5 }}>
          <Avatar sx={{ width: 34, height: 34, bgcolor: "primary.main", fontSize: "0.85rem", fontWeight: 700 }}>
            DU
          </Avatar>
        </IconButton>
      </Tooltip>
      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={handleClose}
        PaperProps={{
          sx: { mt: 1, minWidth: 160, borderRadius: "12px" },
        }}
      >
        <MenuItem disabled sx={{ opacity: 0.9 }}>
          <ListItemIcon>
            <PersonRoundedIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText primary="Demo User" secondary="demo@example.com" />
        </MenuItem>
        <MenuItem
          onClick={() => {
            handleClose();
            auth.signOut();
          }}
        >
          <ListItemIcon>
            <LogoutRoundedIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText primary="Sign Out" />
        </MenuItem>
      </Menu>
    </>
  );
};

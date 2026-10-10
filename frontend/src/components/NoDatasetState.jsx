import { Box, Paper, Typography, useTheme } from "@mui/material";
import { useNavigate } from "react-router-dom";

const NoDatasetState = ({ message = "Please upload a dataset to view insights and analytics.", buttonText = "Upload Dataset", uploadPath = "/upload" }) => {
  const navigate = useNavigate();
  const theme = useTheme();

  return (
    <Box sx={{ display: "flex", justifyContent: "center", alignItems: "center", minHeight: "60vh" }}>
      <Paper
        elevation={0}
        sx={{
          p: 5,
          textAlign: "center",
          borderRadius: 4,
          border: "1px dashed",
          borderColor: "divider",
          backgroundColor: "transparent",
          maxWidth: 480,
        }}
      >
        <Typography variant="h5" sx={{ mb: 1, fontWeight: 600 }}>
          No Dataset Uploaded
        </Typography>
        <Typography variant="body1" color="text.secondary" sx={{ mb: 4 }}>
          {message}
        </Typography>
        <Box sx={{ display: "flex", justifyContent: "center" }}>
          <Box
            component="button"
            onClick={() => navigate(uploadPath)}
            sx={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              px: 3,
              py: 1.5,
              borderRadius: "12px",
              backgroundColor: theme.palette.primary.main,
              color: theme.palette.primary.contrastText,
              fontWeight: 600,
              border: "none",
              cursor: "pointer",
              transition: "all 0.2s",
              "&:hover": {
                backgroundColor: theme.palette.primary.dark,
                transform: "translateY(-1px)",
              },
            }}
          >
            {buttonText}
          </Box>
        </Box>
      </Paper>
    </Box>
  );
};

export default NoDatasetState;

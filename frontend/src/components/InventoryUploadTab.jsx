import CheckCircleRoundedIcon from "@mui/icons-material/CheckCircleRounded";
import CloudUploadRoundedIcon from "@mui/icons-material/CloudUploadRounded";
import StorageRoundedIcon from "@mui/icons-material/StorageRounded";
import VerifiedRoundedIcon from "@mui/icons-material/VerifiedRounded";
import { Box, Button, Grid, Paper, Stack, Typography } from "@mui/material";
import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import UploadZone from "./UploadZone";
import { uploadInventoryDataset } from "../services/api";

const uploadChecklist = [
  {
    title: "Accepted formats",
    detail: "CSV, XLS, and XLSX inventory exports are validated and sent to the API.",
    icon: StorageRoundedIcon,
  },
  {
    title: "Update existing stock",
    detail: "Uploading a new inventory sheet will update existing stock levels and register new products.",
    icon: VerifiedRoundedIcon,
  },
  {
    title: "Real-time visibility",
    detail: "Once uploaded, the inventory dashboard updates immediately to reflect the new counts.",
    icon: CloudUploadRoundedIcon,
  },
];

const InventoryUploadTab = () => {
  const [file, setFile] = useState(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState("");
  const [error, setError] = useState("");
  const [response, setResponse] = useState(null);

  const handleFileSelect = (nextFile) => {
    const isValidType = /\.(csv|xls|xlsx)$/i.test(nextFile.name);

    if (!isValidType) {
      setError("Please upload a CSV, XLS, or XLSX file.");
      setFile(null);
      return;
    }

    setFile(nextFile);
    setResponse(null);
    setUploadStatus("");
    setError("");
  };

  const handleUpload = async () => {
    if (!file) {
      setError("Select an inventory sheet before uploading.");
      return;
    }

    setIsUploading(true);
    setError("");
    setUploadStatus("");

    try {
      const result = await uploadInventoryDataset(file);
      setResponse(result);
      setUploadStatus(result.message || "Inventory updated successfully.");
    } catch (uploadError) {
      setError(uploadError?.response?.data?.detail || uploadError?.message || "Upload failed. Please try again.");
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <Grid container spacing={3}>
      <Grid item xs={12} lg={7}>
        <UploadZone
          file={file}
          isUploading={isUploading}
          uploadStatus={uploadStatus}
          error={error}
          onFileSelect={handleFileSelect}
          onUpload={handleUpload}
          endpointLabel="/upload-inventory"
          title="Drag and drop your inventory sheet here"
          description="Upload CSV, XLS, or XLSX files exported from your warehouse system"
          uploadButtonLabel="Import Inventory Data"
        />
      </Grid>

      <Grid item xs={12} lg={5}>
        <Paper sx={{ p: 2.8, height: "100%" }}>
          <Typography variant="h6">Inventory upload workflow</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.8 }}>
            This tab updates the central inventory state and immediately reflects the latest stock data in the inventory dashboard.
          </Typography>

          <Stack spacing={1.7} sx={{ mt: 2.5 }}>
            {uploadChecklist.map((item) => {
              const Icon = item.icon;

              return (
                <Stack
                  key={item.title}
                  direction="row"
                  spacing={1.5}
                  sx={{ p: 1.7, borderRadius: 3.5, border: "1px solid", borderColor: "divider" }}
                >
                  <Icon color="primary" />
                  <Box>
                    <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
                      {item.title}
                    </Typography>
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                      {item.detail}
                    </Typography>
                  </Box>
                </Stack>
              );
            })}
          </Stack>
        </Paper>
      </Grid>

      {response ? (
        <Grid item xs={12}>
          <Paper sx={{ p: 2.6 }}>
            <Stack
              direction={{ xs: "column", md: "row" }}
              spacing={2}
              alignItems={{ xs: "flex-start", md: "center" }}
              justifyContent="space-between"
            >
              <Stack direction="row" spacing={1.5} alignItems="center">
                <CheckCircleRoundedIcon color="success" sx={{ fontSize: 34 }} />
                <Box>
                  <Typography variant="h6">Inventory updated successfully</Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                    Your warehouse data has been successfully imported and processed.
                  </Typography>
                </Box>
              </Stack>

              <Button component={RouterLink} to="/inventory" variant="contained">
                Go To Inventory Dashboard
              </Button>
            </Stack>
          </Paper>
        </Grid>
      ) : null}
    </Grid>
  );
};

export default InventoryUploadTab;

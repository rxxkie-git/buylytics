import CheckCircleRoundedIcon from "@mui/icons-material/CheckCircleRounded";
import CategoryRoundedIcon from "@mui/icons-material/CategoryRounded";
import CloudUploadRoundedIcon from "@mui/icons-material/CloudUploadRounded";
import Inventory2RoundedIcon from "@mui/icons-material/Inventory2Rounded";
import VerifiedRoundedIcon from "@mui/icons-material/VerifiedRounded";
import { Box, Grid, Paper, Stack, Typography } from "@mui/material";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import UploadZone from "./UploadZone";
import { uploadProductDataset } from "../services/api";

const uploadChecklist = [
  {
    title: "Accepted formats",
    detail: "CSV, XLS, and XLSX inputs are validated before being posted to the backend.",
    icon: Inventory2RoundedIcon,
  },
  {
    title: "Separate product context",
    detail: "Product uploads are saved independently so they do not interfere with customer analytics datasets.",
    icon: VerifiedRoundedIcon,
  },
  {
    title: "Structured intake",
    detail: "Rows and columns are prepared for a future storage layer without coupling this step to a specific database backend.",
    icon: CloudUploadRoundedIcon,
  },
];

const ProductUploadTab = () => {
  const navigate = useNavigate();
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
      setError("Select a product dataset before uploading.");
      return;
    }

    setIsUploading(true);
    setError("");
    setUploadStatus("");

    try {
      const result = await uploadProductDataset(file);
      setResponse(result);
      setUploadStatus(result.message);
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
          endpointLabel="/upload-product"
          title="Drag and drop your product dataset here"
          description="Upload CSV, XLS, or XLSX files and send them to the backend endpoint"
          uploadButtonLabel="Upload Product Dataset"
        />
      </Grid>

      <Grid item xs={12} lg={5}>
        <Paper sx={{ p: 2.8, height: "100%" }}>
          <Typography variant="h6">Product upload workflow</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.8 }}>
            This section stores product data for future processing. It follows the same upload experience as customer data but keeps a separate product dataset reference.
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
            <Stack direction="row" spacing={1.5} alignItems="center">
              <CheckCircleRoundedIcon color="success" sx={{ fontSize: 34 }} />
              <Box>
                <Typography variant="h6">Product dataset uploaded successfully</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                  {response.uploadMeta?.fileName} - {response.preview?.rows} rows - {response.preview?.columns} columns
                </Typography>
              </Box>
            </Stack>

            <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1.8 }}>
              <CategoryRoundedIcon color="primary" fontSize="small" />
              <Typography variant="caption" color="text.secondary">
                Product dataset ID: {response.datasetId}
              </Typography>
            </Stack>

            <Typography
              variant="body2"
              color="primary.main"
              sx={{ mt: 1.5, fontWeight: 700, cursor: "pointer" }}
              onClick={() => navigate("/product-analysis")}
            >
              Open Product Analysis
            </Typography>
          </Paper>
        </Grid>
      ) : null}
    </Grid>
  );
};

export default ProductUploadTab;

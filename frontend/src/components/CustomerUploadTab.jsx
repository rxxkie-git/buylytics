import CheckCircleRoundedIcon from "@mui/icons-material/CheckCircleRounded";
import CloudUploadRoundedIcon from "@mui/icons-material/CloudUploadRounded";
import TableChartRoundedIcon from "@mui/icons-material/TableChartRounded";
import VerifiedRoundedIcon from "@mui/icons-material/VerifiedRounded";
import {
  Box,
  Button,
  Grid,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import { useState } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import UploadZone from "./UploadZone";
import { uploadDataset } from "../services/api";

const uploadChecklist = [
  {
    title: "Accepted formats",
    detail: "CSV, XLS, and XLSX inputs are validated and sent to the FastAPI backend.",
    icon: TableChartRoundedIcon,
  },
  {
    title: "Validation flow",
    detail: "Basic client-side validation ensures a supported spreadsheet format before submission.",
    icon: VerifiedRoundedIcon,
  },
  {
    title: "Backend-ready contract",
    detail: "Files are posted to the `/upload` endpoint using FormData, ready for a real backend later.",
    icon: CloudUploadRoundedIcon,
  },
];

const CustomerUploadTab = () => {
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
      setError("Select a dataset before uploading.");
      return;
    }

    setIsUploading(true);
    setError("");
    setUploadStatus("");

    try {
      const result = await uploadDataset(file);
      setResponse(result);
      setUploadStatus(result.message);
    } catch (uploadError) {
      setError(uploadError?.message || "Upload failed. Please try again.");
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
          title="Drag and drop your customer dataset here"
          description="Upload CSV, XLS, or XLSX files for RFM segmentation and analytics"
          uploadButtonLabel="Upload Customer Dataset"
        />
      </Grid>

      <Grid item xs={12} lg={5}>
        <Paper sx={{ p: 2.8, height: "100%" }}>
          <Typography variant="h6">Upload workflow</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.8 }}>
            This tab posts files to the live backend contract and stores dataset context for analytics pages
            for your project demo.
          </Typography>

          <Stack spacing={1.7} sx={{ mt: 2.5 }}>
            {uploadChecklist.map((item) => {
              const Icon = item.icon;

              return (
                <Stack
                  key={item.title}
                  direction="row"
                  spacing={1.5}
                  sx={{ p: 1.7, borderRadius: "24px", border: "1px solid", borderColor: "divider" }}
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
                  <Typography variant="h6">Dataset uploaded successfully</Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                    {response.uploadMeta?.fileName} - {response.preview.rows} rows - {response.preview.columns} columns
                  </Typography>
                </Box>
              </Stack>

              <Stack direction={{ xs: "column", sm: "row" }} spacing={1.25}>
                <Button component={RouterLink} to="/preview" variant="contained">
                  Preview Data
                </Button>
                <Button component={RouterLink} to="/dashboard" variant="outlined">
                  Go To Dashboard
                </Button>
              </Stack>
            </Stack>

            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1.8 }}>
              Uploaded at {new Date(response.uploadMeta?.uploadedAt).toLocaleString("en-IN")}
            </Typography>
          </Paper>
        </Grid>
      ) : null}

      <Grid item xs={12}>
        <Paper
          sx={{
            p: 2.4,
            display: "flex",
            flexDirection: { xs: "column", md: "row" },
            justifyContent: "space-between",
            gap: 2,
          }}
        >
          <Box>
            <Typography variant="h6">Need to inspect sample records first?</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.8 }}>
              The preview screen includes pagination, sorting, and missing-value highlighting for
              the uploaded dataset.
            </Typography>
          </Box>
          <Button variant="text" onClick={() => navigate("/preview")}>
            Open Preview
          </Button>
        </Paper>
      </Grid>
    </Grid>
  );
};

export default CustomerUploadTab;

import InsertDriveFileRoundedIcon from "@mui/icons-material/InsertDriveFileRounded";
import PublishRoundedIcon from "@mui/icons-material/PublishRounded";
import { alpha, Box, Button, LinearProgress, Paper, Stack, Typography, useTheme } from "@mui/material";
import { useRef, useState } from "react";

const acceptedFormats = ".csv,.xls,.xlsx";

// Drag-and-drop upload area with click-to-browse support.
const UploadZone = ({
  file,
  isUploading,
  uploadStatus,
  error,
  onFileSelect,
  onUpload,
  endpointLabel = "/upload",
  title = "Drag and drop your dataset here",
  description = "Upload CSV, XLS, or XLSX files and send them to the backend endpoint.",
  uploadButtonLabel = "Upload Dataset",
}) => {
  const theme = useTheme();
  const inputRef = useRef(null);
  const [isDragActive, setIsDragActive] = useState(false);

  const handleFiles = (fileList) => {
    const nextFile = fileList?.[0];

    if (nextFile) {
      onFileSelect(nextFile);
    }
  };

  const handleDrop = (event) => {
    event.preventDefault();
    setIsDragActive(false);
    handleFiles(event.dataTransfer.files);
  };

  return (
    <Paper sx={{ p: { xs: 2.5, md: 3.5 } }}>
      <input
        ref={inputRef}
        type="file"
        hidden
        accept={acceptedFormats}
        onChange={(event) => handleFiles(event.target.files)}
      />

      <Box
        onDragOver={(event) => {
          event.preventDefault();
          setIsDragActive(true);
        }}
        onDragLeave={() => setIsDragActive(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        sx={{
          cursor: "pointer",
          borderRadius: 5,
          border: "2px dashed",
          borderColor: isDragActive ? "primary.main" : "divider",
          p: { xs: 3, md: 5 },
          textAlign: "center",
          transition: "all 180ms ease",
          backgroundColor: alpha(theme.palette.primary.main, isDragActive ? 0.12 : 0.05),
          "&:hover": {
            borderColor: "primary.main",
            backgroundColor: alpha(theme.palette.primary.main, 0.09),
          },
        }}
      >
        <Box
          sx={{
            width: 72,
            height: 72,
            mx: "auto",
            borderRadius: "24px",
            display: "grid",
            placeItems: "center",
            color: "primary.main",
            backgroundColor: alpha(theme.palette.primary.main, 0.16),
          }}
        >
          <PublishRoundedIcon sx={{ fontSize: 34 }} />
        </Box>

        <Typography variant="h5" sx={{ mt: 2.2 }}>
          {title}
        </Typography>
        <Typography variant="body1" color="text.secondary" sx={{ mt: 1 }}>
          {description} <strong>{endpointLabel}</strong>.
        </Typography>
        <Button variant="outlined" sx={{ mt: 2.5 }}>
          Browse Files
        </Button>
      </Box>

      <Stack spacing={1.5} sx={{ mt: 2.5 }}>
        <Typography variant="body2" color="text.secondary">
          Supported formats: CSV, XLS, XLSX
        </Typography>

        {file ? (
          <Stack
            direction="row"
            spacing={1.5}
            alignItems="center"
            sx={{
              p: 1.75,
              borderRadius: 3,
              backgroundColor: alpha(theme.palette.primary.main, 0.08),
            }}
          >
            <InsertDriveFileRoundedIcon color="primary" />
            <Box sx={{ flexGrow: 1 }}>
              <Typography variant="subtitle2">{file.name}</Typography>
              <Typography variant="caption" color="text.secondary">
                {(file.size / 1024).toFixed(1)} KB
              </Typography>
            </Box>

            <Button variant="contained" onClick={onUpload} disabled={isUploading}>
              {isUploading ? "Uploading..." : uploadButtonLabel}
            </Button>
          </Stack>
        ) : null}

        {isUploading ? <LinearProgress /> : null}

        {uploadStatus ? (
          <Typography variant="body2" color="success.main">
            {uploadStatus}
          </Typography>
        ) : null}

        {error ? (
          <Typography variant="body2" color="error.main">
            {error}
          </Typography>
        ) : null}
      </Stack>
    </Paper>
  );
};

export default UploadZone;


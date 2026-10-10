import StorageRoundedIcon from "@mui/icons-material/StorageRounded";
import TableRowsRoundedIcon from "@mui/icons-material/TableRowsRounded";
import WarningAmberRoundedIcon from "@mui/icons-material/WarningAmberRounded";
import { alpha, Box, Chip, Grid, Paper, Stack, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import DataTable from "../components/DataTable";
import ErrorState from "../components/ErrorState";
import LoadingState from "../components/LoadingState";
import NoDatasetState from "../components/NoDatasetState";
import PageHeader from "../components/PageHeader";
import useApiData from "../hooks/useApiData";
import { getPreviewData } from "../services/api";
import { getDatasetId } from "../utils/storage";
import { formatCurrency, getSegmentColor } from "../utils/formatters";

const emptyPreview = {
  rows: [],
  columns: [],
  quality: {
    totalRows: 0,
    totalColumns: 0,
    missingCells: 0,
    rowsWithMissingValues: 0,
  },
  uploadMeta: null,
};

// Data preview page focuses on data quality and record-level inspection.
const Preview = () => {
  const navigate = useNavigate();
  const hasDataset = Boolean(getDatasetId());
  const { data, loading, error, reload } = useApiData(getPreviewData, emptyPreview);

  if (loading && hasDataset) {
    return (
      <LoadingState
        title="Loading preview..."
        description="Preparing table rows and data quality metrics."
      />
    );
  }

  if (!hasDataset) {
    return (
      <NoDatasetState 
        message="Please upload a customer dataset to preview data and validation."
        buttonText="Upload Dataset"
        uploadPath="/upload"
      />
    );
  }

  if (error) {
    return <ErrorState message={error} onRetry={reload} />;
  }

  const renderers = {
    segment: (value) => (
      <Chip
        label={value}
        size="small"
        sx={{
          color: getSegmentColor(value),
          backgroundColor: alpha(getSegmentColor(value), 0.12),
        }}
      />
    ),
    monetary: (value) => formatCurrency(value),
    avgOrderValue: (value) => formatCurrency(value),
  };

  const columns = data.columns.map((column) => ({
    ...column,
    render: renderers[column.id],
  }));

  return (
    <Box>
      <PageHeader
        eyebrow="Data Validation"
        title="Inspect your uploaded dataset before analysis"
        subtitle="Review row counts, missing values, and the raw customer fields that feed the RFM scoring and clustering pipeline."
        chipLabel={`${data.quality.totalRows} rows loaded`}
        actionLabel="Upload Another File"
        onActionClick={() => navigate("/upload")}
      />

      <Grid container spacing={3}>
        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 2.4, height: "100%" }}>
            <Stack direction="row" spacing={1.3} alignItems="center">
              <StorageRoundedIcon color="primary" />
              <Box>
                <Typography variant="subtitle2" color="text.secondary">
                  Dataset Size
                </Typography>
                <Typography variant="h5">{data.quality.totalRows} records</Typography>
              </Box>
            </Stack>
          </Paper>
        </Grid>

        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 2.4, height: "100%" }}>
            <Stack direction="row" spacing={1.3} alignItems="center">
              <TableRowsRoundedIcon color="secondary" />
              <Box>
                <Typography variant="subtitle2" color="text.secondary">
                  Columns Tracked
                </Typography>
                <Typography variant="h5">{data.quality.totalColumns} fields</Typography>
              </Box>
            </Stack>
          </Paper>
        </Grid>

        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 2.4, height: "100%" }}>
            <Stack direction="row" spacing={1.3} alignItems="center">
              <WarningAmberRoundedIcon color="warning" />
              <Box>
                <Typography variant="subtitle2" color="text.secondary">
                  Missing Values
                </Typography>
                <Typography variant="h5">{data.quality.missingCells} cells</Typography>
              </Box>
            </Stack>
          </Paper>
        </Grid>

        <Grid item xs={12}>
          <Paper sx={{ p: 2.1, mb: 0 }}>
            <Stack
              direction={{ xs: "column", md: "row" }}
              spacing={1.5}
              justifyContent="space-between"
              alignItems={{ xs: "flex-start", md: "center" }}
            >
              <Box>
                <Typography variant="h6">Dataset Table</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.6 }}>
                  Empty values are highlighted in amber so they stand out during data cleaning and
                  preprocessing.
                </Typography>
              </Box>
              <Stack direction="row" spacing={1.25} flexWrap="wrap" useFlexGap>
                <Chip
                  label={`${data.quality.rowsWithMissingValues} rows with missing values`}
                  color="warning"
                  variant="outlined"
                />
                {data.uploadMeta?.fileName ? <Chip label={data.uploadMeta.fileName} /> : null}
              </Stack>
            </Stack>
          </Paper>
          <DataTable columns={columns} rows={data.rows} defaultOrderBy="monetary" />
        </Grid>
      </Grid>
    </Box>
  );
};

export default Preview;


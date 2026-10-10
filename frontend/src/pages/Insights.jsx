import CampaignRoundedIcon from "@mui/icons-material/CampaignRounded";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import LightbulbRoundedIcon from "@mui/icons-material/LightbulbRounded";
import TrackChangesRoundedIcon from "@mui/icons-material/TrackChangesRounded";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Grid,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import ErrorState from "../components/ErrorState";
import LoadingState from "../components/LoadingState";
import NoDatasetState from "../components/NoDatasetState";
import PageHeader from "../components/PageHeader";
import useApiData from "../hooks/useApiData";
import { getInsightsData } from "../services/api";
import { getDatasetId } from "../utils/storage";

const emptyInsights = {
  highlights: [],
  recommendations: [],
  opportunityAreas: [],
  trainedModelSummary: {
    selectedAlgorithm: "unknown",
    bestSilhouetteScore: 0,
    candidateScores: [],
  },
  customerRiskSnapshot: {
    highRiskCustomers: 0,
    inactiveCustomers: 0,
    totalCustomers: 0,
    clusters: [],
  },
};

// Insights page translates model output into business actions and talking points.
const Insights = () => {
  const navigate = useNavigate();
  const hasDataset = Boolean(getDatasetId());
  const { data, loading, error, reload } = useApiData(getInsightsData, emptyInsights, [], hasDataset);

  if (loading && hasDataset) {
    return (
      <LoadingState
        title="Loading insights..."
        description="Summarizing patterns, recommendations, and next actions."
      />
    );
  }

  if (!hasDataset) {
    return (
      <NoDatasetState 
        message="Please upload a customer dataset to view AI-generated business insights."
        buttonText="Upload Dataset"
        uploadPath="/upload"
      />
    );
  }

  if (error) {
    return <ErrorState message={error} onRetry={reload} />;
  }

  return (
    <Box>
      <PageHeader
        eyebrow="Decision Support"
        title="Turn customer clusters into recommendations"
        subtitle="These automatically generated insights help explain who is driving revenue, who is inactive, and what actions should follow next."
        chipLabel="Insight engine active"
        actionLabel="Return To Dashboard"
        onActionClick={() => navigate("/dashboard")}
      />

      <Grid container spacing={3}>
        {data.highlights.map((highlight) => (
          <Grid item xs={12} md={4} key={highlight.title}>
            <Paper sx={{ p: 2.5, height: "100%" }}>
              <Typography variant="subtitle2" color="text.secondary">
                {highlight.title}
              </Typography>
              <Typography variant="h3" sx={{ mt: 1.2 }}>
                {highlight.metric}
              </Typography>
              <Typography variant="body1" sx={{ mt: 1.2, lineHeight: 1.8 }}>
                {highlight.statement}
              </Typography>
            </Paper>
          </Grid>
        ))}

        <Grid item xs={12} lg={7}>
          <Paper sx={{ p: 2.5 }}>
            <Stack direction="row" spacing={1.2} alignItems="center">
              <CampaignRoundedIcon color="primary" />
              <Box>
                <Typography variant="h6">Recommendations</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                  Practical follow-up actions for marketing, retention, and customer development.
                </Typography>
              </Box>
            </Stack>

            <Box sx={{ mt: 2.2 }}>
              {data.recommendations.map((item) => (
                <Accordion
                  key={item.title}
                  disableGutters
                  sx={{ mb: 1.4, borderRadius: "16px !important", overflow: "hidden" }}
                >
                  <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
                    <Box>
                      <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
                        {item.title}
                      </Typography>
                      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.4 }}>
                        {item.summary}
                      </Typography>
                    </Box>
                  </AccordionSummary>
                  <AccordionDetails>
                    <Stack spacing={1}>
                      {item.actions.map((action) => (
                        <Typography key={action} variant="body2" color="text.secondary">
                          - {action}
                        </Typography>
                      ))}
                    </Stack>
                  </AccordionDetails>
                </Accordion>
              ))}
            </Box>
          </Paper>
        </Grid>

        <Grid item xs={12} lg={5}>
          <Paper sx={{ p: 2.5, height: "100%" }}>
            <Stack direction="row" spacing={1.2} alignItems="center">
              <TrackChangesRoundedIcon color="secondary" />
              <Box>
                <Typography variant="h6">Opportunity Areas</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                  Quick talking points you can use during demo presentation or review.
                </Typography>
              </Box>
            </Stack>

            <Stack spacing={1.5} sx={{ mt: 2.4 }}>
              {data.opportunityAreas.map((item) => (
                <Paper key={item.label} variant="outlined" sx={{ p: 2 }}>
                  <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
                    {item.label}
                  </Typography>
                  <Typography variant="h5" sx={{ mt: 0.8 }}>
                    {item.value}
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mt: 0.7 }}>
                    {item.detail}
                  </Typography>
                </Paper>
              ))}
            </Stack>

            <Paper
              sx={{
                mt: 2.2,
                p: 2,
                background: "linear-gradient(135deg, rgba(37,99,235,0.12), rgba(15,118,110,0.12))",
              }}
            >
              <Stack spacing={1.1}>
                <Stack direction="row" spacing={1.2} alignItems="center">
                  <LightbulbRoundedIcon color="warning" />
                  <Typography variant="body2" color="text.secondary">
                    {`Model used ${String(data.trainedModelSummary?.selectedAlgorithm || "unknown").toUpperCase()} with best silhouette ${Number(data.trainedModelSummary?.bestSilhouetteScore || 0).toFixed(2)}.`}
                  </Typography>
                </Stack>
                <Typography variant="body2" color="text.secondary">
                  {`High-risk customers: ${data.customerRiskSnapshot?.highRiskCustomers || 0} / ${data.customerRiskSnapshot?.totalCustomers || 0}`}
                </Typography>
                {Array.isArray(data.customerRiskSnapshot?.clusters) && data.customerRiskSnapshot.clusters.length > 0 ? (
                  <Stack spacing={0.5}>
                    {data.customerRiskSnapshot.clusters.map((cluster) => (
                      <Typography
                        key={`${cluster.cluster}-${cluster.segment}`}
                        variant="caption"
                        color="text.secondary"
                      >
                        {`${cluster.cluster} (${cluster.segment}): ${cluster.customers} customers`}
                      </Typography>
                    ))}
                  </Stack>
                ) : null}
              </Stack>
            </Paper>
          </Paper>
        </Grid>
      </Grid>
    </Box>
  );
};

export default Insights;


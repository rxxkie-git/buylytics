import AccountTreeRoundedIcon from "@mui/icons-material/AccountTreeRounded";
import ApiRoundedIcon from "@mui/icons-material/ApiRounded";
import HubRoundedIcon from "@mui/icons-material/HubRounded";
import ScienceRoundedIcon from "@mui/icons-material/ScienceRounded";
import {
  Box,
  Chip,
  Grid,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import PageHeader from "../components/PageHeader";

const workflowSteps = [
  {
    title: "Data Preparation",
    description:
      "Upload customer transaction files, inspect missing values, and validate the structure before analysis.",
    icon: ScienceRoundedIcon,
  },
  {
    title: "RFM Feature Engineering",
    description:
      "Transform transactions into recency, frequency, and monetary measures for each customer profile.",
    icon: AccountTreeRoundedIcon,
  },
  {
    title: "K-Means Clustering",
    description:
      "Apply unsupervised learning to discover natural customer groups based on buying behavior.",
    icon: HubRoundedIcon,
  },
  {
    title: "Insights Delivery",
    description:
      "Present clusters, business insights, and recommended actions through an analytics-focused interface.",
    icon: ApiRoundedIcon,
  },
];

const techStack = ["React.js", "React Router", "Axios", "Material UI", "Recharts", "Mock API Layer"];

// About page explains the project approach and gives the frontend academic context.
const About = () => {
  const navigate = useNavigate();

  return (
    <Box>
      <PageHeader
        eyebrow="Project Context"
        title="About the Customer Buying Pattern Analysis project"
        subtitle="This major project combines machine learning concepts with a modern frontend so stakeholders can understand customer behavior and respond with clear actions."
        chipLabel="RFM + K-Means"
        actionLabel="Open Dashboard"
        onActionClick={() => navigate("/dashboard")}
      />

      <Grid container spacing={3}>
        <Grid item xs={12} lg={7}>
          <Paper sx={{ p: 3 }}>
            <Typography variant="h5">Project Overview</Typography>
            <Typography variant="body1" color="text.secondary" sx={{ mt: 1.5, lineHeight: 1.9 }}>
              The goal of this project is to analyze customer purchasing behavior using RFM
              metrics and K-Means clustering. By grouping customers based on how recently they
              purchased, how often they purchase, and how much they spend, the system helps
              identify high-value customers, loyal buyers, at-risk accounts, and low-value users.
            </Typography>
            <Typography variant="body1" color="text.secondary" sx={{ mt: 1.5, lineHeight: 1.9 }}>
              This frontend is designed to present that workflow like a production analytics
              product, not just a static academic interface. It includes upload handling, data
              preview, model diagnostics, segmentation tables, and recommendation views that can
              later connect directly to a real backend API.
            </Typography>
          </Paper>
        </Grid>

        <Grid item xs={12} lg={5}>
          <Paper sx={{ p: 3, height: "100%" }}>
            <Typography variant="h5">Frontend Highlights</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mt: 2 }}>
              {techStack.map((item) => (
                <Chip key={item} label={item} />
              ))}
            </Stack>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 2.2, lineHeight: 1.8 }}>
              The mock API contract mirrors endpoints such as `/upload`, `/analyze`, and
              `/results`, making it straightforward to replace demo responses with live model
              output.
            </Typography>
          </Paper>
        </Grid>

        {workflowSteps.map((step) => {
          const Icon = step.icon;

          return (
            <Grid item xs={12} md={6} key={step.title}>
              <Paper sx={{ p: 2.7, height: "100%" }}>
                <Stack direction="row" spacing={1.4} alignItems="center">
                  <Icon color="primary" />
                  <Typography variant="h6">{step.title}</Typography>
                </Stack>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1.4, lineHeight: 1.8 }}>
                  {step.description}
                </Typography>
              </Paper>
            </Grid>
          );
        })}
      </Grid>
    </Box>
  );
};

export default About;

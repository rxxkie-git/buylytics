import Inventory2RoundedIcon from "@mui/icons-material/Inventory2Rounded";
import PeopleRoundedIcon from "@mui/icons-material/PeopleRounded";
import { Box, Tab, Tabs } from "@mui/material";
import { useState } from "react";
import CustomerUploadTab from "../components/CustomerUploadTab";
import PageHeader from "../components/PageHeader";
import ProductUploadTab from "../components/ProductUploadTab";

const DataHub = () => {
  const [tabIndex, setTabIndex] = useState(0);

  const handleTabChange = (event, newValue) => {
    setTabIndex(newValue);
  };

  return (
    <Box>
      <PageHeader
        eyebrow="Centralized Ingestion"
        title="Data Hub"
        subtitle="Upload and manage customer transactions and product catalogs."
        chipLabel="2 Data Streams"
      />

      <Box sx={{ borderBottom: 1, borderColor: "divider", mb: 3 }}>
        <Tabs
          value={tabIndex}
          onChange={handleTabChange}
          aria-label="data hub tabs"
          variant="scrollable"
          scrollButtons="auto"
          sx={{
            "& .MuiTab-root": {
              minHeight: 64,
              fontSize: "0.95rem",
              fontWeight: 600,
              textTransform: "none",
            }
          }}
        >
          <Tab icon={<PeopleRoundedIcon />} iconPosition="start" label="Customer Analytics" />
          <Tab icon={<Inventory2RoundedIcon />} iconPosition="start" label="Product Analytics" />
        </Tabs>
      </Box>

      <Box sx={{ mt: 1 }}>
        {tabIndex === 0 && <CustomerUploadTab />}
        {tabIndex === 1 && <ProductUploadTab />}
      </Box>
    </Box>
  );
};

export default DataHub;

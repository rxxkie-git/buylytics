import { useEffect, useState } from "react";

// Reusable data-fetching hook for API-backed pages with loading and error states.
const useApiData = (fetcher, initialData, deps = []) => {
  const [data, setData] = useState(initialData);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadData = async () => {
    setLoading(true);
    setError("");

    try {
      const response = await fetcher();
      setData(response);
    } catch (fetchError) {
      setError(
        fetchError?.response?.data?.message ||
          fetchError?.message ||
          "Something went wrong while loading data."
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, deps);

  return { data, loading, error, reload: loadData, setData };
};

export default useApiData;

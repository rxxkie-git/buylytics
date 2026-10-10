/**
 * Analytics jobs and stored snapshots.
 *
 * A job is queued, not run inline, so `submitJob` returns a job id and the UI
 * polls. `dispatched: false` in the response means the job row exists but no
 * transport carried it to a worker — with `KAFKA_ENABLED=false` and no inline
 * handler, that is the expected state, and the UI has to say so rather than
 * spinning forever.
 */

import { get, post } from "./client";

const BASE = "/api/v1/analytics";

export const JOB_STATUS = {
  QUEUED: "queued",
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
};

/** A job in one of these states will never change again, so polling can stop. */
export const isTerminalStatus = (status) =>
  status === JOB_STATUS.COMPLETED || status === JOB_STATUS.FAILED;

/**
 * Queue an analytics job.
 *
 * `referenceDate` fixes the instant recency is measured from. Leaving it unset
 * means "now", which is what a scheduled run wants; setting it is what makes a
 * result reproducible when you re-run last month's numbers.
 */
export const submitJob = ({ jobType, importId = null, referenceDate = null, ...options } = {}) =>
  post(
    `${BASE}/jobs`,
    {
      job_type: jobType,
      import_id: importId,
      reference_date: referenceDate instanceof Date ? referenceDate.toISOString() : referenceDate,
    },
    options
  );

export const fetchJobs = ({ jobType, status, limit = 50, ...options } = {}) =>
  get(`${BASE}/jobs`, { params: { job_type: jobType, status, limit }, ...options });

export const fetchJob = (jobId, options) => get(`${BASE}/jobs/${encodeURIComponent(jobId)}`, options);

export const cancelJob = (jobId, options) =>
  post(`${BASE}/jobs/${encodeURIComponent(jobId)}/cancel`, {}, options);

export const reapStaleJobs = (options) =>
  post(`${BASE}/jobs/reap-stale`, {}, options);

export const fetchRfm = ({ customerId, ...options } = {}) =>
  get(`${BASE}/rfm`, { params: { customer_id: customerId }, ...options });

export const fetchSegments = ({ customerId, ...options } = {}) =>
  get(`${BASE}/segments`, { params: { customer_id: customerId }, ...options });


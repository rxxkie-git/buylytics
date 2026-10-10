"""Analytics job vocabulary.

The job-type names live in their own dependency-free module because three very
different layers need to agree on them: the API validating a request body, the
event handlers naming a job to queue, and the engine dispatching one. Only the
engine needs SQLAlchemy, so keeping the names here means the other two can import
the vocabulary without pulling in the database layer.

:mod:`app.analytics.engine` re-exports these, so ``engine.JOB_RFM`` keeps working.
"""

from __future__ import annotations

#: Recompute recency / frequency / monetary from the orders tables.
JOB_RFM = "rfm"

#: Cluster customers from the stored RFM snapshot.
JOB_SEGMENTATION = "segmentation"

#: RFM followed by segmentation, in one run.
JOB_FULL_REFRESH = "full_refresh"

SUPPORTED_JOB_TYPES: tuple[str, ...] = (JOB_RFM, JOB_SEGMENTATION, JOB_FULL_REFRESH)


def is_supported(job_type: object) -> bool:
    return isinstance(job_type, str) and job_type in SUPPORTED_JOB_TYPES

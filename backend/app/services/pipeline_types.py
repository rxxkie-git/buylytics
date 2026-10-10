from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class ClusteringPreparationResult:
    """Container returned by the clustering data preparation pipeline."""

    customers_frame: pd.DataFrame
    feature_frame: pd.DataFrame
    feature_matrix: np.ndarray
    feature_columns: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

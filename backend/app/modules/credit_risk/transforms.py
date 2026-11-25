"""Feature transforms referenced by fitted model pipelines.

A pickled scikit-learn pipeline stores *references* to the functions it calls, so any
function used in a model must be importable wherever the model is loaded. Keeping them here,
in the backend, means the API can load a model without the offline ML package installed.
Changing one of these functions changes every model that uses it, so treat them as frozen.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray


def signed_log(values: ArrayLike) -> NDArray[np.float64]:
    """asinh(x / 1000): log-like for large amounts of either sign, linear near zero."""
    return np.asarray(np.arcsinh(np.asarray(values, dtype=np.float64) / 1000.0))

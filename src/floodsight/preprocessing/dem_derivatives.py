"""DEM derivatives: depression filling, D8 flow direction/accumulation,
slope and height above nearest drainage (HAND).

Pure numpy/scipy implementations sized for Copernicus GLO-30 tiles:
*   priority-flood depression filling (Barnes et al. 2014) with an epsilon
    gradient so flats drain;
*   D8 flow directions with resolved edge ties;
*   flow accumulation by processing cells in filled-elevation order;
*   HAND by walking D8 from each cell to its first drainage cell
    (accumulation at/above threshold) — the same routing the flood-path
    bonus uses, so HAND is now real (closes KNOWN_ISSUES K13).
"""

from __future__ import annotations

import heapq
import logging

import numpy as np

logger = logging.getLogger(__name__)

# D8 neighbour offsets: (dr, dc) clockwise from north
_D8 = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]
# D8 codes 1..8 following the order above (0 = sink / undefined)
_D8_CODES = np.array([1, 2, 4, 8, 16, 32, 64, 128])


class DEMError(Exception):
    """DEM derivative computation failed."""


def fill_depressions(dem: np.ndarray, eps: float = 1e-4) -> np.ndarray:
    """Priority-flood depression filling (Barnes 2014) with epsilon.

    Returns the hydrologically conditioned DEM: pits filled, flats given a
    small downward gradient toward their spill point.
    """
    dem = dem.astype("float64", copy=True)
    n_rows, n_cols = dem.shape
    filled = np.full_like(dem, np.inf)
    closed = np.zeros(dem.shape, dtype=bool)
    heap: list[tuple[float, int, int]] = []

    def push(r: int, c: int) -> None:
        if not closed[r, c]:
            closed[r, c] = True
            heapq.heappush(heap, (dem[r, c], r, c))

    for r in range(n_rows):
        push(r, 0)
        push(r, n_cols - 1)
    for c in range(1, n_cols - 1):
        push(0, c)
        push(n_rows - 1, c)

    while heap:
        z, r, c = heapq.heappop(heap)
        filled[r, c] = z
        for dr, dc in _D8:
            rr, cc = r + dr, c + dc
            if 0 <= rr < n_rows and 0 <= cc < n_cols and not closed[rr, cc]:
                closed[rr, cc] = True
                zz = max(dem[rr, cc], z + eps)
                dem[rr, cc] = zz
                heapq.heappush(heap, (zz, rr, cc))
    if not np.isfinite(filled).all():
        raise DEMError("fill_depressions left unfilled cells (non-finite DEM?)")
    return filled


def flow_direction(filled: np.ndarray) -> np.ndarray:
    """D8 flow direction codes (1..8); 0 where no lower neighbour exists.

    For each neighbour offset (dr, dc) the drop toward that neighbour is
    (filled[r, c] - filled[r+dr, c+dc]) / distance; the steepest wins.
    """
    n_rows, n_cols = filled.shape
    steepest = np.zeros(filled.shape, dtype=np.uint8)
    best_drop = np.full(filled.shape, -np.inf)
    for k, (dr, dc) in enumerate(_D8):
        dist = 1.0 if (dr == 0 or dc == 0) else float(np.sqrt(2.0))
        tr0, tr1 = max(0, -dr), min(n_rows, n_rows - dr)
        tc0, tc1 = max(0, -dc), min(n_cols, n_cols - dc)
        drop = np.full(filled.shape, -np.inf)
        drop[tr0:tr1, tc0:tc1] = (
            filled[tr0:tr1, tc0:tc1]
            - filled[tr0 + dr:tr1 + dr, tc0 + dc:tc1 + dc]
        ) / dist
        better = drop > best_drop
        best_drop = np.where(better, drop, best_drop)
        steepest = np.where(better, _D8_CODES[k], steepest)
    return steepest


def flow_accumulation(direction: np.ndarray) -> np.ndarray:
    """Number of upstream cells draining through each cell (D8)."""
    n_rows, n_cols = direction.shape
    acc = np.ones((n_rows, n_cols), dtype=np.float64)
    # process in an order where every upstream cell precedes its receiver:
    # iterate by descending filled elevation is ideal, but D8 over the
    # FILLED surface guarantees a DAG we can topologically relax by
    # repeated sweeps of the steepest-receiver map. Simple Kahn approach:
    indegree = np.zeros((n_rows, n_cols), dtype=np.int32)
    receiver_r = np.full((n_rows, n_cols), -1, dtype=np.int32)
    receiver_c = np.full((n_rows, n_cols), -1, dtype=np.int32)
    for k, (dr, dc) in enumerate(_D8):
        code = int(_D8_CODES[k])
        mask = direction == code
        rr = np.where(mask, np.arange(n_rows)[:, None] + dr, -1)
        cc = np.where(mask, np.arange(n_cols)[None, :] + dc, -1)
        valid = mask & (rr >= 0) & (rr < n_rows) & (cc >= 0) & (cc < n_cols)
        receiver_r[valid] = rr[valid]
        receiver_c[valid] = cc[valid]
        indegree[rr[valid], cc[valid]] += 1
    # cells with no receiver feed nothing; queue by indegree
    from collections import deque

    queue = deque(zip(*np.where((direction > 0) & (indegree == 0))))
    remaining = indegree.copy()
    while queue:
        r, c = queue.popleft()
        rr, cc = receiver_r[r, c], receiver_c[r, c]
        if rr < 0:
            continue
        acc[rr, cc] += acc[r, c]
        remaining[rr, cc] -= 1
        if remaining[rr, cc] == 0:
            queue.append((rr, cc))
    return acc


def hand_fast(
    filled: np.ndarray, drainage_mask: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized height above nearest drainage (2-D nearest-cell lookup).

    Approximation of :func:`hand` (which walks the flow path downstream):
    here the elevation of the spatially nearest drainage cell is used
    instead, via ``scipy.ndimage.distance_transform_edt`` with
    ``return_indices``. This is the standard fast HAND variant; it differs
    from the along-flow HAND mainly on hillslopes between parallel
    channels. Documented as an approximation in DECISIONS (Round 4 T2);
    validate with :func:`hand` on a window before relying on it.

    Returns ``(hand, distance)`` where ``distance`` is the Euclidean
    distance in cells to the nearest drainage cell. Drainage cells have
    HAND 0 by definition.
    """
    from scipy.ndimage import distance_transform_edt

    if not drainage_mask.any():
        nan = np.full(filled.shape, np.nan)
        return nan, nan
    dist, (ir, ic) = distance_transform_edt(
        ~drainage_mask, return_indices=True
    )
    nearest_elev = filled[ir, ic]
    with np.errstate(invalid="ignore"):
        hand_val = np.where(np.isfinite(nearest_elev), filled - nearest_elev, np.nan)
    return np.clip(hand_val, 0.0, None), dist


def hand(
    filled: np.ndarray, direction: np.ndarray, accumulation: np.ndarray,
    acc_threshold: float = 1000.0,
) -> np.ndarray:
    """Height above nearest drainage (HAND).

    For each cell, the elevation of the first downstream cell whose
    accumulation is at/above ``acc_threshold`` (a drainage line) is found
    with memoized D8 walks; HAND = filled[cell] - that elevation, clipped
    at 0. Drainage cells have HAND 0 by definition.
    """
    n_rows, n_cols = filled.shape
    drainage = accumulation >= acc_threshold
    d_elev = np.full(filled.shape, np.nan)
    d_elev[drainage] = filled[drainage]
    code_to_offset = {int(code): _D8[k] for k, code in enumerate(_D8_CODES)}
    for r, c in np.argwhere(~drainage):
        if np.isfinite(d_elev[r, c]):
            continue
        path: list[tuple[int, int]] = []
        rr, cc = int(r), int(c)
        while 0 <= rr < n_rows and 0 <= cc < n_cols and not np.isfinite(d_elev[rr, cc]):
            path.append((rr, cc))
            code = int(direction[rr, cc])
            if code == 0:  # edge/sea sink: treat as drainage
                d_elev[rr, cc] = filled[rr, cc]
                break
            dr, dc = code_to_offset[code]
            rr, cc = rr + dr, cc + dc
        if 0 <= rr < n_rows and 0 <= cc < n_cols and np.isfinite(d_elev[rr, cc]):
            terminal = d_elev[rr, cc]
        elif path:
            terminal = filled[path[-1]]
        else:
            continue
        for pr, pc in path:
            d_elev[pr, pc] = terminal
    with np.errstate(invalid="ignore"):
        hand_val = np.where(np.isfinite(d_elev), filled - d_elev, np.nan)
    return np.clip(hand_val, 0.0, None)


def slope_degrees(dem: np.ndarray, pixel_m: float) -> np.ndarray:
    """Slope in degrees (central differences), matching baseline.detection."""
    gy, gx = np.gradient(dem.astype("float64"), pixel_m, pixel_m)
    return np.degrees(np.arctan(np.hypot(gx, gy)))

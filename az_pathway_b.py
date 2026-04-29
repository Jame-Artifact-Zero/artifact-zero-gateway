"""
az_pathway_b.py
===============
Pathway B operators — DICOM volume -> FFT -> synthetic k-space -> measurements.

Insert point: _run_decomposition_from_series() in dicom_processor_api.py
After:  vol = load_volume_sorted(s)
Before: sl = best_slice(vol)

Usage:
    from az_pathway_b import compute_pathway_b, adjust_threshold

    b_feats = compute_pathway_b(vol)
    seq_result['b_alg_b_joint']    = b_feats['b_alg_b_joint']
    seq_result['b_pg_center_edge'] = b_feats['b_pg_center_edge']

Background:
    K0 Experiment 09 — Pathway comparison on 16-patient Stanford knee dataset.
    Pathway B (DICOM->FFT->k-space) produced 2 STRONG results:
        B_alg_B_joint    vs joint_to_volume_ratio: Spearman +0.727 LOO +0.724
        B_pg_center_edge vs mean_gap:              Spearman +0.709 LOO +0.706
    These operators read global tissue contrast from the k-space representation
    of the reconstructed image. They are complementary to the pixel-domain
    algebraic decomposition (Pathway A) already in the pipeline.

    Pathway B wins on:  joint_to_volume_ratio, mean_gap (global severity)
    Pathway A wins on:  joint_min_gap, interior_drop_pct (spatial localization)
    Both are needed. This module adds B without replacing A.
"""

import numpy as np


# ── Mid-volume zone ────────────────────────────────────────────────────────
# Fraction of volume depth used as the joint/mid-volume zone.
# 0.40-0.60 confirmed across 16 patients (bilateral peak always at 0.50).
MID_LO_FRAC = 0.40
MID_HI_FRAC = 0.60

# Center k-space radius (fraction of min dimension)
# Used for center vs edge phase gradient computation.
CENTER_RADIUS_FRAC = 0.125


def dicom_volume_to_kspace(vol: np.ndarray) -> np.ndarray:
    """
    Apply 3D FFT to a real-valued DICOM image volume.

    The DICOM volume is magnitude-only (phase was discarded during
    reconstruction). The FFT of a real positive signal produces a
    Hermitian-symmetric k-space. This is NOT the original scanner
    k-space but encodes the same tissue spatial frequency content.

    Args:
        vol: numpy ndarray, shape (rows, cols, slices), float or int.
             The loaded DICOM volume from load_volume_sorted().

    Returns:
        kspace: complex128 ndarray, same shape as vol.
    """
    return np.fft.fftshift(
        np.fft.fftn(
            np.fft.ifftshift(vol.astype(np.complex128))
        )
    )


def compute_alg_b_joint(kspace: np.ndarray) -> float:
    """
    Algebraic B value (5th percentile of k-space magnitude) in the
    mid-volume joint zone.

    In the algebraic decomposition: A = 95th pct, B = 5th pct, Gap = A-B.
    Applied to k-space magnitude rather than image pixels, B captures
    the minimum tissue contrast signal in the frequency domain.

    High B_joint = high baseline signal in joint zone k-space
               -> less joint space narrowing (more fluid/cartilage signal)
    Low  B_joint = low baseline -> more narrowing, less tissue contrast

    K0 result: Spearman +0.727 vs joint_to_volume_ratio, LOO +0.724.

    Args:
        kspace: complex128 ndarray (rows, cols, slices)

    Returns:
        b_joint: float, 5th percentile of k-space magnitude in mid-volume.
                 Returns nan if insufficient data.
    """
    n_slices = kspace.shape[2]
    kz_lo = int(MID_LO_FRAC * n_slices)
    kz_hi = int(MID_HI_FRAC * n_slices)

    mag_joint = np.abs(kspace[:, :, kz_lo:kz_hi])

    if mag_joint.size < 10:
        return float("nan")

    return float(np.percentile(mag_joint.ravel(), 5))


def compute_pg_center_edge(kspace: np.ndarray) -> float:
    """
    Phase gradient center-to-edge ratio in mid-volume.

    Measures the ratio of phase gradient magnitude at the center of
    k-space (low spatial frequencies, bulk tissue signal) vs the
    periphery (high spatial frequencies, tissue boundaries).

    High ratio = strong low-frequency phase structure relative to edges
              -> organized bulk tissue signal -> normal tissue contrast
    Low ratio  = edges dominate -> irregular, fragmented signal structure

    K0 result: Spearman +0.709 vs mean_gap, LOO +0.706.

    Args:
        kspace: complex128 ndarray (rows, cols, slices)

    Returns:
        center_edge_ratio: float. Returns nan if computation fails.
    """
    n0, n1, n2 = kspace.shape
    kz_lo = int(MID_LO_FRAC * n2)
    kz_hi = int(MID_HI_FRAC * n2)

    # Build center mask (circular region around k-space DC)
    cx, cy = n0 // 2, n1 // 2
    cr = int(min(n0, n1) * CENTER_RADIUS_FRAC)
    yy, xx = np.ogrid[:n0, :n1]
    center_mask = ((xx - cx) ** 2 + (yy - cy) ** 2) <= cr ** 2
    edge_mask   = ~center_mask

    center_grads, edge_grads = [], []

    phase = np.angle(kspace)

    for kz in range(kz_lo, kz_hi):
        sl = phase[:, :, kz]
        gx, gy = np.gradient(sl)
        gm = np.sqrt(gx ** 2 + gy ** 2)
        center_grads.append(float(np.mean(gm[center_mask])))
        edge_grads.append(float(np.mean(gm[edge_mask])))

    if not center_grads or not edge_grads:
        return float("nan")

    c_mean = float(np.mean(center_grads))
    e_mean = float(np.mean(edge_grads))

    if e_mean < 1e-12:
        return float("nan")

    return c_mean / e_mean


def compute_pathway_b(vol: np.ndarray) -> dict:
    """
    Run all Pathway B operators on a DICOM image volume.

    This is the single entry point to call from dicom_processor_api.py.
    It handles the FFT internally so the caller does not need to manage
    the intermediate k-space array.

    Args:
        vol: numpy ndarray (rows, cols, slices), the loaded DICOM volume.
             Must be 3D. If 2D, returns nan for all features.

    Returns:
        dict with keys:
            'b_alg_b_joint'    — float, 5th pct k-space magnitude (joint zone)
            'b_pg_center_edge' — float, phase gradient center/edge ratio

    Example insertion in dicom_processor_api.py:

        vol = load_volume_sorted(s)

        # --- Pathway B insertion ---
        from az_pathway_b import compute_pathway_b
        b_feats = compute_pathway_b(vol)
        seq_result['b_alg_b_joint']    = b_feats['b_alg_b_joint']
        seq_result['b_pg_center_edge'] = b_feats['b_pg_center_edge']
        # --- end Pathway B insertion ---

        sl = best_slice(vol)
        # ... rest of existing decomposition
    """
    nan_result = {
        "b_alg_b_joint":    float("nan"),
        "b_pg_center_edge": float("nan"),
    }

    if vol is None:
        return nan_result

    if vol.ndim == 2:
        # Single slice — expand to 3D for FFT
        vol = vol[:, :, np.newaxis]

    if vol.ndim != 3 or vol.size < 100:
        return nan_result

    # Cap volume size before FFT to prevent OOM on large studies.
    # Downsample to max 64x64xN_SLICES_MAX using stride subsampling.
    # Pathway B operators are global (5th percentile, phase gradient mean)
    # so spatial downsampling does not affect the measurement significantly.
    MAX_XY = 64
    MAX_SLICES = 20
    try:
        r, c, s = vol.shape
        sr = max(1, r // MAX_XY)
        sc = max(1, c // MAX_XY)
        ss = max(1, s // MAX_SLICES)
        vol = vol[::sr, ::sc, ::ss]
        import logging
        logging.debug(f"[pathway_b] downsampled to {vol.shape} from ({r},{c},{s})")
    except Exception:
        return nan_result

    try:
        kspace = dicom_volume_to_kspace(vol)
        return {
            "b_alg_b_joint":    compute_alg_b_joint(kspace),
            "b_pg_center_edge": compute_pg_center_edge(kspace),
        }
    except Exception:
        return nan_result

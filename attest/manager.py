"""
ATTEST Manager-side operations (private, off-chain).

ManagerState holds ONLY what the manager needs to compute future epoch updates:
    compass  : COMPASS instance (pp + sk)
    Z        : current aggregate ring element  (private, needed for ΔZ)
    Acc      : current accumulator digest      (cached from Z; published to DLT)
    L        : [(β_i, c1_i, c2_i, z_i), ...]  (private, needed for ΔZ_del)
    members  : [VC, ...]                       (ordered, matches L)
    epoch    : int

The manager does NOT own the BF or the epoch log — those live on the DLT.
After each epoch the manager hands an EpochPackage to the DLT via dlt_publish().
"""
import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from compass.compass import COMPASS
from compass.utils import HashToLat, negacyclic_conv, zero_center

from .credential import VC, H_tag
from .bloom_filter import BloomFilter
from .params import PARAM_SET, K_MAX, BF_FP_RATE, LAMBDA_SEC


# ------------------------------------------------------------------
# Epoch package — what the manager hands to the DLT each epoch
# ------------------------------------------------------------------

@dataclass
class EpochPackage:
    """
    Broadcast material for one epoch transition.

    delta_Z  : np.ndarray       — ΔZ ring element (~4 KB at N=1024)
    bf_delta : BloomFilter|None — BF_Δ bitmap (~12 KB); None for add-only epochs
    acc_new  : np.ndarray       — new accumulator digest (32 bytes)
    epoch    : int
    sig      : bytes            — manager signature over (epoch||acc_new||ΔZ||BF_Δ)
    """
    delta_Z  : np.ndarray
    bf_delta : Optional[BloomFilter]
    acc_new  : np.ndarray
    epoch    : int
    sig      : bytes

    def size_bytes(self, q: int = None) -> int:
        """
        Serialised wire size in bytes.

        Uses bytes_per_q(q) bytes per ring coefficient (4 bytes for set2 q≈2^32).
        If q is not supplied, falls back to 8 bytes/coeff (numpy int64 size).
        """
        from compass.utils import bytes_per_q as _bpq
        bpq = _bpq(q) if q is not None else 8
        dz  = len(self.delta_Z) * bpq
        bf  = self.bf_delta.size_bytes() if self.bf_delta is not None else 0
        acc = len(self.acc_new) * bpq
        return dz + bf + acc + len(self.sig)


# ------------------------------------------------------------------
# Manager state  (private, off-chain)
# ------------------------------------------------------------------

@dataclass
class ManagerState:
    compass : COMPASS
    Z       : np.ndarray   # current aggregate (N coeffs, mod q)
    Acc     : np.ndarray   # cached accumulator digest (derived from Z)
    L       : list         # [(β_i, c1_i, c2_i, z_i), ...]
    members : List[VC]     # ordered member list (matches L indices)
    epoch   : int = 0


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def manager_setup(K: int = K_MAX, param_set: str = PARAM_SET,
                  epoch_label: str = "2026-01") -> "ManagerState":
    """
    Initialise a fresh ATTEST manager (private state only).

    Call dlt_init(mgr) afterwards to create the corresponding DLT state.
    """
    c = COMPASS(K=K, param_set=param_set, lambda_sec=LAMBDA_SEC)
    c.Setup(epoch=epoch_label)
    Z   = np.zeros(c.N, dtype=np.int64)
    Acc = c.HAcc(c.PF.F(Z))
    return ManagerState(compass=c, Z=Z, Acc=Acc, L=[], members=[], epoch=0)


def batch_add(state: ManagerState,
              new_vcs: List[VC]) -> Tuple[ManagerState, List, EpochPackage]:
    """
    BatchAdd: accumulate m new VCs, compute ΔZ_add, issue witnesses.

    Returns
    -------
    state'    : updated ManagerState
    witnesses : List[WitnessPacked] — one per new VC
    pkg       : EpochPackage to hand to dlt_publish()  (bf_delta=None)
    """
    c = state.compass

    new_f = []
    for vc in new_vcs:
        f = HashToLat(vc.issuer_id + vc.cred_id + vc.holder_pk, c.N)
        vc._f = f
        new_f.append(f)

    Z_new, L_new = _accumulate_incremental(c, state.Z, new_f)
    delta_Z = (Z_new - state.Z) % c.q
    Acc_new = c.HAcc(c.PF.F(Z_new))
    epoch_new = state.epoch + 1

    pkg = EpochPackage(
        delta_Z  = delta_Z,
        bf_delta = None,
        acc_new  = Acc_new,
        epoch    = epoch_new,
        sig      = _sign_epoch(c, epoch_new, Acc_new, delta_Z, None),
    )

    full_L    = state.L + L_new
    witnesses = [c.Witness(Z_new, full_L, len(state.L) + i)
                 for i in range(len(new_vcs))]

    state_new = ManagerState(
        compass = c,
        Z       = Z_new,
        Acc     = Acc_new,
        L       = full_L,
        members = state.members + list(new_vcs),
        epoch   = epoch_new,
    )
    return state_new, witnesses, pkg


def batch_del(state: ManagerState,
              del_vcs: List[VC]) -> Tuple[ManagerState, EpochPackage]:
    """
    BatchDel: revoke ℓ VCs.

    Computes ΔZ_del = -Σ β_i z_i and BF_Δ (tags of revoked VCs).
    Does NOT touch the DLT BF — hands BF_Δ to the caller to pass to dlt_publish().

    Returns
    -------
    state'  : updated ManagerState (revoked entries removed from L/members)
    pkg     : EpochPackage to hand to dlt_publish()
    """
    c = state.compass
    cred_id_set = {vc.cred_id for vc in del_vcs}
    revoke_idx  = [i for i, m in enumerate(state.members)
                   if m.cred_id in cred_id_set]
    if not revoke_idx:
        raise ValueError("None of the specified VCs are current members.")

    # BF_Δ: insert H_tag for each revoked VC  (same K/p as the DLT's BF)
    bf_delta = BloomFilter(K=c.K, p=BF_FP_RATE)
    for i in revoke_idx:
        bf_delta.insert(H_tag(state.members[i]))

    # ΔZ_del = -Σ β_i z_i
    delta_Z = np.zeros(c.N, dtype=np.int64)
    for i in revoke_idx:
        beta_i, _c1, _c2, z_i = state.L[i]
        delta_Z = (delta_Z - beta_i * z_i) % c.q

    Z_new   = (state.Z + delta_Z) % c.q
    Acc_new = c.HAcc(c.PF.F(Z_new))
    epoch_new = state.epoch + 1

    keep        = [i for i in range(len(state.members)) if i not in set(revoke_idx)]
    L_new       = [state.L[i] for i in keep]
    members_new = [state.members[i] for i in keep]

    pkg = EpochPackage(
        delta_Z  = delta_Z,
        bf_delta = bf_delta,
        acc_new  = Acc_new,
        epoch    = epoch_new,
        sig      = _sign_epoch(c, epoch_new, Acc_new, delta_Z, bf_delta),
    )

    state_new = ManagerState(
        compass = c,
        Z       = Z_new,
        Acc     = Acc_new,
        L       = L_new,
        members = members_new,
        epoch   = epoch_new,
    )
    return state_new, pkg


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _accumulate_incremental(c: COMPASS, Z_current: np.ndarray,
                             new_f: list) -> Tuple[np.ndarray, list]:
    """Rejection-sampling loop for new members, appended to Z_current."""
    F_hat = [c.PF.F(f) for f in new_f]
    Z     = Z_current.copy()
    L_new = []

    for i, f in enumerate(new_f):
        attempts = 0
        while attempts < 30_000:
            attempts += 1
            y = np.random.normal(0.0, c.sigma, size=c.N).round().astype(np.int64)
            if np.max(np.abs(y)) > c.sy:
                continue
            y_hat = c.PF.F(y)
            c1 = c.Hc1(y_hat, F_hat[i])
            c2 = c.Hc2(y_hat, c.pk)
            u  = (negacyclic_conv(c.sk, c1, c.q) +
                  negacyclic_conv(f,    c2, c.q)) % c.q
            z  = zero_center((u + y) % c.q, c.q)
            u  = zero_center(u, c.q)
            if np.linalg.norm(z) > c.Boundz:
                continue
            if not c._rejection_sampling(u, z):
                continue
            break
        else:
            raise RuntimeError(f"Rejection sampling failed for member {i}")

        beta_i = c.Hbeta(c1, c2, F_hat[i])
        Z = (Z + beta_i * z) % c.q
        L_new.append((beta_i, c1, c2, z))

    return Z, L_new


def _sign_epoch(c: COMPASS, epoch: int, acc_new: np.ndarray,
                delta_Z: np.ndarray,
                bf_delta: Optional[BloomFilter]) -> bytes:
    """Keyed-hash prototype of manager signature over epoch material."""
    from compass.utils import vec_modq_to_bytes
    h = hashlib.shake_256()
    h.update(b"ATTEST-EPOCH\x00")
    h.update(epoch.to_bytes(4, "big"))
    h.update(vec_modq_to_bytes(acc_new, c.q))
    h.update(vec_modq_to_bytes(delta_Z, c.q))
    if bf_delta is not None:
        h.update(bf_delta.serialize())
    h.update(c.sk.astype(np.int8).tobytes())
    return h.digest(32)

"""
ATTEST DLT-side state and relay state.

DLTState (public, on-chain):
    Acc       : current accumulator digest
    BF        : authoritative revocation Bloom filter
    epoch_log : dict[epoch → EpochPackage]  (for device catch-up)
    epoch     : current epoch number

RelayState (cached subset of DLT, per-relay):
    Acc       : cached accumulator digest
    BF        : cached BF (may be one or more epochs stale)
    epoch_log : cached packages from DLT (for serving to devices)
    epoch     : epoch at last sync

Separation of concerns
----------------------
- Manager computes epoch packages and calls dlt_publish().
- DLT owns the BF and merges BF_Δ on publish.
- Devices fetch packages from DLT (direct) or relay (indirect).
- Gate check (BF.Query) happens at DLT or relay BEFORE serving ΔZ to device.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .bloom_filter import BloomFilter
from .credential import VC, H_tag
from .manager import EpochPackage, ManagerState
from .params import K_MAX, BF_FP_RATE


# ------------------------------------------------------------------
# DLT state
# ------------------------------------------------------------------

@dataclass
class DLTState:
    Acc       : np.ndarray
    BF        : BloomFilter
    epoch_log : Dict[int, EpochPackage]   # epoch → EpochPackage
    epoch     : int
    K         : int
    p         : float


def dlt_init(mgr: ManagerState) -> DLTState:
    """
    Create an initial DLT state from a freshly set-up manager.

    The DLT starts with the manager's initial (empty) accumulator,
    an empty BF, and an empty epoch log.
    """
    return DLTState(
        Acc       = mgr.Acc.copy(),
        BF        = BloomFilter(K=mgr.compass.K, p=BF_FP_RATE),
        epoch_log = {},
        epoch     = mgr.epoch,
        K         = mgr.compass.K,
        p         = BF_FP_RATE,
    )


def dlt_publish(dlt: DLTState, pkg: EpochPackage) -> DLTState:
    """
    Publish an EpochPackage to the DLT.

    - Updates Acc to pkg.acc_new.
    - Merges BF_Δ into the authoritative BF (if revocation epoch).
    - Appends pkg to epoch_log.

    Returns updated DLTState (immutable-style: original is unchanged).
    """
    # Merge BF_Δ into a fresh copy of the BF
    bf_new = BloomFilter(K=dlt.K, p=dlt.p, _bits=bytearray(dlt.BF.serialize()))
    if pkg.bf_delta is not None:
        bf_new.merge(pkg.bf_delta)

    new_log = dict(dlt.epoch_log)
    new_log[pkg.epoch] = pkg

    return DLTState(
        Acc       = pkg.acc_new.copy(),
        BF        = bf_new,
        epoch_log = new_log,
        epoch     = pkg.epoch,
        K         = dlt.K,
        p         = dlt.p,
    )


def dlt_gate_check(dlt: DLTState, vc: VC) -> bool:
    """
    BF gate check on the DLT side.

    Returns True  → credential NOT revoked → serve epoch update to device.
    Returns False → credential IS revoked  → deny update request.

    This is the gatekeeping step verified by Tamarin lemmas
    update_gatekeeping_dlt and update_gatekeeping_relay.
    """
    return not dlt.BF.query(H_tag(vc))


def dlt_fetch_updates(dlt: DLTState,
                      from_epoch: int,
                      to_epoch: int) -> List[EpochPackage]:
    """
    Return the list of EpochPackages for epochs (from_epoch, to_epoch]
    in ascending order — used by devices for catch-up.

    Raises KeyError if any epoch in the range is missing from the log.
    """
    result = []
    for e in range(from_epoch + 1, to_epoch + 1):
        if e not in dlt.epoch_log:
            raise KeyError(f"Epoch {e} not found in DLT epoch log.")
        result.append(dlt.epoch_log[e])
    return result


# ------------------------------------------------------------------
# Relay state
# ------------------------------------------------------------------

@dataclass
class RelayState:
    """
    Eligible relay: caches (Acc, BF, epoch_log) from the DLT.

    An eligible relay enforces the same BF gate as the DLT before
    forwarding epoch packages to devices.  Its BF may be one or
    more epochs stale if it has not yet synced.
    """
    Acc       : np.ndarray
    BF        : BloomFilter
    epoch_log : Dict[int, EpochPackage]
    epoch     : int        # epoch at last sync


def relay_init(dlt: DLTState) -> RelayState:
    """Create a relay with a full snapshot of the current DLT state."""
    return relay_sync(None, dlt)


def relay_sync(relay: Optional[RelayState], dlt: DLTState) -> RelayState:
    """
    Sync relay state from DLT (full snapshot).

    In a real deployment this would be an incremental delta; for the
    prototype we copy the full state.
    """
    return RelayState(
        Acc       = dlt.Acc.copy(),
        BF        = BloomFilter(K=dlt.K, p=dlt.p,
                                _bits=bytearray(dlt.BF.serialize())),
        epoch_log = dict(dlt.epoch_log),
        epoch     = dlt.epoch,
    )


def relay_gate_check(relay: RelayState, vc: VC) -> bool:
    """
    BF gate check on the relay side (using cached BF).

    Same logic as dlt_gate_check; may be stale by relay.epoch lag.
    Returns True → serve update; False → deny.
    """
    return not relay.BF.query(H_tag(vc))


def relay_fetch_updates(relay: RelayState,
                        from_epoch: int,
                        to_epoch: int) -> List[EpochPackage]:
    """
    Return epoch packages from relay's local cache for catch-up.
    """
    result = []
    for e in range(from_epoch + 1, to_epoch + 1):
        if e not in relay.epoch_log:
            raise KeyError(f"Epoch {e} not in relay cache.")
        result.append(relay.epoch_log[e])
    return result

"""
ATTEST Device-side operations.

    wit_update(compass, pkg, witness, bf_local, vc)
        → (witness', bf_local')

        Device applies one epoch package after receiving it from DLT or relay.
        The DLT/relay BF gate check is NOT performed here — it happens on the
        DLT/relay side (dlt_gate_check / relay_gate_check) before the device
        is served the package at all.

        Extended deployment adds a device-side self-gate: if the device's own
        local BF already contains its tag, it knows it is revoked and refuses
        to apply the update.

    verify_with_revocation(compass, acc, vc, witness, bf_local)
        → bool

        Baseline deployment: bf_local=None  → accumulator check only.
        Extended deployment: bf_local set   → BF check first, then accumulator.
"""
import hashlib
from typing import Optional, Tuple

import numpy as np

from compass.compass import COMPASS
from compass.utils import WitnessPacked

from .credential import VC, H_tag
from .bloom_filter import BloomFilter
from .manager import EpochPackage


def wit_update(c: COMPASS,
               pkg: EpochPackage,
               witness_packed: WitnessPacked,
               bf_local: Optional[BloomFilter],
               vc: VC) -> Tuple[WitnessPacked, Optional[BloomFilter]]:
    """
    WitUpdate — device-side epoch update.

    Steps
    -----
    1. Verify manager signature on pkg.
    2. Self-revocation gate (extended only): if bf_local contains H_tag(vc) → raise.
    3. Z_i* = Z_i + ΔZ  (single ring addition mod q).
    4. BF_local* = BF_local ∪ BF_Δ  (bitwise OR, extended deployment only).

    Parameters
    ----------
    c              : COMPASS instance (pp)
    pkg            : EpochPackage received from DLT or relay
    witness_packed : current packed witness (Z_i, z_i, c1, c2)
    bf_local       : device's local BF — None for baseline deployment
    vc             : this device's own credential (for self-gate tag check)
    """
    # 1. Verify manager signature
    _verify_sig(c, pkg)

    # 2. Self-gate (extended deployment): refuse if already marked revoked locally
    if bf_local is not None and bf_local.query(H_tag(vc)):
        raise RuntimeError("WitUpdate denied: credential is revoked in local BF.")

    # 3. Z_i* = Z_i + ΔZ
    Zi, zi, ci1, ci2 = c.deserialize_witness(witness_packed.data)
    Zi_new = (Zi + pkg.delta_Z) % c.q
    witness_new = c.serialize_witness((Zi_new, zi, ci1, ci2))

    # 4. Merge BF_Δ (extended deployment only)
    bf_new = bf_local
    if bf_local is not None and pkg.bf_delta is not None:
        bf_new = BloomFilter(K=bf_local.K, p=bf_local.p,
                             _bits=bytearray(bf_local.serialize()))
        bf_new.merge(pkg.bf_delta)

    return witness_new, bf_new


def verify_with_revocation(c: COMPASS,
                            acc: np.ndarray,
                            vc: VC,
                            witness_packed: WitnessPacked,
                            bf_local: Optional[BloomFilter]) -> bool:
    """
    VerifyWithRevocation — two-phase verification.

    Phase 1 (extended deployment, bf_local set):
        BF.Query(bf_local, H_tag(vc)) == 1  →  return False (revoked).

    Phase 2:
        COMPASS.Verify(pp, Acc, f, witness).
    """
    if bf_local is not None and bf_local.query(H_tag(vc)):
        return False

    f = vc._f
    if f is None:
        from compass.utils import HashToLat
        f = HashToLat(vc.issuer_id + vc.cred_id + vc.holder_pk, c.N)
    return c.Verify(acc, f, witness_packed)


# ------------------------------------------------------------------
# Internal
# ------------------------------------------------------------------

def _verify_sig(c: COMPASS, pkg: EpochPackage) -> None:
    from compass.utils import vec_modq_to_bytes
    h = hashlib.shake_256()
    h.update(b"ATTEST-EPOCH\x00")
    h.update(pkg.epoch.to_bytes(4, "big"))
    h.update(vec_modq_to_bytes(pkg.acc_new, c.q))
    h.update(vec_modq_to_bytes(pkg.delta_Z, c.q))
    if pkg.bf_delta is not None:
        h.update(pkg.bf_delta.serialize())
    h.update(c.sk.astype(np.int8).tobytes())
    if h.digest(32) != pkg.sig:
        raise ValueError("EpochPackage signature verification failed.")

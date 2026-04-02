"""
Verifiable Credential (VC) structure and H_tag for ATTEST.

H_tag maps a VC to a fixed-length status tag used as the BF element.
Domain separation: SHAKE256("ATTEST-TAG" || issuerID || credID || pk_bytes)
"""
import hashlib
from dataclasses import dataclass, field
from typing import Optional
import numpy as np


@dataclass
class VC:
    """
    Minimal verifiable credential structure for ATTEST.

    Fields
    ------
    issuer_id : bytes   — issuer identifier (e.g. DID or public key hash)
    cred_id   : bytes   — unique credential identifier
    holder_pk : bytes   — holder's public key bytes (serialized)
    issued_epoch : int  — epoch at which credential was issued
    expires_epoch : Optional[int] — expiry epoch (None = no expiry)
    """
    issuer_id     : bytes
    cred_id       : bytes
    holder_pk     : bytes
    issued_epoch  : int
    expires_epoch : Optional[int] = None

    # lattice vector f ∈ B_∞(1) derived at issuance (set by Manager)
    _f: Optional[np.ndarray] = field(default=None, repr=False, compare=False)


def H_tag(vc: VC) -> bytes:
    """
    H_tag(vc) → 32-byte status tag used as the BF element.

    SHAKE256( b"ATTEST-TAG" || len(issuer_id) || issuer_id
                             || len(cred_id)   || cred_id
                             || holder_pk )
    """
    h = hashlib.shake_256()
    h.update(b"ATTEST-TAG\x00")
    h.update(len(vc.issuer_id).to_bytes(2, "big") + vc.issuer_id)
    h.update(len(vc.cred_id).to_bytes(2, "big")   + vc.cred_id)
    h.update(vc.holder_pk)
    return h.digest(32)

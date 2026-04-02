"""
ATTEST — Accumulator-based Tamarin-verified Trust for crEdential Systems in IoT

A post-quantum verifiable credential revocation framework built on the
COMPASS lattice-based accumulator (INDOCRYPT 2025).

Quick start
-----------
    from attest import SET1, SET2, manager_setup, dlt_init, dlt_publish
    from attest import batch_add, batch_del, dlt_gate_check
    from attest import wit_update, verify_with_revocation
    from attest import VC, BloomFilter

    mgr = manager_setup(param_set="set2")  # or "set1" for quick tests
    dlt = dlt_init(mgr)

    vcs = [VC(issuer_id=b"issuer", cred_id=f"c{i}".encode(),
               holder_pk=b"pk" * 16, issued_epoch=0) for i in range(5)]
    mgr, witnesses, pkg = batch_add(mgr, vcs)
    dlt = dlt_publish(dlt, pkg)

    # Verify (baseline — no local BF)
    ok = verify_with_revocation(mgr.compass, dlt.Acc, vcs[0], witnesses[0], None)
"""

# Parameter sets (choose before calling manager_setup)
from .params import SET1, SET2, PARAM_SETS, ParamSet

# Credential
from .credential import VC, H_tag

# Bloom filter
from .bloom_filter import BloomFilter

# Manager (private, off-chain)
from .manager import manager_setup, batch_add, batch_del, ManagerState, EpochPackage

# DLT (public, on-chain)
from .dlt import (
    DLTState, dlt_init, dlt_publish, dlt_gate_check, dlt_fetch_updates,
    RelayState, relay_init, relay_sync, relay_gate_check, relay_fetch_updates,
)

# Device
from .device import wit_update, verify_with_revocation

__version__ = "0.1.0"
__all__ = [
    # params
    "SET1", "SET2", "PARAM_SETS", "ParamSet",
    # credential
    "VC", "H_tag",
    # bloom filter
    "BloomFilter",
    # manager
    "manager_setup", "batch_add", "batch_del", "ManagerState", "EpochPackage",
    # dlt
    "DLTState", "dlt_init", "dlt_publish", "dlt_gate_check", "dlt_fetch_updates",
    "RelayState", "relay_init", "relay_sync", "relay_gate_check", "relay_fetch_updates",
    # device
    "wit_update", "verify_with_revocation",
]

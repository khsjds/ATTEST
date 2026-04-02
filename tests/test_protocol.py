"""
ATTEST end-to-end protocol tests.

Covers all six performance scenarios:
  1. Manager accumulates (BatchAdd)
  2. Manager epoch update — add and revoke
  3. Base model: device direct update via DLT
  4. Base model: device indirect update via relay
  5. Extended model: device indirect update via relay (local BF)
  6. Catch-up: device fetches multiple missed epochs from DLT / relay

Uses set1 (N=512) with small K so tests complete in ~15 s.
"""
import pytest
import numpy as np

from attest.credential import VC, H_tag
from attest.bloom_filter import BloomFilter
from attest.manager import manager_setup, batch_add, batch_del
from attest.dlt import (dlt_init, dlt_publish, dlt_gate_check, dlt_fetch_updates,
                        relay_init, relay_sync, relay_gate_check, relay_fetch_updates)
from attest.device import wit_update, verify_with_revocation

SMALL_K = 20
PARAM   = "set1"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_vcs(n: int, issuer: bytes = b"issuer1") -> list:
    return [
        VC(issuer_id=issuer, cred_id=f"cred_{i}".encode(),
           holder_pk=f"pk_{i}".encode() * 4, issued_epoch=0)
        for i in range(n)
    ]


def _fresh_world(n_members: int = 5):
    """Manager + DLT with n_members already added."""
    mgr = manager_setup(K=SMALL_K, param_set=PARAM)
    dlt = dlt_init(mgr)
    vcs = _make_vcs(n_members)
    mgr, witnesses, pkg = batch_add(mgr, vcs)
    dlt = dlt_publish(dlt, pkg)
    return mgr, dlt, vcs, witnesses


# ==================================================================
# Scenario 1 — Manager accumulates (BatchAdd)
# ==================================================================

class TestManagerAccumulate:

    def test_accumulate_produces_witnesses(self):
        """BatchAdd returns one witness per new VC."""
        mgr, dlt, vcs, witnesses = _fresh_world(4)
        assert len(witnesses) == 4

    def test_accumulate_witness_size(self):
        """set1 witness size = 4,288 bytes (from COMPASS paper Table 2)."""
        mgr, dlt, vcs, witnesses = _fresh_world(2)
        for w in witnesses:
            assert len(w.data) == 4288, f"Unexpected witness size: {len(w.data)}"

    def test_accumulate_dlt_epoch_advances(self):
        """DLT epoch increments after publish."""
        mgr = manager_setup(K=SMALL_K, param_set=PARAM)
        dlt = dlt_init(mgr)
        assert dlt.epoch == 0
        vcs = _make_vcs(3)
        mgr, _, pkg = batch_add(mgr, vcs)
        dlt = dlt_publish(dlt, pkg)
        assert dlt.epoch == 1

    def test_delta_z_constant_size(self):
        """ΔZ is always one ring element regardless of batch size."""
        mgr = manager_setup(K=SMALL_K, param_set=PARAM)
        dlt = dlt_init(mgr)
        for batch in [1, 3, 5]:
            vcs = _make_vcs(batch, issuer=f"issuer_{batch}".encode())
            mgr, _, pkg = batch_add(mgr, vcs)
            dlt = dlt_publish(dlt, pkg)
            assert len(pkg.delta_Z) == mgr.compass.N  # always N coefficients


# ==================================================================
# Scenario 2 — Manager epoch update (add + revoke)
# ==================================================================

class TestManagerEpochUpdate:

    def test_add_epoch_no_bf_delta(self):
        """Add-only epoch: bf_delta must be None."""
        mgr = manager_setup(K=SMALL_K, param_set=PARAM)
        dlt = dlt_init(mgr)
        mgr, _, pkg = batch_add(mgr, _make_vcs(3))
        assert pkg.bf_delta is None

    def test_revoke_epoch_has_bf_delta(self):
        """Revocation epoch: bf_delta must be non-None and non-empty."""
        mgr, dlt, vcs, _ = _fresh_world(3)
        mgr, pkg = batch_del(mgr, [vcs[0]])
        assert pkg.bf_delta is not None
        assert not pkg.bf_delta.is_empty()

    def test_revoked_tag_in_dlt_bf_after_publish(self):
        """After dlt_publish, the revoked VC's tag must be in DLT's BF."""
        mgr, dlt, vcs, _ = _fresh_world(3)
        target = vcs[1]
        mgr, pkg = batch_del(mgr, [target])
        dlt = dlt_publish(dlt, pkg)
        assert dlt.BF.query(H_tag(target))

    def test_non_revoked_tags_absent_from_dlt_bf(self):
        """VCs that were NOT revoked must not be in DLT's BF."""
        mgr, dlt, vcs, _ = _fresh_world(4)
        mgr, pkg = batch_del(mgr, [vcs[0]])
        dlt = dlt_publish(dlt, pkg)
        for vc in vcs[1:]:
            assert not dlt.BF.query(H_tag(vc)), f"{vc.cred_id} incorrectly in BF"


# ==================================================================
# Scenario 3 — Base model: device direct update (DLT path)
# ==================================================================

class TestDirectUpdateDLT:

    def test_dlt_gate_allows_valid_device(self):
        """dlt_gate_check returns True for a non-revoked device."""
        mgr, dlt, vcs, _ = _fresh_world(3)
        mgr, pkg = batch_del(mgr, [vcs[0]])
        dlt = dlt_publish(dlt, pkg)
        for vc in vcs[1:]:
            assert dlt_gate_check(dlt, vc), f"{vc.cred_id} wrongly denied by gate"

    def test_dlt_gate_blocks_revoked_device(self):
        """dlt_gate_check returns False for a revoked device."""
        mgr, dlt, vcs, _ = _fresh_world(3)
        mgr, pkg = batch_del(mgr, [vcs[0]])
        dlt = dlt_publish(dlt, pkg)
        assert not dlt_gate_check(dlt, vcs[0])

    def test_direct_update_and_verify_baseline(self):
        """
        Baseline deployment: device passes DLT gate, applies WitUpdate (no local BF),
        then verifies against the new accumulator.
        """
        mgr, dlt, vcs, witnesses = _fresh_world(4)
        target = vcs[2]
        mgr, pkg = batch_del(mgr, [target])
        dlt = dlt_publish(dlt, pkg)

        for i, vc in enumerate(vcs):
            if vc.cred_id == target.cred_id:
                continue
            assert dlt_gate_check(dlt, vc), "Valid device denied by gate"
            w_new, _ = wit_update(mgr.compass, pkg, witnesses[i], None, vc)
            ok = verify_with_revocation(mgr.compass, dlt.Acc, vc, w_new, None)
            assert ok, f"Device {i} failed verification after direct update"

    def test_direct_update_witness_verifies(self):
        """Restate clearly: witness updated via DLT path verifies correctly."""
        mgr, dlt, vcs, witnesses = _fresh_world(3)
        mgr2, pkg = batch_del(mgr, [vcs[0]])
        dlt2 = dlt_publish(dlt, pkg)

        # Device 1 takes the DLT direct path
        assert dlt_gate_check(dlt2, vcs[1])
        w_new, _ = wit_update(mgr2.compass, pkg, witnesses[1], None, vcs[1])
        assert verify_with_revocation(mgr2.compass, dlt2.Acc, vcs[1], w_new, None)


# ==================================================================
# Scenario 4 — Base model: device indirect update via relay
# ==================================================================

class TestIndirectUpdateRelay:

    def test_relay_sync_mirrors_dlt(self):
        """After relay_sync, relay.epoch == dlt.epoch."""
        mgr, dlt, vcs, _ = _fresh_world(3)
        mgr, pkg = batch_del(mgr, [vcs[0]])
        dlt = dlt_publish(dlt, pkg)
        relay = relay_init(dlt)
        relay = relay_sync(relay, dlt)
        assert relay.epoch == dlt.epoch

    def test_relay_gate_blocks_revoked(self):
        """relay_gate_check blocks a revoked device."""
        mgr, dlt, vcs, _ = _fresh_world(3)
        mgr, pkg = batch_del(mgr, [vcs[0]])
        dlt = dlt_publish(dlt, pkg)
        relay = relay_init(dlt)
        assert not relay_gate_check(relay, vcs[0])

    def test_relay_gate_allows_valid(self):
        """relay_gate_check allows a non-revoked device."""
        mgr, dlt, vcs, _ = _fresh_world(3)
        mgr, pkg = batch_del(mgr, [vcs[0]])
        dlt = dlt_publish(dlt, pkg)
        relay = relay_init(dlt)
        for vc in vcs[1:]:
            assert relay_gate_check(relay, vc)

    def test_indirect_update_via_relay_verifies(self):
        """Device fetches pkg from relay (indirect path), applies WitUpdate, verifies."""
        mgr, dlt, vcs, witnesses = _fresh_world(3)
        mgr2, pkg = batch_del(mgr, [vcs[0]])
        dlt2 = dlt_publish(dlt, pkg)
        relay = relay_init(dlt2)

        # Device 2 takes the relay path
        assert relay_gate_check(relay, vcs[2])
        # Device was issued witness at epoch 1; catch up from epoch 1 → relay.epoch
        pkgs = relay_fetch_updates(relay, from_epoch=1, to_epoch=relay.epoch)
        assert len(pkgs) == 1
        w_new, _ = wit_update(mgr2.compass, pkgs[0], witnesses[2], None, vcs[2])
        assert verify_with_revocation(mgr2.compass, relay.Acc, vcs[2], w_new, None)

    def test_stale_relay_blocks_revoked_after_sync(self):
        """
        A relay that hasn't synced yet has a stale BF.
        After sync it correctly blocks the revoked device.
        """
        mgr, dlt, vcs, _ = _fresh_world(3)
        relay = relay_init(dlt)   # relay synced BEFORE revocation epoch

        # Manager revokes vcs[0], DLT publishes
        mgr2, pkg = batch_del(mgr, [vcs[0]])
        dlt2 = dlt_publish(dlt, pkg)

        # Stale relay: still allows revoked device (pre-sync)
        assert relay_gate_check(relay, vcs[0])  # stale — expected True

        # After sync, relay correctly blocks revoked device
        relay = relay_sync(relay, dlt2)
        assert not relay_gate_check(relay, vcs[0])


# ==================================================================
# Scenario 5 — Extended model: device indirect update (local BF)
# ==================================================================

class TestExtendedModelIndirect:

    def test_extended_witupdate_merges_bf(self):
        """After WitUpdate in extended mode, local BF contains revoked tag."""
        mgr, dlt, vcs, witnesses = _fresh_world(4)
        mgr2, pkg = batch_del(mgr, [vcs[0]])
        dlt2 = dlt_publish(dlt, pkg)
        relay = relay_init(dlt2)

        # Device 1 in extended deployment: has local BF
        bf_local = BloomFilter(K=SMALL_K, p=0.01)
        assert relay_gate_check(relay, vcs[1])
        pkgs = relay_fetch_updates(relay, from_epoch=1, to_epoch=relay.epoch)
        w_new, bf_new = wit_update(mgr2.compass, pkgs[0], witnesses[1], bf_local, vcs[1])

        # After merge: local BF now contains the revoked tag
        assert bf_new.query(H_tag(vcs[0]))

    def test_extended_verify_rejects_revoked_even_with_valid_witness(self):
        """
        Extended deployment: verify_with_revocation rejects a revoked VC
        purely from local BF, even though the accumulator witness is still
        technically valid (linear accumulator property).
        """
        mgr, dlt, vcs, witnesses = _fresh_world(3)
        mgr2, pkg = batch_del(mgr, [vcs[2]])
        dlt2 = dlt_publish(dlt, pkg)

        # Give device[2] a local BF with the revocation already applied
        bf_local = BloomFilter(K=SMALL_K, p=0.01)
        bf_local.merge(pkg.bf_delta)

        assert not verify_with_revocation(
            mgr2.compass, dlt2.Acc, vcs[2], witnesses[2], bf_local
        )

    def test_extended_self_gate_blocks_update(self):
        """Extended: device whose local BF already has its tag cannot apply WitUpdate."""
        mgr, dlt, vcs, witnesses = _fresh_world(3)
        mgr2, pkg = batch_del(mgr, [vcs[0]])
        dlt2 = dlt_publish(dlt, pkg)

        bf_local = BloomFilter(K=SMALL_K, p=0.01)
        bf_local.insert(H_tag(vcs[0]))   # device already knows it's revoked

        with pytest.raises(RuntimeError, match="revoked"):
            wit_update(mgr2.compass, pkg, witnesses[0], bf_local, vcs[0])

    def test_extended_valid_device_update_and_verify(self):
        """Extended: a valid device updates via relay and verifies with local BF."""
        mgr, dlt, vcs, witnesses = _fresh_world(4)
        mgr2, pkg = batch_del(mgr, [vcs[0]])
        dlt2 = dlt_publish(dlt, pkg)
        relay = relay_init(dlt2)

        bf_local = BloomFilter(K=SMALL_K, p=0.01)
        pkgs = relay_fetch_updates(relay, from_epoch=1, to_epoch=relay.epoch)
        w_new, bf_new = wit_update(mgr2.compass, pkgs[0], witnesses[3], bf_local, vcs[3])
        assert verify_with_revocation(mgr2.compass, relay.Acc, vcs[3], w_new, bf_new)


# ==================================================================
# Scenario 6 — Catch-up: device fetches multiple missed epochs
# ==================================================================

class TestCatchUp:

    def test_dlt_fetch_returns_correct_packages(self):
        """dlt_fetch_updates returns packages in epoch order."""
        mgr, dlt, vcs, _ = _fresh_world(3)

        # Epoch 2: add 2 more
        more_vcs = _make_vcs(2, issuer=b"issuer2")
        mgr, _, pkg2 = batch_add(mgr, more_vcs)
        dlt = dlt_publish(dlt, pkg2)

        # Epoch 3: revoke one original
        mgr, pkg3 = batch_del(mgr, [vcs[0]])
        dlt = dlt_publish(dlt, pkg3)

        pkgs = dlt_fetch_updates(dlt, from_epoch=0, to_epoch=3)
        assert len(pkgs) == 3
        assert [p.epoch for p in pkgs] == [1, 2, 3]

    def test_catchup_sequential_witness_verifies(self):
        """Device misses epochs 2 and 3, applies catch-up, verifies."""
        mgr, dlt, vcs, witnesses = _fresh_world(3)

        # Epoch 2
        more_vcs = _make_vcs(2, issuer=b"issuer2")
        mgr, _, pkg2 = batch_add(mgr, more_vcs)
        dlt = dlt_publish(dlt, pkg2)

        # Epoch 3: revoke vcs[0]
        mgr, pkg3 = batch_del(mgr, [vcs[0]])
        dlt = dlt_publish(dlt, pkg3)

        # Device[1] missed epochs 2 and 3
        bf_local = BloomFilter(K=SMALL_K, p=0.01)
        w = witnesses[1]
        # Device was issued at epoch 1; catch up from epoch 1 to current
        pkgs = dlt_fetch_updates(dlt, from_epoch=1, to_epoch=dlt.epoch)
        for pkg in pkgs:
            w, bf_local = wit_update(mgr.compass, pkg, w, bf_local, vcs[1])

        assert verify_with_revocation(mgr.compass, dlt.Acc, vcs[1], w, bf_local)

    def test_catchup_via_relay(self):
        """Device catches up using relay's cached epoch log."""
        mgr, dlt, vcs, witnesses = _fresh_world(3)
        more_vcs = _make_vcs(2, issuer=b"issuer2")
        mgr, _, pkg2 = batch_add(mgr, more_vcs)
        dlt = dlt_publish(dlt, pkg2)
        mgr, pkg3 = batch_del(mgr, [vcs[2]])
        dlt = dlt_publish(dlt, pkg3)

        relay = relay_init(dlt)

        bf_local = BloomFilter(K=SMALL_K, p=0.01)
        w = witnesses[1]
        # Device was issued at epoch 1; catch up from epoch 1 to current
        pkgs = relay_fetch_updates(relay, from_epoch=1, to_epoch=relay.epoch)
        for pkg in pkgs:
            w, bf_local = wit_update(mgr.compass, pkg, w, bf_local, vcs[1])

        assert verify_with_revocation(mgr.compass, relay.Acc, vcs[1], w, bf_local)

    def test_catchup_missing_epoch_raises(self):
        """Requesting a non-existent epoch from DLT log raises KeyError."""
        mgr = manager_setup(K=SMALL_K, param_set=PARAM)
        dlt = dlt_init(mgr)
        with pytest.raises(KeyError):
            dlt_fetch_updates(dlt, from_epoch=0, to_epoch=5)

    def test_forged_witness_always_rejected(self):
        """Witness from one device cannot be used to verify a different VC."""
        mgr, dlt, vcs, witnesses = _fresh_world(3)
        ok = verify_with_revocation(mgr.compass, dlt.Acc, vcs[1], witnesses[0], None)
        assert not ok

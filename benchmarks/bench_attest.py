"""
ATTEST performance benchmarks — set2 (N=1024, q≈2^32).

Measures every operation needed to populate §5 tables in the paper.

Sections
--------
1. Component micro-benchmarks  (BF insert/query/merge, ring addition)
2. VerifyWithRevocation         (baseline and extended)
3. WitUpdate                    (baseline and extended)
4. Manager EpochUpdate          (BatchAdd and BatchDel vs batch size)
5. DLT / Relay operations       (gate check, epoch log fetch)
6. Wire sizes                   (witness, ΔZ, BF_Δ, epoch package)

Usage
-----
    cd 4_implementation
    python3 benchmarks/bench_attest.py [--quick]

    --quick : use set1 (N=512) for fast local checks; default is set2 (N=1024)

Typical runtime: ~5-10 min for set2 (verify loop dominates).
"""
import sys
import time
import statistics
import argparse

import numpy as np

# Allow running from 4_implementation/
sys.path.insert(0, ".")

from attest.credential import VC, H_tag
from attest.bloom_filter import BloomFilter
from attest.manager import manager_setup, batch_add, batch_del
from attest.dlt import (dlt_init, dlt_publish, dlt_gate_check,
                         dlt_fetch_updates, relay_init, relay_fetch_updates)
from attest.device import wit_update, verify_with_revocation
from attest.params import BF_FP_RATE


# ------------------------------------------------------------------ helpers ---

def _make_vcs(n, issuer=b"bench"):
    return [
        VC(issuer_id=issuer, cred_id=f"cred_{i}".encode(),
           holder_pk=(f"pk_{i}".encode() * 4)[:32], issued_epoch=0)
        for i in range(n)
    ]

def _timeit(fn, n_runs=10):
    """Run fn() n_runs times; return (mean_ms, std_ms, results)."""
    times = []
    results = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        r  = fn()
        times.append((time.perf_counter() - t0) * 1000)
        results.append(r)
    return statistics.mean(times), statistics.stdev(times) if n_runs > 1 else 0.0, results

def hdr(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def row(label, mean_ms, std_ms, note=""):
    std_str = f" ± {std_ms:.3f}" if std_ms > 0 else ""
    note_str = f"  ({note})" if note else ""
    print(f"  {label:<42} {mean_ms:>9.3f} ms{std_str}{note_str}")


# ================================================================ MAIN ========

def main(param_set="set2"):
    K_BENCH   = 20    # initial pool size (small: fast setup, representative ops)
    BF_K      = K_BENCH
    N_VERIFY  = 10    # verification runs (80 ms each at set2 → ~10 s)
    N_FAST    = 200   # fast ops (μs-range)

    print(f"\nATTEST Benchmark  —  param_set={param_set}")
    print(f"K_pool={K_BENCH}, N_verify={N_VERIFY}, N_fast={N_FAST}")

    # ----------------------------------------------------------
    # Setup: accumulate K_BENCH members  (slow — do once)
    # ----------------------------------------------------------
    hdr("Setup  (one-time, not benchmarked)")
    print("  Building initial accumulator …", flush=True)
    t0   = time.perf_counter()
    mgr  = manager_setup(K=K_BENCH, param_set=param_set)
    dlt  = dlt_init(mgr)
    vcs  = _make_vcs(K_BENCH)
    mgr, witnesses, pkg = batch_add(mgr, vcs)
    dlt  = dlt_publish(dlt, pkg)
    relay = relay_init(dlt)
    setup_s = time.perf_counter() - t0
    print(f"  Done in {setup_s:.1f} s  "
          f"(avg {setup_s*1000/K_BENCH:.0f} ms/member)")

    c = mgr.compass
    print(f"\n  Parameters: N={c.N}, q={c.q}, κ={c.kappa}, t={c.t}")
    print(f"  K_pool={K_BENCH}, BF K={BF_K}, p={BF_FP_RATE}")

    # ============================================================
    # 1. Component micro-benchmarks
    # ============================================================
    hdr("1. Component micro-benchmarks")

    tag = H_tag(vcs[0])
    bf  = BloomFilter(K=BF_K, p=BF_FP_RATE)

    m_ins,  s_ins,  _ = _timeit(lambda: bf.insert(tag),               N_FAST)
    m_qry,  s_qry,  _ = _timeit(lambda: bf.query(tag),                N_FAST)

    bf2 = BloomFilter(K=BF_K, p=BF_FP_RATE)
    bf2.insert(tag)
    m_mrg,  s_mrg,  _ = _timeit(
        lambda: BloomFilter(K=BF_K, p=BF_FP_RATE,
                            _bits=bytearray(bf.serialize())).merge(bf2),
        N_FAST)

    # Ring addition (Z_i + ΔZ mod q) — the core of WitUpdate
    Zi, zi, ci1, ci2 = c.deserialize_witness(witnesses[0].data)
    dZ = pkg.delta_Z
    q  = c.q
    m_radd, s_radd, _ = _timeit(lambda: (Zi + dZ) % q, N_FAST)

    # Partial Fourier F_Ω(Z) — used in Verify and EpochUpdate
    Z = mgr.Z
    m_pf, s_pf, _ = _timeit(lambda: c.PF.F(Z), N_FAST)

    print(f"\n  {'Operation':<42} {'Mean':>9}  ±std")
    row("BF.insert (1 tag)", m_ins, s_ins)
    row("BF.query  (1 tag)", m_qry, s_qry)
    row(f"BF.merge  (full {bf.size_bytes()} B bitmap)", m_mrg, s_mrg)
    row("Ring addition  Z_i + ΔZ mod q", m_radd, s_radd)
    row(f"PartialFourier F_Ω(Z)  (N={c.N}, t={c.t})", m_pf, s_pf)

    # ============================================================
    # 2. VerifyWithRevocation
    # ============================================================
    hdr("2. VerifyWithRevocation")

    # Revoke one device so BF is non-trivial
    mgr2, del_pkg = batch_del(mgr, [vcs[-1]])
    dlt2 = dlt_publish(dlt, del_pkg)
    bf_local = BloomFilter(K=BF_K, p=BF_FP_RATE)

    # Update witness[0] to current epoch first
    pkgs_catchup = dlt_fetch_updates(dlt2, from_epoch=1, to_epoch=dlt2.epoch)
    w0 = witnesses[0]
    for p in pkgs_catchup:
        w0, bf_local = wit_update(c, p, w0, bf_local, vcs[0])

    # Baseline: no local BF (accumulator only)
    m_vb, s_vb, _ = _timeit(
        lambda: verify_with_revocation(c, dlt2.Acc, vcs[0], w0, None),
        N_VERIFY)

    # Extended: local BF check + accumulator
    m_ve, s_ve, _ = _timeit(
        lambda: verify_with_revocation(c, dlt2.Acc, vcs[0], w0, bf_local),
        N_VERIFY)

    # BF check alone (phase 1 only — tag NOT in BF)
    bf_local_copy = BloomFilter(K=BF_K, p=BF_FP_RATE,
                                _bits=bytearray(bf_local.serialize()))
    m_bf1, s_bf1, _ = _timeit(
        lambda: bf_local_copy.query(H_tag(vcs[0])),
        N_FAST)

    print(f"\n  {'Operation':<42} {'Mean':>9}  ±std")
    row("Phase 1: BF.query (not revoked)", m_bf1, s_bf1)
    row("Phase 2: COMPASS.Verify", m_vb, s_vb, "accumulator only")
    row("VerifyWithRevocation  baseline", m_vb, s_vb, "bf_local=None")
    row("VerifyWithRevocation  extended", m_ve, s_ve, "BF check + accumulator")
    print(f"\n  BF overhead: +{m_ve - m_vb:.3f} ms  "
          f"({(m_ve-m_vb)/m_vb*100:.2f}% of verify time)")

    # ============================================================
    # 3. WitUpdate
    # ============================================================
    hdr("3. WitUpdate")

    # Prepare a fresh add epoch for WitUpdate measurement
    extra_vcs = _make_vcs(1, issuer=b"extra")
    mgr3, extra_witnesses, add_pkg = batch_add(mgr2, extra_vcs)
    dlt3 = dlt_publish(dlt2, add_pkg)

    # Re-fetch w0 to current epoch for this chain
    w0_chain = witnesses[0]
    for p in dlt_fetch_updates(dlt3, from_epoch=1, to_epoch=dlt3.epoch):
        w0_chain, _ = wit_update(c, p, w0_chain, None, vcs[0])

    # Baseline: no BF (ring addition only)
    m_wu_b, s_wu_b, _ = _timeit(
        lambda: wit_update(c, add_pkg, witnesses[0], None, vcs[0]),
        N_FAST)

    # Extended: ring addition + BF merge
    bf_dev = BloomFilter(K=BF_K, p=BF_FP_RATE)
    m_wu_e, s_wu_e, _ = _timeit(
        lambda: wit_update(c, add_pkg, witnesses[0],
                           BloomFilter(K=BF_K, p=BF_FP_RATE,
                                       _bits=bytearray(bf_dev.serialize())),
                           vcs[0]),
        N_FAST)

    # Extended with revocation epoch (has BF_Δ to merge)
    bf_dev2 = BloomFilter(K=BF_K, p=BF_FP_RATE)
    m_wu_r, s_wu_r, _ = _timeit(
        lambda: wit_update(c, del_pkg, witnesses[0],
                           BloomFilter(K=BF_K, p=BF_FP_RATE,
                                       _bits=bytearray(bf_dev2.serialize())),
                           vcs[0]),
        N_FAST)

    print(f"\n  {'Operation':<42} {'Mean':>9}  ±std")
    row("WitUpdate  baseline (ring add only)", m_wu_b, s_wu_b)
    row("WitUpdate  extended, add epoch", m_wu_e, s_wu_e, "no BF_Δ to merge")
    row("WitUpdate  extended, revoke epoch", m_wu_r, s_wu_r, "ring add + BF merge")

    # ============================================================
    # 4. Manager EpochUpdate vs batch size
    # ============================================================
    hdr("4. Manager EpochUpdate vs batch size")

    add_sizes = [1, 5, 10]
    del_sizes = [1, 5, 10]

    print(f"\n  BatchAdd (manager side — rejection sampling)")
    print(f"  {'m (additions)':<20} {'Mean':>9}  ±std")

    add_times = {}
    for m_size in add_sizes:
        new_vcs = _make_vcs(m_size, issuer=f"add_{m_size}".encode())
        t_list = []
        n_add_runs = max(1, 3)  # 3 runs per batch size
        for _ in range(n_add_runs):
            t0 = time.perf_counter()
            mgr_tmp, _, _ = batch_add(mgr, new_vcs)
            t_list.append((time.perf_counter() - t0) * 1000)
        mean_t = statistics.mean(t_list)
        std_t  = statistics.stdev(t_list) if n_add_runs > 1 else 0.0
        add_times[m_size] = mean_t
        print(f"  m={m_size:<18} {mean_t:>9.1f} ms  ± {std_t:.1f}  "
              f"({mean_t/m_size:.1f} ms/member)")

    print(f"\n  BatchDel (manager side — deterministic subtraction)")
    print(f"  {'ℓ (revocations)':<20} {'Mean':>9}  ±std")

    del_times = {}
    for l_size in del_sizes:
        del_vcs = vcs[:l_size]
        t_list = []
        n_del_runs = max(1, 10)
        for _ in range(n_del_runs):
            t0 = time.perf_counter()
            _, _ = batch_del(mgr, del_vcs)
            t_list.append((time.perf_counter() - t0) * 1000)
        mean_t = statistics.mean(t_list)
        std_t  = statistics.stdev(t_list) if n_del_runs > 1 else 0.0
        del_times[l_size] = mean_t
        print(f"  ℓ={l_size:<18} {mean_t:>9.3f} ms  ± {std_t:.3f}  "
              f"({mean_t/l_size:.3f} ms/revocation)")

    # ============================================================
    # 5. DLT / Relay operations
    # ============================================================
    hdr("5. DLT and Relay operations")

    # Gate check (BF.query on DLT or relay BF)
    m_gc, s_gc, _ = _timeit(lambda: dlt_gate_check(dlt2, vcs[0]), N_FAST)
    m_gc_rev, s_gc_rev, _ = _timeit(lambda: dlt_gate_check(dlt2, vcs[-1]), N_FAST)

    # dlt_publish (BF merge + log update)
    m_pub, s_pub, _ = _timeit(
        lambda: dlt_publish(dlt, del_pkg), N_FAST)

    # Epoch log fetch (1 missed epoch)
    m_f1, s_f1, _ = _timeit(
        lambda: dlt_fetch_updates(dlt2, from_epoch=1, to_epoch=2), N_FAST)

    # Build a longer log for catch-up fetch benchmark
    dlt_long = dlt2
    mgr_long = mgr2
    for i in range(8):
        extra = _make_vcs(1, issuer=f"long_{i}".encode())
        mgr_long, _, p_tmp = batch_add(mgr_long, extra)
        dlt_long = dlt_publish(dlt_long, p_tmp)

    m_f5, s_f5, _ = _timeit(
        lambda: dlt_fetch_updates(dlt_long, from_epoch=2, to_epoch=7), N_FAST)

    m_f10, s_f10, _ = _timeit(
        lambda: dlt_fetch_updates(dlt_long,
                                  from_epoch=dlt_long.epoch - 8,
                                  to_epoch=dlt_long.epoch),
        N_FAST)

    print(f"\n  {'Operation':<42} {'Mean':>9}  ±std")
    row("dlt_gate_check  (not revoked)", m_gc, s_gc)
    row("dlt_gate_check  (revoked — deny)", m_gc_rev, s_gc_rev)
    row("dlt_publish     (BF merge + log write)", m_pub, s_pub)
    row("dlt_fetch_updates  1 missed epoch", m_f1, s_f1)
    row("dlt_fetch_updates  5 missed epochs", m_f5, s_f5)
    row("dlt_fetch_updates  8 missed epochs", m_f10, s_f10)

    # ============================================================
    # 6. Wire sizes
    # ============================================================
    hdr("6. Wire sizes")

    from compass.utils import bytes_per_q as _bpq
    bpq = _bpq(c.q)   # bytes per ring coefficient on the wire (4 for set2)

    w_size  = len(witnesses[0].data)
    dz_size = len(pkg.delta_Z) * bpq          # serialised wire size
    bf_size = BloomFilter(K=BF_K, p=BF_FP_RATE).size_bytes()
    bf_paper_size = BloomFilter(K=10_000, p=BF_FP_RATE).size_bytes()

    add_pkg_size = add_pkg.size_bytes(q=c.q)   # serialised
    del_pkg_size = del_pkg.size_bytes(q=c.q)

    lbl_wit = "Witness (Z_i, z_i, c1, c2)"
    lbl_dz  = f"ΔZ (ring element, N={c.N})"
    lbl_bfp = f"BF bitmap bench (K={BF_K}, p=1%)"
    lbl_bff = "BF bitmap paper (K=10000, p=1%)"
    lbl_add = "EpochPackage: add-only"
    lbl_del = "EpochPackage: revocation (bench K)"

    print(f"\n  {'Component':<44} {'Size':>8}")
    for lbl, sz in [(lbl_wit, w_size), (lbl_dz, dz_size),
                    (lbl_bfp, bf_size), (lbl_bff, bf_paper_size),
                    (lbl_add, add_pkg_size), (lbl_del, del_pkg_size)]:
        print(f"  {lbl:<44} {sz:>6} B  ({sz/1024:.1f} KB)")

    # ============================================================
    # Summary table (paper-ready)
    # ============================================================
    hdr("Summary (paper §5)")
    print(f"""
  Scheme: ATTEST  param_set={param_set}  N={c.N}  q={c.q}

  ┌─────────────────────────────────┬──────────────────────────┐
  │ Operation                       │ Time                     │
  ├─────────────────────────────────┼──────────────────────────┤
  │ VerifyWithRevocation (baseline) │ {m_vb:>7.1f} ± {s_vb:.1f} ms      │
  │ VerifyWithRevocation (extended) │ {m_ve:>7.1f} ± {s_ve:.1f} ms      │
  │ WitUpdate (baseline)            │ {m_wu_b:>7.3f} ± {s_wu_b:.3f} ms  │
  │ WitUpdate (extended, add)       │ {m_wu_e:>7.3f} ± {s_wu_e:.3f} ms  │
  │ WitUpdate (extended, revoke)    │ {m_wu_r:>7.3f} ± {s_wu_r:.3f} ms  │
  │ DLT gate check                  │ {m_gc*1000:>7.3f} ± {s_gc*1000:.3f} μs  │
  │ BatchAdd per member             │ {add_times[1]:>7.1f} ms (m=1)          │
  │ BatchDel per revocation         │ {del_times[1]*1000:>7.3f} μs (ℓ=1)         │
  └─────────────────────────────────┴──────────────────────────┘

  ┌─────────────────────────────────┬──────────────────────────┐
  │ Wire size                       │ Bytes                    │
  ├─────────────────────────────────┼──────────────────────────┤
  │ Witness                         │ {w_size:>7} B  ({w_size/1024:.1f} KB)      │
  │ ΔZ (add or revoke epoch)        │ {dz_size:>7} B  ({dz_size/1024:.1f} KB)       │
  │ BF_Δ (K=10000, p=1%)            │ {bf_paper_size:>7} B  ({bf_paper_size/1024:.1f} KB)       │
  │ EpochPackage: add-only          │ {add_pkg_size:>7} B  ({add_pkg_size/1024:.1f} KB)       │
  │ EpochPackage: revocation        │ {del_pkg_size:>7} B  ({del_pkg_size/1024:.1f} KB)       │
  └─────────────────────────────────┴──────────────────────────┘
""")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true",
                        help="Use set1 (N=512) for fast local checks")
    args = parser.parse_args()
    main(param_set="set1" if args.quick else "set2")

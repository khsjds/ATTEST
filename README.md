# ATTEST

**Accumulator-based Tamarin-verified Trust for crEdential Systems in IoT**

ATTEST is a post-quantum verifiable credential (VC) revocation framework for IoT,
built on [COMPASS](https://doi.org/10.1007/978-3-031-80311-6) — a lattice-based
accumulator accepted at INDOCRYPT 2025.

The framework supports **batch add/revoke** operations with constant-size epoch
broadcasts (4 KB ΔZ regardless of batch size), and is formally verified in
Tamarin Prover with 15 lemmas covering revocation enforcement, witness secrecy,
and liveness.

---

## Key Properties

| Property | Value |
|----------|-------|
| Post-quantum security | Lattice-based (PASS lineage), 128-bit |
| Witness size | 4.2 KB (set1) / **8.2 KB (set2)** |
| Epoch broadcast: add | 2.0 KB (set1) / **4.0 KB (set2)** |
| Epoch broadcast: revoke | 13.7 KB (set1) / **15.7 KB (set2)** |
| Verification time (Colab) | ~2 ms (set1) / **~44–107 ms (set2)** |
| WitUpdate time | ~0.8 ms (set1) / ~1.5–2.4 ms (set2) |
| Formal verification | 15 Tamarin lemmas — all verified |

---

## Repository Structure

```
ATTEST/
├── attest/               Python library (accumulator + BF + protocol)
│   ├── params.py         Parameter sets SET1 / SET2
│   ├── credential.py     VC dataclass and H_tag
│   ├── bloom_filter.py   BloomFilter (insert / query / merge / serialize)
│   ├── manager.py        manager_setup, batch_add, batch_del
│   ├── dlt.py            DLTState, RelayState, dlt_publish, dlt_gate_check
│   └── device.py         wit_update, verify_with_revocation
├── benchmarks/
│   ├── bench_attest.py   CLI benchmark (all 6 sections)
│   ├── attest_benchmark.ipynb   Google Colab notebook
│   └── attest_benchmark.ipynb   Google Colab notebook (auto-clones dependencies)
├── tests/
│   ├── test_bloom.py     Bloom filter unit tests (7)
│   └── test_protocol.py  Protocol integration tests (26)
├── tamarin/
│   ├── attest_baseline.spthy    13 safety lemmas (all verified)
│   ├── attest_liveness.spthy    2 liveness lemmas (both verified)
│   └── README.md                Model design and verification guide
└── requirements.txt
```

> **Note:** The COMPASS base library is a dependency but is not included here.
> See [Installation](#installation) below.

---

## Installation

ATTEST depends on [COMPASS](https://github.com/your-org/COMPASS).
Clone both repositories side-by-side:

```bash
git clone https://github.com/khsjds/COMPASS.git
git clone https://github.com/khsjds/ATTEST.git

cd ATTEST
pip install -r requirements.txt

# Make COMPASS importable
export PYTHONPATH="$PYTHONPATH:../COMPASS"
```

Run the test suite to verify the setup:

```bash
pytest tests/
# Expected: 33 passed
```

---

## Quick Start

```python
from attest import SET2, manager_setup, dlt_init, dlt_publish
from attest import batch_add, batch_del, dlt_gate_check
from attest import wit_update, verify_with_revocation
from attest import VC, BloomFilter

# Manager setup (private, off-chain)
mgr = manager_setup(param_set="set2")   # or "set1" for lighter parameters
dlt = dlt_init(mgr)

# Issue credentials
vcs = [VC(issuer_id=b"issuer", cred_id=f"c{i}".encode(),
          holder_pk=b"pk" * 16, issued_epoch=0) for i in range(10)]
mgr, witnesses, pkg = batch_add(mgr, vcs)
dlt = dlt_publish(dlt, pkg)

# Verify (baseline — no local BF)
ok = verify_with_revocation(mgr.compass, dlt.Acc, vcs[0], witnesses[0], None)
print(ok)  # True

# Revoke a credential
mgr, del_pkg = batch_del(mgr, [vcs[-1]])
dlt = dlt_publish(dlt, del_pkg)

# Gate check: DLT refuses to serve update material to revoked device
print(dlt_gate_check(dlt, vcs[-1]))  # False — revoked
print(dlt_gate_check(dlt, vcs[0]))   # True  — still valid
```

---

## Parameter Sets

Two parameter sets are provided, both inherited from COMPASS:

| | **set1** | **set2** (recommended) |
|---|---|---|
| Ring dim N | 512 | **1024** |
| Modulus q | ~2²⁷·⁶ | **~2³²** |
| Hash param κ | 44 | 36 |
| Gaussian σ | 11,336 | **167,771** |
| Witness size | 4.2 KB | **8.2 KB** |
| ΔZ wire size | 2.0 KB | **4.0 KB** |
| Verify (Colab) | ~3 ms | **~100 ms** |

**set2** is the recommended production parameter set and is the basis for all
numbers reported in the paper.
**set1** offers faster verification (~3 ms) at a lower security margin;
its BatchAdd operation benefits from parallel execution across members.

---

## Benchmarks

### CLI

```bash
cd ATTEST
python3 benchmarks/bench_attest.py          # set2 (paper numbers)
python3 benchmarks/bench_attest.py --quick  # set1 (fast check)
```

### Google Colab

Open `benchmarks/attest_benchmark.ipynb` in Google Colab and run all cells.
The setup cell automatically clones both ATTEST and COMPASS from GitHub — no manual file upload needed.

---

## Formal Verification (Tamarin)

All 15 protocol security lemmas are verified with Tamarin Prover 1.10.0.

```bash
# Run sequentially — never in parallel (memory-intensive)
tamarin-prover tamarin/attest_baseline.spthy --prove   # ~160 s, 13 safety lemmas
tamarin-prover tamarin/attest_liveness.spthy --prove   #  ~51 s,  2 liveness lemmas
```

See [`tamarin/README.md`](tamarin/README.md) for the full lemma table and
model design documentation.

---

## License

MIT — see [LICENSE](LICENSE).

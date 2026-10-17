"""Run once before the app: downloads TabPFN weights and shows where time goes.

Usage:  python warmup_tabpfn.py
"""
import time

t0 = time.perf_counter()
print("1/4 Importing PyTorch + TabPFN (slow the first time on Windows)...", flush=True)
import numpy as np  # noqa: E402
import torch  # noqa: E402
from tabpfn import TabPFNRegressor  # noqa: E402
from tabpfn.constants import ModelVersion  # noqa: E402
from tabpfn.model_loading import get_cache_dir  # noqa: E402

import predict  # noqa: E402

print(f"    done in {time.perf_counter() - t0:.0f}s  (torch {torch.__version__}, CUDA available: {torch.cuda.is_available()})")
print(f"    weights folder: {get_cache_dir()}")

t = time.perf_counter()
print(f"2/4 Loading TabPFN {predict.TABPFN_VERSION} weights (downloads once)...", flush=True)
X, y = np.random.rand(14, 5), np.random.rand(14)
model = TabPFNRegressor.create_default_for_version(
    ModelVersion(predict.TABPFN_VERSION), n_estimators=predict.TABPFN_ESTIMATORS, device=predict.TABPFN_DEVICE)
model.fit(X, y)
print(f"    done in {time.perf_counter() - t:.0f}s")

t = time.perf_counter()
print("3/4 Predicting...", flush=True)
model.predict(np.random.rand(42, 5))
print(f"    done in {time.perf_counter() - t:.1f}s")

t = time.perf_counter()
print("4/4 Second full fit + predict (what the app does from now on)...", flush=True)
model.fit(X, y)
model.predict(np.random.rand(42, 5))
print(f"    done in {time.perf_counter() - t:.1f}s")
print(f"\nAll good. Total {time.perf_counter() - t0:.0f}s. The Best slots tab will now be fast.")

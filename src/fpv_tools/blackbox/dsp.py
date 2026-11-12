"""Signal helpers on evenly sampled arrays: boolean masks, spectra, filters."""

import numpy as np

LOBE_BINS = 2  # half width of the Hann window's main lobe, in bins


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(start, end) index pairs of the True runs in mask, end exclusive."""
    edges = np.flatnonzero(np.diff(np.r_[0, np.asarray(mask, dtype=np.int8), 0]))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist(), strict=True))


def widen(mask: np.ndarray, fs: float, before: float = 0.0, after: float = 0.0) -> np.ndarray:
    """True from `before` seconds ahead of each True sample until `after` seconds behind it."""
    mask = np.asarray(mask, dtype=bool)
    b, a = round(before * fs), round(after * fs)
    if not mask.any() or (a == 0 and b == 0):
        return mask.copy()
    # In the full convolution, sample i spreads to i .. i+b+a; shifting by b maps that to i-b .. i+a
    hits = np.convolve(mask, np.ones(a + b + 1))
    return hits[b : b + len(mask)] > 0


def sustained(mask: np.ndarray, fs: float, seconds: float) -> np.ndarray:
    """True where mask is True throughout the centred window of `seconds`."""
    n = max(1, round(seconds * fs))
    return np.convolve(np.asarray(mask, dtype=float), np.ones(n) / n, "same") > 1 - 1e-9


def moving_average(x: np.ndarray, fs: float, seconds: float) -> np.ndarray:
    n = max(1, round(seconds * fs))
    return np.convolve(x, np.ones(n) / n, "same")


def psd_masked(
    x: np.ndarray, fs: float, mask: np.ndarray, nperseg: int = 256
) -> tuple[np.ndarray, np.ndarray, float] | None:
    """Welch PSD over the samples where mask holds, without joining separate stretches (a join is a step that
    would leak into the spectrum): each contiguous stretch of at least nperseg samples gets its own estimate,
    averaged weighted by length. Returns (Hz, power per Hz, seconds used), or None if no stretch is long enough."""
    from scipy import signal  # takes ~0.5 s to import: only when a spectrum is needed

    total, acc, freqs = 0, None, None
    for s, e in runs(mask):
        if e - s < nperseg:
            continue
        f, p = signal.welch(x[s:e] - np.mean(x[s:e]), fs=fs, window="hann", nperseg=nperseg)
        acc = p * (e - s) if acc is None else acc + p * (e - s)
        freqs, total = f, total + (e - s)
    if acc is None or freqs is None:
        return None
    return freqs, acc / total, total / fs


def peaks_of(
    freqs: np.ndarray, power: np.ndarray, lo: float, count: int, separation: float
) -> list[tuple[float, float]]:
    """The `count` strongest local maxima of a PSD above `lo` Hz, at least `separation` Hz apart, by frequency:
    [(Hz, rms amplitude)]. The amplitude is the RMS within the peak's main lobe: the window spreads a tone over
    several bins, so the peak bin alone holds only part of it (2/3 for a tone at a bin centre)."""
    df = freqs[1] - freqs[0]
    candidates = [
        i for i in range(1, len(freqs) - 1) if freqs[i] >= lo and power[i] >= power[i - 1] and power[i] >= power[i + 1]
    ]
    picked: list[int] = []
    for i in sorted(candidates, key=lambda i: -power[i]):
        if all(abs(freqs[i] - freqs[j]) >= separation for j in picked):
            picked.append(i)
        if len(picked) == count:
            break
    lobe = [power[max(0, i - LOBE_BINS) : i + LOBE_BINS + 1].sum() for i in sorted(picked)]
    return [(float(freqs[i]), float(np.sqrt(p * df))) for i, p in zip(sorted(picked), lobe, strict=True)]


def band_rms_of(freqs: np.ndarray, power: np.ndarray, lo: float, hi: float | None = None) -> float:
    sel = (freqs >= lo) & (freqs < (hi if hi is not None else np.inf))
    return float(np.sqrt(power[sel].sum() * (freqs[1] - freqs[0])))

//! lifetime_fast: a Rust copy of the hot part of lifetime_availability.py, used by the server when it is installed.
//!
//! Python is the reference. Each function below copies the Python function named in its comment line by line, and
//! test_availability_engines.py checks that both give exactly the same results. Change the Python first; that test
//! then tells you whether this copy must follow (see README.md).
//!
//! Times are whole microseconds since 1970-01-01 UTC (`i64`): the exact value of a Python datetime, converted by
//! lifetime_availability._fast_sums. An interval is a (start, end) pair with start < end; lists of intervals are
//! sorted and do not overlap ("merged"), as in the Python.

use pyo3::prelude::*;

/// (start, end) in microseconds since 1970. Python: a (start, end) tuple of datetimes.
type Interval = (i64, i64);

/// Python: `timedelta.total_seconds()`. Python divides the whole microseconds by 10**6, correctly rounded; so does
/// this, exactly, for any length below 2**53 microseconds (285 years; a period is at most 3,660 days).
fn seconds(microseconds: i64) -> f64 {
    microseconds as f64 / 1e6
}

/// Python: `_intersect(first, second)`. Both merged and sorted: one sweep.
fn intersect(first: &[Interval], second: &[Interval]) -> Vec<Interval> {
    let mut result = Vec::new();
    let (mut i, mut j) = (0, 0);
    while i < first.len() && j < second.len() {
        let start = first[i].0.max(second[j].0);
        let end = first[i].1.min(second[j].1);
        if start < end {
            result.push((start, end));
        }
        if first[i].1 < second[j].1 {
            i += 1;
        } else {
            j += 1;
        }
    }
    result
}

/// Python: `_subtract(first, second)`: `first` minus `second`, both merged and sorted, one sweep.
fn subtract(first: &[Interval], second: &[Interval]) -> Vec<Interval> {
    let mut result = Vec::new();
    let mut j = 0;
    for &(start, end) in first {
        let mut start = start; // Python changes `start` inside the loop
        while j < second.len() && second[j].1 <= start {
            j += 1;
        }
        let mut k = j;
        while k < second.len() && second[k].0 < end {
            if second[k].0 > start {
                result.push((start, second[k].0));
            }
            start = start.max(second[k].1);
            if start >= end {
                break;
            }
            k += 1;
        }
        if start < end {
            result.push((start, end));
        }
    }
    result
}

/// Python: `_spread(intervals, edges)`: seconds of `intervals` inside each [edges[i], edges[i+1]), added in the same
/// order as the Python, so every total is the same float.
fn spread(intervals: &[Interval], edges: &[i64]) -> Vec<f64> {
    let mut totals = vec![0.0; edges.len().saturating_sub(1)]; // Python: [0.0] * (len(edges) - 1)
    let mut k = 0;
    for &(start, end) in intervals {
        while k < totals.len() && edges[k + 1] <= start {
            k += 1;
        }
        let mut m = k;
        while m < totals.len() && edges[m] < end {
            let low = start.max(edges[m]);
            let high = end.min(edges[m + 1]);
            if low < high {
                totals[m] += seconds(high - low);
            }
            m += 1;
        }
    }
    totals
}

/// Python: `outages(counted, offline)`: (number, longest seconds). The longest is returned as the float; Python
/// rounds it (Rust's `round` rounds halves differently from Python's).
fn outages(counted: &[Interval], offline: &[Interval]) -> (usize, f64) {
    let (mut number, mut longest) = (0, 0.0);
    let mut j = 0;
    for &(start, end) in offline {
        while j < counted.len() && counted[j].1 <= start {
            j += 1;
        }
        let mut k = j;
        let mut total = 0.0;
        while k < counted.len() && counted[k].0 < end {
            total += seconds(end.min(counted[k].1) - start.max(counted[k].0));
            k += 1;
        }
        if total > 0.0 {
            number += 1; // Python: lengths.append(seconds); len(lengths), max(lengths)
            longest = f64::max(longest, total);
        }
    }
    (number, longest)
}

/// Python: `_sums(scheduled, cuts, online, offline, edges)`, the only function Python calls:
/// (good, bad, expected, excluded seconds per bucket, number of outages, longest outage in seconds, unrounded).
#[pyfunction]
fn sums(
    scheduled: Vec<Interval>,
    cuts: Vec<Interval>,
    online: Vec<Interval>,
    offline: Vec<Interval>,
    edges: Vec<i64>,
) -> (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, usize, f64) {
    let counted = subtract(&scheduled, &cuts);
    let good = spread(&intersect(&counted, &online), &edges);
    let bad = spread(&intersect(&counted, &offline), &edges);
    let expected = spread(&counted, &edges);
    let excluded = spread(&intersect(&scheduled, &cuts), &edges);
    let (number, longest) = outages(&counted, &offline);
    (good, bad, expected, excluded, number, longest)
}

/// The Python module `lifetime_fast`.
#[pymodule]
fn lifetime_fast(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(sums, m)?)?;
    Ok(())
}

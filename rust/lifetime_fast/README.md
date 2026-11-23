# lifetime_fast: the server's optional Rust accelerator

**You never need to touch this to change Lifetime.** It is a copy, in Rust, of one Python function, `_sums` in
`lifetime_availability.py`, which the server uses when it is installed. Without it the server runs the Python
version, with the same results.

## What it does

When the dashboard asks for the uptime bars of the whole fleet (for example 500 computers x 365 daily bars), the server
works out for each computer how many seconds of each day were good (online), bad (offline), expected and under
maintenance, and its outages. That is `_sums`: four helpers (`_subtract`, `_intersect`, `_spread`, `outages`) that
walk sorted lists of time intervals. Measured with 500 computers x 365 days, it was the largest part of the time the
server spent on that request.

`src/lib.rs` has the same functions with the same names, line by line, and the same results: times go over as whole
microseconds since 1970 (exact), and seconds come back as the same floating-point numbers Python computes, added in
the same order. Everything else (reading the database, time zones, schedules, rounding) stays in Python.

## How it is chosen

`lifetime_availability.py` tries `import lifetime_fast`. If that works, the server uses it; if not, it uses the Python
functions. `LIFETIME_NO_RUST=1` in the environment forces Python. The server tells you which one runs: `server_info`
has `availability_engine`, either `Rust accelerator` or `Python`:

```sh
python lifetime_server.py admin --directory DATA --operation server_info --field availability_engine
```

Only the server packages contain it (Windows server package, Linux server `.tar.gz`). The desktop app, the agent and
source installs never do.

## If you change the Python

Run `python -m unittest test_availability_engines`. Without the Rust module built it checks the Python against a
simple slow computation and says the Rust comparison was skipped. GitHub builds the Rust module and runs the same test
with it: if the Python and Rust results differ, it fails and shows the case. Then either make the same change in
`src/lib.rs` (the matching function has the same name), or ask someone who knows Rust.

## Building it yourself

You need Rust 1.91.0 (<https://rustup.rs>, then `rustup toolchain install 1.91.0`). From the repository folder:

```sh
python -m pip install maturin==1.10.2
python -m maturin build --release --locked -m rust/lifetime_fast/Cargo.toml --out build/rust-wheels
python -m pip install --no-index --find-links=build/rust-wheels lifetime_fast==0.1.0
python -m unittest test_availability_engines
```

Inside a virtual environment `python -m maturin develop --release -m rust/lifetime_fast/Cargo.toml` does the last
three steps in one. To remove it: `python -m pip uninstall lifetime_fast`.

`--locked` builds exactly the crate versions in `Cargo.lock`. To update them: change the version in `Cargo.toml`, run
`cargo update -p pyo3 --precise NEW` (or `cargo generate-lockfile`), let CI build and test on every platform, then tag.

Never install it on employees' Windows 11 computers: Smart App Control blocks unsigned native files, and the desktop
app does not use it anyway.

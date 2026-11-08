# Validation record

Verified locally on Windows with CPython 3.13.3 and Node 24.22.1.

## Automated checks

- `python -m unittest discover -v`: 29 tests passed.
- `python -m app.cli demo`: completed the four-step investigation, reproduced the original output and checked both blocked and passing releases.
- `node --check static/app.js`: passed.
- The exported bundle downloaded through the interface was independently verified with `python -m app.cli verify`.

The repository's GitHub Actions workflow configures the same suite on Windows and Linux, on Python 3.11 and 3.13. Configuration is not a claim that every matrix job has already run; see the repository's Actions tab for remote results.

## Browser checks

Checked in the desktop app's Chromium-based in-app browser:

- Initial original/corrected comparison: 4.3125% versus 4.2500%, a −6.25 bp difference at T+5.
- Four-step guided investigation, including actual server-side reference replay and a blocked changed candidate.
- Passing release check with the same approved artifacts.
- Custom experiment capture with the recency-weighted model, 15-observation lookback and three-step horizon; comparison updated to the common T+3 step.
- Artifact ledger, captured source and environment, and downloadable JSON evidence bundle.
- Default panel layout, 1440 × 1100 desktop layout and 390 × 844 mobile layout. Capture remained reachable on mobile, with no document-level horizontal overflow in the checked views.
- Browser error log was empty after the desktop investigation check.
- The technical interface revision was checked with captured run metadata, five artifact fingerprints, and explicit expected/actual replay digests visible in the comparison workspace.
- The dark terminal theme was checked on desktop and at 390 × 844, including the configuration dialog, dark native controls, chart colour roles and a successful exact-output replay.

`workbench.jpg` is a screenshot of the running interface, not a mockup. Local test databases and downloaded test artifacts are not committed.

## Expected limits

This validation covers the bundled models and local single-user workflow. It does not establish predictive model performance, authenticated approval, cloud deployment, load capacity, or compatibility with arbitrary Python packages. Strict replay intentionally refuses a changed recorded execution context.

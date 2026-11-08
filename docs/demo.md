# A 90-second walkthrough

Start the app with `python -m app.server`, open http://127.0.0.1:8765 and select **Start the investigation**.

## 0–20 seconds: the changed answer

“This captures 124 observations of the US 10-year Treasury yield. Both runs use the same model source and parameters. I deliberately corrected one older observation by 25 basis points; the final observed yield is unchanged. The five-step forecast moved by −6.25 basis points.”

Show the metrics, chart and identical model/configuration fingerprints.

## 20–40 seconds: the changed input

“The input comparison identifies 30 May 2024 as the changed observation: 4.55% to 4.80%. This correction is injected, not a real FRED revision. The controlled replay changes only the data at this step, so we can inspect its contribution while holding the other inputs fixed.”

Show the input diff and replay contributions. The window-boundary explanation is in the README and can be checked by hand.

## 40–60 seconds: the original result

“Replay uses the exact captured source, data and parameters, checks the execution context, and re-executes the model. The newly computed output has the same SHA-256 digest as the original. The current corrected snapshot does not overwrite the old evidence.”

Advance the tour to the replay step and show the matching output digest.

## 60–80 seconds: the handover boundary

“The original run is approved against its manifest. The changed candidate fails the release check because its data and output no longer match the approved artifacts. Selecting identical approved artifacts passes.”

Advance to the release-gate step. This is a local decision check; it deploys nothing.

## 80–90 seconds: the evidence can travel

“The artifact ledger exposes the exact source, environment and full hashes. An exported bundle can be verified and replayed without the original database, using the recorded environment.”

Open **Artifact ledger** and show the export control. The financial model is a transparent baseline; the engineering problem is reproducibility, not predictive performance.

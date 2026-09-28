# Local app product validation

Executed with Streamlit AppTest against the current app.py and configured Neon database, using database_only mode. The server on localhost:8510 was healthy; no browser session was available, so browser-level interaction was not tested. No live market-provider refresh or policy download was performed.

| Input | Resolved stored product | History days | Final output | Market evidence | Policy evidence | UI errors |
|---|---|---:|---|---|---|---|
| iPhone 14 256 GB Blue | No exact stored match | 0 | REFUSE_NO_HISTORY | insufficient_evidence | insufficient_evidence | None |
| Noise Pulse 2 Max | B0B6BLTGTT | 325 | WAIT | insufficient_evidence | insufficient_evidence | None |
| Oppo Reno14 5G | B0FCTS8CVJ | 250 | WAIT | insufficient_evidence | insufficient_evidence | None |

All three inputs returned and rendered reports. No current verified offer exists in these stored-data results. WAIT outputs are timing assessments, not verified purchasable deals. Agent 3 explicitly reports LOCAL_CORPUS_NOT_READY because saved policy bodies have not been supplied/indexed.

Oppo matched the stored Pearl White 8GB/256GB variant. Noise matched the stored Jet Black record. Broad product queries do not establish that these are the user's preferred variants.

Fixed during validation: iPhone query previously attached Samsung Android 14 history; resolver now checks family, requested variant and ambiguity. Invalid LLM evidence now gets a deterministic, reverified recovery attempt. The UI no longer claims the verifier modified an unchanged draft merely because it appended metadata.

The full 146-test suite passed before the final verifier-recovery change; that change then passed 30 focused orchestration/synthesizer/verifier tests. The invalid-citation recovery path is covered by a regression test.

The before-fix JSON is a superseded diagnostic and must not be used for purchase recommendations.

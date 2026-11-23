# Implementation validation

## Verified

- **28 automated tests passed** using Python 3.12 and the dependency versions in `requirements-tested.txt`.
- Live Cornell client smoke test: **173 SP27 subjects**, **98 CS offerings**, normalized successfully. Subject retrieval omits the unreliable `q` filter.
- Official snapshot: **308 offerings**, five subjects; demo baseline assessed 65 schedulable courses, retained 14 eligible candidates, and produced **3 families / 5 total variants**.
- Four real-snapshot evaluation scenarios matched their expected feasibility outcome; full results are in `evaluation-results.json`.
- Browser checks: demo generation, family selection, one-elective variant with updated credits/match and explicit course difference, dated week selection, and JSON download.
- The weekly view removes CS 2043 after its March end date. It displays the second-half STSCI 4981 project in the relevant weeks.
- Narrow-screen check at 390 px: document width 390 px, no horizontal page overflow. The temporary viewport override was reset.
- Browser console error check returned no errors.
- Exported sample plan contains no API key. Upload tests verify files remain in the in-memory multipart spool and oversized request bodies are rejected.
- Public Render deployment succeeded on the Free Oregon instance. The current homepage, application script, stylesheet and static example match commit `44eaf3c` by SHA-256. Online browser checks verified demo generation, family selection, elective variants, the static example, and the workflow explanation.
- Five hosted demo requests each returned 3 families / 5 variants with solver status `OPTIMAL`. Median end-to-end latency was 671.62 ms; Python model construction and optimization took a median 300.32 ms. Live Cornell roster lookup also succeeded. See `PERFORMANCE.md` and `benchmarks/render-warm.json`.
- The static example opens directly with `?example=1` and supports family and elective-variant selection. A dedicated local test server recorded only the document, CSS, application JavaScript and bundled example JavaScript requests: no `/api/` calls during loading or those interactions.
- Static export includes its precomputed-result label and generation timestamp, a four-course plan, and no API key field. Switching from the solver demo back to the static example resets the mode label and hides the live activity panel.
- The built-in workflow and algorithm explanation expands and collapses successfully. At a 390 px viewport, the expanded page remains 390 px wide with no horizontal page overflow. The viewport override was reset; browser error logs were empty.

The test runner reports a Starlette deprecation warning about its HTTPX-based test client; the tests pass. This concerns the test adapter, not the application's HTTP client behavior.

## Not yet verified

- A paid OpenAI request with a real user's API key. SDK request/response structure, source validation, reflection, and upload orchestration are covered with controlled test doubles. They do not establish live model relevance or prerequisite-parsing accuracy.
- Docker image execution.
- OCR of scanned transcripts, degree-audit correctness, unexposed linked-section rules, or actual registration eligibility.

The offline demo is labeled as deterministic sample scoring everywhere it is presented. No LLM accuracy or end-to-end paid-model performance result is claimed from that demo.

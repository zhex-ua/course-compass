# Deployment performance

Course Compass is live at <https://course-compass-wmw7.onrender.com>.

## Environment and method

- Deployed application commit: `44eaf3c` (static example and workflow explanation). The public homepage and all three JavaScript/CSS assets were checked against the local commit by SHA-256.
- Render Free web service, Oregon (US West), 0.1 CPU and 512 MB RAM.
- Python 3.12.12; one Uvicorn worker serving both the website and API.
- The initial build and deployment, before the UI additions, completed in 1 minute 17 seconds according to the Render dashboard.
- The current warm benchmark started on 2026-11-23 at 06:11:29 UTC, from the user's test computer. Network timings include the route from that computer to Render.
- Five serial requests per repeated measurement, using one persistent HTTP client. Values below are the median and observed range, not a load-test guarantee.
- The demo uses 308 official Cornell SP27 offerings and a synthetic completed-course profile. It assesses 65 schedulable courses and puts 14 eligible courses into the solver pool. Scoring is deterministic; no OpenAI calls or charges occurred.

## Warm-instance results

| Measurement | Median | Observed range |
| --- | ---: | ---: |
| Homepage HTML response | 67.09 ms | 63.30–70.18 ms |
| Complete demo planning request | 671.62 ms | 618.87–1,319.97 ms |
| Server-side demo pipeline | 540.68 ms | 477.88–1,071.37 ms |
| Python model construction and optimization | 300.32 ms | 296.21–897.42 ms |

Every demo request returned **3 plan families and 5 total variants**, with solver status `OPTIMAL` for the bounded model. Progress events reached the client before the final result. The hosted browser also generated plans and switched families and elective variants successfully.

The live Cornell roster lookup took **1,229.85 ms** on its first request and **70.64 ms** on the next cached request. Those are individual observations, not medians. The first health request in this benchmark took **117.53 ms**; the instance was already running, so that is not a cold-start result.

The HTML figure measures downloading the document, not all static assets, browser rendering, or a Web Vitals metric. The optimizer figure includes Python model construction and thread scheduling around CP-SAT, not just native solver execution. Server timings exclude client network transfer and can exclude waiting for a request slot. A larger candidate pool or concurrent users can take longer.

## Static example and workflow

The [static example](https://course-compass-wmw7.onrender.com/?example=1) loads a bundled 104,503-byte JavaScript result fixture. It supports families, variants, weekly schedules and JSON export without API requests or solver execution. A dedicated local server's access log verified that direct loading and these interactions requested only the HTML, CSS and two JavaScript files. The live deployed page also rendered the saved example successfully.

The built-in **How it works** flowchart is static HTML/CSS. Its process and algorithm explanation expands without any API calls. Browser validation covered the 390 px layout and switching between the solver demo and static example. No browser errors were observed.

## Idle-instance measurement

No verified cold-start latency is reported. A preliminary idle window did not establish that the service had gone to sleep, so the subsequent fast health response is not counted as a wake-up measurement. Further cold-start testing was omitted at the user's request.

## Reproduce

```bash
python benchmark.py https://course-compass-wmw7.onrender.com \
  --runs 5 --output benchmark-results.json
```

Current measurements: [benchmarks/render-warm.json](benchmarks/render-warm.json). The first deployment's measurements are retained in [benchmarks/render-initial-warm.json](benchmarks/render-initial-warm.json).

Live OpenAI scoring, transcript extraction, and full live-agent latency have not been measured with a paid API key. The demo measures hosting, streaming, normalization and constraint solving; it does not measure LLM recommendation accuracy or the duration of model calls.

Render documents a 15-minute idle threshold and a roughly one-minute wake-up period for Free services: [Free service behavior](https://render.com/docs/free). Open the website before a classroom presentation so any wake-up completes before the demonstration.

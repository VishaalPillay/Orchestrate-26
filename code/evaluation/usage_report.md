# Usage report

Every model call went to a free tier (Google Gemini; Groq only as a fallback that the final configuration does not use),
so the **billed cost is $0.00**. The cost columns are *estimates at assumed list prices*:

- `gemini-3.5-flash-lite`: $0.30 per 1M input tokens, $2.50 per 1M output tokens
- `gemini-3.6-flash`: $1.50 per 1M input tokens, $9.00 per 1M output tokens

The rates are assumptions, not provider-verified: the `gemini-3.5-flash-lite` rate is a third-party listing, and the
`gemini-3.6-flash` rate is assumed (no published rate found). Change `PRICES` in `code/tools/usage_report.py` and rerun it to reprice.

## 1. The final full-dataset run

`python code/main.py --mode live --split all` (`LLM_PROVIDERS=gemini`) wrote the committed `output.csv`. The intake,
category, perception and skeptic answers were already in the disk cache, so the run made only the provider calls
below: 3 skeptic calls that re-ran one target left unfinished by the previous run. `--mode replay` reproduces the
same `output.csv` byte for byte with 0 provider calls. The final run made only 3 calls because every other answer was
already cached, so its token count is not the cost of the solution: section 2 (about 571 tokens and about $0.0006 per
request, over 550 requests) is the real cost basis.

| Provider | Model | Provider calls | Cache hits | Input tokens | Output tokens | Est. cost |
|---|---|---|---|---|---|---|
| gemini | gemini-3.5-flash-lite | 3 | 87 | 9,414 | 156 | $0.0032 |
| gemini | gemini-3.6-flash | 0 | 11 | 0 | 0 | $0.0000 |
| **Overall** | | **3** | **98** | **9,414** | **156** | **$0.0032** |

Requests processed: 550 (518 evaluation + 32 samples). Tokens in this run: 9,570 in total, 17.4 per request; estimated cost $0.0032 in total, $0.000006 per request.

## 2. Everything the final output depends on

The answers that `--mode replay` reads, by task, from the ledger: intake (28 batches of 20 requests, gemini-3.5-flash-lite),
the category fallback (2 names), screenshot perception (11 images, gemini-3.6-flash) and the sample-skeptic
conversations (60 calls; ledger calls from 2026-10-25T08:00Z on: the regenerations of iterations 20 and 21 and the final run).
The evaluation requests need no skeptic call (budget 0, iteration 13).

| Task | Provider | Model | Provider calls | Cache hits | Input tokens | Output tokens | Est. cost |
|---|---|---|---|---|---|---|---|
| category.fallback | gemini | gemini-3.5-flash-lite | 4 | 96 | 2,367 | 136 | $0.0011 |
| intake.batch | gemini | gemini-3.5-flash-lite | 28 | 1447 | 40,194 | 50,752 | $0.1389 |
| perception.image | gemini | gemini-3.6-flash | 11 | 2625 | 19,617 | 10,694 | $0.1257 |
| skeptic | gemini | gemini-3.5-flash-lite | 79 | 471 | 187,689 | 2,669 | $0.0630 |
| **Total** | | | **122** | **4639** | **249,867** | **64,251** | **$0.3286** (priced models) |

Per request over 550: 571.1 tokens (454.3 in, 116.8 out), $0.000598. Over the 518 evaluation requests: 606.4 tokens, $0.000634. Cache-hit counts are cumulative over every run in the ledger.
The category rows include the 2 calls of the LLM-only experiment of iteration 12a; the perception row includes one retry.

## 3. All provider calls recorded during development

Every call in `code/.cache/runs/run_log.jsonl` since the first run, including superseded prompts, failed and
discarded attempts, the Groq fallback runs, the skeptic's test-split run (iteration 13) and the negative control.

| Task | Provider | Model | Provider calls | Cache hits | Input tokens | Output tokens | Est. cost |
|---|---|---|---|---|---|---|---|
| category.fallback | gemini | gemini-3.5-flash-lite | 4 | 96 | 2,367 | 136 | $0.0011 |
| intake.batch | gemini | gemini-3.5-flash-lite | 28 | 1447 | 40,194 | 50,752 | $0.1389 |
| intake.batch | groq | openai/gpt-oss-120b | 28 | 0 | 39,752 | 46,352 | unpriced |
| perception.image | gemini | gemini-3.6-flash | 11 | 2625 | 19,617 | 10,694 | $0.1257 |
| perception.image | gemini | gemini-3.8-flash | 12 | 59 | 18,925 | 14,912 | unpriced |
| skeptic | gemini | gemini-3.5-flash-lite | 503 | 1375 | 1,046,590 | 16,636 | $0.3556 |
| skeptic | groq | openai/gpt-oss-120b | 55 | 0 | 86,429 | 4,478 | unpriced |
| **Total** | | | **641** | **5602** | **1,253,874** | **143,960** | **$0.6212** (priced models) |

Recorded provider errors (quota, timeouts, malformed answers; no tokens billed): 363.

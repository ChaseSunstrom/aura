# Embedder and reranker: TEI against llama-swap (T-0513)

The question: should jarvis-core move from the two TEI containers (`BAAI/bge-small-en-v1.5` on
:7997, `cross-encoder/ms-marco-MiniLM-L-6-v2` on :7998) to llama-swap's CPU pair
(`qwen3-embedding-0.6b`, `qwen3-reranker-0.6b`, :9000)? Both pairs run on the CPU only.

Measured by `python3 scripts/verify/embed-rerank-bench.py` on production, 2026-11-08 14:19 UTC,
load average 0.97 on 10 CPUs at the start, no CI job running. The run took about 25 minutes,
almost all of it the llama-swap reranker. The raw line is `embed-rerank.json` beside this file.

Who uses what (grounded first): jarvis-core memory embeds with TEI bge and has reranking off;
research reranks web pages with TEI MiniLM; Hermes' memory provider (holographic) uses no
embedder at all. jarvis-core's memory store held 0 notes on production, so the notes are made up
(`scripts/verify/embed-rerank-fixture.json`, 119 one-line house notes, each with one reworded
question).

## Latency, ms (5 warmups, 50 timed runs)

| | TEI p50 / p95 | llama-swap p50 / p95 | limit (p95) |
|---|---|---|---|
| embed 1 question | 6.2 / 31.2 | 86.5 / **98.5** | 150 |
| embed 16 notes | 100.5 / 112.0 | 673.2 / 781.5 | - |
| rerank 1 question over 20 notes | 87.0 / 91.2 | 5389.7 / **5580.9** | 300 |

During the llama-swap reranker's run jarvis-models held about 8 cores (`docker stats`: 800%) and
the host's load average went from 1 to 10.7.

## Quality, 119 questions (bootstrap 95% interval)

| | TEI recall@1 | llama-swap recall@1 | TEI MRR | llama-swap MRR |
|---|---|---|---|---|
| embed, every note ranked by cosine (memory's path) | 0.824 [0.756, 0.882] | 0.924 [0.874, 0.966] | 0.885 | 0.957 |
| rerank, 20 fixed candidates per question (research's path) | 0.916 [0.866, 0.958] | 0.983 [0.958, 1.0] | 0.957 | 0.992 |

The held-out half (59 questions, odd pairs) agrees: embed 0.831 against 0.949, rerank 0.898
against 0.983.

## Verdict

**Keep TEI as the pair** (the script's verdict: `keep TEI: rerank-20 p95 >= 300 ms`). The
llama-swap reranker ranks better, but 5.6 s for twenty candidates is about 18 times the limit and
takes most of the host's CPU while it runs; research reads up to 8 pages per question.

The embedder on its own does meet its limit: +10 points recall@1 at 98.5 ms p95. Switching
memory's embedder alone is T-0684. It needs a qwen row in `memory/vectors.py` (its query
instruction, and a floor measured on its own cosine scale), the llama-swap key in the house's
environment, and a re-embed (0 notes today).

Does not cover: research's real pages (a page is long, a note one line; the two are not the same
retrieval problem, see `config/configuration.yaml`), a model server already busy with another
caller, or the GPU.

## Memory's floor, on each embedder's own scale (T-0684)

`python3 scripts/verify/embed-rerank-bench.py --scales --out bench/results/embed-rerank-scales.json`
(about a minute; it only embeds). Per question: the right note's cosine, the best wrong note's,
and every wrong note's. The floor is the 0.05 step at or below the worst right answer.

| | right: min / p5 / p50 | best wrong: p50 / p95 / max | any wrong: p50 / p95 | floor | wrong notes at or above it |
|---|---|---|---|---|---|
| TEI, bge-small-en-v1.5 | 0.402 / 0.483 / 0.636 | 0.570 / 0.680 / 0.715 | 0.375 / 0.511 | 0.40 | 36.2% |
| llama-swap, qwen3-embedding-0.6b | 0.352 / 0.430 / 0.617 | 0.469 / 0.646 / 0.730 | 0.222 / 0.399 | 0.35 | 9.0% |

Both floors keep every right answer on the held-out half. bge's scale is compressed (a typical
wrong note scores 0.375, just under its floor); qwen's is spread, so at its floor it lets through
a quarter as many wrong notes. A floor cannot separate the right note from the best wrong one on
either model (they overlap; ordering is what recall@1 above measures): it only decides whether
recall answers at all. `memory/vectors.py` gives qwen 0.35 and keeps bge at 0.35, set earlier from
six paraphrases and deliberately under its worst right answer.
`test_qwens_floor_keeps_its_worst_right_answer_and_drops_most_wrong_ones` reads
`embed-rerank-scales.json`, so the floor in the code and its measurement cannot drift apart.

Does not cover: notes longer than a line, or a store of thousands of notes (more wrong notes
above the floor, the same ordering).

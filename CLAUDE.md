# News Radio — project instructions

Automated pipeline that runs overnight, turns the day's economy and geopolitics
news into a two-voice podcast episode (Maria and Pedro), synthesises the audio
locally, and publishes to a private RSS feed for the iOS Podcasts app. See
README.md for setup and the cost table.

## Commands

```bash
python -m pytest                                  # 221 tests, none touch the network
python -m podcast.cli doctor                      # what is installed/configured, runs no stage
python -m podcast.cli sources --check             # which feeds respond right now
python -m podcast.cli collect --dry-run           # stage 1, prints without saving
python -m podcast.cli script --from-raw FILE --show  # stage 3 without a GPU
python -m podcast.cli run                         # all five — this is what the scheduler calls
python -m podcast.cli demo                        # the pipeline over fixtures; no key, GPU or network
```

## The one architectural invariant

**Every stage that touches an external service takes it as a parameter.**
Stage 1 takes a `fetcher`, stages 2 and 3 take a `client`, stage 4 an `engine`,
stage 5 a `pusher`. Each defaults to the real implementation and is overridden
in tests.

This is not a style preference — it is the only reason the project is
developable at all. The pipeline needs a 12 GB GPU, a paid API key and a
network; the whole 221-test suite needs none of them and finishes in under a
second. A stage that reaches for its dependency by import instead of accepting
it as an argument breaks CI and breaks `demo`, and neither failure shows up on
the machine that has the GPU, the key and the models already installed.

If a new stage cannot be expressed this way, the seam is wrong and should be
fixed rather than worked around.

---

## Rules that are easy to violate

**Stage 1 never fetches the article.** It requests the feed URL and nothing
else — there is no scraper in this codebase, and adding one would change the
project's legal position, not just its data. `_entry_summary` reads `summary` /
`description` and deliberately *not* `content`, because `content` usually
carries the whole article. Summaries are capped at `MAX_SUMMARY_CHARS = 600`.
The copyright claim in the README rests on these three facts being mechanically
true; the stage 3 prompt rule is an instruction to a model and cannot carry the
claim alone.

**A speech rate measured on a short sample is wrong.** The same voice reads
202 wpm as one continuous block, 177 across an 11-line dialogue, and 172 across
a 75-line episode — each line adds a pause and an end-of-sentence cadence, so
the rate falls as the sample grows. `WORDS_PER_MINUTE = 172` came from a
complete 18.74-minute episode (3,227 words). At 195 the "30 minute" ceiling
delivered 33 real minutes. **Re-measure on a full episode, never a clip,** if
`KOKORO_SPEED` or `KOKORO_GAP_MS` change.

**Ask for a per-theme word budget, never a total.** "About 3,900 words"
returned 1,904. Decomposing it — "5 themes, ~780 words each, 12-16 lines per
theme" — returned 3,227 from the same digest. The model cannot estimate 4,000
words in aggregate but can check itself against a per-theme number while
writing. Expect ~83% of whatever is requested and let the ceiling absorb the
rest; raising the target beats insisting on the total.

**A model swap needs the cost recomputed before it is made.** The budget is
R$50/month and stage 3 is the only metered call. `claude-sonnet-5` measured
4,145 input / 10,141 output tokens per episode — US$0.165/day, ~R$27/month at
R$5.50/US$. Adaptive thinking bills as output and dominates that. `claude-opus-5`
at US$5/US$25 exceeds R$60/month and breaks the budget.

**`claude-haiku-4-5` is rejected as the default, on evidence, not price.** It
costs a sixth as much. On the same digest it invented an arithmetic equivalence
("1.8% of a month = two days of work"; it is half a day), ranged from 1,753 to
2,614 words across identical runs, and made grammatical errors. In a programme
that exists to explain economics, a fabricated number is the one defect that
cannot be traded for cost. It stays as plan B if the budget tightens.

**Maria is `ef_dora`, and the reason is not obvious.** Kokoro's pt-BR pack has
one female voice, `pf_dora`, which sounds artificial. `ef_dora` is the same
voice actor in the Spanish pack with a better-trained style vector; with
`lang="pt-br"` the phonemisation stays correct. Six variants were compared by
ear before deciding. **Do not change it without repeating the listening test** —
the comparison clips are in `docs/voices/`.

**Do not switch to the English voices.** They are better voices and it was
tried. They mispronounce exactly the proper nouns that saturate the programme —
Ibovespa, Selic, Copom, Petrobras — and a wrong pronunciation in every sentence
costs more than synthetic timbre does.

**The script is written to be heard, not read.** Numbers are spelled out,
because TTS reads "13,25%" wrong. No markdown, no bullets, no emoji, no stage
directions in parentheses. Speakers strictly alternate Maria/Pedro; two
consecutive lines from one speaker means the other should have asked a question.
The episode is generated overnight and heard in the morning, so it opens with
"bom dia" and never "boa tarde".

**Items go to the local model numbered, never by id.** Small models corrupt
hash ids. Title, source and link are always taken from the original item, never
from what the model wrote back — the model returns an index, and the index is
all that is trusted from it.

**A failure in one unit never kills the stage.** A feed that times out is
recorded in `Collection.errors` and collection continues; a batch that fails in
stage 2 is dropped with a log. One outlet being down cannot cost the day's
episode, and there is no human awake at 5 a.m. to retry it.

**Items with no publication date are kept.** Several official feeds (BCB, IBGE)
omit `pubDate`, and discarding undated items would drop exactly the primary
sources. The seen-cache is what stops them recurring, not the date filter.

**The seen-cache is why yesterday's news is not in today's episode.** A story
published at 23:00 is still inside a 24-hour window the next morning. Without
`SeenStore` the same item lands in two consecutive episodes. A corrupt cache
restarts empty rather than failing the run.

**Disabled sources stay in the registry with their reason.** BCB serves JSON
not RSS, IBGE is behind a Cloudflare challenge, FGV's TLS handshake fails.
Deleting them means someone re-tries them blind in six months. `enabled=False`
plus the comment is the record.

---

## Publication and privacy

**The feed's only protection is that its URL is hard to guess — so the URL must
never appear in a tracked file.** It was hardcoded in `tests/test_stage5_publish.py`
once; that is the failure mode. `PODCAST_BASE_URL` lives in `.env` (gitignored)
and nowhere else. Tests use obvious placeholders.

**Obscurity cannot survive hosting the feed from this public repo.** If the
pages branch lives here, the URL is `https://<user>.github.io/News_radio/feed.xml`
— derivable by anyone who can read the setup instructions. The feed is hosted
from a separate, privately-named repository, and that name does not appear in
this one. Never submit the URL to a podcast directory.

**Stage 5 never creates the pages branch.** It only does `git add/commit/push`
on an existing checkout at `data/public/`. Branch creation is one-time manual
setup, not something to run blind at 5 a.m.

**The feed is rebuilt from scratch every run, never appended.** Scanning the
existing mp3s and regenerating the whole `feed.xml` is simpler than incremental
append and self-heals: a corrupt feed is fixed by the next run. Episodes beyond
`MAX_EPISODES_IN_FEED` (30) are pruned, mp3 and sidecar together.

---

## Environment

**Python 3.13, not 3.14.** `kokoro-onnx` declares `Requires-Python >=3.10,<3.14`,
so stage 4 cannot install on 3.14. Stages 1, 2, 3 and 5 are fine there.

**`pydub` needs `audioop-lts` on 3.13+.** `audioop` left the stdlib in PEP 594;
without the backport `import pydub` fails. It is already in
`requirements-audio.txt` behind a version marker.

**The package is English; the tests and the product are not.** Docstrings,
comments, log lines and exception messages in `podcast/` are English. Four things
stay Portuguese on purpose, and each is marked in place: the prompts in stages 2
and 3, which instruct models that must answer in Portuguese; the strings that
reach the feed and the mp3's ID3 tags, which listeners read in their podcast app;
the stopword list and boilerplate patterns in `textutils.py`, which are matched
against Portuguese text; and the alternative JSON keys the local model emits.
`tests/` also stays Portuguese -- its names describe behaviour over Portuguese
text (`test_remove_titulo_semelhante_de_veiculos_diferentes`) and its fixtures are Portuguese feeds,
so translating the names would make them less accurate, not more. Do not
"finish the job" on any of these.

**Everything is UTF-8; Windows is not.** The console defaults to cp1252 here and
mangles the first accented character. Pass `encoding="utf-8"` explicitly on every
`open()`, and set `PYTHONIOENCODING=utf-8` before printing non-ASCII.

**Never write an absolute path into the package.** `TestSemCaminhosAbsolutosNoCodigo`
fails the build if `C:\Users`, `/home/`, `/Users/` or `OneDrive` appears in
`podcast/`. Paths in `.env` are relative and resolved from the repo root, because
the requirement is that a fresh clone runs anywhere without editing code -- on
the CI runner and on a stranger's laptop, not just here.

**`data/` and `models/` never enter git.** Generated episodes, the seen-cache and
the Kokoro weights are all gitignored. The one deliberate exception is
`docs/sample-voice.mp3` and `docs/voices/`, whitelisted for the README, plus
`demo/`, which holds trimmed real stage 2 and stage 3 output so `demo` has
something to run on.

**One machine runs everything, but nothing may assume that.** Production is a
single RTX 4070 box. The injected clients are still non-negotiable, because the
things that depend on them are not this machine: CI runs the suite on a bare
Linux runner with no GPU and no secrets, and `podcast demo` reproduces the
pipeline from a fresh clone with no credentials at all. A stage that imports its
dependency instead of accepting it breaks both, and neither failure shows up
here.

---

## Content and use

The pipeline consumes only headlines and the short summaries the feeds publish,
and synthesises in its own words. Brazilian outlets' RSS is free but licensed
for personal, non-commercial use without modifying the content; this project is
built for exactly that — a private, unlisted feed that is not republished. **If
the feed ever became genuinely public, that assessment needs redoing.**

Premium wires (Reuters, Bloomberg, FT) are deliberately excluded: paywalled, and
their terms do not permit automated use. Do not add them.

Review is light and periodic, not per-episode: listen to a few a week and adjust
the stage 3 prompt.

## Do not

- Do not use a paid TTS API. Local TTS is what makes a two-voice format free, and
  free TTS is what makes the two-voice decision affordable at all.
- Do not deliver over WhatsApp. A private RSS feed in the native Podcasts app is
  free and needs no Business API.
- Do not reproduce source article text. Synthesise, always.

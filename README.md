# Government Service Navigator

A bilingual (Bangla/English) RAG chatbot that answers questions about four Bangladeshi
government services — **NID** (National ID), **e-Passport**, **e-Return** (income tax),
and **BDRIS** (birth & death registration) — grounded in official source documents.

Built as a CSE299 (Junior Design Project I) capstone.

Ask in English, Bangla, or romanized Bangla ("Banglish"); the bot finds the right official
content, answers in your language, and cites its sources.

```
question> reissue fee koto?
আপনি কোন সেবার বিষয়ে জানতে চাচ্ছেন — পাসপোর্ট নাকি জাতীয় পরিচয়পত্র (NID)?
question> passport
পাসপোর্ট রিইস্যু ফি পাসপোর্টের ধরন, পৃষ্ঠাসংখ্যা এবং বিতরণের গতির উপর নির্ভর করে...
```

## Why this project?

Large language models like ChatGPT and Gemini are trained on general internet data. For the
specific, changing details of Bangladeshi government services — exact fees, required
documents, current procedures — they often give **outdated or invented** answers, because
that information either was never in their training data or has changed since.

A concrete example this project actually ran into: asked for the passport reissue fee, a
general LLM confidently quoted old **MRP** passport fees from its training data — the wrong
fees for today's **e-Passport** system. This chatbot instead retrieves the *current official*
e-Passport fee table from the source documents, answers from that, and shows the source link.

That is the point of **Retrieval-Augmented Generation (RAG)**: instead of trusting the model's
memory, retrieve the relevant official passages first, then have the model answer *grounded in
those passages*, with citations. For government information — where a wrong fee or a missing
document requirement has real consequences — being correct and sourced matters more than being
fluent.

The project also centralizes information otherwise scattered across four separate government
portals, behind one bilingual interface that understands English, Bangla, and Banglish.

## How it works

Each question flows through five steps:

1. **Normalize** — the query's language is detected first, deterministically. Text already in
Bangla script, and text clearly in English, are both passed straight through without any
language-model call. Only romanized Bangla ("Banglish") is transliterated to Bangla script by
an LLM. Both the original and the converted forms are kept and searched ("expand, don't
replace"), so a bad conversion can only fail to help, never hurt. Detecting English
deterministically — rather than asking the model to leave English alone — is what keeps an
English question from being accidentally answered in Bangla.
2. **Disambiguate** — some questions are ambiguous across services (e.g. "reissue fee" could
mean NID or passport). A registry (`concept_registry.json`), built from an audit of all four
corpora, records which concepts genuinely exist in which service. If a question is genuinely
ambiguous, the bot asks a short clarifying question — in the user's language, offering **only**
the services that actually have that concept. This decision is rule-based, not left to the LLM.
3. **Retrieve** — the question is embedded (`BAAI/bge-m3`) and matched against the relevant
service's content in a vector database (ChromaDB). A named or disambiguated service is searched
alone; otherwise all four are searched and merged by similarity. If nothing clears a minimum
relevance threshold, the bot declines rather than guessing.
4. **Generate** — an LLM writes the answer using **only** the retrieved passages as its source
of facts. It can explain in its own words and ask a follow-up when the exact answer depends on
details the user hasn't given (e.g. passport validity, page count) — but every specific figure
must come from the retrieved content, never from the model's own memory. For action requests
("how do I apply?") the answer surfaces the actual application portal link, not a passive
"read more" page. Recent conversation turns are included so follow-ups ("and the 5-year one?")
are understood in context, without letting an earlier topic leak into a later, unrelated
question.
5. **Cite** — the official source URL(s) behind the answer are listed.

## Why RAG instead of just prompting an LLM?

- **Grounded, current facts.** Fees, forms, and procedures come from official documents, not the
model's possibly-stale training data (see the MRP-fee example above).
- **Citations.** Every answer points to its official source, so users can verify.
- **Honest limits.** When the documents don't cover a question, the bot says so and points to the
official portal — instead of hallucinating an answer.
- **No fine-tuning needed.** Updating the bot's knowledge means updating the source corpus, not
retraining a model.

## Tech stack

- **Embeddings:** `BAAI/bge-m3` (multilingual, handles Bangla + English)
- **Vector store:** ChromaDB (one collection per service)
- **LLM:** provider-agnostic via `llm_backends.py` — any OpenAI-compatible API (currently z.ai's
free GLM-4.7-Flash); switching to Gemini, Claude, etc. is a config change, not a code change
- **UI:** Streamlit web app (`app.py`) plus a command-line interface (`answer.py`)

## Project layout

```
data/chunks/          the four corpora (one .jsonl per service)
data/raw/             source transcripts the corpora were built from
data/vectordb/        ChromaDB database (generated, not stored in Git)
embed_<service>.py    builds the vector index for a service
query_<service>.py    ad-hoc retrieval testing for a service
eval_<service>.py     gold-query evaluation for a service
answer.py             the chatbot logic + command-line interface
app.py                Streamlit web interface
normalize_query.py    language detection + Banglish -> Bangla normalization
disambiguate.py       cross-service disambiguation logic
concept_registry.json which concepts exist in which service (tunable, no code change)
llm_backends.py       swappable LLM provider
test_multiturn.py     hermetic routing / dialogue-state regression tests
docs/                 build methodology notes per service
```

## Setup

**1. Install dependencies**

```
pip install chromadb sentence-transformers openai ftfy streamlit
```

**2. Configure the LLM provider**

Any OpenAI-compatible provider works, set entirely through environment variables. Currently
configured for [z.ai](https://z.ai)'s free GLM-4.7-Flash. Create `setenv.bat` (kept out of Git —
see `.gitignore`) with:

```
set LLM_PROVIDER=openai
set LLM_BASE_URL=https://api.z.ai/api/paas/v4
set LLM_MODEL=glm-4.7-flash
set LLM_DISABLE_THINKING=1
set LLM_API_KEY=your_key_here
```

Run `setenv.bat` once per terminal session before running `answer.py` or `app.py`. (The
embedding and evaluation scripts don't call the LLM, so they don't need it.)

**3. Build the vector database** (run once, or after editing any corpus)

```
python embed_nid.py
python embed_epassport.py
python embed_ereturn.py
python embed_bdris.py
```

## Usage

**Web app** (recommended):

```
setenv.bat
streamlit run app.py
```

**Command line:**

```
python answer.py
```

**Evaluate retrieval for one service:**

```
python eval_nid.py
```

**Run the routing / dialogue-state tests** (no LLM or database needed):

```
python test_multiturn.py
```

## Example queries

- `passport fee koto?` — lays out the full fee table and asks which option
- `reissue fee koto?` — ambiguous, so it asks: NID or passport?
- `জন্ম নিবন্ধন করতে কি লাগে?` — documents needed for birth registration
- `income tax return joma dewar last date koto?` — tax return deadline
- `What is the deadline for filing an income tax return online?` — answered in English
- `আজকে ঢাকায় বৃষ্টি হবে?` — out of scope, so it declines instead of guessing

## Known limitations

- **Out-of-scope robustness.** The bot reliably declines an out-of-scope question when nothing
in the corpus is relevant. When an off-topic query happens to overlap in wording with an
in-corpus record (e.g. "gold price" brushing a tax FAQ about declaring the market value of
gold assets), the decline is less reliable, because separating a genuinely off-topic query from
a legitimately low-relevance in-scope one is a semantic distinction a similarity threshold alone
can't make.
- **Banglish variation.** Heavily inconsistent romanized spelling can occasionally
mistransliterate; the corpus is unaffected since normalization only changes the query. Terse
English fragments with no grammatical function words (e.g. "passport fee") are also handled by
the transliteration model rather than the deterministic English path.
- **Corpus coverage.** A few edges remain by design or deferral — for example, the corpus names
specific forms (Form-8, Form-13) but does not host their downloadable PDFs. These are documented
as known gaps rather than hidden.
- **Free-tier model.** The free model runs under a rate limit (~1 request/second, ~1000/day),
fine for development and demos but not for public traffic. The architecture is model-agnostic —
a stronger or paid model is a config change.

## Future work

- Public deployment on a small paid VM (bge-m3 + ChromaDB are too heavy for typical free hosting)
- Fuller multi-turn conversation memory (beyond the current bounded window)
- Fill the remaining corpus coverage gaps
- Automated freshness checks to detect when official pages change, with a human-approval gate
- Additional services (visa, further NBR services, and later transport/land/city-corporation),
following the same corpus → embed → registry pattern with no pipeline rewrite

## Corpus provenance

Each corpus was built from official government portals and documents, with Bangla content kept
verbatim (never machine-translated) and English clearly labeled as translation. See `docs/` for
the per-service methodology and source list.

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

1. **Normalize** — romanized Bangla ("Banglish") is converted to Bangla script by an LLM.
   Both the original and converted forms are kept and searched ("expand, don't replace"), so a
   bad conversion can only fail to help, never hurt.
2. **Disambiguate** — some questions are ambiguous across services (e.g. "reissue fee" could
   mean NID or passport). A registry (`concept_registry.json`), built from an audit of all four
   corpora, records which concepts genuinely exist in which service. If a question is genuinely
   ambiguous, the bot asks a short clarifying question — in the user's language, offering **only**
   the services that actually have that concept. This decision is rule-based, not left to the LLM.
3. **Retrieve** — the question is embedded (`BAAI/bge-m3`) and matched against the relevant
   service's content in a vector database (ChromaDB). A named or disambiguated service is searched
   alone; otherwise all four are searched and merged by similarity.
4. **Generate** — an LLM writes the answer using **only** the retrieved passages as its source of
   facts. It can explain in its own words and ask a follow-up when the exact answer depends on
   details the user hasn't given (e.g. passport validity, page count) — but every specific figure
   must come from the retrieved content, never from the model's own memory. Recent conversation
   turns are included so follow-ups ("and the 5-year one?") are understood in context.
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
  free GLM-4.5-Flash); switching to Gemini, Claude, etc. is a config change, not a code change
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
normalize_query.py    Banglish -> Bangla normalization
disambiguate.py       cross-service disambiguation logic
concept_registry.json which concepts exist in which service (tunable, no code change)
llm_backends.py       swappable LLM provider
docs/                 build methodology notes per service
```

## Setup

**1. Install dependencies**

```
pip install chromadb sentence-transformers openai ftfy streamlit
```

**2. Configure the LLM provider**

Any OpenAI-compatible provider works, set entirely through environment variables. Currently
configured for [z.ai](https://z.ai)'s free GLM-4.5-Flash. Create `setenv.bat` (kept out of Git —
see `.gitignore`) with:

```bat
set LLM_PROVIDER=openai
set LLM_BASE_URL=https://api.z.ai/api/paas/v4
set LLM_MODEL=glm-4.5-flash
set LLM_DISABLE_THINKING=1
set LLM_API_KEY=your_key_here
```

Run `setenv.bat` once per terminal session before running any script.

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

## Example queries

- `passport fee koto?` — lays out the full fee table and asks which option
- `reissue fee koto?` — ambiguous, so it asks: NID or passport?
- `জন্ম নিবন্ধন করতে কি লাগে?` — documents needed for birth registration
- `income tax return joma dewar last date koto?` — tax return deadline
- `what's the best color for a car?` — out of scope, so it declines instead of guessing

## Known limitations

- **Model ceiling.** The free model (GLM-4.5-Flash) occasionally mixes English/Bangla numerals
  or over-explains. The architecture is model-agnostic — a stronger model is a config change.
- **Banglish variation.** Heavily inconsistent romanized spelling can occasionally mistransliterate;
  the corpus is unaffected since normalization only changes the query.
- **Corpus coverage.** A few edges remain (e.g. an income-tax homepage overview), documented as
  known gaps rather than hidden.

## Future work

- Fuller multi-turn conversation memory (beyond the current bounded window)
- Upgrade to a stronger LLM for more fluent, accurate generation
- Fill the remaining corpus coverage gaps
- Automated freshness checks to detect when official pages change

## Corpus provenance

Each corpus was built from official government portals and documents, with Bangla content kept
verbatim (never machine-translated) and English clearly labeled as translation. See `docs/` for
the per-service methodology and source list.

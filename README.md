# Government Service Navigator

A bilingual (Bangla/English) RAG chatbot that answers questions about four Bangladeshi
government services: **NID** (National ID), **e-Passport**, **e-Return** (income tax),
and **BDRIS** (birth & death registration).

Built as a CSE299 (Junior Design Project I) capstone.

Ask a question in English, Bangla, or romanized Bangla ("Banglish") — the bot finds the
right official content and answers in your language, with sources cited.

```
question> reissue fee koto?
আপনি কোন সেবার বিষয়ে জানতে চাচ্ছেন — পাসপোর্ট নাকি জাতীয় পরিচয়পত্র (NID)?
question> passport
পাসপোর্ট রিইস্যু ফি পাসপোর্টের ধরন, পৃষ্ঠাসংখ্যা এবং বিতরণের গতির উপর নির্ভর করে...
```

## How it works

Each question goes through five steps:

1. **Normalize** — if the question is romanized Bangla ("Banglish"), an LLM converts it
   to Bangla script. Both the original and the converted version are kept and searched
   ("expand, don't replace"), so a bad conversion can only fail to help, never hurt.
2. **Disambiguate** — some questions are ambiguous across services (e.g. "reissue fee"
   could mean NID or passport). A small registry (`concept_registry.json`) records which
   concepts genuinely exist in which service, built from an actual audit of all four
   corpora — not guessed. If a question is genuinely ambiguous, the bot asks a short
   clarifying question, in the user's language, offering only the services that
   actually have that concept. This decision is rule-based, not left to the LLM.
3. **Retrieve** — the question is embedded (`BAAI/bge-m3`) and matched against the
   relevant service's content in a vector database (ChromaDB). If a service was named
   or resolved by disambiguation, only that service is searched; otherwise all four are
   searched and merged by similarity.
4. **Generate** — an LLM writes the answer using only the retrieved passages as its
   source of facts. It can explain in its own words and ask a follow-up when the exact
   answer depends on details the user hasn't given (e.g. passport validity, page count),
   but every specific figure must come from the retrieved content, never from the
   model's own memory.
5. **Cite** — the official source URL(s) behind the answer are listed.

## Project layout

```
data/chunks/          the four corpora (one .jsonl per service)
data/raw/              source transcripts the corpora were built from
data/vectordb/         ChromaDB database (generated, not stored in Git)
embed_<service>.py     builds the vector index for a service
query_<service>.py     ad-hoc retrieval testing for a service
eval_<service>.py      gold-query evaluation for a service
answer.py              the chatbot itself (all 4 services)
normalize_query.py     Banglish → Bangla normalization
disambiguate.py        cross-service disambiguation logic
concept_registry.json  which concepts exist in which service (tunable, no code change)
llm_backends.py        swappable LLM provider (Gemini / Claude / any OpenAI-compatible)
docs/                  build methodology notes per service
```

## Setup

**1. Install dependencies**

```
pip install chromadb sentence-transformers openai ftfy
```

(`openai` is needed even for non-OpenAI providers — it's used for any OpenAI-compatible
API, including the free provider below.)

**2. Set your LLM provider**

The chatbot needs an LLM for query normalization and answer generation. Any
OpenAI-compatible provider works, configured entirely through environment variables —
no code changes needed to switch. Currently configured for [z.ai](https://z.ai)'s free
GLM-4.5-Flash model. Create `setenv.bat` (not tracked in Git — see `.gitignore`) with:

```bat
set LLM_PROVIDER=openai
set LLM_BASE_URL=https://api.z.ai/api/paas/v4
set LLM_MODEL=glm-4.5-flash
set LLM_DISABLE_THINKING=1
set LLM_API_KEY=your_key_here
```

Run `setenv.bat` once per terminal session before running any script.

To switch providers later (e.g. to Claude for higher answer quality), only the env
vars change — see `llm_backends.py` for the supported providers.

**3. Build the vector database**

Run once (or after editing any corpus):

```
python embed_nid.py
python embed_epassport.py
python embed_ereturn.py
python embed_bdris.py
```

## Usage

**Chat with the bot:**
```
python answer.py
```

**Evaluate retrieval quality for one service:**
```
python eval_nid.py
```

**Check the corpora for encoding corruption** (useful after editing any `.jsonl` by hand):
```
python check_encoding.py data/chunks/nid.jsonl data/chunks/bdris.jsonl data/chunks/epassport.jsonl data/chunks/ereturn.jsonl
```

## Current status

All four corpora are built, embedded, and passing retrieval evaluation. The chatbot
handles all four services with cross-service disambiguation and Banglish support.

**Known limitations:**
- GLM-4.5-Flash (the free model currently configured) occasionally mixes English and
  Bangla numerals in the same answer, or over-explains simple factual questions. The
  architecture is model-agnostic — switching to a stronger model later is a config
  change, not a rewrite.
- Romanized Bangla with heavy, inconsistent spelling variation can still be
  mistransliterated in rare cases; the corpus is unaffected since normalization only
  changes the query, never the stored data.
- No web UI yet — currently a command-line chatbot.

## Corpus provenance

Each service's corpus was built from official government portals and documents, with
Bangla content kept verbatim (never machine-translated) and English content clearly
labeled as translation. See `docs/` for the per-service build methodology and source
list.

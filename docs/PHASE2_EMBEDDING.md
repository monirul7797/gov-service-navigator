# Phase 2 — Embedding & Retrieval (how to run and explain it)

This turns `data/chunks/nid.jsonl` (Phase 1 output) into a searchable vector store
and lets you ask questions in English, Bangla, or Banglish and get the right chunks.

## The idea in three sentences

1. An **embedding model** converts each text chunk into a vector (a point in a
   high-dimensional space) such that texts with similar *meaning* are close together —
   even across languages.
2. We store those vectors in a **vector database** (ChromaDB) alongside each chunk's
   metadata (category, source URL, language).
3. To answer a question, we embed the question the same way and ask the database for
   the **nearest** chunks by cosine similarity. That's retrieval — the "R" in RAG.

## Run it

```bash
# from the project root
pip install -r requirements_phase2.txt

python embed_nid.py     # embeds 107 chunks -> data/vectordb/  (one time)
python query_nid.py     # runs demo queries, then an interactive prompt
```

First run of `embed_nid.py` downloads the model weights once (BGE-M3 ≈ 2.2 GB).
Embedding 107 short chunks then takes seconds, even on CPU.

## Why BGE-M3 and not BanglaBERT

BanglaBERT is an **encoder** — great at understanding Bangla, but it was never trained
to make *similar sentences* have *similar vectors*. Drop raw chunks through it and
retrieval is weak. BGE-M3 is a **sentence-transformer** trained specifically for
semantic search, and it's strong at multilingual + cross-lingual matching, which is
exactly what a Bangla/English/Banglish citizen assistant needs. (This is the point the
Gemini note raised, and it's correct.)

If your laptop is tight today, set `MODEL_NAME` at the top of both scripts to
`intfloat/multilingual-e5-small` (~0.47 GB). The scripts auto-handle the one gotcha
that comes with E5 models (see below).

## Two details that matter (good things to know when asked)

- **Normalization + cosine.** We embed with `normalize_embeddings=True` (unit-length
  vectors) and create the Chroma collection with `hnsw:space="cosine"`. With unit
  vectors, cosine similarity is just the dot product, and scores are comparable across
  queries. In `query_nid.py`, `similarity = 1 − distance`.
- **E5 prefixes.** E5-family models must prefix documents with `passage:` and questions
  with `query:`; BGE-M3 does not. The scripts detect this from the model name and apply
  the prefix automatically, so switching models stays correct.

## What to show your professor (2-minute script)

1. Open `nid.jsonl` — "Phase 1 gives every chunk a category, language, and its official
   gov.bd source URL. 107 chunks, zero broken Bangla."
2. Run `embed_nid.py` — "Each chunk becomes a 1024-dim vector from BGE-M3, stored in
   ChromaDB with its metadata."
3. Run `query_nid.py` — the demo asks the *same* knowledge base in **English, Bangla,
   and Banglish**. Point out that a Banglish query ("notun voter registration er fee
   koto?") retrieves the correct **Bangla** fee chunk — that's cross-lingual semantic
   search, impossible with keyword matching.
4. Point at the output metadata — "every answer carries its category and the official
   source URL it came from, so the final RAG can cite its source."

## What this is not (be honest if asked)

This is retrieval only — the "R" of RAG. It finds the right chunks; it does not yet
generate a written answer. Phase 3 feeds the top retrieved chunks to an LLM with a
"answer only from these official sources, and cite them" prompt. Retrieval quality
(what you're showing today) is what makes or breaks the final answer.

## Optional next experiment (from the Gemini note)

Query-augmented indexing: for each chunk, generate a few likely citizen questions and
embed them *alongside* the chunk (as a separate vector or an extra field), so a real
question matches more reliably. Do this at index time and keep the original `text`
clean for citation — don't paste synthetic questions into the stored text. Measure it
against the plain baseline before adopting.

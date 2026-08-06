"""
paths.py — anchored project paths + config loaders.

Everything anchors to the repo root computed from THIS file's real location,
so the pipeline behaves identically no matter what directory you run it from.
This is the fix for the path-doubling bug from the first attempt: never build
paths relative to os.getcwd(), always relative to BASE_DIR.
"""
from __future__ import annotations

import os
from functools import lru_cache

import yaml

# ---- anchor everything to the repo root --------------------------------------
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SRC_DIR)

CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")          # cached fetched pages (html/pdf/txt)
PROCESSED_DIR = os.path.join(DATA_DIR, "processed")  # cleaned, normalized text
CHUNKS_DIR = os.path.join(DATA_DIR, "chunks")    # final JSONL, one file per service
DOCS_DIR = os.path.join(BASE_DIR, "docs")

SOURCES_YAML = os.path.join(CONFIG_DIR, "sources.yaml")
TAXONOMY_YAML = os.path.join(CONFIG_DIR, "taxonomy.yaml")


def ensure_dirs() -> None:
    for d in (RAW_DIR, PROCESSED_DIR, CHUNKS_DIR, DOCS_DIR):
        os.makedirs(d, exist_ok=True)


@lru_cache(maxsize=1)
def load_sources() -> dict:
    with open(SOURCES_YAML, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def load_taxonomy() -> dict:
    with open(TAXONOMY_YAML, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def raw_path(service: str, slug: str, ext: str) -> str:
    d = os.path.join(RAW_DIR, service)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{slug}.{ext.lstrip('.')}")


def chunks_path(service: str) -> str:
    os.makedirs(CHUNKS_DIR, exist_ok=True)
    return os.path.join(CHUNKS_DIR, f"{service}.jsonl")

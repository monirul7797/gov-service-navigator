"""
app.py — Streamlit web UI for the Government Service Navigator chatbot.

Run it (set your LLM env vars first — run setenv_deepseek.bat in the SAME terminal, then):
    streamlit run app.py

A browser tab opens with a chat interface. All the real work is reused from the existing
modules: answer.handle_turn() runs understand → retrieve → grounded answer (registry router as fallback)
→ cite, exactly like the command-line version. The embedding model and vector database
load once and are cached across interactions.
"""
import streamlit as st
import chromadb
from sentence_transformers import SentenceTransformer

from llm_backends import get_backend
from answer import load_collections, handle_turn, MODEL_NAME, DB_DIR
from disambiguate import load_registry

st.set_page_config(page_title="Government Service Navigator", page_icon="🏛️", layout="centered")


@st.cache_resource(show_spinner="Loading the model and knowledge base… (first run takes a moment)")
def load_resources():
    """Loaded once and cached — Streamlit reruns the script on every interaction, so without
    this the ~400MB embedding model would reload on every message."""
    backend = get_backend()
    model = SentenceTransformer(MODEL_NAME)
    colls = load_collections(chromadb.PersistentClient(path=DB_DIR))
    reg = load_registry()
    return backend, model, colls, reg


def render_source(s: str) -> str:
    return f"- [{s}]({s})" if s.startswith("http") else f"- {s}"


# ---- startup (with friendly errors) ----
try:
    backend, model, colls, reg = load_resources()
    load_error = None
except Exception as e:               # noqa: BLE001 - surface any startup problem to the user
    backend = model = colls = reg = None
    load_error = e

st.title("🏛️ Government Service Navigator")
st.caption(
    "Ask about **NID**, **e-Passport**, **e-Return (income tax)**, or **birth/death "
    "registration** — in English, Bangla, or Banglish. Answers are grounded in official sources."
)

if load_error is not None:
    st.error(
        "Couldn't start up. Two common causes:\n\n"
        "1. **LLM key not set** — run `setenv_deepseek.bat` in the same terminal *before* "
        "`streamlit run app.py`, so the API key is available to the app.\n"
        "2. **Vector database missing** — run the `embed_*.py` scripts to build `data/vectordb/`.\n\n"
        f"Technical detail: `{load_error}`"
    )
    st.stop()

if not colls:
    st.error("No embedded collections found in the vector database. Run the `embed_*.py` scripts first.")
    st.stop()

# ---- sidebar ----
with st.sidebar:
    st.header("About")
    st.write("A bilingual RAG chatbot over four Bangladeshi government services.")
    st.write("**Loaded services:**")
    for name, coll in colls.items():
        st.write(f"- {name} — {coll.count()} records")
    st.divider()
    st.write("**Try asking:**")
    st.markdown(
        "- passport fee koto?\n"
        "- reissue fee koto?\n"
        "- জন্ম নিবন্ধন করতে কি লাগে?\n"
        "- income tax return joma dewar last date koto?"
    )
    st.divider()
    if st.button("🗑️ Clear conversation"):
        st.session_state.messages = []
        st.session_state.conv_state = {"pending": None, "last": None}
        st.rerun()

# ---- conversation state ----
if "messages" not in st.session_state:
    st.session_state.messages = []
if "conv_state" not in st.session_state:
    st.session_state.conv_state = {"pending": None, "last": None}

# ---- replay history ----
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sources"):
            with st.expander("Sources"):
                st.markdown("\n".join(render_source(s) for s in msg["sources"]))
        if msg.get("caption"):
            st.caption(msg["caption"])

# ---- handle a new message ----
if prompt := st.chat_input("Ask a question…"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            resp, st.session_state.conv_state = handle_turn(
                prompt, st.session_state.conv_state, backend, model, colls, reg
            )

        if resp["kind"] == "clarify":
            st.markdown(resp["question"])
            st.session_state.messages.append({"role": "assistant", "content": resp["question"]})
        else:
            st.markdown(resp["answer"])
            sources = resp.get("sources") or []
            if sources:
                with st.expander("Sources"):
                    st.markdown("\n".join(render_source(s) for s in sources))
            caption = None
            if resp.get("scoped"):
                caption = f"answered from: {resp['scoped']}"
            elif resp.get("services"):
                caption = f"answered from: {', '.join(resp['services'])}"
            if caption:
                st.caption(caption)
            st.session_state.messages.append(
                {"role": "assistant", "content": resp["answer"], "sources": sources, "caption": caption}
            )

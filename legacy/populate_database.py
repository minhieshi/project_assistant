import argparse
import os
import shutil
from typing import List

import fitz  # PyMuPDF

from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_chroma import Chroma

from get_embedding_function import get_embedding_function

CHROMA_PATH = "chroma"
DATA_PATH = "data"


# --- PDF extraction (page-based, header/footer trimming) ---

def load_pdf_pages(pdf_path: str, top: float = 50, bottom: float = 50) -> List[Document]:
    """
    Extract one LangChain Document per page with metadata:
      source, page
    Trims likely header/footer using clip margins (tune top/bottom per corpus).
    """
    pdf = fitz.open(pdf_path)
    docs: List[Document] = []

    for i, page in enumerate(pdf, start=1):
        r = page.rect
        clip = fitz.Rect(r.x0, r.y0 + top, r.x1, r.y1 - bottom)
        text = page.get_text("text", sort=True, clip=clip).strip()
        if not text:
            continue

        docs.append(
            Document(
                page_content=text,
                metadata={"source": pdf_path, "page": i},
            )
        )
    return docs


def load_all_pdfs_as_pages(data_path: str) -> List[Document]:
    pdf_paths = []
    for root, _, files in os.walk(data_path):
        for f in files:
            if f.lower().endswith(".pdf"):
                pdf_paths.append(os.path.join(root, f))

    all_pages: List[Document] = []
    for p in sorted(pdf_paths):
        all_pages.extend(load_pdf_pages(p))
    return all_pages


# --- Page windowing (improves retrieval of headings + details across pages) ---

def group_pages(pages: List[Document], window: int = 3, stride: int = 2) -> List[Document]:
    """
    Combine consecutive pages into overlapping windows.
    Metadata preserved as:
      source, page_start, page_end
    Content includes page markers for optional citation parsing.
    """
    if not pages:
        return []

    # assume all pages belong to the same source; group per source outside if needed
    pages = sorted(pages, key=lambda d: d.metadata.get("page", 0))

    out: List[Document] = []
    for start in range(0, len(pages), stride):
        group = pages[start:start + window]
        if not group:
            break

        text = "\n\n".join(
            f"<!-- page:{d.metadata['page']} -->\n{d.page_content}"
            for d in group
            if d.page_content.strip()
        ).strip()

        if text:
            out.append(
                Document(
                    page_content=text,
                    metadata={
                        "source": group[0].metadata["source"],
                        "page_start": group[0].metadata["page"],
                        "page_end": group[-1].metadata["page"],
                    },
                )
            )

        if start + window >= len(pages):
            break

    return out


def group_all_pages_by_source(all_pages: List[Document], window: int = 3, stride: int = 2) -> List[Document]:
    by_source = {}
    for d in all_pages:
        by_source.setdefault(d.metadata["source"], []).append(d)

    out: List[Document] = []
    for src, pages in by_source.items():
        out.extend(group_pages(pages, window=window, stride=stride))
    return out


# --- Existing pipeline ---

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Reset the database.")
    args = parser.parse_args()

    if args.reset:
        print("✨ Clearing Database")
        clear_database()

    documents = load_documents()
    chunks = split_documents(documents)
    add_to_chroma(chunks)


def load_documents() -> List[Document]:
    # PDFs -> page docs -> grouped windows
    pdf_pages = load_all_pdfs_as_pages(DATA_PATH)
    pdf_docs = group_all_pages_by_source(pdf_pages, window=3, stride=2)

    # JSON -> keep as-is (TextLoader makes one doc per file typically)
    json_loader = DirectoryLoader(DATA_PATH, glob="**/*.json", loader_cls=TextLoader)
    json_docs = json_loader.load()

    return pdf_docs + json_docs


def split_documents(documents: List[Document]) -> List[Document]:
    """
    Split grouped pages (and json files) into chunks with paragraph-aware separators.
    """
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1200,
        chunk_overlap=200,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""],  # better for manuals
        is_separator_regex=False,
    )
    return text_splitter.split_documents(documents)


def add_to_chroma(chunks: List[Document]):
    db = Chroma(persist_directory=CHROMA_PATH, embedding_function=get_embedding_function())

    chunks_with_ids = calculate_chunk_ids(chunks)

    existing_items = db.get(include=[])
    existing_ids = set(existing_items["ids"])
    print(f"Number of existing documents in DB: {len(existing_ids)}")

    new_chunks = [c for c in chunks_with_ids if c.metadata["id"] not in existing_ids]

    if new_chunks:
        print(f"👉 Adding new documents: {len(new_chunks)}")
        batch_size = 50
        for i in range(0, len(new_chunks), batch_size):
            batch = new_chunks[i:i + batch_size]
            batch_ids = [chunk.metadata["id"] for chunk in batch]
            try:
                db.add_documents(batch, ids=batch_ids)
                print(f"✅ Successfully added batch {i//batch_size + 1}")
            except Exception as e:
                print(f"❌ Error adding batch {i//batch_size + 1}: {e}")
    else:
        print("✅ No new documents to add")


def calculate_chunk_ids(chunks: List[Document]) -> List[Document]:
    """
    Create stable IDs based on source + page or page range (for grouped windows),
    plus an index within that page/page-range.
    """
    page_counters = {}
    for chunk in chunks:
        source = chunk.metadata.get("source", "unknown")

        page = chunk.metadata.get("page")
        page_start = chunk.metadata.get("page_start")
        page_end = chunk.metadata.get("page_end")

        if page is not None:
            current_page_id = f"{source}:p{page}"
        elif page_start is not None and page_end is not None:
            current_page_id = f"{source}:p{page_start}-p{page_end}"
        else:
            current_page_id = source

        current_chunk_index = page_counters.get(current_page_id, 0)
        chunk_id = f"{current_page_id}:{current_chunk_index}"

        chunk.metadata["id"] = chunk_id
        page_counters[current_page_id] = current_chunk_index + 1

    return chunks


def clear_database():
    if os.path.exists(CHROMA_PATH):
        shutil.rmtree(CHROMA_PATH)


if __name__ == "__main__":
    main()
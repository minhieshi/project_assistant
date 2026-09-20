import argparse
import re
from typing import List, Tuple

from langchain_chroma import Chroma
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import OllamaLLM

from sentence_transformers import CrossEncoder

from get_embedding_function import get_embedding_function

CHROMA_PATH = "chroma"

PROMPT_TEMPLATE = """
Answer the question based only on the following context:
{context}
---
Answer the question based on the above context: {question}
If the answer isn't in the retrieved text, say you can't find it.
"""

ANSIBLE_TASK_PROMPT_TEMPLATE = """
You are an expert Ansible automation engineer.
Convert the RAG output below into a valid YAML task list that queries an API.

RAG answer:
{response_text}

RAG sources:
{sources_text}

Requirements:
1. Output YAML only (no markdown fences, no explanation).
2. Output a task list that starts with "- name:".
3. Use ansible.builtin.uri as the primary task for the API query.
4. If API details are missing, use placeholders like {{{{ api_base_url }}}}, {{{{ api_path }}}}, and {{{{ api_token }}}}.
5. Register the uri result and add a second debug task to print useful query output.
6. Add a source comment at the top that includes the RAG sources.
7. Follow additional user instructions when provided.

Additional instructions:
{extra_instructions}
"""

# Stage 1 (vector search) - increase for recall
VECTOR_TOP_K = 25

# Stage 2 (rerank) - keep fewer, higher precision
RERANK_TOP_N = 6

RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
# If too slow on CPU Mac, try:
# RERANKER_MODEL = "BAAI/bge-reranker-base"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query_text", type=str, help="The query text.")
    parser.add_argument(
        "-w",
        "--write-task",
        action="store_true",
        help="Write an Ansible task file using the response from query_rag().",
    )
    parser.add_argument(
        "-o",
        "--task-file",
        type=str,
        default="generated_api_query_task.yml",
        help="Path to write the generated Ansible task YAML.",
    )
    parser.add_argument(
        "-i",
        "--task-instructions",
        type=str,
        default="",
        help="Extra instructions to guide Ansible task generation (used with -w).",
    )
    args = parser.parse_args()
    formatted_response = query_rag(args.query_text)
    if args.write_task:
        write_ansible_task(
            formatted_response,
            output_path=args.task_file,
            extra_instructions=args.task_instructions,
        )


def rerank(
    query: str,
    docs_with_scores: List[Tuple[object, float]],
    reranker: CrossEncoder,
    top_n: int = RERANK_TOP_N,
):
    """
    docs_with_scores: [(Document, vector_score), ...]
    returns: [(Document, rerank_score), ...] sorted desc
    """
    docs = [d for d, _s in docs_with_scores]
    pairs = [(query, d.page_content) for d in docs]

    rerank_scores = reranker.predict(pairs)  # higher is better
    ranked = sorted(zip(docs, rerank_scores), key=lambda x: x[1], reverse=True)
    return ranked[:top_n]


def _split_formatted_response(formatted_response: str) -> Tuple[str, str]:
    response_text = formatted_response.strip()
    sources_text = "[]"

    if "\nSources:" in response_text:
        response_text, sources_text = response_text.split("\nSources:", maxsplit=1)

    if response_text.startswith("Response:"):
        response_text = response_text.removeprefix("Response:").strip()

    sources_text = sources_text.strip() or "[]"
    return response_text, sources_text


def _strip_markdown_fences(text: str) -> str:
    match = re.search(r"```(?:ya?ml)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def _default_ansible_task(
    response_text: str,
    sources_text: str,
    extra_instructions: str = "",
) -> str:
    guidance = " ".join(response_text.split())
    requested = " ".join(extra_instructions.split()) or "None"
    return f"""# Sources: {sources_text}
- name: Query API endpoint from RAG guidance
  ansible.builtin.uri:
    url: "{{{{ api_base_url }}}}/{{{{ api_path }}}}"
    method: "{{{{ api_method | default('GET') }}}}"
    headers:
      Authorization: "Bearer {{{{ api_token }}}}"
      Accept: "application/json"
    return_content: true
    status_code: [200]
  register: api_query_result

- name: Show API query result summary
  ansible.builtin.debug:
    msg:
      - "status={{{{ api_query_result.status | default('unknown') }}}}"
      - "content_length={{{{ api_query_result.content | default('') | length }}}}"
# RAG guidance: {guidance}
# Requested task instructions: {requested}
"""


def _build_ansible_task_prompt(
    response_text: str,
    sources_text: str,
    extra_instructions: str = "",
) -> str:
    instructions = extra_instructions.strip() or "None"
    return ANSIBLE_TASK_PROMPT_TEMPLATE.format(
        response_text=response_text,
        sources_text=sources_text,
        extra_instructions=instructions,
    )


def write_ansible_task(
    formatted_response: str,
    output_path: str = "generated_api_query_task.yml",
    model_name: str = "qwen3-coder:30b",
    extra_instructions: str = "",
) -> str:
    response_text, sources_text = _split_formatted_response(formatted_response)

    ansible_task_text = ""
    try:
        prompt = _build_ansible_task_prompt(
            response_text,
            sources_text,
            extra_instructions=extra_instructions,
        )
        model = OllamaLLM(model=model_name)
        ansible_task_text = _strip_markdown_fences(model.invoke(prompt))
    except Exception as exc:
        print(f"Could not auto-generate Ansible task with model '{model_name}': {exc}")

    if not ansible_task_text or "- name:" not in ansible_task_text:
        ansible_task_text = _default_ansible_task(
            response_text,
            sources_text,
            extra_instructions=extra_instructions,
        )

    with open(output_path, "w", encoding="utf-8") as file:
        file.write(ansible_task_text.rstrip() + "\n")

    print(f"Ansible task written to: {output_path}")
    return output_path


def query_rag(query_text: str):
    embedding_function = get_embedding_function()
    db = Chroma(persist_directory=CHROMA_PATH, embedding_function=embedding_function)

    # --- Stage 1: high-recall vector search ---
    candidates = db.similarity_search_with_score(query_text, k=VECTOR_TOP_K)

    # --- Stage 2: rerank for precision ---
    reranker = CrossEncoder(RERANKER_MODEL)
    reranked = rerank(query_text, candidates, reranker, top_n=RERANK_TOP_N)

    # Build context from reranked docs
    context_text = "\n\n---\n\n".join([doc.page_content for doc, _rscore in reranked])

    prompt_template = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)
    prompt = prompt_template.format(context=context_text, question=query_text)

    model = OllamaLLM(model="qwen3-coder:30b")
    response_text = model.invoke(prompt)

    sources = [doc.metadata.get("id") for doc, _rscore in reranked]
    formatted_response = f"Response: {response_text}\nSources: {sources}"
    print(formatted_response)
    return formatted_response


if __name__ == "__main__":
    main()

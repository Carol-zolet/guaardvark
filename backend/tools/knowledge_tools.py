"""Navigation tools for the knowledge base.

search_knowledge_base answers "find me passages about X". That is a lookup
primitive, and lookup alone is not navigation: there was no way to ask what the
corpus contains, what a document is made of, or what sits next to a passage. The
code side has had that surface for a while (get_repository_map -> list_code_files
-> read_code -> read_ast_node); documents had one search box.

These tools read the index directly from Postgres rather than the Document table,
for two reasons: the MCP server runs as a separate process with no Flask
application context, and the index is the honest answer to "what can actually be
retrieved" -- a registry row for a file that failed to chunk is not navigable.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)

_MAX_TEXT = 1200

# RAPTOR writes corpus-level summaries into the same table as the documents they
# were built from (backend/services/raptor_service.py). They are retrievable
# passages, not documents, and their source_filename is a synthetic
# "[corpus summary L<n>#<i>]", so listing them invents documents the user never
# added. Either marker identifies one.
_NOT_A_SUMMARY = (
    "metadata_->>'content_type' IS DISTINCT FROM 'raptor_summary' "
    "AND metadata_->>'parsed_by' IS DISTINCT FROM 'raptor'"
)


def _table() -> Tuple[Optional[str], Optional[str]]:
    """Return (qualified_table, error)."""
    try:
        from backend.services.indexing_service import (
            resolve_existing_vector_table, _vector_backend,
        )
        if _vector_backend() != "pgvector":
            return None, "These tools require the pgvector backend."
        # Discovery rather than derivation: deriving the name needs the embedding
        # model's dimension, and the MCP server is a bare subprocess with no Flask
        # context and no initialised index, so that probe returns nothing. These
        # tools are read-only and the dimension is already in the table name.
        t = resolve_existing_vector_table(None)
        if not t:
            return None, ("No knowledge index found. Index some documents first, "
                          "or check that the pgvector table exists.")
        return f"data_{t}", None
    except Exception as e:
        return None, f"Index unavailable: {e}"


def _query(sql: str, params: tuple) -> Tuple[Optional[List[tuple]], Optional[str]]:
    try:
        from backend.services.indexing_service import _pg_connect
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall(), None
        finally:
            conn.close()
    except Exception as e:
        logger.error("knowledge_tools query failed: %s", e)
        return None, str(e)[:200]


# The outline's label for passages that carry neither a heading nor a page, and
# the heading_path value read_document_section accepts for exactly those rows.
NO_SECTION = "(no section)"


def _section_label(heading: Optional[str], page: Optional[str]) -> str:
    """How the outline names a passage's place; read_document_section accepts it back."""
    return heading or (f"page {page}" if page else NO_SECTION)


def _meta(raw) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


class ListDocumentsTool(BaseTool):
    """Enumerate the documents present in the knowledge base."""

    name = "list_documents"
    read_only = True
    description = (
        "List the documents in the local knowledge base, most passages first: one line per file with "
        "its passage count, its section count when above one, and the parser that read it, under a "
        "header giving how many documents match. Use it to see what is indexed, or to get the exact "
        "filename get_document_outline and read_document_section take. Covers every project. "
        "name_contains keeps filenames containing that text; limit and offset page through the list, "
        "and when more remain the reply ends with the offset for the next page. To find content by topic use "
        "search_knowledge_base; corpus summaries are in summarize_corpus, not here."
    )
    parameters = {
        "name_contains": ToolParameter(
            name="name_contains", type="string", required=False,
            description="Only list documents whose filename contains this text, case-insensitive, e.g. 'manual' or '.pdf'.",
        ),
        "limit": ToolParameter(
            name="limit", type="int", required=False, default=40, minimum=1, maximum=200,
            description="Documents per page, 1-200 (default 40).",
        ),
        "offset": ToolParameter(
            name="offset", type="int", required=False, default=0, minimum=0,
            description="How many documents to skip, for paging (default 0).",
        ),
    }

    def execute(self, name_contains: str = None, limit: int = None, offset: int = None) -> ToolResult:
        table, err = _table()
        if err:
            return ToolResult(success=False, error=err)
        limit = max(1, min(int(limit or 40), 200))
        offset = max(0, int(offset or 0))

        conditions, filter_params = [_NOT_A_SUMMARY], []
        if name_contains:
            conditions.append("metadata_->>'source_filename' ILIKE %s")
            filter_params.append(f"%{name_contains}%")
        where = "WHERE " + " AND ".join(conditions)
        params = filter_params + [limit, offset]

        rows, qerr = _query(
            f"""SELECT metadata_->>'source_filename' AS src,
                       count(*) AS chunks,
                       count(DISTINCT metadata_->>'heading_path') AS sections,
                       max(metadata_->>'parsed_by') AS parsed_by
                FROM "{table}" {where}
                GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT %s OFFSET %s""",
            tuple(params),
        )
        if qerr:
            return ToolResult(success=False, error=f"Query failed: {qerr}")

        # The total counts what the filter matches, so paging hints add up.
        total_rows, count_err = _query(
            f"SELECT count(DISTINCT metadata_->>'source_filename') FROM \"{table}\" {where}",
            tuple(filter_params),
        )
        # A failed count used to fall back to the page length, so "how big is the
        # knowledge base" was answered with "as many as fit on this page". The
        # page is still useful; the total is reported as unknown, not invented.
        if count_err:
            total = None
            logger.warning("list_documents: corpus count failed: %s", count_err)
        else:
            total = total_rows[0][0] if total_rows else 0

        if not rows:
            if offset and total:
                return ToolResult(success=True, output=f"No documents at offset {offset}; {total} match in all.")
            if name_contains:
                return ToolResult(success=True, output=f"No indexed document's filename contains '{name_contains}'.")
            return ToolResult(success=True, output="The knowledge base has no documents yet.")

        head = (f"KNOWLEDGE BASE — {total} document(s)" + (" match" if name_contains else " indexed")
                if total is not None
                else f"KNOWLEDGE BASE — document count unavailable ({count_err})")
        lines = [head
                 + (f", filtered by '{name_contains}'" if name_contains else "")
                 + f" · showing {offset + 1}-{offset + len(rows)}"]
        for src, chunks, sections, parsed_by in rows:
            extra = f", {sections} sections" if sections and sections > 1 else ""
            lines.append(f"  {src or '(unknown)'} — {chunks} passages{extra} [{parsed_by or '?'}]")
        if total is not None and offset + len(rows) < total:
            lines.append(f"\n({total - offset - len(rows)} more — call again with offset={offset + len(rows)})")
        elif total is None and len(rows) == limit:
            lines.append(f"\n(there may be more — call again with offset={offset + len(rows)})")

        return ToolResult(success=True, output="\n".join(lines),
                          metadata={"total": total, "returned": len(rows), "offset": offset})


class DocumentOutlineTool(BaseTool):
    """Show the section structure of one indexed document."""

    name = "get_document_outline"
    read_only = True
    description = (
        "Show the structure of one indexed document: its sections (heading paths such as 'Setup > "
        "Install') and pages in the order they appear, each with a passage count, under a header with "
        "the document's total. Use it after list_documents and before read_document_section, which "
        "takes a heading path, a page label or '(no section)' exactly as this outline prints them "
        "('(no section)' covers passages with neither a heading nor a page, as in a plain .txt file). "
        "An unknown filename returns a short notice, not an error. To find a topic across documents "
        "use search_knowledge_base."
    )
    parameters = {
        "source_filename": ToolParameter(
            name="source_filename", type="string", required=True,
            description="Exact filename as list_documents prints it, e.g. 'handbook.pdf' (case-sensitive).",
        ),
    }

    def execute(self, source_filename: str) -> ToolResult:
        table, err = _table()
        if err:
            return ToolResult(success=False, error=err)

        rows, qerr = _query(
            f"""SELECT coalesce(metadata_->>'heading_path', ''),
                       coalesce(metadata_->>'page_label', ''),
                       count(*)
                FROM "{table}"
                WHERE metadata_->>'source_filename' = %s
                GROUP BY 1, 2
                ORDER BY min(id)""",
            (source_filename,),
        )
        if qerr:
            return ToolResult(success=False, error=f"Query failed: {qerr}")
        if not rows:
            return ToolResult(
                success=True,
                output=f"No indexed content for '{source_filename}'. Use list_documents to see available names.",
            )

        lines = [f"OUTLINE — {source_filename} ({sum(r[2] for r in rows)} passages)"]
        for heading, page, count in rows:
            label = _section_label(heading, page)
            loc = f" p.{page}" if page and heading else ""
            lines.append(f"  {label}{loc} — {count} passage(s)")
        return ToolResult(success=True, output="\n".join(lines),
                          metadata={"sections": len(rows)})


class ReadDocumentSectionTool(BaseTool):
    """Read the indexed passages of a specific section or page."""

    name = "read_document_section"
    read_only = True
    description = (
        "Read the stored text of an indexed document, without searching, in document order. Pass "
        "source_filename plus heading_path or page_label from get_document_outline to read one "
        "section or page; giving both narrows to that section on that page. heading_path "
        "'(no section)' reads the passages the outline lists under that label (no heading and no "
        "page, e.g. a plain .txt file); any other heading_path matches every section whose path "
        "contains the text (case-insensitive), so a short value can return several sections. With "
        "neither, it reads the whole document from the start. Returns up to 25 passages per call, "
        "each cut at 1,200 characters and labelled with its section or page as the outline names "
        "it; the header gives the total and the offset for the next call. To locate a topic first "
        "use search_knowledge_base; for a file that is not indexed, process_file."
    )
    parameters = {
        "source_filename": ToolParameter(
            name="source_filename", type="string", required=True,
            description="Exact filename as list_documents prints it (case-sensitive).",
        ),
        "heading_path": ToolParameter(
            name="heading_path", type="string", required=False,
            description="Section path as get_document_outline shows it, e.g. 'Installation > Requirements'; any section whose path contains this text matches. '(no section)' reads the passages that have neither a heading nor a page. Leave out together with page_label to read the whole document.",
        ),
        "page_label": ToolParameter(
            name="page_label", type="string", required=False,
            description="Page label as get_document_outline shows it, e.g. '12' for the line 'page 12' (exact match).",
        ),
        "offset": ToolParameter(
            name="offset", type="int", required=False, default=0, minimum=0,
            description="Passages to skip, to read past the first 25 (default 0).",
        ),
    }

    def execute(self, source_filename: str, heading_path: str = None, page_label: str = None,
                offset: int = None) -> ToolResult:
        table, err = _table()
        if err:
            return ToolResult(success=False, error=err)
        heading_path = (heading_path or "").strip()
        page_label = str(page_label).strip() if page_label is not None else ""
        no_section = heading_path.lower() == NO_SECTION

        clauses = ["metadata_->>'source_filename' = %s"]
        params: List[Any] = [source_filename]
        if no_section:
            # The outline's '(no section)' group: ILIKE never matches a NULL
            # heading, so these rows are selected by their missing metadata.
            clauses.append("coalesce(metadata_->>'heading_path', '') = ''")
            if not page_label:
                clauses.append("coalesce(metadata_->>'page_label', '') = ''")
        elif heading_path:
            clauses.append("metadata_->>'heading_path' ILIKE %s")
            params.append(f"%{heading_path}%")
        if page_label:
            clauses.append("metadata_->>'page_label' = %s")
            params.append(page_label)

        offset = max(0, int(offset or 0))
        where = " AND ".join(clauses)
        rows, qerr = _query(
            f'SELECT text, metadata_ FROM "{table}" WHERE {where} ORDER BY id LIMIT 25 OFFSET %s',
            tuple(params) + (offset,),
        )
        if qerr:
            return ToolResult(success=False, error=f"Query failed: {qerr}")
        total_rows, _ = _query(f'SELECT count(*) FROM "{table}" WHERE {where}', tuple(params))
        total = total_rows[0][0] if total_rows else None
        if not rows:
            if offset and total:
                return ToolResult(success=True, output=f"No passages at offset {offset}; {total} match in all.")
            if not heading_path and not page_label:
                return ToolResult(
                    success=True,
                    output=f"No indexed content for '{source_filename}'. Use list_documents to see available names.",
                )
            return ToolResult(success=True, output="No passages match that section or page.")

        head = f"{source_filename}"
        if no_section:
            head += f" · {NO_SECTION}"
        elif heading_path:
            head += f" · section ~ {heading_path}"
        if page_label:
            head += f" · page {page_label}"
        if not heading_path and not page_label:
            head += " · whole document"
        span = f"passages {offset + 1}-{offset + len(rows)}" + (f" of {total}" if total is not None else "")
        lines = [f"{head} — {span}"]
        if total is not None and offset + len(rows) < total:
            lines.append(f"(more: call again with offset={offset + len(rows)})")
        for i, (text, meta) in enumerate(rows, 1):
            m = _meta(meta)
            # Chunks are stored with a contextual prefix for embedding; show the raw text.
            body = (m.get("original_text") or text or "").strip()
            if len(body) > _MAX_TEXT:
                body = body[:_MAX_TEXT].rstrip() + "…"
            label = _section_label(m.get("heading_path"), m.get("page_label"))
            lines.append(f"\n[{i}] {label}\n{body}")
        return ToolResult(success=True, output="\n".join(lines), metadata={"passages": len(rows)})


class CorpusSummaryTool(BaseTool):
    """Retrieve corpus-level summaries produced by the RAPTOR pass."""

    name = "summarize_corpus"
    read_only = True
    description = (
        "Return precomputed summaries of the knowledge base, written by the local LLM when a summary "
        "build last ran (Settings > Build summaries). Until one has run the tool says none exist, and "
        "documents added since are not reflected. One summary per cluster of related passages, "
        "largest first, each with its size, the files it draws on (listed up to 160 characters) and up to 1,200 characters of "
        "text. Use it for broad questions (main themes, what a collection is about) before "
        "searching; for facts and quotable text use search_knowledge_base."
    )
    parameters = {
        "level": ToolParameter(
            name="level", type="int", required=False, default=1, minimum=1,
            description="1 (default): summaries of groups of related passages. 2: broader summaries of the level-1 summaries, whose sources show as summary ids.",
        ),
        "limit": ToolParameter(
            name="limit", type="int", required=False, default=8, minimum=1, maximum=30,
            description="How many summaries to return, largest clusters first, 1-30 (default 8).",
        ),
    }

    def execute(self, level: int = None, limit: int = None) -> ToolResult:
        table, err = _table()
        if err:
            return ToolResult(success=False, error=err)
        level = int(level or 1)
        limit = max(1, min(int(limit or 8), 30))

        rows, qerr = _query(
            f"""SELECT text, metadata_ FROM "{table}"
                WHERE metadata_->>'content_type' = 'raptor_summary'
                  AND metadata_->>'raptor_level' = %s
                ORDER BY (metadata_->>'cluster_size')::int DESC LIMIT %s""",
            (str(level), limit),
        )
        if qerr:
            return ToolResult(success=False, error=f"Query failed: {qerr}")
        if not rows:
            return ToolResult(
                success=True,
                output=(f"No level-{level} corpus summaries exist yet. They are produced by the "
                        "RAPTOR build, which is an explicit operation, not part of indexing."),
            )

        lines = [f"CORPUS SUMMARIES — level {level}, {len(rows)} cluster(s)"]
        for i, (text, meta) in enumerate(rows, 1):
            m = _meta(meta)
            covers = m.get("covers_sources") or ""
            body = (text or "").strip()
            if len(body) > _MAX_TEXT:
                body = body[:_MAX_TEXT].rstrip() + "…"
            lines.append(f"\n[{i}] {m.get('cluster_size', '?')} passages"
                         + (f" from: {covers[:160]}" if covers else "") + f"\n{body}")
        return ToolResult(success=True, output="\n".join(lines), metadata={"summaries": len(rows)})


KNOWLEDGE_NAV_TOOLS = [
    ListDocumentsTool,
    DocumentOutlineTool,
    ReadDocumentSectionTool,
    CorpusSummaryTool,
]

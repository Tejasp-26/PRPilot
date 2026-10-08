"""
tools/ast_utils.py — tree-sitter AST utilities for function extraction and performance analysis.
"""

from __future__ import annotations

import logging
from typing import Any

import tree_sitter
from tree_sitter import Language, Parser

logger = logging.getLogger(__name__)

# Cache language objects so we don't load them repeatedly
_LANGUAGES: dict[str, Language] = {}


def _get_language(grammar_name: str) -> Language | None:
    """Load and return the tree-sitter Language for the given grammar name."""
    if grammar_name in _LANGUAGES:
        return _LANGUAGES[grammar_name]

    try:
        if grammar_name == "python":
            import tree_sitter_python
            lang = Language(tree_sitter_python.language())
        elif grammar_name == "javascript":
            import tree_sitter_javascript
            lang = Language(tree_sitter_javascript.language())
        elif grammar_name == "typescript":
            # tree-sitter-typescript ships two grammars and no plain language().
            import tree_sitter_typescript
            lang = Language(tree_sitter_typescript.language_typescript())
        elif grammar_name == "tsx":
            import tree_sitter_typescript
            lang = Language(tree_sitter_typescript.language_tsx())
        elif grammar_name == "go":
            import tree_sitter_go
            lang = Language(tree_sitter_go.language())
        else:
            logger.warning("[ast_utils] Unsupported grammar name: %s", grammar_name)
            return None

        _LANGUAGES[grammar_name] = lang
        return lang
    except Exception as e:
        logger.error("[ast_utils] Failed to load tree-sitter grammar for %s: %s", grammar_name, e)
        return None


def parse_file(content: str, language: str, file_path: str | None = None) -> tree_sitter.Tree | None:
    """
    Parse file contents into a tree-sitter Tree.

    Args:
        content:   The full content of the file.
        language:  The canonical language identifier (e.g. 'python', 'javascript', 'typescript', 'go').
        file_path: Optional path; used to pick the TSX grammar for .tsx files
                   (the plain TypeScript grammar cannot parse JSX syntax).

    Returns:
        tree_sitter.Tree or None on parsing/loading failure.
    """
    grammar_name = language
    if language == "typescript" and file_path and file_path.lower().endswith(".tsx"):
        grammar_name = "tsx"
    lang = _get_language(grammar_name)
    if not lang:
        return None

    try:
        parser = Parser(lang)
        # tree-sitter parse expects bytes
        tree = parser.parse(content.encode("utf-8"))
        return tree
    except Exception as e:
        logger.error("[ast_utils] Failed to parse content for %s: %s", language, e)
        return None


def get_node_text(node: Any, content_bytes: bytes) -> str:
    """Retrieve text of a tree-sitter node using raw byte offsets."""
    return content_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def extract_functions(tree: tree_sitter.Tree, content: str) -> list[str]:
    """Extract all function/method definition names from the AST."""
    return [d["name"] for d in extract_function_defs(tree, content)]


def extract_function_defs(tree: tree_sitter.Tree, content: str) -> list[dict]:
    """
    Extract function/method definitions with their line spans.

    Returns:
        List of dicts: {"name": str, "start_line": int, "end_line": int}
        (1-indexed, inclusive). Used to tell which functions a diff touches.
    """
    if not tree or not tree.root_node:
        return []

    defs: list[dict] = []
    content_bytes = content.encode("utf-8")

    def add(name_node: Any, def_node: Any) -> None:
        defs.append({
            "name": get_node_text(name_node, content_bytes),
            "start_line": def_node.start_point[0] + 1,
            "end_line": def_node.end_point[0] + 1,
        })

    function_types = {
        "function_definition",
        "function_declaration",
        "method_definition",
        "method_declaration",
        "generator_function",
        "generator_function_declaration",
    }

    def traverse(node: Any) -> None:
        if node.type in function_types:
            name_node = node.child_by_field_name("name")
            if name_node:
                add(name_node, node)
        elif node.type == "variable_declarator":
            # JS/TS: const handler = () => {...} / const f = function () {...}
            value = node.child_by_field_name("value")
            name_node = node.child_by_field_name("name")
            if (
                value is not None
                and name_node is not None
                and value.type in ("arrow_function", "function_expression", "function")
            ):
                add(name_node, node)
        for child in node.children:
            traverse(child)

    traverse(tree.root_node)
    return defs


# Loop-like constructs across the supported grammars. Python comprehensions
# count as loops for the "I/O inside a loop" heuristic.
_LOOP_TYPES = {
    "for_statement",
    "while_statement",
    "do_statement",
    "for_in_statement",
    "for_of_statement",
    "for_each_statement",
    "list_comprehension",
    "set_comprehension",
    "dictionary_comprehension",
    "generator_expression",
}

# Call nodes: 'call_expression' in JS/TS/Go, 'call' in Python.
_CALL_TYPES = {"call_expression", "call"}


def get_loop_depth(tree: tree_sitter.Tree) -> int:
    """Determine the maximum loop nesting depth in the AST."""
    if not tree or not tree.root_node:
        return 0

    max_depth = 0
    loop_types = _LOOP_TYPES

    def traverse(node: Any, current_depth: int) -> None:
        nonlocal max_depth
        is_loop = node.type in loop_types
        if is_loop:
            current_depth += 1
            if current_depth > max_depth:
                max_depth = current_depth

        for child in node.children:
            traverse(child, current_depth)

    traverse(tree.root_node, 0)
    return max_depth


def find_calls_in_loops(
    tree: tree_sitter.Tree,
    content: str,
    io_keywords: list[str] | None = None,
) -> list[dict]:
    """
    Find function/method calls inside loops.
    Optionally flags them if the function name matches an I/O keyword.

    Returns:
        List of dicts:
        {
          "line": 1-indexed line number (int),
          "name": function name (str),
          "is_io": bool
        }
    """
    if not tree or not tree.root_node:
        return []

    results = []
    content_bytes = content.encode("utf-8")
    io_set = {k.lower() for k in (io_keywords or [])}

    def traverse(node: Any, inside_loop: bool) -> None:
        is_loop = node.type in _LOOP_TYPES
        current_inside = inside_loop or is_loop

        if current_inside and node.type in _CALL_TYPES:
            # Extract the function name node.
            # In JS/TS/Go/Python, function identifier is usually the 'function' child,
            # or it's a member expression/attribute.
            func_node = node.child_by_field_name("function")
            if not func_node and node.children:
                # Fallback: check first child
                func_node = node.children[0]

            if func_node:
                func_name = get_node_text(func_node, content_bytes)
                # Keep only the last part if it is a method call (e.g. conn.execute -> execute)
                simple_name = func_name.split(".")[-1].split("->")[-1]

                # Check if it looks like I/O: exact keyword or keyword-prefixed name
                # (readFile, fetchAll). Plain substring matching flagged names like
                # 'target' (contains 'get') or 'thread' (contains 'read').
                lowered = simple_name.lower()
                is_io = lowered in io_set or any(lowered.startswith(kw) for kw in io_set)

                # Get 1-indexed line number (tree-sitter start_point is 0-indexed row)
                line_no = node.start_point[0] + 1

                results.append({
                    "line": line_no,
                    "name": func_name,
                    "is_io": is_io,
                })

        for child in node.children:
            traverse(child, current_inside)

    traverse(tree.root_node, False)
    return results

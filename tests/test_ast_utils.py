"""
tests/test_ast_utils.py — Unit tests for tools/ast_utils.py (real tree-sitter grammars, no mocks).
"""

from __future__ import annotations

from prpilot.tools.ast_utils import (
    extract_function_defs,
    extract_functions,
    find_calls_in_loops,
    get_loop_depth,
    parse_file,
)


def test_typescript_and_tsx_parse() -> None:
    """tree-sitter-typescript has no plain language(); both grammars must load."""
    ts = "export function add(a: number, b: number): number { return a + b; }\n"
    tree = parse_file(ts, "typescript", "math.ts")
    assert tree is not None
    assert extract_functions(tree, ts) == ["add"]

    tsx = "export const Button = (p: Props) => <button>{p.label}</button>;\n"
    tree = parse_file(tsx, "typescript", "Button.tsx")
    assert tree is not None
    assert not tree.root_node.has_error
    assert extract_functions(tree, tsx) == ["Button"]


def test_python_calls_in_loops_detected() -> None:
    """Python uses 'call' nodes (not 'call_expression')."""
    code = "for u in users:\n    requests.get(u.url)\n    print(u)\n"
    tree = parse_file(code, "python")
    calls = find_calls_in_loops(tree, code, ["get", "requests"])
    names = {c["name"]: c["is_io"] for c in calls}
    assert names == {"requests.get": True, "print": False}
    assert all(c["line"] == 2 or c["line"] == 3 for c in calls)


def test_python_comprehension_counts_as_loop() -> None:
    code = "rows = [db.query(i) for i in ids]\n"
    tree = parse_file(code, "python")
    calls = find_calls_in_loops(tree, code, ["query"])
    assert [c["name"] for c in calls if c["is_io"]] == ["db.query"]


def test_io_keyword_matching_not_substring() -> None:
    """'target' must not match the 'get' keyword; 'readFile' must match 'read'."""
    code = "for x in xs:\n    target(x)\n    readFile(x)\n"
    tree = parse_file(code, "python")
    io = {c["name"]: c["is_io"] for c in find_calls_in_loops(tree, code, ["get", "read"])}
    assert io == {"target": False, "readFile": True}


def test_loop_depth_and_go_calls() -> None:
    code = "package m\nfunc f(){ for i:=0;i<3;i++ { for j:=0;j<3;j++ { db.Query(i) } } }\n"
    tree = parse_file(code, "go")
    assert get_loop_depth(tree) == 2
    assert find_calls_in_loops(tree, code, ["Query"])[0]["is_io"] is True


def test_function_defs_have_spans() -> None:
    code = "def a():\n    return 1\n\n\ndef b():\n    x = 2\n    return x\n"
    tree = parse_file(code, "python")
    assert extract_function_defs(tree, code) == [
        {"name": "a", "start_line": 1, "end_line": 2},
        {"name": "b", "start_line": 5, "end_line": 7},
    ]

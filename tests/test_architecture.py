"""Part B requirement: the agent has no direct file-system tools -- everything goes through MCP."""
from __future__ import annotations

import ast
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
FORBIDDEN_MODULES = {"fs_tools", "tool_schemas", "sqlite3", "shutil", "pathlib", "glob", "pypdf", "docx",
                     "filesystem_mcp_server", "candidate_db_mcp_server"}
FORBIDDEN_CALLS = {"open", "listdir", "scandir", "walk", "read_text", "write_text", "remove", "unlink", "rename"}


def _tree(name: str) -> ast.AST:
    return ast.parse((PROJECT / name).read_text(encoding="utf-8"))


def test_agent_imports_no_direct_io_modules():
    imported = set()
    for node in ast.walk(_tree("matching_agent.py")):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & FORBIDDEN_MODULES, imported & FORBIDDEN_MODULES
    assert "mcp_client" in imported


def test_agent_makes_no_direct_file_calls():
    calls = set()
    for node in ast.walk(_tree("matching_agent.py")):
        if isinstance(node, ast.Call):
            f = node.func
            calls.add(f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else "")
    assert not calls & FORBIDDEN_CALLS, calls & FORBIDDEN_CALLS


def test_scoring_is_pure():
    for node in ast.walk(_tree("resume_scoring.py")):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = (node.module if isinstance(node, ast.ImportFrom) else node.names[0].name) or ""
            assert mod.split(".")[0] in {"__future__", "re", "dataclasses", "datetime", "typing"}, mod


def test_filesystem_server_does_not_use_the_sdk():
    """The server is hand-written: protocol handling must not be delegated to the SDK."""
    for node in ast.walk(_tree("filesystem_mcp_server.py")):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = (node.module if isinstance(node, ast.ImportFrom) else node.names[0].name) or ""
            assert not mod.startswith("mcp"), mod

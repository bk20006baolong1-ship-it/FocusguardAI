import os
import hashlib
import ast
import json
import re
from collections import defaultdict

workspace = os.path.abspath(".")
ignored_dirs = {".git", "venv", "__pycache__", ".pytest_cache", ".idea", ".vscode", "node_modules"}

files_by_name = defaultdict(list)
files_by_hash = defaultdict(list)
syntax_errors = []
conflict_markers = []
all_py_files = []

for root, dirs, files in os.walk(workspace):
    dirs[:] = [d for d in dirs if d not in ignored_dirs]
    for f in files:
        full_path = os.path.join(root, f)
        rel_path = os.path.relpath(full_path, workspace).replace("\\", "/")
        files_by_name[f].append(rel_path)
        
        try:
            with open(full_path, "rb") as fp:
                data = fp.read()
                if len(data) > 0:
                    h = hashlib.md5(data).hexdigest()
                    files_by_hash[(h, len(data))].append(rel_path)
                
                # Check for merge conflict markers in text files
                if f.endswith(('.py', '.js', '.html', '.css', '.md', '.json', '.txt', '.bat', '.sh')):
                    try:
                        text = data.decode('utf-8', errors='ignore')
                        if re.search(r'^[<]{7} |^[=]{7}$|^[>]{7} ', text, re.MULTILINE):
                            conflict_markers.append(rel_path)
                    except Exception:
                        pass
        except Exception as e:
            pass
            
        if f.endswith(".py"):
            all_py_files.append((rel_path, full_path))
            try:
                with open(full_path, "r", encoding="utf-8") as fp:
                    code = fp.read()
                ast.parse(code, filename=rel_path)
            except Exception as e:
                syntax_errors.append({"file": rel_path, "error": str(e)})

# Duplicates
content_dups = [v for k, v in files_by_hash.items() if len(v) > 1 and k[1] > 100]
ignored_name_dups = {"__init__.py", "README.md", ".gitkeep", "conftest.py"}
name_dups = {k: v for k, v in files_by_name.items() if len(v) > 1 and k not in ignored_name_dups}

# Route duplicates in Flask
route_defs = defaultdict(list)
route_pattern = re.compile(r'@(?:[a-zA-Z0-9_]+\.)?route\(\s*["\']([^"\']+)["\'](?:\s*,\s*methods=\[([^\]]+)\])?')

for rel_path, full_path in all_py_files:
    try:
        with open(full_path, "r", encoding="utf-8") as fp:
            lines = fp.readlines()
        for idx, line in enumerate(lines, 1):
            m = route_pattern.search(line)
            if m:
                path = m.group(1)
                methods = m.group(2) or "GET"
                route_defs[(path, methods)].append((rel_path, idx))
    except Exception:
        pass

dup_routes = {f"{k[0]} [{k[1]}]": v for k, v in route_defs.items() if len(v) > 1}

print("=== AUDIT SUMMARY ===")
print("Total Python files:", len(all_py_files))
print("Syntax errors:", len(syntax_errors))
for s in syntax_errors:
    print(f"  [SYNTAX ERROR] {s['file']}: {s['error']}")

print("Conflict markers:", len(conflict_markers))
for c in conflict_markers:
    print(f"  [CONFLICT MARKER] {c}")

print("Identical duplicate files (same content):", len(content_dups))
for cd in content_dups:
    print(f"  [IDENTICAL CONTENT] {cd}")

print("Duplicate file names (different directories):", len(name_dups))
for name, paths in name_dups.items():
    print(f"  [NAME COLLISION] {name}: {paths}")

print("Duplicate routes:", len(dup_routes))
for route, locs in dup_routes.items():
    print(f"  [DUP ROUTE] {route}: {locs}")

import os
import sys
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import create_app

print("Instantiating Flask app...")
try:
    flask_app, socketio = create_app(start_background=False)
    print("Flask app instantiated successfully.")
except Exception as e:
    print(f"FAILED to instantiate Flask app: {e}")
    sys.exit(1)

# List all registered routes
routes = {}
for rule in flask_app.url_map.iter_rules():
    endpoint = rule.endpoint
    methods = [m for m in rule.methods if m not in ('HEAD', 'OPTIONS')]
    routes[rule.rule] = {'endpoint': endpoint, 'methods': methods}

print(f"Total registered routes: {len(routes)}")

# Check template files
template_dir = os.path.join(ROOT, "templates")
existing_templates = set(os.listdir(template_dir))

# Scan python files for render_template calls
used_templates = set()
for root, _, files in os.walk(ROOT):
    if any(s in root for s in [".venv", "venv", ".git", "node_modules"]):
        continue
    for f in files:
        if f.endswith(".py"):
            path = os.path.join(root, f)
            with open(path, "r", encoding="utf-8", errors="ignore") as fp:
                for line in fp:
                    m = re.findall(r'render_template\(\s*["\']([^"\']+)["\']', line)
                    for t in m:
                        used_templates.add((t, os.path.relpath(path, ROOT)))

missing_templates = []
for tmpl, src in used_templates:
    if tmpl not in existing_templates:
        missing_templates.append((tmpl, src))

if missing_templates:
    print("MISSING TEMPLATES:")
    for t, s in missing_templates:
        print(f"  {t} (referenced in {s})")
else:
    print("All referenced templates exist.")

# Scan JS files for fetch/api calls
js_dir = os.path.join(ROOT, "static", "js")
js_endpoints = []
for f in os.listdir(js_dir):
    if f.endswith(".js"):
        p = os.path.join(js_dir, f)
        with open(p, "r", encoding="utf-8", errors="ignore") as fp:
            for idx, line in enumerate(fp, 1):
                # find fetch('/api/...') or url: '/api/...'
                matches = re.findall(r'["\'](/api/[^"\'?#]+)', line)
                for m in matches:
                    js_endpoints.append((m, f, idx))

print(f"Checking {len(js_endpoints)} JS API calls against registered routes...")
unmatched = []
route_patterns = list(routes.keys())

for api, js_file, line_num in js_endpoints:
    # Check if api matches any route
    matched = False
    for r in route_patterns:
        # turn /api/class/<int:class_id>/... into regex
        r_regex = "^" + re.sub(r'<[^>]+>', r'[^/]+', r) + "$"
        if re.match(r_regex, api):
            matched = True
            break
        # also match if api is a prefix ending in / and route has parameter after that prefix
        if api.endswith('/'):
            prefix_pattern = "^" + re.sub(r'<[^>]+>.*', '', r)
            if r.startswith(api) or re.match(prefix_pattern, api):
                matched = True
                break
    if not matched:
        unmatched.append((api, js_file, line_num))

if unmatched:
    print("UNMATCHED API CALLS IN JS:")
    for api, js_file, line_num in unmatched:
        print(f"  {api} in {js_file}:{line_num}")
else:
    print("All JS API endpoints match registered backend routes!")

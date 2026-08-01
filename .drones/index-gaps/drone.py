import os
import re

def run(request):
    repo = request.args.get("repo", "")
    target = os.path.join(repo, "docs", "live-test-results")
    readme_path = os.path.join(target, "README.md")

    if not os.path.isfile(readme_path):
        return {"cannot": "README.md not found in docs/live-test-results/"}

    with open(readme_path, "r", encoding="utf-8") as f:
        readme_text = f.read()

    linked = {os.path.basename(m.strip()) for m in re.findall(r'\]\(([^)]+\.md)', readme_text)}
    all_md = {f for f in os.listdir(target) if f.endswith(".md") and f != "README.md"}
    unlinked = sorted(all_md - linked)

    detail = []
    for fname in unlinked:
        fpath = os.path.join(target, fname)
        first_heading = ""
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#"):
                        first_heading = line.strip()
                        break
        except Exception:
            pass

        verdict = request.ask("substantive", {"filename": fname, "first_heading": first_heading})
        detail.append({"filename": fname, "verdict": verdict})

    return {
        "answer": f"Found {len(detail)} unlinked markdown file(s) in docs/live-test-results/.",
        "detail": {"unlinked": detail},
    }

"""Check local documentation links, translated commands and shell syntax.

Run with bash .agent/run.sh python .agent/check-docs.py; no dependencies needed.
"""

from pathlib import Path
import re
import subprocess
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("zh-CN", "ja", "en")
GUIDES = ("user-guide.md", "development.md", "docker-deployment.md")
INTRANET_GUIDES = ("deploy/README.md", "deploy/README.ja.md", "deploy/README.en.md")


def anchors(content: str) -> set[str]:
    return {
        re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        for heading in re.findall(r"^#{1,6} (.+)$", content, re.MULTILINE)
    }


def commands(content: str) -> list[str]:
    return [
        line.strip()
        for block in re.findall(r"```bash\n(.*?)```", content, re.DOTALL)
        for line in block.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def main() -> int:
    errors = []
    documents = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "docs/README.md"]
    documents += [ROOT / "docs" / language / guide for language in LANGUAGES for guide in GUIDES]
    documents += [ROOT / guide for guide in INTRANET_GUIDES]
    for document in documents:
        content = document.read_text()
        for target in re.findall(r"\[[^\]\n]+\]\(([^)]+)\)", content):
            if "://" in target or target.startswith("mailto:"):
                continue
            path, _, fragment = target.partition("#")
            destination = (document.parent / unquote(path)).resolve() if path else document
            if not destination.exists():
                errors.append(f"{document.relative_to(ROOT)}: missing target {target}")
            elif fragment and destination.suffix == ".md" and unquote(fragment) not in anchors(destination.read_text()):
                errors.append(f"{document.relative_to(ROOT)}: missing heading {target}")
        for block in re.findall(r"```bash\n(.*?)```", content, re.DOTALL):
            result = subprocess.run(["bash", "-n"], input=block, capture_output=True, text=True)
            if result.returncode:
                errors.append(f"{document.relative_to(ROOT)}: shell syntax error: {result.stderr.strip()}")
    for guide in GUIDES:
        baseline = commands((ROOT / "docs/zh-CN" / guide).read_text())
        for language in LANGUAGES[1:]:
            translated = commands((ROOT / "docs" / language / guide).read_text())
            if translated != baseline:
                errors.append(f"{language}/{guide}: shell commands differ from the Chinese edition")
    baseline = commands((ROOT / INTRANET_GUIDES[0]).read_text())
    for guide in INTRANET_GUIDES[1:]:
        if commands((ROOT / guide).read_text()) != baseline:
            errors.append(f"{guide}: shell commands differ from the Chinese edition")
    shell_files = [(".agent/run.sh", "bash"), (".agent/deploy.sh", "sh"), (".agent/compose.sh", "sh")]
    shell_files += [
        (str(path.relative_to(ROOT)), "bash" if path.name == "deploy.sh" else "sh")
        for path in sorted((ROOT / "deploy").glob("*.sh"))
    ]
    for path, shell in shell_files:
        result = subprocess.run([shell, "-n", str(ROOT / path)], capture_output=True, text=True)
        if result.returncode:
            errors.append(f"{path}: {result.stderr.strip()}")
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"PASS: {len(documents)} documents; local links/headings, three-language commands and shell syntax.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

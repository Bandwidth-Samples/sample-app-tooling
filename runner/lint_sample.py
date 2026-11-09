#!/usr/bin/env python3
"""Checklist lint: checks a sample repo against the mechanical items in bandwidth-template/checklist.md."""
import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

TYPES = [
    "messaging-send-receive",
    "messaging-auto-respond",
    "mfa-cli",
    "voice-bridge",
    "voice-gather",
    "voice-record",
    "voice-redirect-loop",
]
LANGUAGES = ["csharp", "java", "js", "php", "python", "ruby"]
JUNK_NAMES = {".DS_Store", ".env"}
JUNK_DIRS = {"node_modules", "__pycache__", "target", "vendor", "bin", "obj"}
WORKFLOWS = {"csharp": "csharp", "java": "java", "js": "node", "php": "php", "python": "python", "ruby": "ruby"}
# (ecosystem, directory, sdk package); C#'s directory is the project folder, read from the sample
DEPENDABOT = {
    "csharp": ("nuget", None, "Bandwidth.Sdk"),
    "java": ("maven", "/", "com.bandwidth.sdk:bandwidth-sdk"),
    "js": ("npm", "/", "bandwidth-sdk"),
    "php": ("composer", "/", "bandwidth/sdk"),
    "python": ("pip", "/", "bandwidth-sdk"),
    "ruby": ("bundler", "/", "bandwidth-sdk"),
}
JAVA_DEPS = {"bandwidth-sdk", "spring-boot-starter-web"}
HEADERS = {
    "voice": ("https://dev.bandwidth.com/docs/voice/programmable-voice/quickStart/", "Voice Quick Start Guide"),
    "messaging": ("https://dev.bandwidth.com/docs/messaging/quickStart", "Messaging Quick Start Guide"),
    "mfa": ("https://dev.bandwidth.com/docs/mfa/", "Multi-Factor Auth About Page"),
}


@dataclass(frozen=True)
class Finding:
    check: str
    file: str
    message: str


@dataclass(frozen=True)
class Sample:
    app: Path
    template: Path
    repo: str
    type: str | None
    lang: str | None
    product: str | None
    files: frozenset[str]

    def read(self, path: str) -> str | None:
        return (self.app / path).read_text() if path in self.files else None


class TemplateError(Exception):
    pass


def parse_repo_name(name: str) -> tuple[str, str] | None:
    for t in TYPES:
        for lang in LANGUAGES:
            if name == f"{t}-{lang}":
                return t, lang
    return None


def product_for(sample_type: str) -> str:
    if sample_type == "mfa-cli":
        return "mfa"
    return "voice" if sample_type.startswith("voice-") else "messaging"


def tracked_files(app: Path) -> frozenset[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=app, capture_output=True, text=True, check=True,
    ).stdout
    return frozenset(line for line in out.splitlines() if (app / line).is_file())


def check_license(s: Sample) -> list[Finding]:
    text = s.read("LICENSE")
    if text is None:
        return [Finding("L1", "LICENSE", "missing, expected the MIT license from bandwidth-template")]
    findings = []
    if text.splitlines()[:1] != ["MIT License"]:
        findings.append(Finding("L1", "LICENSE", "first line should be \"MIT License\""))
    if not re.search(r"^Copyright \(c\) \d{4} ", text, re.M):
        findings.append(Finding("L1", "LICENSE", "no \"Copyright (c) <year> ...\" line"))
    return findings


def check_committed_junk(s: Sample) -> list[Finding]:
    junk = sorted(
        f for f in s.files
        if PurePosixPath(f).name in JUNK_NAMES
        or JUNK_DIRS.intersection(PurePosixPath(f).parts[:-1])
        or (s.lang == "ruby" and f == "Gemfile.lock")
    )
    if not junk:
        return []
    shown = ", ".join(junk[:5]) + (f", ...and {len(junk) - 5} more" if len(junk) > 5 else "")
    return [Finding("L8", "(repo)", f"files that shouldn't be committed: {shown}")]


def check_stray_files(s: Sample) -> list[Finding]:
    findings = []
    if "checklist.md" in s.files:
        findings.append(Finding("L9", "checklist.md", "the checklist belongs in bandwidth-template, not the sample"))
    if ".github/dependabot.yaml" in s.files:
        findings.append(Finding("L9", ".github/dependabot.yaml", "use .github/dependabot.yml only"))
    images = sorted(f for f in s.files if "/" not in f and re.search(r"\.(png|jpe?g|gif)$", f, re.I))
    if images:
        findings.append(Finding("L9", "(root)", f"image files in the repo root: {', '.join(images)}"))
    workflows = sorted(
        f for f in s.files
        if f.startswith(".github/workflows/") and f != ".github/workflows/test.yml"
    )
    if workflows:
        findings.append(Finding("L9", ".github/workflows/", f"only test.yml expected, also found: {', '.join(workflows)}"))
    return findings


def env_block(readme: str) -> list[str] | None:
    section = re.search(r"^# Environmental Variables\s*$(.*?)(?=^ *#|\Z)", readme, re.M | re.S)
    m = section and re.search(r"^```sh\n(.*?)^```", section.group(1), re.M | re.S)
    if not m:
        return None
    return [line.split()[0] for line in m.group(1).splitlines() if line.strip()]


_env_order_cache: dict[Path, list[str]] = {}


def template_env_order(template: Path) -> list[str]:
    if template not in _env_order_cache:
        readme = template / "README.md"
        names = env_block(readme.read_text()) if readme.is_file() else None
        if not names:
            raise TemplateError(f"couldn't read the env var block from {readme}")
        _env_order_cache[template] = names
    return _env_order_cache[template]


def check_icon(s: Sample) -> list[Finding]:
    if s.product is None:
        return []
    icon = f"icon-{s.product}.svg"
    template_icon = s.template / "icons" / icon
    if not template_icon.is_file():
        raise TemplateError(f"{template_icon} is missing")
    findings = []
    text = s.read(icon)
    if text is None:
        findings.append(Finding("L2", icon, f"missing, copy icons/{icon} from bandwidth-template"))
    elif re.sub(r"\s", "", text) != re.sub(r"\s", "", template_icon.read_text()):
        findings.append(Finding("L2", icon, f"doesn't match icons/{icon} in bandwidth-template"))
    extra = sorted(f for f in s.files if re.fullmatch(r"icon-.*\.svg", f) and f != icon)
    if extra:
        findings.append(Finding("L2", "(root)", f"only {icon} expected for a {s.product} sample, also found: {', '.join(extra)}"))
    return findings


def check_readme_header(s: Sample) -> list[Finding]:
    readme = s.read("README.md")
    if readme is None:
        return [Finding("L3", "README.md", "missing")]
    if s.product is None:
        return []
    url, title = HEADERS[s.product]
    header = f'<a href="{url}">\n  <img src="./icon-{s.product}.svg" title="{title}" alt="{title}"/>\n</a>'
    if header in readme:
        return []
    return [Finding("L3", "README.md", f"the {s.product} header block from the checklist wasn't found, expected:\n{header}")]


def check_readme_env(s: Sample) -> list[Finding]:
    readme = s.read("README.md")
    if readme is None:
        return []
    order = template_env_order(s.template)
    findings = []
    names = env_block(readme)
    if names is None:
        findings.append(Finding("L4", "README.md", "no ```sh block under \"# Environmental Variables\""))
    else:
        unknown = [n for n in names if n not in order]
        if unknown:
            findings.append(Finding("L4", "README.md", f"env vars not in the template: {', '.join(unknown)}"))
        known = [n for n in names if n in order]
        for a, b in zip(known, known[1:]):
            if order.index(a) > order.index(b):
                expected = " ".join(sorted(known, key=order.index))
                findings.append(Finding("L4", "README.md", f"env vars out of order: {a} comes before {b}, expected: {expected}"))
                break
    if re.search(r"BW_USERNAME|BW_PASSWORD", readme):
        findings.append(Finding("L4", "README.md", "mentions BW_USERNAME/BW_PASSWORD, samples use BW_CLIENT_ID/BW_CLIENT_SECRET"))
    return findings


def slug(header: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", header.strip().lower()).replace(" ", "-")


def check_readme_toc(s: Sample) -> list[Finding]:
    readme = s.read("README.md")
    if readme is None:
        return []
    m = re.search(r"^ *# Table of Contents\s*$(.*?)^#", readme, re.M | re.S)
    if not m:
        return []
    headers = {slug(h) for h in re.findall(r"^ *#+ (.+)$", readme, re.M)}
    dangling = [a for a in re.findall(r"\]\(#([^)]+)\)", m.group(1)) if a not in headers]
    if not dangling:
        return []
    return [Finding("L10", "README.md", f"Table of Contents links with no matching header: {', '.join('#' + a for a in dangling)}")]


def load_yaml(s: Sample, path: str, check: str) -> tuple[object, list[Finding]]:
    text = s.read(path)
    if text is None:
        return None, [Finding(check, path, "missing")]
    try:
        return yaml.safe_load(text), []
    except yaml.YAMLError as e:
        return None, [Finding(check, path, f"isn't valid YAML: {str(e).splitlines()[0]}")]


def first_difference(got: object, want: object, path: str = "") -> str:
    if isinstance(got, dict) and isinstance(want, dict):
        for key in list(want) + [k for k in got if k not in want]:
            if got.get(key) != want.get(key):
                return first_difference(got.get(key), want.get(key), f"{path}.{key}" if path else str(key))
    elif isinstance(got, list) and isinstance(want, list) and len(got) == len(want):
        for i, (g, w) in enumerate(zip(got, want)):
            if g != w:
                return first_difference(g, w, f"{path}[{i}]")
    return f"{path or '(top level)'} is {got!r}, expected {want!r}"


def check_dependabot(s: Sample) -> list[Finding]:
    if s.lang is None:
        return []
    path = ".github/dependabot.yml"
    template_file = s.template / ".github" / "dependabot.template.yml"
    if not template_file.is_file():
        raise TemplateError(f"{template_file} is missing")
    got, findings = load_yaml(s, path, "L5")
    if findings:
        return findings
    ecosystem, directory, sdk = DEPENDABOT[s.lang]
    if directory is None:
        try:
            directory = str(got["updates"][0]["directory"])
        except (TypeError, KeyError, IndexError):
            return [Finding("L5", path, "no updates[0].directory set, expected the folder holding the .csproj")]
        folder = directory.strip("/")
        if not any(PurePosixPath(f).suffix == ".csproj" and str(PurePosixPath(f).parent).strip(".") == folder for f in s.files):
            findings.append(Finding("L5", path, f"directory {directory} doesn't contain a .csproj"))
    filled = (template_file.read_text()
              .replace("<ecosystem>", ecosystem).replace("<directory>", directory).replace("<sdk-package>", sdk))
    want = yaml.safe_load(filled)
    if got != want:
        findings.append(Finding("L5", path, f"doesn't match bandwidth-template's dependabot.template.yml for {s.lang}: {first_difference(got, want)}"))
    return findings


def check_test_workflow(s: Sample) -> list[Finding]:
    if s.lang is None:
        return []
    path = ".github/workflows/test.yml"
    doc, findings = load_yaml(s, path, "L6")
    if findings:
        return findings
    prefix = f"Bandwidth-Samples/sample-app-tooling/.github/workflows/{WORKFLOWS[s.lang]}.yml@"
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    jobs = [j for j in (jobs or {}).values() if isinstance(j, dict) and isinstance(j.get("uses"), str)]
    job = next((j for j in jobs if j["uses"].startswith(prefix)), None)
    if job is None:
        found = ", ".join(j["uses"] for j in jobs) or "no reusable workflow"
        return [Finding("L6", path, f"should call {prefix}<ref>, found: {found}")]
    sample = (job.get("with") or {}).get("sample")
    if sample != s.type:
        return [Finding("L6", path, f"sample is {sample!r}, expected {s.type!r} (from the repo name)")]
    return []


def check_java_deps(s: Sample) -> list[Finding]:
    if s.lang != "java":
        return []
    text = s.read("pom.xml")
    if text is None:
        return [Finding("L7", "pom.xml", "missing")]
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        return [Finding("L7", "pom.xml", f"isn't valid XML: {e}")]
    ns = {"m": root.tag[1:].split("}")[0]} if root.tag.startswith("{") else {}
    q = "m:" if ns else ""
    deps = [d.text for d in root.findall(f"{q}dependencies/{q}dependency/{q}artifactId", ns)]
    extra = [d for d in deps if d not in JAVA_DEPS]
    if extra:
        return [Finding("L7", "pom.xml", f"only bandwidth-sdk and spring-boot-starter-web allowed, also found: {', '.join(extra)}")]
    return []


CHECKS = [
    check_license, check_icon, check_readme_header, check_readme_env,
    check_dependabot, check_test_workflow, check_java_deps,
    check_committed_junk, check_stray_files, check_readme_toc,
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", required=True, type=Path)
    parser.add_argument("--template-dir", required=True, type=Path)
    parser.add_argument("--repo-name", required=True)
    args = parser.parse_args()

    findings = []
    parsed = parse_repo_name(args.repo_name)
    if parsed is None:
        findings.append(Finding(
            "L0", "(repo name)",
            f"\"{args.repo_name}\" isn't <type>-<lang> with a known type and language",
        ))
    sample_type, lang = parsed or (None, None)
    sample = Sample(
        app=args.app_dir,
        template=args.template_dir,
        repo=args.repo_name,
        type=sample_type,
        lang=lang,
        product=product_for(sample_type) if sample_type else None,
        files=tracked_files(args.app_dir),
    )

    try:
        for check in CHECKS:
            findings.extend(check(sample))
    except TemplateError as e:
        print(f"Tooling/template problem, not a problem with this sample: {e}")
        return 2

    for f in sorted(findings, key=lambda f: int(f.check[1:])):
        print(f"{f.check}  {f.file}: {f.message}")
    if findings:
        print(f"\n{len(findings)} problem(s) found. See bandwidth-template/checklist.md")
        return 1
    print("Checklist lint passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

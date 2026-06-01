# Sphinx configuration for the firesim-lab documentation portal.
# See https://www.sphinx-doc.org/en/master/usage/configuration.html

project = "firesim-lab"
author = "firesim-lab contributors"
copyright = "2026, firesim-lab contributors"


# -- Version (single-sourced from fslab-cli/pyproject.toml) ------------------
# Keeps the documented version in lockstep with the package version. On Read
# the Docs the URL/flyout version still comes from the built branch/tag; this
# only controls the value Sphinx substitutes for |release| / |version|.
def _fslab_version() -> str:
    import re
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[2] / "fslab-cli" / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else ""


release = _fslab_version()
version = ".".join(release.split(".")[:2]) if release else ""

extensions = [
    "myst_parser",
]

source_suffix = {
    ".md": "markdown",
}

master_doc = "index"

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# -- MyST --------------------------------------------------------------------

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "linkify",
    "substitution",
    "attrs_inline",
]

myst_heading_anchors = 3

# -- HTML output -------------------------------------------------------------

html_theme = "sphinx_book_theme"
html_static_path = ["_static"]
html_title = "firesim-lab"
html_baseurl = "https://firesim-lab.readthedocs.io/en/latest/"

html_theme_options = {
    "repository_url": "https://github.com/pentarisc/firesim-lab",
    "repository_branch": "main",
    "path_to_docs": "docs/portal",
    "use_repository_button": True,
    "use_edit_page_button": True,
    "use_issues_button": True,
    "home_page_in_toc": False,
}

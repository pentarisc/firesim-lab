# Sphinx configuration for the firesim-lab documentation portal.
# See https://www.sphinx-doc.org/en/master/usage/configuration.html

project = "firesim-lab"
author = "firesim-lab contributors"
copyright = "2026, firesim-lab contributors"

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

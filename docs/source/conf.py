#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
#
#
# Note that not all possible configuration values are present in this
# autogenerated file.
#
# All configuration values have a default; values that are commented out
# serve to show the default.

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#
# import os
# import sys
# sys.path.insert(0, os.path.abspath('.'))

import os
import sys
from docutils.parsers import rst
import pytorch_sphinx_theme

sys.path.append(os.path.abspath("."))

# -- General configuration ------------------------------------------------

# Required version of sphinx is set from docs/requirements.txt

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.doctest",
    "sphinx.ext.intersphinx",
    "sphinx.ext.todo",
    "sphinx.ext.mathjax",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.duration",
    "sphinx_tabs.tabs",
    "sphinx_design",
    "sphinx_gallery.gen_gallery",
    "sphinx_copybutton",
]

sphinx_gallery_conf = {
    "examples_dirs": "tutorials_source",  # path to your sphinx-gallery examples
    "gallery_dirs": "tutorials",  # path to where to save shpinx-gallery generated output
    "filename_pattern": "./*.py",  # any .py file in docs/source/tutorials will be built by sphinx-gallery
    "backreferences_dir": "gen_modules/backreferences",  # path to store the backreferences
    "remove_config_comments": True,
}

napoleon_use_ivar = True
napoleon_numpy_docstring = False
napoleon_google_docstring = True
project = "torchao"

# Get TORCHAO_VERSION_DOCS during the build.
torchao_version_docs = os.environ.get("TORCHAO_VERSION_DOCS", None)

# The code below will cut version displayed in the dropdown like this:
# tags like v0.1.0 = > 0.1
# branch like release/0.1 => 0.1
# main will remain main
# if not set will fail back to main
# the version varible is used in layout.html: https://github.com/pytorch/torchao/blob/main/docs/source/_templates/layout.html#L29
if torchao_version_docs:
    # Check if starts with release/ and set the version to the number after slash
    if torchao_version_docs.startswith("release/"):
        version = torchao_version_docs.split("/")[-1]
    else:
        # Remove "v" prefix if present
        if torchao_version_docs.startswith("v"):
            torchao_version_docs = torchao_version_docs[1:]
        # Split to major, minor, and patch
        version_components = torchao_version_docs.split(".")

        # Combine the major and minor version components:
        if len(version_components) >= 2:
            version = release = ".".join(version_components[:2])
        else:
            # If there are not enough components, use the full version
            version = release = torchao_version_docs

    html_title = " ".join((project, version, "documentation"))
# IF TORCHAO_VERSION_DOCS not set, set version to main.
else:
    version = "main"
    release = "main"

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# The suffix(es) of source filenames.
# You can specify multiple suffix as a list of string:
#
source_suffix = [".rst"]

# The master toctree document.
master_doc = "index"

# General information about the project.
copyright = "2024-present, torchao Contributors"
author = "torchao Contributors"

# The language for content autogenerated by Sphinx. Refer to documentation
# for a list of supported languages.
#
# This is also used if you do content translation via gettext catalogs.
# Usually you set "language" from the command line for these cases.
language = "en"

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This patterns also effect to html_static_path and html_extra_path
exclude_patterns = []

# The name of the Pygments (syntax highlighting) style to use.
pygments_style = "sphinx"

# If true, `todo` and `todoList` produce output, else they produce nothing.
todo_include_todos = True


# -- Options for HTML output ----------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = "pytorch_sphinx_theme"
html_theme_path = [pytorch_sphinx_theme.get_html_theme_path()]

# Theme options are theme-specific and customize the look and feel of a theme
# further.  For a list of options available for each theme, see the
# documentation.
#
html_theme_options = {
    "collapse_navigation": False,
    "display_version": True,
    "logo_only": True,
    "pytorch_project": "docs",
    "navigation_with_keys": True,
}

html_logo = "_static/img/pytorch-logo-dark.svg"

html_css_files = ["css/custom.css"]

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]

# -- Options for HTMLHelp output ------------------------------------------

# Output file base name for HTML help builder.
htmlhelp_basename = "PyTorchdoc"


autosummary_generate = True

# Example configuration for intersphinx: refer to the Python standard library.
intersphinx_mapping = {
    "python": ("https://docs.python.org/3/", None),
    "torch": ("https://pytorch.org/docs/stable/", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
}

# -- A patch that prevents Sphinx from cross-referencing ivar tags -------
# See http://stackoverflow.com/a/41184353/3343043

from docutils import nodes
from sphinx import addnodes
from sphinx.util.docfields import TypedField

from custom_directives import CustomCardEnd, CustomCardItem, CustomCardStart
from docutils.parsers import rst

rst.directives.register_directive("customcardstart", CustomCardStart)
rst.directives.register_directive("customcarditem", CustomCardItem)
rst.directives.register_directive("customcardend", CustomCardEnd)

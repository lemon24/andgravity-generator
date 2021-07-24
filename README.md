The static site generator behind [death.andgravity.com](https://death.andgravity.com).

Covers exactly one use case: turn Markdown files into HTML files hosted on GitHub Pages.

Features:

* fancy Markdown (Mistune + custom extensions)
  * wiki links
  * heading links
  * heading slugs
  * syntax highlighting (Pygments)
  * include highlighted code fragments from file (like Sphinx' literalinclude)
* attachments
* tags
* Atom feeds (global, per-tag)
* internal URL validation (including fragments)
* fast generation through aggressive caching
* live preview

Vaguely inspired by Lektor, but much less flexible, on purpose. Rougly following the ideas [here](https://github.com/lemon24/urlspace). 

Uses Flask to serve a web app, and Frozen-Flask to write the pages to disk.

Needs a project directory to work (`.` by default). See `tests/data/integration/in` for an example.

Usage:

```sh
# serve the website locally
python -m gen serve --open 

# generate HTML files, overwriting whatever is in $repo;
# if successful, commit and push
python -m gen freeze $repo -f --deploy --cache

# serve the website locally for development
GEN_PROJECT_ROOT=... FLASK_DEBUG=1 FLASK_APP=gen/wsgi.py flask run

```

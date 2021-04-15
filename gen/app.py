import ntpath
import os.path
import warnings
from contextlib import nullcontext
from functools import lru_cache
from itertools import chain
from urllib.parse import urlparse

import bs4
import feedgen.ext.base
import feedgen.feed
import flask
import humanize
import jinja2
import markupsafe
import readtime
import soupsieve.util
from flask import abort
from flask import Blueprint
from flask import current_app
from flask import Flask
from flask import g
from flask import render_template
from flask import request
from flask import Response
from flask import send_from_directory
from flask import url_for
from werkzeug.exceptions import HTTPException
from werkzeug.routing import BaseConverter

from .core import Thingie
from .markdown import make_markdown


def build_url(url, text=None):
    url_parsed = urlparse(url)
    if url_parsed.scheme not in ('node', ''):
        return None

    if url_parsed.hostname:
        raise ValueError(f"node: does not support host yet, got {url!r}")

    # TODO: disallow query strings, port etc

    id = url_parsed.path.lstrip('/')
    if not id:
        id = request.view_args['id']

    # check for existence
    # TODO: raise a nicer exception
    page = get_thingie().get_page(id)

    if url_parsed.path:
        new_url = url_for("main.page", id=id)
    else:
        new_url = ''

    if url_parsed.fragment:
        # fragment existence gets checked somewhere else to avoid cycles
        new_url = f"{new_url}#{url_parsed.fragment}"

    if not text:
        text = id

    return new_url, text


def get_thingie():
    if hasattr(g, 'thingie'):
        return g.thingie
    # TODO: get path from app config
    g.thingie = Thingie(os.path.join(current_app.project_root, 'content'))
    return g.thingie


main_bp = Blueprint('main', __name__)


@main_bp.app_template_filter('readtime_minutes')
def readtime_minutes_filter(html):
    return readtime.of_html(html).minutes


@main_bp.app_template_filter('humanize_apnumber')
def humanize_apnumber_filter(value):
    return humanize.apnumber(value)


@main_bp.route('/', defaults={'id': 'index'})
@main_bp.route('/<id>')
def page(id):
    try:
        page = get_thingie().get_page(id)
    # TODO: be more precise in what we're catching
    except FileNotFoundError:
        return abort(404)
    # TODO: page should have a template attribute we're using
    template = current_app.jinja_env.select_template(
        [os.path.join('custom', id + '.html'), 'base.html']
    )
    return render_template(template, page=page)


check_bp = Blueprint('check', __name__)

warnings.filterwarnings(
    'ignore', message='No parser was explicitly specified', module='gen.app'
)


def get_page_fragments(id):
    # TODO: this is temporary, until we fix caching (maybe?)
    get_soup = request.get_soup

    soup = get_soup(id)
    rv = set()

    for element in soup.select('[id], a[name]'):
        if 'id' in element.attrs:
            rv.add(element['id'])
        if element.name == 'a' and 'name' in element.attrs:
            rv.add(element['name'])

    return rv


def match_app_url(url):
    url_adapter = flask._request_ctx_stack.top.url_adapter
    try:
        return url_adapter.match(url)
    except HTTPException as e:
        return None


def get_internal_links(id):
    # TODO: this is temporary, until we fix caching (maybe?)
    get_soup = request.get_soup

    soup = get_soup(id)
    rv = set()
    seen = set()

    for anchor in soup.select('a[href]'):
        url = anchor['href']

        if url in seen:
            continue

        seen.add(url)

        url_parsed = urlparse(url)
        if url_parsed.scheme not in ('http', 'https', ''):
            continue

        if not url_parsed.hostname and not url_parsed.path:
            url_parsed = url_parsed._replace(
                path=urlparse(url_for('main.page', id=id)).path
            )

        match = match_app_url(url_parsed._replace(fragment='').geturl())
        if not match:
            continue

        match_endpoint, match_args = match
        if match_endpoint != 'main.page':
            continue

        rv.add((url, match_args['id'], url_parsed.fragment))

    return rv


@check_bp.route('/internal-urls.json')
def internal_urls():
    # TODO: also check attachments/images here?

    thingie = get_thingie()

    @lru_cache
    def get_soup(id):
        return bs4.BeautifulSoup(render_node(id))

    request.get_soup = get_soup

    errors = {}
    data = {}

    for id in thingie.get_page_ids(hidden=None, discoverable=None):
        data[id] = urls = {}

        for url, target_id, fragment in get_internal_links(id):
            error = None

            try:
                thingie.get_page(target_id)
            except FileNotFoundError:
                error = "node not found"
            else:
                if fragment and fragment not in get_page_fragments(target_id):
                    error = "fragment not found"

            urls[url] = {'error': error}
            if error:
                errors.setdefault(id, {})[url] = error

    rv = {'errors': errors, 'data': data}

    return rv


feed_bp = Blueprint('feed', __name__)


def abs_page_url_for(id):
    return current_app.project_url + url_for('main.page', id=id)


def abs_feed_url_for(id, tags=None):
    if not tags:
        url = url_for('feed.feed', id=id)
    else:
        url = url_for('feed.tags_feed', id=id, tags=tags)
    return current_app.project_url + url


class AtomXMLBaseExt(feedgen.ext.base.BaseEntryExtension):
    def extend_atom(self, entry):
        entry.base = entry.find("./link[@rel='alternate']").attrib['href']
        return entry


@feed_bp.route('/<id>.xml')
def feed(id):
    return make_feed_response(id)


@feed_bp.route('/<id>/_tags/<list:tags>.xml')
def tags_feed(id, tags):
    # TODO: check tags exist in thingie
    # TODO: check tags are in canonical order
    return make_feed_response(id, tags)


def make_feed_response(*args, **kwargs):
    try:
        fg = make_feed(get_thingie(), *args, **kwargs)
    # TODO: be more precise in what we're catching
    # (both exc type, and that it's for id and not other node)
    except FileNotFoundError:
        return abort(404)

    return Response(
        fg.atom_str(pretty=True),
        # should be application/atom+xml, but we get warnings when freezing
        mimetype='application/xml',
    )


def make_feed(thingie, id, tags=None):
    page = thingie.get_page(id)
    index = thingie.get_page('index')

    fg = feedgen.feed.FeedGenerator()
    # TODO: link to tag page once we have one
    fg.id(abs_page_url_for(id))  # required

    feed_title = page.title
    if id != 'index':
        feed_title = index.title + ': ' + feed_title
    if tags:
        feed_title += f" {' '.join(f'#{t}' for t in tags)}"
    fg.title(feed_title)  # required

    fg.link(href=abs_page_url_for(id), rel='alternate')
    # TODO: link to tag page once we have one
    fg.link(href=abs_feed_url_for(id, tags), rel='self')
    # remove the default generator
    fg.generator(generator="")

    author_source = page if 'author' in page.meta else index
    fg.author(
        name=author_source.meta['author']['name'],
        email=author_source.meta['author'].get('email'),
    )

    # sort ascending, because feedgen reverses the entries
    children = list(thingie.get_children(id, sort='published', tags=tags))

    if not children:
        feed_updated = '1970-01-01T00:00:00Z'
    else:
        feed_updated = max(
            date
            for child in children
            for date in (
                child.meta.get('updated', child.meta['published']),
                child.meta['published'],
            )
        )

    fg.updated(feed_updated)  # required

    for child in children:
        fe = fg.add_entry()
        fe.register_extension('atomxmlbase', AtomXMLBaseExt, atom=True, rss=False)

        fe.id(abs_page_url_for(child.id))  # required
        fe.title(child.title)  # required
        fe.link(href=abs_page_url_for(child.id))

        if 'author' in child.meta:
            fe.author(
                name=child.meta['author']['name'],
                email=child.meta['author'].get('email'),
            )

        fe.updated(child.meta.get('updated', child.meta['published']))  # required
        fe.published(child.meta['published'])

        # TODO: summary feature
        fe.content(content=render_node(child.id), type='html')

    return fg


file_bp = Blueprint('file', __name__)


@file_bp.route('/<id>/<path:path>')
def file(id, path):
    # TODO: Thingie should tell us what the path to files is
    return send_from_directory(
        os.path.join(current_app.project_root, 'files'),
        os.path.join(id, path),
    )


def build_file_url(url, text=None):
    url_parsed = urlparse(url)
    if url_parsed.scheme != 'attachment':
        return None

    if url_parsed.hostname:
        raise ValueError(f"attachment: does not support host yet, got {url!r}")

    path = url_parsed.path.lstrip('/')

    id = request.view_args['id']
    # check for existence
    # TODO: raise a nicer exception
    page = get_thingie().get_page(id)

    if not text:
        raise ValueError("attachment: getting text not supported yet")

    # TODO: disallow fragments, query string etc

    # TODO: maybe raise if the file doesn't exist?
    # the freezer fails for 404s, so it's not urgent
    return url_for("file.file", id=id, path=path), text


class ListConverter(BaseConverter):
    def to_python(self, value):
        return value.split(',')

    def to_url(self, values):
        to_url = super().to_url
        return ','.join(to_url(value) for value in values)


def _render_node(id):
    page = get_thingie().get_page(id)
    url = url_for('main.page', id=id)
    with current_app.test_request_context(url):
        html = current_app.markdown(page.content)
    return markupsafe.Markup(html)


def render_node(id=None):
    if id is None:
        id = request.view_args['id']
    # a level of indirection to allow caching stuff, maybe
    return current_app.render_node(id)


def create_app(project_root, project_url, *, enable_checks=True, cache_markdown=False):
    app = Flask(
        __name__,
        template_folder=os.path.join(project_root, 'templates'),
        static_url_path='/_static',
        static_folder=os.path.join(project_root, 'static'),
    )
    app.project_root = project_root
    app.project_url = project_url
    app.jinja_env.undefined = jinja2.StrictUndefined
    app.url_map.converters['list'] = ListConverter

    app.add_template_global(get_thingie)

    app.markdown = make_markdown([build_url, build_file_url])

    app.render_node = _render_node
    if cache_markdown:
        app.render_node = lru_cache(app.render_node)

    app.add_template_global(render_node)

    app.register_blueprint(main_bp)
    app.register_blueprint(feed_bp, url_prefix='/_feed')
    app.register_blueprint(file_bp, url_prefix='/_file')

    def enable_checks_fn():
        app.register_blueprint(check_bp, url_prefix='/_check')

    app.enable_checks = enable_checks_fn
    if enable_checks:
        app.enable_checks()

    return app

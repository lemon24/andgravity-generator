import functools
import ntpath
import os.path
import warnings
from contextlib import nullcontext
from dataclasses import dataclass
from dataclasses import field
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
import yaml
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
from werkzeug.exceptions import NotFound
from werkzeug.routing import BaseConverter

from .checks import LinkChecker
from .core import Thingie
from .markdown import make_markdown


# BEGIN main blueprint


main_bp = Blueprint('main', __name__)


# various helpers, mostly related to the node state


def get_state():
    return current_app.extensions['state']


@main_bp.app_template_global()
def get_thingie():
    # for convenience
    return get_state().thingie


@main_bp.app_template_global()
def render_node(id=None):
    if id is None:
        id = request.view_args['id']
    return get_state().render_node(id, **request.args)


@main_bp.app_template_global()
def node_read_time(id=None):
    if id is None:
        id = request.view_args['id']
    return get_state().node_read_time(id)


@main_bp.app_template_global()
def url_for_node(id=None, **values):
    if id is None:
        id = request.view_args['id']
    kwargs = dict(request.args)
    kwargs.update(values)
    return url_for('main.page', id=id, **kwargs)


def get_project_url():
    return current_app.config.get('PROJECT_URL') or get_thingie().get_project_url()


def abs_page_url_for(id):
    return get_project_url().rstrip('/') + url_for('main.page', id=id)


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


@main_bp.route('/_file/<id>/<path:path>')
def file(id, path):
    return send_from_directory(
        os.path.join(current_app.config['PROJECT_ROOT'], 'files'),
        os.path.join(id, path),
    )


@main_bp.app_template_filter('humanize_apnumber')
def humanize_apnumber_filter(value):
    return humanize.apnumber(value)


main_bp.add_app_template_filter(yaml.safe_dump, 'to_yaml')


# BEGIN feed blueprint


feed_bp = Blueprint('feed', __name__)


@feed_bp.route('/<id>.xml')
def feed(id):
    return make_feed_response(id)


@feed_bp.route('/<id>/_tags/<list:tags>.xml')
def tag_feed(id, tags):
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

        if child.summary:
            fe.summary(child.summary)

        fe.content(content=render_node(child.id), type='html')

    return fg


def abs_feed_url_for(id, tags=None):
    if not tags:
        url = url_for('feed.feed', id=id)
    else:
        url = url_for('feed.tag_feed', id=id, tags=tags)
    return get_project_url().rstrip('/') + url


class AtomXMLBaseExt(feedgen.ext.base.BaseEntryExtension):
    def extend_atom(self, entry):
        entry.base = entry.find("./link[@rel='alternate']").attrib['href']
        return entry


# BEGIN check blueprint


check_bp = Blueprint('check', __name__)


@check_bp.route('/internal-links.json')
def internal_links():
    # TODO: instantiate and cache link checker here, maybe
    return dict(get_state().link_checker.check_internal_links())


# BEGIN markdown


def build_page_url(url, text=None):
    """Markdown schema-less URL -> web app page URL."""
    url_parsed = urlparse(url)
    if url_parsed.scheme not in ('node', ''):
        return None

    if url_parsed.hostname:
        raise ValueError(f"node: does not support host yet, got {url!r}")

    # TODO: disallow query strings, port etc

    path = url_parsed.path.lstrip('/')
    if path:
        id = path
    else:
        id = request.view_args['id']

    kwargs = {}
    if url_parsed.fragment:
        kwargs['_anchor'] = url_parsed.fragment

    new_url = url_for_node(id=id, **kwargs)
    if not path:
        new_url = urlparse(new_url)._replace(path='').geturl()

    if not text:
        text = id

    return new_url, text


def build_file_url(url, text=None):
    """Markdown attachment: URL -> web app file URL."""

    # TODO: maybe use file: instead?

    url_parsed = urlparse(url)
    if url_parsed.scheme != 'attachment':
        return None

    if url_parsed.hostname:
        raise ValueError(f"attachment: does not support host yet, got {url!r}")

    path = url_parsed.path.lstrip('/')

    id = request.view_args['id']

    if not text:
        raise ValueError("attachment: getting text not supported yet")

    # TODO: disallow fragments, query string etc

    return url_for("main.file", id=id, path=path), text


# For now, we're OK with a global, non-configurable markdown instance.

markdown = make_markdown([build_page_url, build_file_url])


# BEGIN node state


def init_node_state(app, node_cache_decorator=None):
    """Store node-related state on the app.

    Implemented as a Flask extension, to avoid subclassing /
    setting attributes directly on the Flask instance.

    """
    project_root = app.config['PROJECT_ROOT']

    thingie = Thingie(os.path.join(project_root, 'content'))
    if node_cache_decorator:
        thingie.get_page_metadata = node_cache_decorator(thingie.get_page_metadata)
        thingie.get_page_content = node_cache_decorator(thingie.get_page_content)

    state = _NodeState(app, thingie)
    if node_cache_decorator:
        state.render_node = node_cache_decorator(state.render_node)
        state.node_read_time = node_cache_decorator(state.node_read_time)

    state.link_checker = link_checker = LinkChecker(state)
    if node_cache_decorator:
        link_checker.get_fragments = node_cache_decorator(link_checker.get_fragments)
        link_checker.get_internal_links = node_cache_decorator(
            link_checker.get_internal_links
        )

    app.extensions['state'] = state


@dataclass
class _NodeState:
    """Node-related state for an app.

    Provides cacheable methods, without having to directly expose the whole app.

    The circular dependency between the app and this state is probably OK.
    This exists to limit the API others (e.g. LinkChecker) should depend on.

    For now, it's OK to have one of everything per thread.
    If we change our mind, we can make the various attributes
    properties that set/return state from g.

    """

    _app: Flask
    thingie: Thingie
    link_checker: LinkChecker = field(init=False)

    def render_node(self, id, **values):
        # This is here because we need a method to cache.
        page = self.thingie.get_page(id)
        with self._node_context(id, values=values):
            return markupsafe.Markup(markdown(page.content))
        
    def node_read_time(self, id):
        # This is here because we need a method to cache.
        return readtime.of_html(self.render_node(id))

    def _node_context(self, id, values=None):
        url = self.url_for_node(id, **(values or {}))
        return self._app.test_request_context(url)

    def render_page(self, id):
        with self._app.test_client() as client:
            rv = client.get(self.url_for_node(id))
            assert rv.status_code == 200, rv.status
            return rv.get_data(as_text=True)

    def url_for_node(self, id, **values):
        with self._app.test_request_context():
            return url_for_node(id, **values)

    def match_url(self, *args, **kwargs):
        ctx = self._app.test_request_context()
        try:
            return ctx.url_adapter.match(*args, **kwargs)
        except NotFound as e:
            return None


# BEGIN app creation


class ListConverter(BaseConverter):
    def to_python(self, value):
        return value.split(',')

    def to_url(self, values):
        to_url = super().to_url
        
        # for some reason, starting with Flask/Werkzeug ~2.0 or 
        # with Frozen-Flask 0.17 or 0.18,
        # werkzeug.routing.MapAdapter.build() does
        # "if len(value) == 1: value = value[0]"
        # https://github.com/pallets/werkzeug/blob/2.0.1/src/werkzeug/routing.py#L2294-L2295

        if isinstance(values, str):
            return to_url(values)
            
        return ','.join(to_url(value) for value in values)


def create_app(
    project_root,
    *,
    project_url=None,
    enable_checks=True,
    node_cache_decorator=None,
):
    app = Flask(
        __name__,
        template_folder=os.path.join(project_root, 'templates'),
        static_url_path='/_static',
        static_folder=os.path.join(project_root, 'static'),
    )

    app.config['PROJECT_ROOT'] = project_root
    if project_url:
        app.config['PROJECT_URL'] = project_url

    app.jinja_env.undefined = jinja2.StrictUndefined
    app.url_map.converters['list'] = ListConverter

    app.register_blueprint(main_bp)
    app.register_blueprint(feed_bp, url_prefix='/_feed')

    if enable_checks:
        app.register_blueprint(check_bp, url_prefix='/_check')

    init_node_state(app, node_cache_decorator)

    return app

import datetime
import os.path
import textwrap
from collections import deque
from urllib.parse import urlparse

import feedgen.ext.base
import feedgen.feed
import humanize
import jinja2
import markupsafe
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
from werkzeug.routing import BaseConverter

from .caching import EndpointInfo
from .caching import init_node_state
from .markdown import make_markdown


# BEGIN main blueprint


main_bp = Blueprint('main', __name__)


# various helpers, mostly related to the node state


def get_state():
    return current_app.extensions['state']


@main_bp.app_template_global()
def get_storage():
    # for convenience
    return get_state().storage


@main_bp.app_template_global()
def render_node(id=None):
    if id is None:
        id = request.view_args['id']

    if not hasattr(g, 'endpoint_info_stack'):
        g.endpoint_info_stack = deque()
    try:
        endpoint_info = request.endpoint, request.view_args
    except RuntimeError:
        endpoint_info = None
    else:
        g.endpoint_info_stack.append(endpoint_info)

    try:
        return get_state().render_node(id, get_real_endpoint(), **request.args)
    finally:
        if endpoint_info:
            g.endpoint_info_stack.pop()


def get_real_endpoint(endpoint_info_stack=None):
    if endpoint_info_stack is None:
        endpoint_info_stack = getattr(g, 'endpoint_info_stack', ())
    for endpoint, view_args in endpoint_info_stack:
        if endpoint == 'feed.tag_feed':
            return EndpointInfo('feed', tuple(sorted(view_args['tags'])))
        if endpoint == 'feed.feed':
            return EndpointInfo('feed')
    return EndpointInfo('main')


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
    return current_app.config.get('PROJECT_URL') or get_storage().get_project_url()


def abs_page_url_for(id):
    return get_project_url().rstrip('/') + url_for('main.page', id=id)


@main_bp.route('/', defaults={'id': 'index'})
@main_bp.route('/<id>')
def page(id):
    try:
        page = get_storage().get_page(id)
    # TODO: be more precise in what we're catching
    except FileNotFoundError:
        return abort(404)
    # TODO: page should have a template attribute we're using
    template = current_app.jinja_env.select_template(
        [os.path.join('custom', id + '.html'), 'base.html']
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    return render_template(template, page=page, now=now)


@main_bp.route('/_file/<id>/<path:path>')
def file(id, path):
    return send_from_directory(
        os.path.join(current_app.config['PROJECT_ROOT'], 'files'),
        os.path.join(id, path),
    )


@main_bp.app_template_filter('humanize_apnumber')
def humanize_apnumber_filter(value):
    return humanize.apnumber(value)


@main_bp.app_template_filter('markdown')
def markdown_filter(text):
    return markupsafe.Markup(markdown(text))


main_bp.add_app_template_filter(yaml.safe_dump, 'to_yaml')


@main_bp.app_template_filter('percent_encode')
def percent_encode(s, encoding="ascii"):
    return ''.join([f'%{b:0>2x}' for b in s.encode(encoding)])


# BEGIN feed blueprint


feed_bp = Blueprint('feed', __name__)


@feed_bp.route('/<id>.xml')
def feed(id):
    return make_feed_response(id)


@feed_bp.route('/<id>/_tags/<list:tags>.xml')
def tag_feed(id, tags):
    # TODO: check tags exist in storage
    # TODO: check tags are in canonical order
    return make_feed_response(id, tags)


def make_feed_response(*args, **kwargs):
    try:
        fg = make_feed(get_storage(), *args, **kwargs)
    # TODO: be more precise in what we're catching
    # (both exc type, and that it's for id and not other node)
    except FileNotFoundError:
        return abort(404)

    return Response(
        fg.atom_str(pretty=True),
        # should be application/atom+xml, but we get warnings when freezing
        mimetype='application/xml',
    )


def make_feed(storage, id, tags=None):
    page = storage.get_page(id)
    index = storage.get_page('index')

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
    children = list(storage.get_children(id, sort='published', tags=tags))

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
        id = url_parsed.hostname
    else:
        id = request.view_args['id']

    path = url_parsed.path.lstrip('/')

    if not text:
        raise ValueError("attachment: getting text not supported yet")

    # TODO: disallow fragments, query string etc

    return url_for("main.file", id=id, path=path), text


def load_literalinclude(url):
    """Attachment contents: URL -> list of lines."""

    url_parsed = urlparse(url)
    if url_parsed.scheme not in ('', 'attachment'):
        raise ValueError(f"literalinclude file must not have scheme, got {url!r}")

    if url_parsed.hostname:
        id = url_parsed.hostname
    else:
        id = request.view_args['id']

    path = url_parsed.path.lstrip('/')

    # TODO: check path doesn't go above <project_root>/files/<id>
    # TODO: this is cacheable, and should be done by storage

    actual_path = os.path.join(current_app.config['PROJECT_ROOT'], 'files', id, path)
    with open(actual_path) as f:
        return list(f)


def render_snippet(snippet, text, options):
    template = current_app.jinja_env.get_template(
        os.path.join('snippets', snippet + '.html')
    )

    page = get_storage().get_page(request.view_args['id'])

    return render_template(
        template,
        snippet=snippet,
        text=text,
        options=options,
        page=page,
        endpoint_info=get_real_endpoint(),
    )


# For now, we're OK with a global, non-configurable markdown instance.

markdown = make_markdown(
    url_rewriters=[build_page_url, build_file_url],
    load_literalinclude=load_literalinclude,
    render_snippet=render_snippet,
)


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
    app.jinja_env.filters['dedent'] = textwrap.dedent
    app.url_map.converters['list'] = ListConverter

    app.register_blueprint(main_bp)
    app.register_blueprint(feed_bp, url_prefix='/_feed')

    init_node_state(app, node_cache_decorator)

    return app

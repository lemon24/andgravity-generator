import functools
import os.path
import pathlib
import warnings
from dataclasses import dataclass
from typing import NamedTuple

import bs4
import markupsafe
import readtime
from werkzeug.exceptions import NotFound

import gen
from .checks import LinkChecker
from .checks import MetaChecker
from .checks import RenderingChecker
from .storage import Storage


warnings.filterwarnings(
    'ignore', message='No parser was explicitly specified', module='gen.caching'
)


@dataclass
class NodeState:
    """Node-related state for an app.

    Provides cacheable methods, without having to directly expose the whole app.

    The circular dependency between the app and this state is probably OK.
    This exists to limit the API others (e.g. LinkChecker) should depend on.

    For now, it's OK to have one of everything per thread.
    If we change our mind, we can make the various attributes
    properties that set/return state from g.

    """

    _app: 'Flask'  # noqa
    storage: 'Storage'
    checker: MetaChecker = None
    link_checker: LinkChecker = None
    dependency_tracker: 'DependecyTracker' = None

    def render_node(self, id, real_endpoint, **values):
        # This is here because we need a method to cache.
        # real_endpoint is set by render_node() for cache invalidation.
        from .app import markdown

        page = self.storage.get_page(id)
        with self._node_context(id, values=values):
            return markupsafe.Markup(markdown(page.content))

    def node_read_time(self, id):
        # This is here because we need a method to cache.
        return readtime.of_html(self.render_node(id, EndpointInfo()))

    def get_soup(self, id, real_endpoint):
        # This is here because we need a method to cache.
        return bs4.BeautifulSoup(self.render_page(id, real_endpoint))

    def _node_context(self, id, values=None):
        url = self.url_for_node(id, **(values or {}))
        return self._app.test_request_context(url)

    def render_page(self, id, real_endpoint):
        # real_endpoint is set for cache invalidation.
        with self._app.test_client() as client:
            rv = client.get(self.url_for_node(id))
            assert rv.status_code == 200, rv.status
            return rv.get_data(as_text=True)

    def url_for_node(self, id, **values):
        from .app import url_for_node

        with self._app.test_request_context():
            return url_for_node(id, **values)

    def match_url(self, *args, **kwargs):
        ctx = self._app.test_request_context()
        try:
            return ctx.url_adapter.match(*args, **kwargs)
        except NotFound:
            return None


"""
def cache_node_methods(self, cache_decorator):
    for name in self.cacheable_node_methods:
        setattr(self, name, cache_decorator(getattr(self, name)))
"""


def init_node_state(app, node_cache_decorator=None):
    """Store node-related state on the app.

    Implemented as a Flask extension, to avoid subclassing /
    setting attributes directly on the Flask instance.

    """
    project_root = app.config['PROJECT_ROOT']

    storage = Storage(os.path.join(project_root, 'content'))
    if node_cache_decorator:
        storage.get_page_metadata = node_cache_decorator(storage.get_page_metadata)
        storage.get_page_content = node_cache_decorator(storage.get_page_content)

    state = NodeState(app, storage)
    if node_cache_decorator:
        state.render_node = node_cache_decorator(state.render_node)
        state.node_read_time = node_cache_decorator(state.node_read_time)
        # node_cache_decorator doesn't work, because pickle fails;
        # lru_cache saves less than .1s ...
        # state.get_soup = functools.lru_cache(state.get_soup)

    state.link_checker = link_checker = LinkChecker(state)
    if node_cache_decorator:
        link_checker.get_fragments = node_cache_decorator(link_checker.get_fragments)
        link_checker.get_internal_links = node_cache_decorator(
            link_checker.get_internal_links
        )

    rendering_checker = RenderingChecker(state)
    # nothing to cache for RenderingChecker? nothing to cache...

    state.checker = MetaChecker(state, [link_checker, rendering_checker])

    state.dependency_tracker = dependency_tracker = DependecyTracker()
    # we are only tracking dependencies during page rendering;
    # if in the feed there are different dependencies
    # (e.g. because of endpoint-dependent logic in snippets),
    # those won't be tracked
    app.view_functions['main.page'] = intercept_node(
        app.view_functions['main.page'], dependency_tracker
    )
    storage.get_page = intercept(storage.get_page, on_return=dependency_tracker.page)
    storage.get_pages = intercept(storage.get_pages, on_return=dependency_tracker.pages)

    app.extensions['state'] = state


class DependecyTracker:
    def __init__(self, debug=False):
        self.dependencies = {}
        self.debug = debug
        self._stack = []

    def start_node(self, fn, id, *_, **__):
        if self.debug:
            print(f"{'  ' * len(self._stack)}{fn.__name__}({id})")
        self._stack.append(id)

    def end_node(self, fn, _):
        self._stack.pop()

    def page(self, fn, page):
        if not self._stack:
            return
        self.dependencies.setdefault(self._stack[-1], set()).add(page.id)
        if self.debug:
            print(f"{'  ' * len(self._stack)}{fn.__name__} -> {page.id}")

    def pages(self, fn, pages):
        if not self._stack:
            return
        self.dependencies.setdefault(self._stack[-1], set()).update(p.id for p in pages)
        if self.debug:
            print(
                f"{'  ' * len(self._stack)}{fn.__name__} -> "
                f"{', '.join(p.id for p in pages)}"
            )


def intercept(fn, on_called=None, on_return=None):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if on_called:
            on_called(fn, *args, **kwargs)
        rv = fn(*args, **kwargs)
        if on_return:
            on_return(fn, rv)
        return rv

    return wrapper


def intercept_node(fn, target):
    return intercept(fn, on_called=target.start_node, on_return=target.end_node)


class EndpointInfo(NamedTuple):
    endpoint: "Literal['main', 'feed']" = 'main'  # noqa
    tags: tuple = ()


def make_node_cache_decorator(cache: 'diskcache.Cache', log):  # noqa
    def node_cache_decorator(fn):
        @functools.lru_cache
        @functools.wraps(fn)
        def wrapper(id, *args):
            key = (f'{fn.__module__}.{fn.__qualname__}', id) + args

            rv = cache.get(key)
            if rv is not None:
                log('hit ', *key)
                return rv

            log('miss', *key)

            rv = fn(id, *args)
            cache.set(key, rv, tag=f'node:{id}')
            return rv

        return wrapper

    return node_cache_decorator


def invalidate_cache(project, storage, cache):
    content_root = os.path.join(project, 'content')

    old_mtimes = cache.get('mtimes', {})
    new_mtimes = {}

    def check_mtime(key, path, glob):
        old_mtime = old_mtimes.get(key, 0)
        new_mtime = old_mtime
        for path in pathlib.Path(path).glob(glob):  # noqa
            new_mtime = max(new_mtime, path.stat().st_mtime)
        if new_mtime > old_mtime:
            new_mtimes[key] = new_mtime

    check_mtime('dir:gen', gen.__path__[0], '**/*.py')
    check_mtime('dir:templates', os.path.join(project, 'templates'), '**/*')

    for id, path in storage.get_page_paths():
        check_mtime(f'node:{id}', content_root, path)

    to_evict = set()

    if new_mtimes:
        if any(key.startswith('dir:') for key in new_mtimes):
            cache.clear(retry=True)
        else:
            dependents = invert_dependencies(cache.get('dependencies', {}))

            for key in new_mtimes:
                assert key.startswith('node:'), key
                to_evict.add(key)
                to_evict.update(
                    f'node:{d}' for d in dependents.get(key.partition(':')[2], ())
                )

            for key in to_evict:
                cache.evict(key, retry=False)

        mtimes = old_mtimes.copy()
        mtimes.update(new_mtimes)

        cache.set('mtimes', mtimes)

    return {key.partition(':')[2] for key in to_evict}


def invert_dependencies(dependencies):
    rv = {}
    for key, values in dependencies.items():
        for value in values:
            rv.setdefault(value, set()).add(key)
    return rv

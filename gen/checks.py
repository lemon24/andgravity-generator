import warnings
from dataclasses import dataclass
from typing import NamedTuple
from urllib.parse import urlparse

import bs4

from .core import Thingie

warnings.filterwarnings(
    'ignore', message='No parser was explicitly specified', module='gen.checks'
)


class InternalLink(NamedTuple):
    endpoint: str
    args: dict
    fragment: str


"""
def cache_node_methods(self, cache_decorator):
    for name in self.cacheable_node_methods:
        setattr(self, name, cache_decorator(getattr(self, name)))
"""

# TODO: get_soup() is shared, and the loop in check_* is shared
# almost doubles rendering time when fully cached,
# maybe soup should be provided by state


@dataclass
class LinkChecker:
    state: 'gen.app._NodeState'
    endpoint_info: 'gen.app._EndpointInfo'

    def get_soup(self, id):
        return bs4.BeautifulSoup(self.state.render_page(id, self.endpoint_info))

    def get_fragments(self, id):
        soup = self.get_soup(id)
        rv = set()

        for element in soup.select('[id], a[name]'):
            if 'id' in element.attrs:
                rv.add(element['id'])
            if element.name == 'a' and 'name' in element.attrs:
                rv.add(element['name'])

        return rv

    def get_internal_links(self, id):
        soup = self.get_soup(id)
        rv = {}

        for element in soup.select('a[href], img[src]'):
            if element.name == 'a':
                url = element['href']
            elif element.name == 'img':
                url = element['src']
            else:
                assert False, f"unexpected element: {element!r}"

            if url in rv:
                continue

            url_parsed = urlparse(url)
            if url_parsed.scheme not in ('http', 'https', ''):
                continue

            if not url_parsed.hostname and not url_parsed.path:
                url_parsed = url_parsed._replace(
                    path=urlparse(self.state.url_for_node(id)).path
                )

            match = self.state.match_url(url_parsed._replace(fragment='').geturl())
            if not match:
                continue

            rv[url] = InternalLink(*match, url_parsed.fragment)

        return rv

    def check_internal_links(self):
        for id in self.state.thingie.get_page_ids(hidden=None, discoverable=None):
            internal_links = self.get_internal_links(id)
            urls = {}

            for url, link in internal_links.items():
                error = None
                target_id = link.args['id']

                try:
                    self.state.thingie.get_page(target_id)
                except FileNotFoundError:
                    error = "node not found"

                # freezing checks if the URL actually exists,
                # we only check special stuff here

                if not error:
                    if link.endpoint == 'main.page':
                        if link.fragment:
                            if link.fragment not in self.get_fragments(target_id):
                                error = "fragment not found"

                    elif link.endpoint == 'main.file':
                        pass

                    elif link.endpoint == 'feed.feed':
                        if link.fragment:
                            error = "feed URL should not have fragment"

                urls[url] = {'error': error} if error else {}

            yield id, urls


@dataclass
class RenderingChecker:
    state: 'gen.app._NodeState'
    endpoint_info: 'gen.app._EndpointInfo'

    def get_soup(self, id):
        return bs4.BeautifulSoup(self.state.render_page(id, self.endpoint_info))

    def get_markdown_errors(self, id):
        soup = self.get_soup(id)
        return [element.text for element in soup.select('div.error')]

    def check_markdown_errors(self):
        for id in self.state.thingie.get_page_ids(hidden=None, discoverable=None):
            yield id, self.get_markdown_errors(id)

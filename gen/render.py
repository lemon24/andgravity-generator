import warnings
from dataclasses import dataclass
from typing import NamedTuple
from urllib.parse import urlparse

import bs4

from .core import Thingie

warnings.filterwarnings(
    'ignore', message='No parser was explicitly specified', module='gen.render'
)


class InternalLink(NamedTuple):
    endpoint: str
    args: dict
    fragment: str


@dataclass
class RenderThingie(Thingie):
    app: 'gen.app.Application'

    cacheable_node_methods = [
        'render_node',
        'get_fragments',
        'get_internal_links',
        'get_page_metadata',
        'get_page_content',
    ]

    def cache_node_methods(self, cache_decorator):
        for name in self.cacheable_node_methods:
            setattr(self, name, cache_decorator(getattr(self, name)))

    def render_node(self, id, **values):
        page = self.get_page(id)
        with self.app.node_context(id, values=values):
            return self.app.markdown(page.content)

    def get_soup(self, id):
        return bs4.BeautifulSoup(self.render_node(id))

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

        for anchor in soup.select('a[href]'):
            url = anchor['href']

            if url in rv:
                continue

            url_parsed = urlparse(url)
            if url_parsed.scheme not in ('http', 'https', ''):
                continue

            if not url_parsed.hostname and not url_parsed.path:
                url_parsed = url_parsed._replace(
                    path=urlparse(self.app.url_for_node(id)).path
                )

            match = self.app.match_url(url_parsed._replace(fragment='').geturl())
            if not match:
                continue

            rv[url] = InternalLink(*match, url_parsed.fragment)

        return rv

    def check_internal_links(self):
        for id in self.get_page_ids(hidden=None, discoverable=None):
            internal_links = self.get_internal_links(id)
            urls = {}

            for url, link in internal_links.items():
                # TODO: also check attachments/images here?
                if link.endpoint != 'main.page':
                    continue

                target_id = link.args['id']

                error = None

                try:
                    self.get_page(target_id)
                except FileNotFoundError:
                    error = "node not found"
                else:
                    if link.fragment:
                        if link.fragment not in self.get_fragments(target_id):
                            error = "fragment not found"

                urls[url] = {'error': error} if error else {}

            yield id, urls

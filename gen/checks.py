from dataclasses import dataclass
from typing import NamedTuple
from urllib.parse import urlparse


class InternalLink(NamedTuple):
    endpoint: str
    args: dict
    fragment: str


@dataclass
class MetaChecker:
    state: 'gen.cache.NodeState'
    checkers: 'list[Checker]'

    def check(self, id, endpoint):
        rv = {}
        for checker in self.checkers:
            rv.update(checker.check(id, endpoint))
        return rv

    def check_all(self):
        from .app import EndpointInfo

        endpoint = EndpointInfo()
        # TODO: check for all endpoints, not just endpoint
        # we'll have one for main+feed,
        # and one for each feed+tag combo for which we generate feeds

        for id in self.state.storage.get_page_ids(hidden=None, discoverable=None):
            errors = self.check(id, endpoint)
            if errors:
                yield id, errors


@dataclass
class LinkChecker:
    state: 'gen.cache.NodeState'

    def get_fragments(self, id, endpoint):
        soup = self.state.get_soup(id, endpoint)
        rv = set()

        for element in soup.select('[id], a[name]'):
            if 'id' in element.attrs:
                rv.add(element['id'])
            if element.name == 'a' and 'name' in element.attrs:
                rv.add(element['name'])

        return rv

    def get_internal_links(self, id, endpoint):
        soup = self.state.get_soup(id, endpoint)
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

    def check(self, id, endpoint):
        internal_links = self.get_internal_links(id, endpoint)
        urls = []

        for url, link in internal_links.items():
            error = None
            target_id = link.args['id']

            try:
                self.state.storage.get_page(target_id)
            except FileNotFoundError:
                error = "node not found"

            # freezing checks if the URL actually exists,
            # we only check special stuff here

            if not error:
                if link.endpoint == 'main.page':
                    if link.fragment:
                        if link.fragment not in self.get_fragments(target_id, endpoint):
                            error = "fragment not found"

                elif link.endpoint == 'main.file':
                    pass

                elif link.endpoint == 'feed.feed':
                    if link.fragment:
                        error = "feed URL should not have fragment"

            if error:
                urls.append({url: error})

        return {'internal-links': urls} if urls else {}


@dataclass
class RenderingChecker:
    state: 'gen.cache.NodeState'

    def check(self, id, endpoint):
        soup = self.state.get_soup(id, endpoint)
        errors = [element.text for element in soup.select('div.error')]
        return {'markdown': errors} if errors else {}

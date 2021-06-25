import ntpath
import os.path

import flask_frozen
from flask import url_for

from .app import get_state
from .app import get_thingie


class GitHubPagesFreezer(flask_frozen.Freezer):
    def urlpath_to_filepath(self, path):
        # https://github.com/Frozen-Flask/Frozen-Flask/issues/41#issuecomment-38000978
        path = super().urlpath_to_filepath(path)

        # this works for github pages alone, which rewrite /one to /one.html;
        # for disk, the page() route '/<id>' needs to change to '/<id>.html'

        name, ext = ntpath.splitext(path)
        if not ext:
            path += '.html'

        return path


# TODO: handle redirects


def make_freezer(app):
    freezer = GitHubPagesFreezer(app, log_url_for=True)

    # when developing URL generators, log_url_for should be False,
    # to make you're generating a URL and it's not from a template;
    # the rest of the time, log_url_for should be True,
    # to catch broken URLs in templates
    #
    # note that with log_url_for=True, we may actually generate a hidden
    # node if it's referred to from a template; maybe we should fix that

    @freezer.register_generator
    def page():
        for id in get_thingie().get_page_ids(discoverable=None):
            yield 'main.page', {'id': id}

    @freezer.register_generator
    def feed():
        for page in get_thingie().get_pages(discoverable=None):
            if page.has_feed:
                yield 'feed.feed', {'id': page.id}

    @freezer.register_generator
    def file():
        # only yield linked files
        for id in get_thingie().get_page_ids(discoverable=None):
            for link in get_state().link_checker.get_internal_links(id).values():
                if link.endpoint == 'main.file':
                    yield 'main.file', link.args

    @freezer.register_generator
    def tag_feed():
        for id in get_thingie().get_page_ids(discoverable=None):
            page = get_thingie().get_page(id)
            for tags in page.tag_feeds:
                yield 'feed.tag_feed', {'id': id, 'tags': tags}

    return freezer

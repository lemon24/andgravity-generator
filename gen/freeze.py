import ntpath
import os.path

import flask_frozen
from flask import current_app

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
    freezer = GitHubPagesFreezer(app)

    @freezer.register_generator
    def page():
        for id in get_thingie().get_page_ids(discoverable=None):
            yield '.page', {'id': id}

    # we deliberately do not generate anything for feed,
    # because we only want to generate the feeds linked from
    # templates; later, we can use a has-feed page metadata

    # same for file, at least initially

    # the tags feed(s) happen on-demand, though

    @freezer.register_generator
    def tags_feed():
        for id in get_thingie().get_page_ids(discoverable=None):
            page = get_thingie().get_page(id)
            for tags in page.tags_feed:
                yield 'feed.tags_feed', {'id': id, 'tags': tags}

    return freezer

import flask_frozen 
import ntpath

from .app import get_thingie


class GitHubPagesFreezer(flask_frozen.Freezer):

    def urlpath_to_filepath(self, path):
        # https://github.com/Frozen-Flask/Frozen-Flask/issues/41#issuecomment-38000978
        path = super().urlpath_to_filepath(path)
        
        # this works for github pages alone, which rewrite /one to /one.html;
        # for disk, the page() route '/<id>' needs to change to '/<id>.html'

        name, ext = ntpath.splitext(path)
        if not ext:
            if path.startswith('_feed/'):
                path += '.xml'
            else:
                path += '.html'

        return path


# TODO: handle redirects


def make_freezer(app):
    freezer = GitHubPagesFreezer(app)

    @freezer.register_generator
    def page():
        for id in get_thingie().get_page_ids():
            yield '.page', {'id': id}

    # we deliberately do not generate anything for feed,
    # because we only want to generate the feeds linked from
    # templates; later, we can use a has-feed page metadata

    return freezer
    
